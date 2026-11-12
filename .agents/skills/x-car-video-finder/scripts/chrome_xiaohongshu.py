#!/usr/bin/env python3
"""通过隔离的 Chrome 配置和 CDP 使用本机小红书登录态发布视频。"""

import argparse
import base64
import datetime
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from publish_history import PublishedHistory, default_history_path
from xiaohongshu_publisher import record_result

CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_DATA = "~/Library/Application Support/Google/Chrome"
PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"


def free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def copy_profile(source_dir, target_dir, profile="Default"):
    source = Path(source_dir) / profile
    target = Path(target_dir) / "Default"
    target.mkdir(parents=True, exist_ok=True)
    local_state = Path(source_dir) / "Local State"
    if local_state.is_file():
        shutil.copy2(local_state, Path(target_dir) / "Local State")
    for name in ("Cookies", "Preferences"):
        path = source / name
        if path.is_file():
            shutil.copy2(path, target / name)


def login_page(text, url):
    return "短信登录" in text or "/login" in url


def publish_success(text, url):
    success_text = any(value in text for value in ("发布成功", "笔记发布成功", "发布完成"))
    success_url = any(value in url for value in ("/publish/success", "/creator/notes"))
    return success_text or success_url


def public_note_url(url):
    public_prefixes = (
        "https://www.xiaohongshu.com/explore/",
        "https://www.xiaohongshu.com/discovery/item/",
    )
    return url if url.startswith(public_prefixes) else ""


class ChromeSession:
    def __init__(self, profile="Default", headless=False, timeout=180):
        self.profile = profile
        self.headless = headless
        self.timeout = timeout
        self.work = None
        self.proc = None
        self.ws = None
        self.seq = 0
        self.publish_clicked = False

    def __enter__(self):
        try:
            return self._start()
        except Exception:
            self.__exit__(*sys.exc_info())
            raise

    def _start(self):
        if sys.platform != "darwin":
            raise RuntimeError("目前仅支持 macOS Chrome")
        try:
            from websocket import create_connection
        except ImportError as exc:
            raise RuntimeError("缺少 websocket-client，请安装到 .venv-xfetch") from exc
        self.work = tempfile.mkdtemp(prefix="xhs-chrome-")
        profile_dir = os.path.join(self.work, "profile")
        copy_profile(os.path.expanduser(CHROME_DATA), profile_dir, self.profile)
        port = free_port()
        cmd = [CHROME_BINARY, "--no-sandbox", "--disable-gpu", "--disable-crashpad", "--disable-dev-shm-usage",
               "--remote-debugging-port=%d" % port, "--user-data-dir=%s" % profile_dir,
               "--no-first-run", "--no-default-browser-check"]
        if self.headless:
            cmd.insert(1, "--headless=new")
        cmd.append("about:blank")
        log_path = os.path.join(self.work, "chrome.log")
        with open(log_path, "w") as log_file:
            self.proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT,
                                         start_new_session=True)
        deadline = time.time() + 30
        version = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=2) as resp:
                    version = json.loads(resp.read().decode())
                break
            except Exception:
                time.sleep(0.5)
        if not version:
            raise RuntimeError("隔离 Chrome 的 CDP 启动失败")
        page = None
        for _ in range(20):
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=2) as resp:
                targets = json.loads(resp.read().decode())
            page = next((target for target in targets if target.get("type") == "page"), None)
            if page:
                break
            time.sleep(0.5)
        if not page:
            raise RuntimeError("找不到隔离 Chrome 页面")
        self.ws = create_connection(page["webSocketDebuggerUrl"], timeout=30)
        self.call("Page.enable")
        self.call("Runtime.enable")
        version = self.call("Browser.getVersion")
        self.call("Network.enable")
        self.call("Network.setUserAgentOverride", {
            "userAgent": version.get("userAgent", "").replace("HeadlessChrome", "Chrome"),
        })
        count = 0
        for _ in range(20):
            cookies = self.call("Storage.getCookies").get("cookies", [])
            count = sum("xiaohongshu.com" in cookie.get("domain", "") for cookie in cookies)
            if count:
                break
            time.sleep(0.5)
        if not count:
            raise RuntimeError("Chrome %s 配置中没有小红书登录 Cookie" % self.profile)
        print("已复制 Chrome 小红书会话（不输出 Cookie 值）", flush=True)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.ws:
            self.ws.close()
        if self.proc and self.proc.poll() is None:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        if self.work:
            shutil.rmtree(self.work, ignore_errors=True)

    def call(self, method, params=None):
        self.seq += 1
        current = self.seq
        self.ws.send(json.dumps({"id": current, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == current:
                if "error" in message:
                    raise RuntimeError("CDP %s: %s" % (method, message["error"].get("message")))
                return message.get("result") or {}

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        remote = result.get("result") or {}
        if remote.get("subtype") == "error":
            raise RuntimeError(remote.get("description") or "页面脚本执行失败")
        return remote.get("value")

    def page_state(self):
        value = self.evaluate(
            "JSON.stringify({url:location.href,text:document.body?document.body.innerText:''})")
        return json.loads(value or "{}")

    def publish_controls(self):
        value = self.evaluate(
            "JSON.stringify(Array.from(document.querySelectorAll('*')).filter("
            "element => (element.textContent || '').includes('发布') && element.textContent.trim().length <= 20).slice(0, 60).map("
            "element => ({tag: element.tagName, className: String(element.className),"
            " text: element.textContent.trim().slice(0, 100), disabled: String(element.disabled)})))")
        return json.loads(value or "[]")

    def navigate_publish(self):
        self.call("Page.navigate", {"url": PUBLISH_URL})
        deadline = time.time() + 45
        while time.time() < deadline:
            state = self.page_state()
            if login_page(state.get("text", ""), state.get("url", "")):
                raise RuntimeError("复制后的 Chrome 会话仍停留在小红书登录页")
            if "上传视频" in state.get("text", ""):
                return
            time.sleep(1)
        raise RuntimeError("小红书上传页加载超时")

    def screenshot(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        result = self.call("Page.captureScreenshot", {"format": "png"})
        path.write_bytes(base64.b64decode(result["data"]))

    def upload(self, video_file):
        self.call("DOM.enable")
        document = self.call("DOM.getDocument", {"depth": -1})
        result = self.call("DOM.querySelector", {
            "nodeId": document["root"]["nodeId"], "selector": "input[type=file]",
        })
        if not result.get("nodeId"):
            raise RuntimeError("找不到小红书视频上传控件")
        self.call("DOM.setFileInputFiles", {
            "nodeId": result["nodeId"], "files": [video_file],
        })

    def insert_text(self, selector, text):
        escaped = json.dumps(selector)
        found = self.evaluate("Boolean(document.querySelector(%s))" % escaped)
        if not found:
            raise RuntimeError("找不到发布输入框：%s" % selector)
        self.evaluate("document.querySelector(%s).focus()" % escaped)
        self.call("Input.insertText", {"text": text})

    def verify_content(self, plan):
        title = self.evaluate("document.querySelector('input[placeholder*=标题]').value")
        if title != plan["title"]:
            raise RuntimeError("标题复核不一致，未点击发布")
        body = self.evaluate(
            "Array.from(document.querySelectorAll('textarea,div[contenteditable=true]'))"
            ".map(element => element.innerText || element.value || '').join('\\n')") or ""
        expected = [plan["body"][:30], *plan["topics"]]
        missing = [value for value in expected if value and value not in body]
        if missing:
            raise RuntimeError("正文或话题复核不一致，未点击发布：%s" % "、".join(missing))

    def editor_present(self):
        return self.evaluate("Boolean(document.querySelector('input[placeholder*=标题]'))")

    def publish_backend_node(self):
        result = self.call("Accessibility.getFullAXTree")
        for node in result.get("nodes", []):
            role = (node.get("role") or {}).get("value")
            name = ((node.get("name") or {}).get("value") or "").strip()
            disabled = any(
                prop.get("name") == "disabled" and (prop.get("value") or {}).get("value") is True
                for prop in node.get("properties", [])
            )
            if not node.get("ignored") and role == "button" and name == "发布" and not disabled:
                return node.get("backendDOMNodeId")
        return None

    def publish_enabled(self):
        return self.publish_backend_node() is not None

    def wait_for_upload(self):
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if self.editor_present() and self.publish_enabled():
                return
            time.sleep(2)
        raise RuntimeError("视频上传或转码等待超时，未点击发布")

    def click_publish(self):
        backend_node = self.publish_backend_node()
        if not backend_node:
            raise RuntimeError("发布按钮不可用，未点击发布")
        self.call("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend_node})
        model = self.call("DOM.getBoxModel", {"backendNodeId": backend_node}).get("model", {})
        quad = model["content"]
        x = sum(quad[index] for index in (0, 2, 4, 6)) / 4
        y = sum(quad[index] for index in (1, 3, 5, 7)) / 4
        self.call("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1,
        })
        self.call("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1,
        })
        self.publish_clicked = True

    def publish_plan(self, out_dir, plan):
        self.publish_clicked = False
        self.navigate_publish()
        self.upload(plan["video_file"])
        deadline = time.time() + self.timeout
        while not self.editor_present():
            if time.time() > deadline:
                raise RuntimeError("上传后未出现标题编辑页")
            time.sleep(1)
        self.insert_text("input[placeholder*=标题]", plan["title"])
        body_selector = "textarea[placeholder*=描述], div[contenteditable=true]"
        self.insert_text(body_selector, plan["full_text"])
        self.verify_content(plan)
        self.wait_for_upload()
        before = Path(out_dir) / "browser" / ("%s_before.png" % plan["source_id"])
        self.screenshot(before)
        self.click_publish()
        deadline = time.time() + 60
        state = self.page_state()
        while time.time() < deadline:
            state = self.page_state()
            if publish_success(state.get("text", ""), state.get("url", "")):
                after = Path(out_dir) / "browser" / ("%s_after.png" % plan["source_id"])
                self.screenshot(after)
                return {"status": "published", "note_url": public_note_url(state.get("url", "")),
                        "result_url": state.get("url", "")}
            time.sleep(2)
        after = Path(out_dir) / "browser" / ("%s_unconfirmed.png" % plan["source_id"])
        self.screenshot(after)
        return {"status": "unconfirmed", "note_url": public_note_url(state.get("url", "")),
                "result_url": state.get("url", "")}


def load_plans(out_dir, only=None):
    out_dir = Path(out_dir)
    plans_dir = out_dir / "plans_confirmed"
    if only:
        paths = [plans_dir / ("%s.json" % only)]
    else:
        manifest_path = out_dir / "manifest.json"
        ordered_ids = []
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())
            ordered_ids = [str(item["candidate"]["id"]) for item in manifest.get("downloads", [])]
        paths = [plans_dir / ("%s.json" % source_id) for source_id in ordered_ids]
        included = {path.name for path in paths}
        paths.extend(path for path in sorted(plans_dir.glob("*.json")) if path.name not in included)
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError("找不到确认发布计划")
    plans = []
    for path in paths:
        plan = json.loads(path.read_text())
        if plan.get("status") != "ready" or not plan.get("confirm_publish"):
            raise ValueError("计划未获得实际发布确认：%s" % path)
        if not Path(plan["video_file"]).is_file():
            raise ValueError("计划视频不存在：%s" % plan["video_file"])
        plans.append(plan)
    return plans


def run(out_dir, history, profile, headless, only=None, preflight=False, timeout=180):
    plans = load_plans(out_dir, only)
    results = []
    with ChromeSession(profile, headless, timeout) as chrome:
        chrome.navigate_publish()
        print("Chrome 小红书登录态与上传页验证通过", flush=True)
        if preflight:
            return {"status": "ready", "published": 0, "results": []}
        for plan in plans:
            match = history.check(plan["source_id"], plan["title"])
            if match:
                print("跳过已发布或待确认：%s" % plan["title"], flush=True)
                results.append({"source_id": plan["source_id"], "title": plan["title"],
                                "status": "skipped", "match": match})
                continue
            (Path(out_dir) / "xiaohongshu_publish_plan.json").write_text(
                json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
            print("开始发布：%s" % plan["title"], flush=True)
            try:
                result = chrome.publish_plan(out_dir, plan)
                receipt = record_result(out_dir, history, result["status"], result.get("note_url", ""),
                                        result.get("result_url", ""))
                results.append({"source_id": plan["source_id"], "title": plan["title"],
                                "status": result["status"], "note_url": result.get("note_url", ""),
                                "result_url": result.get("result_url", ""), "receipt": receipt})
                if result["status"] != "published":
                    print("发布结果待确认，停止后续发布：%s" % plan["title"], flush=True)
                    break
                print("发布成功：%s" % plan["title"], flush=True)
                time.sleep(3)
            except Exception as exc:
                try:
                    debug_dir = Path(out_dir) / "browser"
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    state = chrome.page_state()
                    state["publish_controls"] = chrome.publish_controls()
                    (debug_dir / ("%s_error.json" % plan["source_id"])).write_text(
                        json.dumps(state, ensure_ascii=False, indent=2) + "\n")
                    chrome.screenshot(debug_dir / ("%s_error.png" % plan["source_id"]))
                except Exception:
                    pass
                status = "unconfirmed" if chrome.publish_clicked else "error"
                if chrome.publish_clicked:
                    receipt = record_result(out_dir, history, "unconfirmed")
                    results.append({"source_id": plan["source_id"], "title": plan["title"],
                                    "status": status, "receipt": receipt, "error": str(exc)})
                else:
                    results.append({"source_id": plan["source_id"], "title": plan["title"],
                                    "status": status, "error": str(exc)})
                print("%s：%s；停止后续发布" % (status, str(exc)), flush=True)
                break
    summary = {
        "status": "complete" if len(results) == len(plans) and all(
            item["status"] in ("published", "skipped") for item in results) else "incomplete",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "published": sum(item["status"] == "published" for item in results),
        "unconfirmed": sum(item["status"] == "unconfirmed" for item in results),
        "skipped": sum(item["status"] == "skipped" for item in results),
        "results": results,
    }
    (Path(out_dir) / "chrome_publication_status.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--history-file", default=str(default_history_path()))
    parser.add_argument("--browser-profile", default="Default")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--only", help="只发布指定原帖 ID")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    out_dir = Path(args.out).resolve()
    try:
        summary = run(out_dir, PublishedHistory(args.history_file), args.browser_profile,
                      args.headless, args.only, args.preflight_only, args.timeout)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["status"] in ("ready", "complete") else 2
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, "错误: %s\n" % exc)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""通过隔离的 Chrome 配置和 CDP 使用本机小红书登录态发布视频。"""

import argparse
import contextlib
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

from content_policy import strip_preset_ai_disclosure
from publication_runtime import SubmissionJournal, execution_lock, atomic_json
from publication_queue import PublicationQueue
from publish_history import PublishedHistory, default_history_path, default_draft_history_path
from xiaohongshu_publisher import (
    attach_note,
    latest_records,
    ensure_publish_available,
    record_draft,
    record_result,
    release_plan,
    validate_no_material_source_copy,
    valid_note_url,
)

CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_DATA = "~/Library/Application Support/Google/Chrome"
PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target=video"


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
    success_url = "/publish/success" in url
    return success_text or success_url


def public_note_url(url):
    return url if valid_note_url(url) else ""


def draft_success(text):
    if any(value in text for value in ("草稿保存成功", "已保存草稿", "保存成功")):
        return True
    return any("草稿箱(%d)" % count in text for count in range(1, 100))


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
        self.draft_clicked = False
        self.before_submit = None
        self.after_submit = None

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

    def dismiss_topic_suggestions(self):
        for event_type in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", {
                "type": event_type, "key": "Escape", "code": "Escape",
                "windowsVirtualKeyCode": 27,
            })

    def topic_suggestions_visible(self):
        return bool(self.evaluate(
            "Array.from(document.querySelectorAll('*')).some(element => "
            "(element.innerText || '').trim() === '新建话题' && element.offsetParent !== null)"))

    def verify_content(self, plan):
        title = self.evaluate("document.querySelector('input[placeholder*=标题]').value")
        if title != plan["title"]:
            raise RuntimeError("标题复核不一致，未点击发布")
        body = self.evaluate(
            "(() => { const element = document.querySelector('textarea[placeholder*=描述], div[contenteditable=true]');"
            " return element ? (element.value ?? element.innerText ?? '') : ''; })()") or ""
        expected = plan.get("full_text")
        if expected is None:  # 兼容旧调用，但仍检查完整正文。
            expected = "\n".join([plan["body"], " ".join(plan["topics"])])
        if " ".join(body.split()) != " ".join(expected.split()):
            raise RuntimeError("正文或话题全文复核不一致，未点击发布")

    def editor_present(self):
        return self.evaluate("Boolean(document.querySelector('input[placeholder*=标题]'))")

    def publish_backend_node(self, button_name="发布"):
        result = self.call("Accessibility.getFullAXTree")
        for node in result.get("nodes", []):
            role = (node.get("role") or {}).get("value")
            name = ((node.get("name") or {}).get("value") or "").strip()
            disabled = any(
                prop.get("name") == "disabled" and (prop.get("value") or {}).get("value") is True
                for prop in node.get("properties", [])
            )
            if not node.get("ignored") and role == "button" and name == button_name and not disabled:
                return node.get("backendDOMNodeId")
        return None

    def publish_enabled(self, button_name="发布"):
        return self.publish_backend_node(button_name) is not None

    def wait_for_upload(self, button_name="发布"):
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            state = self.page_state()
            uploading = any(value in state.get("text", "") for value in ("上传中", "视频上传中"))
            if (self.editor_present() and self.publish_enabled(button_name) and not uploading
                    and not self.topic_suggestions_visible()):
                return
            time.sleep(2)
        raise RuntimeError("视频上传或转码等待超时，未执行后续操作")

    def click_publish(self, button_name="发布"):
        backend_node = self.publish_backend_node(button_name)
        if not backend_node:
            raise RuntimeError("按钮不可用：%s" % button_name)
        self.call("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend_node})
        model = self.call("DOM.getBoxModel", {"backendNodeId": backend_node}).get("model", {})
        quad = model["content"]
        x = sum(quad[index] for index in (0, 2, 4, 6)) / 4
        y = sum(quad[index] for index in (1, 3, 5, 7)) / 4
        # 落盘后才发送事件；响应丢失也按结果待确认处理。
        if self.before_submit:
            self.before_submit()
        if button_name == "发布":
            self.publish_clicked = True
        else:
            self.draft_clicked = True
        self.call("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1,
        })
        self.call("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1,
        })

    def prepare_plan(self, out_dir, plan, action="publish"):
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
        self.dismiss_topic_suggestions()
        self.verify_content(plan)
        self.wait_for_upload("暂存离开" if action == "draft" else "发布")
        self.screenshot(Path(out_dir) / "browser" / ("%s_before.png" % plan["source_id"]))

    def discover_public_note_url(self):
        value = self.evaluate(
            "JSON.stringify(Array.from(document.querySelectorAll('a[href]')).map("
            "element => element.href).filter(url => url.startsWith("
            "'https://www.xiaohongshu.com/explore/') || url.startsWith("
            "'https://www.xiaohongshu.com/discovery/item/')))")
        urls = json.loads(value or "[]")
        return next((url for url in urls if public_note_url(url)), "")

    def lookup_public_note(self, title):
        """后台精确标题唯一匹配；只取页面实际公开链接，缺失时不猜测 ID。"""
        self.call("Page.navigate", {"url": "https://creator.xiaohongshu.com/creator/notes"})
        deadline = time.monotonic() + min(self.timeout, 30)
        while time.monotonic() < deadline:
            state = self.page_state()
            if login_page(state.get("text", ""), state.get("url", "")):
                raise RuntimeError("公开链接补查需要重新登录小红书")
            candidates = self.evaluate("""(() => {
                const title = TITLE;
                const matches = Array.from(document.querySelectorAll('*')).filter(e =>
                    e.children.length === 0 && (e.innerText || '').trim() === title);
                return matches.map(e => {
                    const container = e.closest('a[href], article, [role=listitem], [data-note-id], .note-item, .note-card');
                    for (const node of (container ? [container] : [])) {
                        const links = Array.from(node.querySelectorAll('a[href]'));
                        if (node.matches('a[href]')) links.push(node);
                        const urls = [...new Set(links.map(a => a.href).filter(url =>
                            url.startsWith('https://www.xiaohongshu.com/explore/') ||
                            url.startsWith('https://www.xiaohongshu.com/discovery/item/')))];
                        if (urls.length) return {title, urls};
                    }
                    return {title, urls: []};
                });
            })()""".replace('TITLE', json.dumps(title))) or []
            if len(candidates) > 1:
                return ""
            if len(candidates) == 1:
                if candidates[0].get('title') != title:
                    return ''
                urls = list(dict.fromkeys(public_note_url(url) for url in candidates[0].get('urls', [])))
                urls = [url for url in urls if url]
                if len(urls) == 1:
                    return urls[0]
                if len(urls) > 1:
                    return ""
            time.sleep(1)
        return ""

    def publish_plan(self, out_dir, plan):
        self.publish_clicked = False
        self.prepare_plan(out_dir, plan)
        self.click_publish()
        deadline = time.time() + 60
        state = self.page_state()
        while time.time() < deadline:
            state = self.page_state()
            if publish_success(state.get("text", ""), state.get("url", "")):
                after = Path(out_dir) / "browser" / ("%s_after.png" % plan["source_id"])
                result = {"status": "published", "note_url": public_note_url(state.get("url", "")),
                          "result_url": state.get("url", "")}
                if self.after_submit:
                    self.after_submit(result)
                try:
                    self.screenshot(after)
                except Exception:
                    pass
                return result
            time.sleep(2)
        after = Path(out_dir) / "browser" / ("%s_unconfirmed.png" % plan["source_id"])
        self.screenshot(after)
        return {"status": "unconfirmed", "note_url": public_note_url(state.get("url", "")),
                "result_url": state.get("url", "")}

    def draft_plan(self, out_dir, plan):
        self.draft_clicked = False
        self.prepare_plan(out_dir, plan, "draft")
        self.click_publish("暂存离开")
        deadline = time.time() + 60
        state = self.page_state()
        while time.time() < deadline:
            state = self.page_state()
            if draft_success(state.get("text", "")):
                after = Path(out_dir) / "browser" / ("%s_draft.png" % plan["source_id"])
                result = {"status": "draft_saved", "result_url": state.get("url", "")}
                if self.after_submit:
                    self.after_submit(result)
                try:
                    self.screenshot(after)
                except Exception:
                    pass
                return result
            time.sleep(2)
        after = Path(out_dir) / "browser" / ("%s_draft_unconfirmed.png" % plan["source_id"])
        self.screenshot(after)
        return {"status": "unconfirmed_draft", "result_url": state.get("url", "")}


def load_plans(out_dir, only=None):
    out_dir = Path(out_dir)
    path = out_dir / "xiaohongshu_publish_plan.json"
    if not path.is_file():
        raise ValueError("找不到发布计划；请先运行 xiaohongshu_publisher.py plan")
    plan = json.loads(path.read_text())
    action = plan.get("action")
    if plan.get("status") != "ready" or action not in ("publish", "draft"):
        raise ValueError("计划必须明确使用发布或草稿动作：%s" % path)
    if only and str(plan.get("source_id")) != str(only):
        raise ValueError("--only 只能核对当前计划，不能绕过队列选择其他素材")
    validate_no_material_source_copy(plan.get("full_text"))
    if "full_text" in plan:
        plan["full_text"] = strip_preset_ai_disclosure(plan["full_text"])
    if "ai_disclosure" in plan:
        plan["ai_disclosure"] = strip_preset_ai_disclosure(plan["ai_disclosure"])
    if not Path(plan["video_file"]).is_file():
        raise ValueError("计划视频不存在：%s" % plan["video_file"])
    return [plan]


def recover_submission(history):
    """恢复已确认结果；提交中断只能阻断，不会自动再次点击。调用方持执行锁。"""
    journal = SubmissionJournal(history.path)
    attempt = journal.read()
    if not attempt or attempt['status'] == 'recorded':
        return
    plan, out_dir = attempt['plan'], attempt['out_dir']
    if attempt['status'] == 'confirmed':
        result = attempt['result']
        if plan['action'] == 'draft':
            record_draft(out_dir, result['status'], result.get('result_url', ''), plan=plan)
        else:
            record_result(out_dir, history, result['status'], result.get('note_url', ''),
                          result.get('result_url', ''), plan=plan)
        journal.write('recorded', plan, out_dir, result=result)
        return
    if attempt['status'] != 'submitting':
        raise ValueError('未知提交日志状态，停止发布')
    if plan['action'] == 'draft':
        drafts = PublishedHistory(default_draft_history_path())
        latest = next((r for r in reversed(drafts.records())
                       if str(r.get('source_id')) == str(plan['source_id'])), None)
        if (latest and latest.get('status') in ('draft_saved', 'failed')
                and latest.get('recorded_at', '')[:19] >= attempt['updated_at'][:19]):
            journal.write('recorded', plan, out_dir, resolution=latest['status'])
            return
        if not latest or latest.get('status') != 'unconfirmed_draft':
            record_draft(out_dir, 'unconfirmed_draft', plan=plan)
        raise RuntimeError('草稿提交结果待确认，必须先核验草稿箱并登记 draft_saved 或 resolve 为 failed；不得重复提交')
    latest = next((r for r in reversed(history.records())
                   if str(r.get('source_id')) == str(plan['source_id'])), None)
    if latest and latest.get('status') == 'failed' and (
            latest.get('recorded_at', '')[:19] >= attempt['updated_at'][:19]):
        PublicationQueue(history_path=history.path).release(plan['source_id'], allow_unconfirmed=True)
        journal.write('recorded', plan, out_dir, resolution='failed')
        return
    if latest and latest.get('status') == 'published':
        record_result(out_dir, history, 'published', latest.get('note_url', ''),
                      latest.get('result_url', ''), plan=plan)
        journal.write('recorded', plan, out_dir, resolution='published')
        return
    record_result(out_dir, history, 'unconfirmed', plan=plan)
    raise RuntimeError('上次提交中断，结果待确认；请核验后台后更正历史，禁止自动重发')


def recover_note_urls(chrome, out_dir, history):
    """只为已确认发布的记录补链接，不把标题匹配当成提交成功证据。"""
    unresolved = []
    for record in latest_records(history.records()):
        if record.get('status') != 'published' or record.get('note_url'):
            continue
        try:
            url = chrome.lookup_public_note(record['title'])
            # 公开地址不能属于另一条已登记内容。
            if url and any(r.get('note_url') == url and r.get('source_id') != record['source_id']
                           for r in latest_records(history.records())):
                url = ''
            if url:
                attach_note(out_dir, history, record['source_id'], url)
                continue
            reason = '后台没有唯一匹配的公开链接'
        except Exception as exc:
            reason = str(exc)
        unresolved.append({'source_id': record['source_id'], 'title': record['title'], 'reason': reason})
    return unresolved


def release_unsubmitted(out_dir, history):
    """启动/导航失败也释放计划，但绝不释放已经开始提交的候选。"""
    attempt = SubmissionJournal(history.path).read()
    if attempt and attempt['status'] in ('submitting', 'confirmed'):
        return
    try:
        release_plan(out_dir, history)
    except (OSError, ValueError, KeyError):
        pass


def run(out_dir, history, profile, headless, only=None, preflight=False, timeout=180,
        sleep_fn=time.sleep):
    with execution_lock(history.path, profile):
        if not preflight:
            recover_submission(history)
        try:
            return run_locked(out_dir, history, profile, headless, only, preflight, timeout, sleep_fn)
        except Exception:
            release_unsubmitted(out_dir, history)
            raise


def run_locked(out_dir, history, profile, headless, only=None, preflight=False, timeout=180,
               sleep_fn=time.sleep, session=None):
    plans = load_plans(out_dir, only)
    results = []
    context = contextlib.nullcontext(session) if session is not None else ChromeSession(profile, headless, timeout)
    with context as chrome:
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
            atomic_json(Path(out_dir) / "xiaohongshu_publish_plan.json", plan)
            action = plan["action"]
            if action == "publish":
                ensure_publish_available(history)
            chrome.publish_clicked = False
            chrome.draft_clicked = False
            journal = SubmissionJournal(history.path)
            chrome.before_submit = lambda: journal.write('submitting', plan, out_dir)
            chrome.after_submit = lambda result: journal.write('confirmed', plan, out_dir, result=result)
            print("开始%s：%s" % ("发布" if action == "publish" else "保存草稿", plan["title"]), flush=True)
            try:
                if action == "draft":
                    result = chrome.draft_plan(out_dir, plan)
                    receipt = record_draft(out_dir, result["status"], result.get("result_url", ""), plan=plan)
                else:
                    result = chrome.publish_plan(out_dir, plan)
                    receipt = record_result(out_dir, history, result["status"], result.get("note_url", ""),
                                            result.get("result_url", ""), plan=plan)
                if result['status'] in ('published', 'draft_saved'):
                    journal.write('recorded', plan, out_dir, result=result)
                if result['status'] == 'published' and not result.get('note_url'):
                    pending = recover_note_urls(chrome, out_dir, history)
                    result['note_url_pending'] = bool(pending)
                    match = history.check(plan['source_id'])
                    if match and match.get('note_url'):
                        result['note_url'] = match['note_url']
                        receipt = attach_note(out_dir, history, plan['source_id'], match['note_url'])
                results.append({"source_id": plan["source_id"], "title": plan["title"],
                                "note_url_pending": result.get('note_url_pending', False),
                                "status": result["status"], "note_url": result.get("note_url", ""),
                                "result_url": result.get("result_url", ""), "receipt": receipt})
                if result["status"] not in ("published", "draft_saved"):
                    print("结果待确认，停止后续操作：%s" % plan["title"], flush=True)
                    break
                print("操作成功：%s" % plan["title"], flush=True)
                sleep_fn(3)
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
                attempt = journal.read()
                if (attempt and attempt['plan']['source_id'] == plan['source_id']
                        and attempt['status'] in ('confirmed', 'recorded')
                        and attempt.get('result', {}).get('status') in ('published', 'draft_saved')):
                    # 成功已落盘，即使后续写历史失败也不能降级成未确认。
                    raise
                if chrome.publish_clicked:
                    status = "unconfirmed"
                    receipt = record_result(out_dir, history, "unconfirmed")
                elif chrome.draft_clicked:
                    status = "unconfirmed_draft"
                    receipt = record_draft(out_dir, "unconfirmed_draft")
                else:
                    status = "error"
                    receipt = None
                    if action == "publish":
                        release_plan(out_dir, history)
                results.append({"source_id": plan["source_id"], "title": plan["title"],
                                "status": status, "receipt": receipt, "error": str(exc)})
                print("%s：%s；停止后续操作" % (status, str(exc)), flush=True)
                break
    summary = {
        "status": "complete" if len(results) == len(plans) and all(
            item["status"] in ("published", "draft_saved", "skipped") for item in results) else "incomplete",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "published": sum(item["status"] == "published" for item in results),
        "draft_saved": sum(item["status"] == "draft_saved" for item in results),
        "unconfirmed": sum(item["status"] in ("unconfirmed", "unconfirmed_draft") for item in results),
        "skipped": sum(item["status"] == "skipped" for item in results),
        "results": results,
    }
    (Path(out_dir) / "chrome_publication_status.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


CHROME_PROFILE = "Default"


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--history-file", default=str(default_history_path()))
    parser.add_argument("--browser-profile", default=CHROME_PROFILE,
                        help="Chrome Profile；默认固定为 Default")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--only", help="只发布指定原帖 ID")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=180)
    return parser


def main(argv=None):
    parser = build_parser()
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

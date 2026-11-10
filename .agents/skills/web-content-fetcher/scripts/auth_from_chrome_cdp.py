#!/usr/bin/env python3
"""macOS 备用方案：用临时 headless Chrome + CDP 读取 X 的 auth_token / ct0。

不需要钥匙串授权：Chrome 自己解密 Cookie，脚本通过 DevTools 协议
`Storage.getCookies` 取结果。会复制一份 Cookie 库到临时目录、启动一个
独立的 headless Chrome（--no-sandbox，端口随机），用完即杀。

用法:
  python3 auth_from_chrome_cdp.py --out /tmp/xfetch-auth.env
依赖:
  pip install websocket-client
"""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

BROWSERS = {
    "chrome": {
        "binary": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "data": "~/Library/Application Support/Google/Chrome",
    },
    "edge": {
        "binary": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "data": "~/Library/Application Support/Microsoft Edge",
    },
    "brave": {
        "binary": "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "data": "~/Library/Application Support/BraveSoftware/Brave-Browser",
    },
    "arc": {
        "binary": "/Applications/Arc.app/Contents/MacOS/Arc",
        "data": "~/Library/Application Support/Arc/User Data",
    },
    "chromium": {
        "binary": "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "data": "~/Library/Application Support/Chromium",
    },
}

WANTED = ("auth_token", "ct0")
X_DOMAINS = (".x.com", "x.com", ".twitter.com", "twitter.com")


def die(msg):
    print("错误: " + msg, file=sys.stderr)
    sys.exit(1)


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def copy_profile(source_dir, profile, target_dir):
    os.makedirs(os.path.join(target_dir, "Default"), exist_ok=True)
    for name in ("Local State",):
        src = os.path.join(source_dir, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(target_dir, name))
    for name in ("Cookies", "Preferences"):
        src = os.path.join(source_dir, profile, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(target_dir, "Default", name))


def wait_for_cdp(port, timeout=25):
    deadline = time.time() + timeout
    url = "http://127.0.0.1:%d/json/version" % port
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return json.loads(resp.read().decode())
        except Exception:  # noqa: BLE001
            time.sleep(0.7)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--browser", choices=sorted(BROWSERS), default="chrome")
    ap.add_argument("--profile", default="Default")
    ap.add_argument("--out", default="/tmp/xfetch-auth.env")
    ap.add_argument("--timeout", type=int, default=25)
    args = ap.parse_args()

    if sys.platform != "darwin":
        die("该脚本只支持 macOS。")
    try:
        from websocket import create_connection
    except ImportError:
        die("缺少 websocket-client：pip install websocket-client")

    conf = BROWSERS[args.browser]
    if not os.path.exists(conf["binary"]):
        die("找不到浏览器: %s" % conf["binary"])
    source = os.path.expanduser(conf["data"])
    if not os.path.isdir(os.path.join(source, args.profile)):
        die("找不到配置目录: %s/%s" % (source, args.profile))

    work = tempfile.mkdtemp(prefix="xcf-cdp-")
    profile_dir = os.path.join(work, "profile")
    log_path = os.path.join(work, "chrome.log")
    port = free_port()
    proc = None
    try:
        copy_profile(source, args.profile, profile_dir)
        cmd = [
            conf["binary"], "--headless=new", "--no-sandbox", "--disable-gpu",
            "--disable-crashpad", "--disable-dev-shm-usage",
            "--remote-debugging-port=%d" % port,
            "--user-data-dir=%s" % profile_dir,
            "--no-first-run", "--no-default-browser-check", "about:blank",
        ]
        with open(log_path, "w") as log_file:
            proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        version = wait_for_cdp(port, args.timeout)
        if not version:
            die("Chrome DevTools 没起来，日志: %s" % log_path)

        ws = create_connection(version["webSocketDebuggerUrl"], timeout=30)
        ws.send(json.dumps({"id": 1, "method": "Storage.getCookies", "params": {}}))
        cookies = []
        while True:
            message = json.loads(ws.recv())
            if message.get("id") == 1:
                cookies = (message.get("result") or {}).get("cookies") or []
                break
        ws.close()

        values = {}
        for cookie in cookies:
            if cookie.get("name") in WANTED and cookie.get("domain") in X_DOMAINS:
                values[cookie["name"]] = cookie.get("value") or ""

        missing = [name for name in WANTED if not values.get(name)]
        if missing:
            die("没取到 %s。确认该浏览器的 %s 配置已登录 x.com。" % (", ".join(missing), args.browser))

        out = os.path.abspath(os.path.expanduser(args.out))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as fp:
            fp.write("XFETCH_AUTH_TOKEN=%s\n" % values["auth_token"])
            fp.write("XFETCH_CT0=%s\n" % values["ct0"])
        os.chmod(out, 0o600)
        print("✓ 通过 CDP 从 %s 读取 %s" % (args.browser, ", ".join(sorted(values))))
        print("  登录态写入: %s (权限 600)" % out)
        print("  用法: finder.py --auth-file %s ..." % out)
    finally:
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            for _ in range(10):
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()

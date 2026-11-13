#!/usr/bin/env python3
"""统一的浏览器 Web 登录会话参数与 X 会话适配。"""

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


BROWSERS = (
    "chrome", "chromium", "edge", "brave", "firefox", "safari",
    "opera", "vivaldi", "whale",
)
X_NATIVE_BROWSERS = {"chrome", "chromium", "edge", "brave", "arc"}
X_EXTRACT_BROWSERS = {
    "chrome", "chromium", "edge", "brave", "firefox", "safari",
    "opera", "vivaldi",
}


class SessionError(ValueError):
    pass


def add_session_arguments(parser, default_browser="off", default_profile=None):
    group = parser.add_argument_group("Web 登录会话")
    source = group.add_mutually_exclusive_group()
    source.add_argument(
        "--web-session", choices=("off",) + BROWSERS, default=default_browser,
        help="使用指定浏览器的登录 Cookie；默认 %s" % default_browser,
    )
    source.add_argument(
        "--cookies-file", default=None,
        help="使用 Netscape cookies.txt；与 --web-session 二选一",
    )
    group.add_argument(
        "--browser-profile", default=default_profile,
        help="浏览器 Profile；默认 %s" % (default_profile or "浏览器默认"),
    )


def validate_session(args):
    browser = getattr(args, "web_session", "off") or "off"
    profile = getattr(args, "browser_profile", None)
    cookies_file = getattr(args, "cookies_file", None)
    if profile and browser == "off":
        raise SessionError("--browser-profile 必须与 --web-session 一起使用")
    if cookies_file:
        path = Path(os.path.expanduser(cookies_file)).resolve()
        if not path.is_file():
            raise SessionError("cookies-file 不存在: %s" % path)
    return browser, profile


def ytdlp_session_args(args):
    browser, profile = validate_session(args)
    cookies_file = getattr(args, "cookies_file", None)
    if cookies_file:
        path = str(Path(os.path.expanduser(cookies_file)).resolve())
        return ["--cookies", path]
    if browser == "off":
        return ["--no-cookies-from-browser"]
    selector = browser + (":" + profile if profile else "")
    return ["--cookies-from-browser", selector]


def session_summary(args):
    browser, profile = validate_session(args)
    cookies_file = getattr(args, "cookies_file", None)
    return {
        "mode": "cookies_file" if cookies_file else ("browser" if browser != "off" else "off"),
        "browser": None if browser == "off" else browser,
        "profile": profile,
        "cookies_file": (
            str(Path(os.path.expanduser(cookies_file)).resolve()) if cookies_file else None
        ),
    }


def load_key_value_file(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def redact_session_values(text, env=None):
    if env is None:
        env = os.environ
    redacted = text or ""
    for name in ("XFETCH_AUTH_TOKEN", "XFETCH_CT0", "AUTH_TOKEN", "CT0"):
        value = env.get(name)
        if value and len(value) >= 8:
            redacted = redacted.replace(value, "<redacted>")
    return redacted


def _temporary_session_dir():
    path = tempfile.mkdtemp(prefix="xcf-web-session-")
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return Path(path)


def _run_auth_command(cmd, env, browser):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SessionError("读取 %s 的 X Web 会话失败: %s" % (browser, type(exc).__name__))
    if proc.returncode != 0:
        raise SessionError(
            "无法从 %s 读取完整且有效的 X 登录会话。请确认浏览器已登录 X，"
            "检查 --browser-profile，或改用 --auth-file/--cookies-file。" % browser
        )


def prepare_x_auth(args, env, py, xfetch):
    """按 auth-file > cookies-file > web-session > 既有会话准备 X 凭据。"""
    browser, profile = validate_session(args)
    cookies_file = getattr(args, "cookies_file", None)
    explicit_auth = bool(getattr(args, "auth_file", None))
    has_pair = bool(env.get("XFETCH_AUTH_TOKEN") and env.get("XFETCH_CT0"))
    if explicit_auth and has_pair:
        return env
    if not cookies_file and browser == "off":
        return env

    work = _temporary_session_dir()
    if cookies_file:
        store = work / "cookies.json"
        cmd = [
            py, xfetch, "auth", "import", "--file",
            str(Path(os.path.expanduser(cookies_file)).resolve()),
            "--cookies", str(store),
        ]
        _run_auth_command(cmd, env, "cookies-file")
        env["XFETCH_COOKIES"] = str(store)
        return env

    if sys.platform == "darwin" and browser in X_NATIVE_BROWSERS:
        profile_arg = profile or "Default"
        auth_file = work / "x-auth.env"
        native_script = str(Path(__file__).with_name("auth_from_chrome.py"))
        cdp_script = str(Path(__file__).with_name("auth_from_chrome_cdp.py"))
        common = ["--browser", browser, "--profile", profile_arg, "--out", str(auth_file)]
        try:
            _run_auth_command([py, native_script] + common, env, browser)
        except SessionError:
            _run_auth_command([py, cdp_script] + common, env, browser + " CDP")
        env.update(load_key_value_file(auth_file))
        return env

    if browser not in X_EXTRACT_BROWSERS:
        raise SessionError("X 不支持从 %s 提取会话；请改用 --cookies-file/--auth-file" % browser)
    if profile:
        raise SessionError(
            "%s 的 X 会话提取暂不支持指定 Profile；请改用 --cookies-file" % browser
        )
    store = work / "cookies.json"
    cmd = [
        py, xfetch, "auth", "extract", "--browser", browser,
        "--cookies", str(store),
    ]
    _run_auth_command(cmd, env, browser)
    env["XFETCH_COOKIES"] = str(store)
    return env

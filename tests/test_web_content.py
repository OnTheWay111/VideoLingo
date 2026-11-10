import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPTS_DIR = Path(__file__).parents[1] / ".agents/skills/x-car-video-finder/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import finder
import web_content
import web_session


def session_args(browser="off", profile=None, cookies_file=None, auth_file=None):
    return SimpleNamespace(
        web_session=browser,
        browser_profile=profile,
        cookies_file=cookies_file,
        auth_file=auth_file,
    )


def test_ytdlp_session_is_off_by_default():
    assert web_session.ytdlp_session_args(session_args()) == ["--no-cookies-from-browser"]


def test_ytdlp_session_passes_browser_and_profile():
    args = session_args("chrome", "Profile 1")

    assert web_session.ytdlp_session_args(args) == [
        "--cookies-from-browser", "chrome:Profile 1"
    ]
    assert web_session.session_summary(args) == {
        "mode": "browser", "browser": "chrome", "profile": "Profile 1",
        "cookies_file": None,
    }


def test_ytdlp_session_accepts_cookie_file(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")

    assert web_session.ytdlp_session_args(session_args(cookies_file=str(cookies))) == [
        "--cookies", str(cookies)
    ]


def test_browser_profile_requires_browser():
    with pytest.raises(web_session.SessionError, match="web-session"):
        web_session.ytdlp_session_args(session_args(profile="Default"))


def test_prepare_x_auth_uses_native_browser_profile(monkeypatch):
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        out = Path(cmd[cmd.index("--out") + 1])
        out.write_text("XFETCH_AUTH_TOKEN=dummy-auth-token\nXFETCH_CT0=dummy-ct0-token\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(web_session.subprocess, "run", fake_run)
    monkeypatch.setattr(web_session.sys, "platform", "darwin")
    env = {}
    args = session_args("chrome", "Profile 2")

    result = web_session.prepare_x_auth(args, env, "python", "xfetch.py")

    assert result["XFETCH_AUTH_TOKEN"] == "dummy-auth-token"
    assert result["XFETCH_CT0"] == "dummy-ct0-token"
    assert "--profile" in commands[0]
    assert "Profile 2" in commands[0]
    assert "dummy-auth-token" not in commands[0]


def test_prepare_x_auth_falls_back_to_cdp(monkeypatch):
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        if "auth_from_chrome.py" in cmd[1]:
            return SimpleNamespace(returncode=1, stdout="", stderr="keychain denied")
        out = Path(cmd[cmd.index("--out") + 1])
        out.write_text("XFETCH_AUTH_TOKEN=cdp-auth-token\nXFETCH_CT0=cdp-ct0-token\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(web_session.subprocess, "run", fake_run)
    monkeypatch.setattr(web_session.sys, "platform", "darwin")

    result = web_session.prepare_x_auth(
        session_args("chrome", "Default"), {}, "python", "xfetch.py"
    )

    assert len(commands) == 2
    assert commands[0][1].endswith("auth_from_chrome.py")
    assert commands[1][1].endswith("auth_from_chrome_cdp.py")
    assert result["XFETCH_AUTH_TOKEN"] == "cdp-auth-token"
    assert result["XFETCH_CT0"] == "cdp-ct0-token"


def test_prepare_x_auth_explicit_auth_file_wins(monkeypatch):
    def unexpected_run(*args, **kwargs):
        raise AssertionError("不应再次读取浏览器")

    monkeypatch.setattr(web_session.subprocess, "run", unexpected_run)
    env = {"XFETCH_AUTH_TOKEN": "existing-auth", "XFETCH_CT0": "existing-ct0"}

    result = web_session.prepare_x_auth(
        session_args("chrome", auth_file="explicit.env"), env, "python", "xfetch.py"
    )

    assert result is env
    assert result["XFETCH_AUTH_TOKEN"] == "existing-auth"


def test_prepare_x_auth_imports_cookie_file_without_saving_default_session(monkeypatch, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("dummy cookie export")
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(web_session.subprocess, "run", fake_run)

    result = web_session.prepare_x_auth(
        session_args(cookies_file=str(cookies)), {}, "python", "xfetch.py"
    )

    assert commands[0][2:4] == ["auth", "import"]
    assert str(cookies) in commands[0]
    assert result["XFETCH_COOKIES"] != str(Path.home() / ".config/xfetch/cookies.json")
    assert Path(result["XFETCH_COOKIES"]).name == "cookies.json"


def test_session_values_are_redacted():
    env = {"XFETCH_AUTH_TOKEN": "secret-auth-value", "XFETCH_CT0": "secret-ct0-value"}

    detail = web_session.redact_session_values(
        "failed: secret-auth-value and secret-ct0-value", env
    )

    assert "secret-auth-value" not in detail
    assert "secret-ct0-value" not in detail
    assert detail.count("<redacted>") == 2


def test_normalize_youtube_entry_matches_finder_fields():
    entry = {
        "id": "abc123",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "extractor_key": "Youtube",
        "title": "Cybertruck off-road review",
        "description": "A car review",
        "uploader": "Car Channel",
        "uploader_id": "@cars",
        "upload_date": "20261109",
        "like_count": 120,
        "comment_count": 12,
        "view_count": 5000,
        "duration": 95,
    }

    item = web_content.normalize_entry(entry)

    assert item["id"] == "youtube-abc123"
    assert item["platform"] == "youtube"
    assert item["author"] == "@cars"
    assert item["date"] == "2026-11-09"
    assert item["likes"] == 120
    assert item["replies"] == 12
    assert item["views"] == 5000
    assert item["duration"] == 95


def test_platform_detection_does_not_trust_lookalike_domains():
    assert web_content.detect_platform("https://x.com/user/status/1") == "x"
    assert web_content.detect_platform("https://x.com.example.org/video") == "web"
    assert web_content.detect_platform("https://youtu.be/abc") == "youtube"


def test_metadata_command_uses_explicit_session_and_playlist_rule():
    args = session_args("firefox", "default-release")

    command = web_content.metadata_command("https://www.tiktok.com/@car/video/1", args, True)

    assert command[:2] == ["yt-dlp", "--ignore-config"]
    assert command[2:4] == ["--cookies-from-browser", "firefox:default-release"]
    assert "--no-playlist" in command
    assert command[-2:] == ["--", "https://www.tiktok.com/@car/video/1"]


def test_download_command_falls_back_when_height_has_no_match(tmp_path):
    args = session_args()
    args.max_height = 480
    item = {"url": "https://x.com/car/status/1"}

    command = web_content.download_command(item, args, tmp_path)

    assert command[command.index("-f") + 1] == "bv*[height<=480]+ba/b[height<=480]/b"


def test_finder_download_reuses_web_session(tmp_path):
    candidate = {"url": "https://x.com/car/status/1"}
    args = session_args("chrome", "Default")
    args.max_height = 1080

    command = finder.build_download_command(candidate, tmp_path, args)

    assert command[:2] == ["yt-dlp", "--ignore-config"]
    assert command[2:4] == ["--cookies-from-browser", "chrome:Default"]
    assert command[-2:] == ["--", candidate["url"]]


def test_cli_writes_normalized_jsonl_and_manifest(monkeypatch, tmp_path):
    entry = {
        "id": "1", "webpage_url": "https://www.instagram.com/p/1/",
        "extractor_key": "Instagram", "title": "Car reel",
    }

    def fake_fetch(target, args, direct_url):
        return [web_content.normalize_entry(entry, target)], None

    monkeypatch.setattr(web_content, "fetch_metadata", fake_fetch)

    result = web_content.main([
        "--url", "https://www.instagram.com/p/1/", "--out", str(tmp_path)
    ])

    assert result == 0
    item = json.loads((tmp_path / "items.jsonl").read_text().strip())
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert item["platform"] == "instagram"
    assert manifest["item_count"] == 1
    assert manifest["session"]["mode"] == "off"
    assert manifest["errors"] == []


def test_cli_returns_error_when_all_sources_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(
        web_content, "fetch_metadata", lambda *args: ([], "login required")
    )

    result = web_content.main([
        "--url", "https://www.tiktok.com/@car/video/1", "--out", str(tmp_path)
    ])

    assert result == 1
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["item_count"] == 0
    assert manifest["errors"][0]["error"] == "login required"

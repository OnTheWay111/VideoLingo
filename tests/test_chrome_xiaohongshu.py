import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).parents[1] / ".agents/skills/x-car-video-finder/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
MODULE_PATH = SCRIPTS_DIR / "chrome_xiaohongshu.py"
SPEC = importlib.util.spec_from_file_location("chrome_xiaohongshu", MODULE_PATH)
chrome = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(chrome)


def test_login_page_detection():
    assert chrome.login_page("短信登录 发送验证码", "https://creator.xiaohongshu.com/login")
    assert chrome.login_page("其他内容", "https://creator.xiaohongshu.com/login?source=official")
    assert not chrome.login_page("上传视频", "https://creator.xiaohongshu.com/publish/publish")


def test_publish_success_requires_explicit_success_state():
    assert chrome.publish_success("笔记发布成功", "https://creator.xiaohongshu.com/publish")
    assert chrome.publish_success("", "https://creator.xiaohongshu.com/creator/notes")
    assert not chrome.publish_success("发布笔记 上传视频", "https://creator.xiaohongshu.com/publish")


def test_copy_profile_only_copies_session_files(tmp_path):
    source = tmp_path / "source"
    (source / "Default").mkdir(parents=True)
    (source / "Local State").write_text("state")
    (source / "Default" / "Cookies").write_bytes(b"cookies")
    (source / "Default" / "Preferences").write_text("preferences")
    (source / "Default" / "History").write_bytes(b"do-not-copy")
    target = tmp_path / "target"

    chrome.copy_profile(source, target)

    assert (target / "Local State").read_text() == "state"
    assert (target / "Default" / "Cookies").read_bytes() == b"cookies"
    assert (target / "Default" / "Preferences").read_text() == "preferences"
    assert not (target / "Default" / "History").exists()


def test_load_plans_requires_confirmation_and_real_video(tmp_path):
    plans = tmp_path / "plans_confirmed"
    plans.mkdir()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    plan = {"status": "ready", "confirm_publish": True, "video_file": str(video)}
    (plans / "123.json").write_text(json.dumps(plan))

    assert chrome.load_plans(tmp_path)[0]["video_file"] == str(video)

    plan["confirm_publish"] = False
    (plans / "123.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="确认"):
        chrome.load_plans(tmp_path)


def test_click_publish_tracks_actual_click_for_unconfirmed_recovery():
    session = chrome.ChromeSession(headless=True)
    session.publish_backend_node = lambda: 42
    calls = []

    def fake_call(method, params=None):
        calls.append((method, params))
        if method == "DOM.getBoxModel":
            return {"model": {"content": [100, 200, 120, 200, 120, 220, 100, 220]}}
        return {}

    session.call = fake_call
    session.click_publish()

    assert session.publish_clicked is True
    assert [method for method, _ in calls][-2:] == [
        "Input.dispatchMouseEvent", "Input.dispatchMouseEvent"]


def test_failed_resolution_releases_unconfirmed_record(tmp_path):
    history = chrome.PublishedHistory(tmp_path / "history.jsonl")
    history.add("123", "标题", status="unconfirmed")
    history.add("123", "标题", status="failed")

    assert history.check(source_id="123") is None


def test_publish_backend_node_waits_for_enabled_button():
    session = chrome.ChromeSession(headless=True)
    session.call = lambda method, params=None: {"nodes": [
        {"ignored": False, "backendDOMNodeId": 1,
         "role": {"value": "button"}, "name": {"value": "发布"},
         "properties": [{"name": "disabled", "value": {"value": True}}]},
        {"ignored": False, "backendDOMNodeId": 2,
         "role": {"value": "button"}, "name": {"value": "发布"},
         "properties": [{"name": "disabled", "value": {"value": False}}]},
    ]}

    assert session.publish_backend_node() == 2

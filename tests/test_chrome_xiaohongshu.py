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


def test_xiaohongshu_defaults_to_chrome_default_profile():
    args = chrome.build_parser().parse_args(["--out", "work/xfetch-run"])

    assert args.browser_profile == "Default"
    assert chrome.ChromeSession().profile == "Default"


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
    session.publish_backend_node = lambda button_name="发布": 42
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


def test_result_page_is_not_public_note_url():
    success = "https://creator.xiaohongshu.com/publish/success?source=official"
    public = "https://www.xiaohongshu.com/explore/abc123"

    assert chrome.public_note_url(success) == ""
    assert chrome.public_note_url(public) == public


def test_copy_profile_honors_source_profile(tmp_path):
    source = tmp_path / "source"
    (source / "Profile 1").mkdir(parents=True)
    (source / "Profile 1" / "Cookies").write_bytes(b"profile-one")
    target = tmp_path / "target"

    chrome.copy_profile(source, target, "Profile 1")

    assert (target / "Default" / "Cookies").read_bytes() == b"profile-one"


def test_load_plans_uses_manifest_download_order(tmp_path):
    plans = tmp_path / "plans_confirmed"
    plans.mkdir()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    for source_id in ("200", "100"):
        (plans / ("%s.json" % source_id)).write_text(json.dumps({
            "status": "ready", "confirm_publish": True, "video_file": str(video),
            "source_id": source_id,
        }))
    (tmp_path / "manifest.json").write_text(json.dumps({"downloads": [
        {"candidate": {"id": "100"}}, {"candidate": {"id": "200"}},
    ]}))

    assert [plan["source_id"] for plan in chrome.load_plans(tmp_path)] == ["100", "200"]


def test_verify_content_rechecks_title_body_and_topics():
    session = chrome.ChromeSession(headless=True)
    plan = {"title": "标题", "body": "这是一段用于复核的正文内容", "topics": ["#汽车", "#改装车"]}
    session.evaluate = lambda expression: (
        plan["title"] if expression.endswith(".value") else plan["body"] + " " + " ".join(plan["topics"])
    )

    session.verify_content(plan)

    session.evaluate = lambda expression: plan["title"] if expression.endswith(".value") else "错误正文"
    with pytest.raises(RuntimeError, match="复核"):
        session.verify_content(plan)


def test_enter_failure_cleans_temporary_profile(tmp_path):
    session = chrome.ChromeSession(headless=True)
    session.work = str(tmp_path / "temporary-profile")
    Path(session.work).mkdir()
    Path(session.work, "Cookies").write_bytes(b"temp")

    def fail_start():
        raise RuntimeError("模拟初始化失败")

    session._start = fail_start
    with pytest.raises(RuntimeError, match="初始化失败"):
        session.__enter__()

    assert not Path(session.work).exists()


def test_random_delay_is_between_one_and_ten_minutes():
    for _ in range(100):
        assert 60 <= chrome.random_delay_seconds() <= 600


def test_load_plans_accepts_draft_action(tmp_path):
    plans = tmp_path / "plans_confirmed"
    plans.mkdir()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    (plans / "123.json").write_text(json.dumps({
        "status": "ready", "action": "draft", "confirm_publish": False,
        "video_file": str(video), "source_id": "123", "title": "草稿标题",
    }))

    assert chrome.load_plans(tmp_path)[0]["action"] == "draft"


def test_random_delay_only_occurs_between_successful_publications(tmp_path, monkeypatch):
    plans = tmp_path / "plans_confirmed"
    plans.mkdir()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    for source_id in ("100", "200"):
        (plans / ("%s.json" % source_id)).write_text(json.dumps({
            "status": "ready", "action": "publish", "confirm_publish": True,
            "video_file": str(video), "source_id": source_id, "title": source_id,
        }))
    (tmp_path / "manifest.json").write_text(json.dumps({"downloads": [
        {"candidate": {"id": "100"}}, {"candidate": {"id": "200"}},
    ]}))

    class FakeChrome:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def navigate_publish(self):
            return None

        def publish_plan(self, out_dir, plan):
            return {"status": "published", "note_url": "", "result_url": "success"}

    monkeypatch.setattr(chrome, "ChromeSession", lambda *args, **kwargs: FakeChrome())
    monkeypatch.setattr(chrome, "record_result", lambda *args, **kwargs: {"status": "published"})
    sleeps = []
    history = chrome.PublishedHistory(tmp_path / "history.jsonl")

    summary = chrome.run(tmp_path, history, "Default", True,
                         sleep_fn=sleeps.append, delay_fn=lambda: 321)

    assert summary["status"] == "complete"
    assert summary["published"] == 2
    assert sleeps == [3, 321, 3]


def test_draft_success_requires_saved_state_or_nonzero_draft_count():
    assert chrome.draft_success("草稿箱(1)")
    assert chrome.draft_success("草稿保存成功")
    assert not chrome.draft_success("草稿箱(0)")


def test_wait_for_upload_uses_action_specific_button():
    session = chrome.ChromeSession(headless=True)
    session.editor_present = lambda: True
    names = []
    session.publish_enabled = lambda name: names.append(name) or True

    session.wait_for_upload("暂存离开")

    assert names == ["暂存离开"]

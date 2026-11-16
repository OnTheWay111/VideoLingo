import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).parents[1] / ".agents/skills/xcar-xhs/scripts"
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


def test_navigate_publish_uses_direct_video_url():
    session = chrome.ChromeSession(headless=True)
    calls = []
    session.call = lambda method, params=None: calls.append((method, params))
    session.page_state = lambda: {"text": "上传视频", "url": chrome.PUBLISH_URL}

    session.navigate_publish()

    assert calls == [("Page.navigate", {
        "url": "https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target=video",
    })]


def test_publish_success_requires_explicit_success_state():
    assert chrome.publish_success("笔记发布成功", "https://creator.xiaohongshu.com/publish")
    assert not chrome.publish_success("", "https://creator.xiaohongshu.com/creator/notes")
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


def test_load_plans_accepts_publish_action_without_confirmation(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    plan = {"status": "ready", "action": "publish", "video_file": str(video)}
    (tmp_path / "xiaohongshu_publish_plan.json").write_text(json.dumps(plan))

    assert chrome.load_plans(tmp_path)[0]["video_file"] == str(video)

    plan["action"] = "preview"
    (tmp_path / "xiaohongshu_publish_plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="发布或草稿动作"):
        chrome.load_plans(tmp_path)


def test_load_plans_rejects_material_source_copy(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    (tmp_path / "xiaohongshu_publish_plan.json").write_text(json.dumps({
        "status": "ready", "action": "publish", "video_file": str(video),
        "full_text": "素材来自品牌官方 X 账号；选题、素材初筛及文字由 AI 辅助整理。",
    }))

    with pytest.raises(ValueError, match="素材来源说明"):
        chrome.load_plans(tmp_path)


def test_load_plans_removes_preset_ai_disclosure_from_legacy_plan(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    (tmp_path / "xiaohongshu_publish_plan.json").write_text(json.dumps({
        "status": "ready", "action": "publish", "video_file": str(video),
        "ai_disclosure": "概念车画面；选题、素材初筛及文字由 AI 辅助整理。",
        "full_text": "正文\n\n讨论：问题？\n\n概念车画面；选题、素材初筛及文字由 AI 辅助整理。\n\n#汽车 #概念车",
    }))

    plan = chrome.load_plans(tmp_path)[0]

    assert plan["ai_disclosure"] == "概念车画面"
    assert plan["full_text"] == "正文\n\n讨论：问题？\n\n概念车画面\n\n#汽车 #概念车"


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


def test_load_plans_reads_only_current_plan(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    for source_id in ("200", "100"):
        (plans / ("%s.json" % source_id)).write_text(json.dumps({
            "status": "ready", "action": "publish", "video_file": str(video),
            "source_id": source_id,
        }))
    (tmp_path / "xiaohongshu_publish_plan.json").write_text(json.dumps({
        "status": "ready", "action": "publish", "video_file": str(video), "source_id": "200",
    }))
    (tmp_path / "manifest.json").write_text(json.dumps({"downloads": [
        {"candidate": {"id": "100"}}, {"candidate": {"id": "200"}},
    ]}))

    assert [plan["source_id"] for plan in chrome.load_plans(tmp_path)] == ["200"]
    with pytest.raises(ValueError, match="不能绕过队列"):
        chrome.load_plans(tmp_path, only="100")


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


def test_verify_content_tolerates_contenteditable_paragraph_spacing():
    session = chrome.ChromeSession(headless=True)
    plan = {
        "title": "雨中 NSX，跳灯太经典",
        "body": "本田 NSX 是 90 年代日本跑车的代表之一。\n中置后驱布局、经典跳灯和低趴车身，在雨里更有辨识度。",
        "topics": ["#本田NSX", "#JDM"],
    }
    page_text = plan["body"].replace("\n", "\n\n") + "\n" + " ".join(plan["topics"])
    session.evaluate = lambda expression: (
        plan["title"] if expression.endswith(".value") else page_text
    )

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


def test_load_plans_accepts_draft_action(tmp_path):
    plans = tmp_path / "plans"
    plans.mkdir()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    (tmp_path / "xiaohongshu_publish_plan.json").write_text(json.dumps({
        "status": "ready", "action": "draft",
        "video_file": str(video), "source_id": "123", "title": "草稿标题",
    }))

    assert chrome.load_plans(tmp_path)[0]["action"] == "draft"


def test_run_executes_only_current_plan_without_batch_delay(tmp_path, monkeypatch):
    plans = tmp_path / "plans"
    plans.mkdir()
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    for source_id in ("100", "200"):
        (plans / ("%s.json" % source_id)).write_text(json.dumps({
            "status": "ready", "action": "publish",
            "video_file": str(video), "source_id": source_id, "title": source_id,
        }))
    (tmp_path / "xiaohongshu_publish_plan.json").write_text(json.dumps({
        "status": "ready", "action": "publish",
        "video_file": str(video), "source_id": "200", "title": "200",
    }))

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

    summary = chrome.run(tmp_path, history, "Default", True, sleep_fn=sleeps.append)

    assert summary["status"] == "complete"
    assert summary["published"] == 1
    assert summary["results"][0]["source_id"] == "200"
    assert sleeps == [3]


def test_draft_success_requires_saved_state_or_nonzero_draft_count():
    assert chrome.draft_success("草稿箱(1)")
    assert chrome.draft_success("草稿保存成功")
    assert not chrome.draft_success("草稿箱(0)")


def test_dismiss_topic_suggestions_sends_escape():
    session = chrome.ChromeSession(headless=True)
    calls = []
    session.call = lambda method, params=None: calls.append((method, params))

    session.dismiss_topic_suggestions()

    assert [params["key"] for method, params in calls
            if method == "Input.dispatchKeyEvent"] == ["Escape", "Escape"]


def test_wait_for_upload_uses_action_specific_button():
    session = chrome.ChromeSession(headless=True)
    session.editor_present = lambda: True
    session.page_state = lambda: {"text": "视频文件 上传完成"}
    session.topic_suggestions_visible = lambda: False
    names = []
    session.publish_enabled = lambda name: names.append(name) or True

    session.wait_for_upload("暂存离开")

    assert names == ["暂存离开"]


def test_wait_for_upload_blocks_while_progress_is_visible(monkeypatch):
    session = chrome.ChromeSession(headless=True)
    session.editor_present = lambda: True
    session.publish_enabled = lambda name: True
    session.topic_suggestions_visible = lambda: False
    states = iter([{"text": "视频文件 上传中 0%"}, {"text": "视频文件 上传完成"}])
    session.page_state = lambda: next(states)
    monkeypatch.setattr(chrome.time, "sleep", lambda seconds: None)

    session.wait_for_upload()

    with pytest.raises(StopIteration):
        session.page_state()


def test_discover_public_note_url_ignores_creator_result_links():
    session = chrome.ChromeSession(headless=True)
    session.evaluate = lambda expression: json.dumps([
        "https://creator.xiaohongshu.com/publish/success",
        "https://www.xiaohongshu.com/explore/real-note",
    ])

    assert session.discover_public_note_url() == "https://www.xiaohongshu.com/explore/real-note"

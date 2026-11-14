import copy
import datetime
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPTS_DIR = Path(__file__).parents[1] / ".agents/skills/xcar-xhs/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import content_policy
import note_metrics
import publication_queue
import publish_history
import xiaohongshu_publisher

FINDER_SPEC = importlib.util.spec_from_file_location("x_car_finder_history_test", SCRIPTS_DIR / "finder.py")
finder = importlib.util.module_from_spec(FINDER_SPEC)
FINDER_SPEC.loader.exec_module(finder)


def candidate(tweet_id):
    return {
        "id": tweet_id,
        "url": "https://x.com/car/status/%s" % tweet_id,
        "author": "car",
        "author_name": "Car",
        "date": "2026-11-12",
        "likes": 100,
        "replies": 2,
        "reposts": 3,
        "views": 1000,
        "text": "Rare car",
    }


def finalized_output(tmp_path, tweet_id="123", title="S660 的 660cc 在哪？"):
    video = tmp_path / "videos" / "clip.mp4"
    video.parent.mkdir()
    video.write_bytes(b"video")
    item = {
        "candidate": candidate(tweet_id),
        "video": {"file": str(video), "duration": 30, "width": 1920, "height": 1080},
        "title": title,
        "recommendation": "这台 Honda S660 的 660cc 发动机位于座椅后方，视频展示了对应结构。",
        "brand": "Honda",
        "model": "S660",
        "modification_project": "",
        "title_detail": "660cc",
        "verifiable_fact": "Honda S660 的 660cc 发动机位于座椅后方",
        "fact_source": "https://example.com/honda-s660",
        "discussion_question": "你更想看哪种机械布局？",
        "score": 80,
        "score_breakdown": {
            "first_three_seconds": 22,
            "knowledge_point": 22,
            "save_value": 16,
            "discussion_question": 10,
            "recent_diversity": 10,
        },
        "topics": ["#罕见车型", "#汽车文化"],
    }
    (tmp_path / "manifest.json").write_text(json.dumps({"downloads": [item]}, ensure_ascii=False))
    return item


def test_history_matches_id_and_normalized_title_without_duplicates(tmp_path):
    path = tmp_path / "published.jsonl"
    history = publish_history.PublishedHistory(path)
    history.add("123", "罕见 跑车 亮相", "https://x.com/car/status/123")

    assert history.check(source_id="123")["matched_by"] == "source_id"
    assert history.check(source_id="999", title="罕见跑车亮相")["matched_by"] == "title"
    assert history.check(source_id="999", title="另一台车") is None

    history.add("123", "罕见 跑车 亮相")
    assert len(history.records()) == 1
    saved = json.loads(path.read_text().strip())
    assert saved["source_id"] == "123"
    assert saved["platform"] == "xiaohongshu"


def test_unconfirmed_publication_also_blocks_reuse(tmp_path):
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    history.add("123", "待确认结果", status="unconfirmed")

    assert history.check(source_id="123")["status"] == "unconfirmed"


def test_finder_filters_published_candidates_before_download(tmp_path):
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    history.add("old", "已经发布")
    candidates = [candidate("old"), candidate("new")]

    fresh, skipped = finder.partition_published_candidates(candidates, history)

    assert [item["id"] for item in fresh] == ["new"]
    assert skipped[0]["candidate"]["id"] == "old"
    assert skipped[0]["reason"] == "已发布过，下载前跳过"


def test_report_records_published_skip_without_rejected_video(tmp_path):
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    history.add("old", "已经发布")
    old = candidate("old")
    skipped = [{"candidate": old, "match": history.check(source_id="old"),
                "reason": "已发布过，下载前跳过"}]
    args = SimpleNamespace(sort="likes", max_duration=300, publish_history=str(history.path))

    finder.write_outputs(args, "汽车", ["car filter:media"], [], [], [], [],
                         {"already_published_skipped": 1}, tmp_path, skipped)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    report = (tmp_path / "REPORT.md").read_text()
    assert manifest["publish_skipped"] == skipped
    assert manifest["publish_history_file"] == str(history.path)
    assert "## 已发布跳过（未下载）" in report
    assert "本次没有新增入选视频" in report


def test_default_plan_publishes_without_additional_confirmation(tmp_path):
    finalized_output(tmp_path)
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")

    plan = xiaohongshu_publisher.build_plan(tmp_path, "123", history)
    xiaohongshu_publisher.save_plan(tmp_path, plan)

    assert plan["status"] == "ready"
    assert plan["action"] == "publish"
    assert plan["browser_actions"][-1] == "publish"
    assert "confirm_publish" not in plan
    assert "rights_confirmed" not in plan
    assert plan["ai_disclosure"] == ""
    assert "AI 辅助整理" not in plan["full_text"]
    assert plan["publish_url"] == (
        "https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target=video")
    assert Path(plan["video_file"]).is_file()
    assert (tmp_path / "plans" / "123.json").is_file()


def test_cli_defaults_to_publish_without_confirmation_flag(tmp_path, capsys):
    finalized_output(tmp_path)

    result = xiaohongshu_publisher.main([
        "--out", str(tmp_path), "--history-file", str(tmp_path / "published.jsonl"),
        "plan", "--tweet-id", "123",
    ])

    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["action"] == "publish"
    assert (tmp_path / "plans" / "123.json").is_file()


def test_preview_plan_cannot_be_recorded_as_published(tmp_path):
    finalized_output(tmp_path)
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    plan = xiaohongshu_publisher.build_plan(tmp_path, "123", history, action="preview")
    stale_plan = tmp_path / "plans" / "123.json"
    stale_plan.parent.mkdir()
    stale_plan.write_text("{}")
    xiaohongshu_publisher.save_plan(tmp_path, plan)

    with pytest.raises(ValueError, match="publish 计划"):
        xiaohongshu_publisher.record_result(tmp_path, history)
    assert history.records() == []
    assert not stale_plan.exists()


def test_successful_record_saves_public_note_and_result_url(tmp_path):
    finalized_output(tmp_path)
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    plan = xiaohongshu_publisher.build_plan(tmp_path, "123", history)
    xiaohongshu_publisher.save_plan(tmp_path, plan)

    receipt = xiaohongshu_publisher.record_result(
        tmp_path, history, note_url="https://www.xiaohongshu.com/explore/note1",
        result_url="https://creator.xiaohongshu.com/publish/success")

    assert receipt["status"] == "published"
    assert receipt["result_url"].endswith("/publish/success")
    assert history.check(source_id="123")["note_url"].endswith("note1")


def test_publish_plan_blocks_new_manifest_with_fewer_than_three_selected(tmp_path):
    item = finalized_output(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps({
        "minimum_selected": 3,
        "downloads": [item],
    }, ensure_ascii=False))
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")

    with pytest.raises(ValueError, match="至少要有 3 条"):
        xiaohongshu_publisher.build_plan(tmp_path, "123", history)

    assert not (tmp_path / "plans" / "123.json").exists()


def test_plan_validates_xiaohongshu_title_length(tmp_path):
    finalized_output(tmp_path, title="这是一个明显超过二十个字符的小红书汽车视频标题")
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")

    with pytest.raises(ValueError, match="1–20"):
        xiaohongshu_publisher.build_plan(tmp_path, "123", history)


def test_main_skips_published_and_forbidden_topics_before_download(tmp_path, monkeypatch):
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    history.add("old", "已经发布")

    def tweet(tweet_id, likes, reposts=100):
        text = "Underground car meet" if tweet_id == "banned" else "Rare car video"
        return {"id": tweet_id, "text": text, "favorite_count": likes,
                "retweet_count": reposts,
                "author": {"screen_name": "car", "name": "Car"},
                "media": [{"type": "video", "url": "https://example.com/video"}]}

    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["finder.py", "--prompt", "汽车", "--out", str(out),
                                      "--publish-history", str(history.path), "--top", "1",
                                      "--min-likes", "1000", "--min-reposts", "100",
                                      "--xiaohongshu"])
    monkeypatch.setattr(finder, "session_summary", lambda args: {})
    monkeypatch.setattr(finder, "resolve_xfetch", lambda value: "xfetch")
    monkeypatch.setattr(finder, "resolve_python", lambda value: "python")
    monkeypatch.setattr(finder, "prepare_x_auth", lambda args, env, py, xfetch: env)
    monkeypatch.setattr(finder, "auth_ok", lambda py, xfetch, env: (True, ""))
    monkeypatch.setattr(finder, "search_query",
                        lambda *args, **kwargs: [tweet("banned", 2000), tweet("old", 1000),
                                                 tweet("new", 2000), tweet("low-reposts", 5000, 99),
                                                 tweet("low-likes", 999, 1000)])
    downloaded = []

    def fake_download(item, videos_dir, args):
        downloaded.append(item["id"])
        return None, "测试停止"

    monkeypatch.setattr(finder, "download_video", fake_download)

    with pytest.raises(SystemExit) as exc_info:
        finder.main()

    assert exc_info.value.code == 3
    assert downloaded == ["new"]
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["stats"]["already_published_skipped"] == 1
    assert manifest["stats"]["engagement_skipped"] == 2
    assert manifest["stats"]["xiaohongshu_topic_skipped"] == 1
    assert manifest["publish_skipped"][0]["candidate"]["id"] == "old"
    assert manifest["topic_skipped"][0]["candidate"]["id"] == "banned"
    assert {item["candidate"]["id"] for item in manifest["engagement_skipped"]} == {
        "low-reposts", "low-likes",
    }
    assert "## 互动不足跳过（未下载）" in (out / "REPORT.md").read_text()


@pytest.mark.parametrize("text", [
    "东京地下车聚实拍", "车主故事：他的第一台车", "普通公路驾驶 POV", "Goodwood Festival of Speed",
])
def test_publisher_rejects_forbidden_topics(text):
    assert xiaohongshu_publisher.forbidden_topic_match({"text": text})


@pytest.mark.parametrize("text", [
    "山路驾驶 POV", "Porsche 911 mountain road driving POV", "Honda S660 touge drive",
])
def test_publisher_allows_mountain_driving_topics(text):
    assert xiaohongshu_publisher.forbidden_topic_match({"text": text}) is None


def test_publisher_blocks_forbidden_topic_even_with_neutral_title(tmp_path):
    item = finalized_output(tmp_path)
    item["candidate"]["text"] = "Underground car meet in Tokyo"
    (tmp_path / "manifest.json").write_text(json.dumps({"downloads": [item]}, ensure_ascii=False))
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")

    with pytest.raises(ValueError, match="禁选主题"):
        xiaohongshu_publisher.build_plan(tmp_path, "123", history)
    assert history.records() == []


def test_auto_topic_metadata_is_saved_to_publish_history(tmp_path):
    item = finalized_output(tmp_path)
    item["brand"] = "Honda"
    item["file_topic"] = "Beat 车身细节"
    topic_plan = {
        "strategy_version": 2,
        "pillar": "rare_models",
        "series_id": "rare_jdm",
        "series_episode": 1,
    }
    (tmp_path / "manifest.json").write_text(json.dumps({
        "downloads": [item],
        "topic_plan": topic_plan,
    }, ensure_ascii=False))
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    plan = xiaohongshu_publisher.build_plan(tmp_path, "123", history)
    xiaohongshu_publisher.save_plan(tmp_path, plan)

    xiaohongshu_publisher.record_result(tmp_path, history)

    saved = history.check(source_id="123")
    assert saved["brand"] == "Honda"
    assert saved["file_topic"] == "Beat 车身细节"
    assert saved["topic_pillar"] == "rare_models"
    assert saved["topic_series_id"] == "rare_jdm"
    assert saved["topic_series_episode"] == 1
    assert saved["topic_strategy_version"] == 2


def test_draft_plan_omits_preset_disclosure_and_uses_separate_history(tmp_path):
    finalized_output(tmp_path)
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    plan = xiaohongshu_publisher.build_plan(tmp_path, "123", history, action="draft")
    xiaohongshu_publisher.save_plan(tmp_path, plan)
    drafts = publish_history.PublishedHistory(tmp_path / "drafts.jsonl")

    assert plan["action"] == "draft"
    assert "AI 辅助整理" not in plan["full_text"]
    receipt = xiaohongshu_publisher.record_draft(tmp_path, draft_history=drafts)

    assert receipt["status"] == "draft_saved"
    assert drafts.records()[0]["status"] == "draft_saved"
    assert history.records() == []


@pytest.mark.parametrize("action", ["publish", "draft"])
def test_publish_and_draft_do_not_require_disclosure(tmp_path, action):
    item = finalized_output(tmp_path)
    item.pop("ai_disclosure", None)
    (tmp_path / "manifest.json").write_text(json.dumps({"downloads": [item]}, ensure_ascii=False))
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")

    plan = xiaohongshu_publisher.build_plan(tmp_path, "123", history, action=action)

    assert plan["status"] == "ready"
    assert plan["ai_disclosure"] == ""
    assert "AI 辅助整理" not in plan["full_text"]


@pytest.mark.parametrize("disclosure, expected", [
    ("素材来自品牌官方 X 账号；选题、素材初筛及文字由 AI 辅助整理。", ""),
    ("概念车画面；素材来自品牌官方 X 账号；选题、素材初筛及文字由 AI 辅助整理。", "概念车画面"),
    ("AI 生成画面；选题、素材初筛及文字由 AI 辅助整理。", "AI 生成画面"),
])
def test_publish_plan_removes_material_source_and_preset_ai_disclosure(
        tmp_path, disclosure, expected):
    item = finalized_output(tmp_path)
    item["ai_disclosure"] = disclosure
    (tmp_path / "manifest.json").write_text(json.dumps({"downloads": [item]}, ensure_ascii=False))
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")

    plan = xiaohongshu_publisher.build_plan(tmp_path, "123", history)

    assert plan["ai_disclosure"] == expected
    assert "素材来自" not in plan["full_text"]
    assert "AI 辅助整理" not in plan["full_text"]
    if expected:
        assert expected in plan["full_text"]


def test_publish_plan_rejects_material_source_in_body(tmp_path):
    item = finalized_output(tmp_path)
    item["recommendation"] = "素材来自品牌官方 X 账号；" + item["recommendation"]
    (tmp_path / "manifest.json").write_text(json.dumps({"downloads": [item]}, ensure_ascii=False))
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")

    with pytest.raises(ValueError, match="素材来源说明"):
        xiaohongshu_publisher.build_plan(tmp_path, "123", history)


def test_publisher_blocks_visual_talking_head_with_neutral_text(tmp_path):
    item = finalized_output(tmp_path)
    item["recommendation"] = "Suzuki Cappuccino 展示车头与改装部件。"
    item["verification"] = {"presentation": "talking_head_explainer"}
    (tmp_path / "manifest.json").write_text(json.dumps({"downloads": [item]}, ensure_ascii=False))
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")

    with pytest.raises(ValueError, match="禁选主题"):
        xiaohongshu_publisher.build_plan(tmp_path, "123", history)
    assert history.records() == []


def test_car_only_detail_video_is_not_blocked_as_explainer():
    item = {
        "text": "Suzuki Cappuccino stationary exterior and engine bay details",
        "verification": {"presentation": "car_only"},
    }

    assert xiaohongshu_publisher.forbidden_topic_match(item) is None


def finalized_batch(tmp_path):
    first = finalized_output(tmp_path, "100", "Honda S660 的 660cc")
    items = [first]
    for source_id, brand, model, score in (
        ("200", "BMW", "Z1", 92),
        ("300", "Toyota", "Sera", 85),
    ):
        item = copy.deepcopy(first)
        item["candidate"] = candidate(source_id)
        item["title"] = "%s %s 的 660cc" % (brand, model)
        item["brand"] = brand
        item["model"] = model
        item["score"] = score
        item["score_breakdown"] = {
            "first_three_seconds": 25 if score == 92 else 23,
            "knowledge_point": 25 if score == 92 else 23,
            "save_value": 20 if score == 92 else 18,
            "discussion_question": 11,
            "recent_diversity": 11 if score == 92 else 10,
        }
        items.append(item)
    (tmp_path / "manifest.json").write_text(json.dumps({
        "minimum_selected": 3, "selection_target": 3, "downloads": items,
    }, ensure_ascii=False))
    return items


def test_three_verified_items_can_generate_another_plan_on_the_same_day(tmp_path):
    finalized_batch(tmp_path)
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    queue = publication_queue.PublicationQueue(history_path=history.path)

    plan = xiaohongshu_publisher.build_plan(tmp_path, None, history, queue=queue)

    assert plan["source_id"] == "200"
    assert plan["score"] == 92
    assert [(entry["source_id"], entry["status"]) for entry in queue.records()] == [
        ("100", "queued"), ("200", "planned"), ("300", "queued"),
    ]
    xiaohongshu_publisher.save_plan(tmp_path, plan)
    xiaohongshu_publisher.record_result(
        tmp_path, history, note_url="https://www.xiaohongshu.com/explore/note200", queue=queue)

    next_plan = xiaohongshu_publisher.build_plan(tmp_path, None, history, queue=queue)

    assert next_plan["source_id"] == "300"
    assert next_plan["score"] == 85


def test_selection_defers_same_brand_as_previous_note(tmp_path):
    finalized_batch(tmp_path)
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    queue = publication_queue.PublicationQueue(history_path=history.path)
    yesterday = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).isoformat()
    history.add("old", "旧 BMW", status="published", brand="BMW", model="M1",
                published_at=yesterday, recorded_at=yesterday,
                note_url="https://www.xiaohongshu.com/explore/old")

    plan = xiaohongshu_publisher.build_plan(tmp_path, None, history, queue=queue)

    assert plan["source_id"] == "300"
    assert plan["brand"] == "Toyota"
    assert queue.records()[1]["status"] == "queued"


def test_same_model_and_same_project_are_duplicates_even_from_other_posts():
    now = datetime.datetime(2026, 11, 13, tzinfo=datetime.timezone.utc)
    history = [{
        "source_id": "old", "title": "迈凯伦转子", "status": "published",
        "brand": "迈凯伦", "model": "720S", "modification_project": "转子发动机移植",
        "recorded_at": "2026-11-01T00:00:00+00:00",
    }]

    same_model = content_policy.content_duplicate(
        content_policy.content_identity({"brand": "McLaren", "model": "720S"}), history, now=now)
    same_project = content_policy.content_duplicate(
        content_policy.content_identity({
            "brand": "McLaren", "model": "750S", "modification_project": "rotary swap",
        }), history, now=now)
    expired = content_policy.content_duplicate(
        content_policy.content_identity({"brand": "McLaren", "model": "720S"}), history,
        now=now + datetime.timedelta(days=30))

    assert same_model["matched_by"] == "model"
    assert same_project["matched_by"] == "modification_project"
    assert expired is None


def test_missing_verifiable_detail_blocks_publish_plan(tmp_path):
    item = finalized_output(tmp_path)
    item["recommendation"] = "这台跑车有多好玩？看完就知道了。"
    (tmp_path / "manifest.json").write_text(json.dumps({"downloads": [item]}, ensure_ascii=False))
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")

    with pytest.raises(ValueError, match="verifiable_fact"):
        xiaohongshu_publisher.build_plan(tmp_path, None, history)


def test_missing_public_note_url_blocks_next_plan_until_attached(tmp_path):
    finalized_batch(tmp_path)
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    queue = publication_queue.PublicationQueue(history_path=history.path)
    plan = xiaohongshu_publisher.build_plan(tmp_path, None, history, queue=queue)
    xiaohongshu_publisher.save_plan(tmp_path, plan)
    receipt = xiaohongshu_publisher.record_result(tmp_path, history, queue=queue)

    assert receipt["note_url_pending"] is True
    with pytest.raises(ValueError, match="attach-note"):
        xiaohongshu_publisher.build_plan(tmp_path, None, history, queue=queue)

    attached = xiaohongshu_publisher.attach_note(
        tmp_path, history, plan["source_id"],
        "https://www.xiaohongshu.com/explore/real-note", queue=queue)
    next_plan = xiaohongshu_publisher.build_plan(tmp_path, None, history, queue=queue)

    assert attached["note_url_pending"] is False
    assert history.check(source_id=plan["source_id"])["note_url"].endswith("real-note")
    assert history.check(source_id=plan["source_id"])["note_url_pending"] is False
    assert next_plan["source_id"] == "300"


def test_note_metrics_due_checkpoints_and_conversion_rates(tmp_path):
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    publication = history.add(
        "200", "BMW Z1", status="published", brand="BMW", model="Z1",
        note_url="https://www.xiaohongshu.com/explore/note200")
    store = note_metrics.NoteMetrics(tmp_path / "metrics.jsonl")
    published_at = datetime.datetime.fromisoformat(publication["published_at"])

    assert store.due(history, now=published_at + datetime.timedelta(hours=23))["due"] == []
    due = store.due(history, now=published_at + datetime.timedelta(hours=24))
    assert [item["checkpoint_hours"] for item in due["due"]] == [24]

    with pytest.raises(ValueError, match="尚未到期"):
        store.add(publication, 72, {
            "views": 1000, "likes": 100, "favorites": 50,
            "comments": 10, "shares": 5, "new_followers": 4,
        }, now=published_at + datetime.timedelta(hours=24))

    saved = store.add(publication, 24, {
        "views": 1000, "likes": 100, "favorites": 50,
        "comments": 10, "shares": 5, "new_followers": 4,
    }, now=published_at + datetime.timedelta(hours=24))

    assert saved["save_rate"] == 0.05
    assert saved["save_rate_percent"] == 5.0
    assert saved["follows_per_1000_views"] == 4.0
    assert store.due(history, now=published_at + datetime.timedelta(hours=24))["due"] == []
    assert [item["checkpoint_hours"] for item in store.due(
        history, now=published_at + datetime.timedelta(hours=72))["due"]] == [72]

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPTS_DIR = Path(__file__).parents[1] / ".agents/skills/x-car-video-finder/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
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


def finalized_output(tmp_path, tweet_id="123", title="罕见跑车亮相"):
    video = tmp_path / "videos" / "clip.mp4"
    video.parent.mkdir()
    video.write_bytes(b"video")
    item = {
        "candidate": candidate(tweet_id),
        "video": {"file": str(video), "duration": 30, "width": 1920, "height": 1080},
        "title": title,
        "recommendation": "这台罕见跑车在海外活动中亮相，展示独特的车身设计。",
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
    assert "没有可下载的新候选" in report


def test_dry_run_plan_cannot_be_recorded_as_published(tmp_path):
    finalized_output(tmp_path)
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")

    plan = xiaohongshu_publisher.build_plan(tmp_path, "123", history)
    xiaohongshu_publisher.save_plan(tmp_path, plan)

    assert plan["status"] == "ready"
    assert plan["confirm_publish"] is False
    assert Path(plan["video_file"]).is_file()
    with pytest.raises(ValueError, match="预演计划"):
        xiaohongshu_publisher.record_result(tmp_path, history)
    assert history.records() == []


def test_successful_record_blocks_future_plan(tmp_path):
    finalized_output(tmp_path)
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    plan = xiaohongshu_publisher.build_plan(tmp_path, "123", history, confirm_publish=True)
    xiaohongshu_publisher.save_plan(tmp_path, plan)

    receipt = xiaohongshu_publisher.record_result(
        tmp_path, history, note_url="https://www.xiaohongshu.com/explore/note1")

    assert receipt["status"] == "published"
    assert history.check(source_id="123")["note_url"].endswith("note1")
    skipped = xiaohongshu_publisher.build_plan(tmp_path, "123", history)
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "already_published"


def test_plan_validates_xiaohongshu_title_length(tmp_path):
    finalized_output(tmp_path, title="这是一个明显超过二十个字符的小红书汽车视频标题")
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")

    with pytest.raises(ValueError, match="1–20"):
        xiaohongshu_publisher.build_plan(tmp_path, "123", history)


def test_main_skips_published_before_invoking_download(tmp_path, monkeypatch):
    history = publish_history.PublishedHistory(tmp_path / "published.jsonl")
    history.add("old", "已经发布")

    def tweet(tweet_id, likes):
        return {"id": tweet_id, "text": "Rare car video", "favorite_count": likes,
                "author": {"screen_name": "car", "name": "Car"},
                "media": [{"type": "video", "url": "https://example.com/video"}]}

    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["finder.py", "--prompt", "汽车", "--out", str(out),
                                      "--publish-history", str(history.path), "--top", "1"])
    monkeypatch.setattr(finder, "session_summary", lambda args: {})
    monkeypatch.setattr(finder, "resolve_xfetch", lambda value: "xfetch")
    monkeypatch.setattr(finder, "resolve_python", lambda value: "python")
    monkeypatch.setattr(finder, "prepare_x_auth", lambda args, env, py, xfetch: env)
    monkeypatch.setattr(finder, "auth_ok", lambda py, xfetch, env: (True, ""))
    monkeypatch.setattr(finder, "search_query",
                        lambda *args, **kwargs: [tweet("old", 1000), tweet("new", 10)])
    downloaded = []

    def fake_download(item, videos_dir, args):
        downloaded.append(item["id"])
        return None, "测试停止"

    monkeypatch.setattr(finder, "download_video", fake_download)

    finder.main()

    assert downloaded == ["new"]
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["stats"]["already_published_skipped"] == 1
    assert manifest["publish_skipped"][0]["candidate"]["id"] == "old"

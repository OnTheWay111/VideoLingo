import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_DIR = Path(__file__).parents[1] / ".agents/skills/x-car-video-finder/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
MODULE_PATH = SCRIPTS_DIR / "finder.py"
SPEC = importlib.util.spec_from_file_location("x_car_video_finder", MODULE_PATH)
finder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finder)


def make_video(tmp_path, duration):
    path = tmp_path / "videos" / "clip.mp4"
    path.parent.mkdir()
    path.write_bytes(b"video")
    return {"file": str(path), "duration": duration, "width": 1920, "height": 1080}


def test_duration_limit_allows_exactly_five_minutes(tmp_path):
    video = make_video(tmp_path, 300)

    reason = finder.reject_overlong_video(video, tmp_path / "rejected", 300)

    assert reason is None
    assert Path(video["file"]).is_file()
    assert not (tmp_path / "rejected").exists()


def test_duration_limit_moves_overlong_video_to_rejected(tmp_path):
    video = make_video(tmp_path, 300.01)
    original = Path(video["file"])

    reason = finder.reject_overlong_video(video, tmp_path / "rejected", 300)

    assert reason == "超时 300.01s > 300s"
    assert not original.exists()
    assert Path(video["file"]) == tmp_path / "rejected" / "clip.mp4"
    assert Path(video["file"]).read_bytes() == b"video"


def test_duration_limit_can_be_disabled(tmp_path):
    video = make_video(tmp_path, 600)

    reason = finder.reject_overlong_video(video, tmp_path / "rejected", 0)

    assert reason is None
    assert Path(video["file"]).is_file()


def test_report_and_manifest_include_duration_rejection(tmp_path):
    candidate = {
        "id": "1", "url": "https://x.com/car/status/1", "author": "car",
        "author_name": "Car", "date": "2026-11-08", "likes": 10,
        "replies": 2, "reposts": 3, "views": 100, "text": "Car video",
    }
    video = {"file": str(tmp_path / "rejected/clip.mp4"), "duration": 301,
             "width": 1920, "height": 1080}
    rejected = [{"candidate": candidate, "video": video, "reason": "超时 301.0s > 300s"}]
    args = SimpleNamespace(sort="likes", max_duration=300)

    finder.write_outputs(args, "汽车", ["car filter:media"], [candidate], [], rejected, [], {}, tmp_path)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    report = (tmp_path / "REPORT.md").read_text()
    assert manifest["max_duration_seconds"] == 300
    assert manifest["rejected"] == rejected
    assert "- 视频时长：不超过 300 秒" in report
    assert "## 淘汰清单" in report
    assert "超时 301.0s > 300s" in report
    assert "| 文件名 | 本地路径 | 时长 | 原帖 | 淘汰原因 |" in report
    assert "`clip.mp4`" in report
    assert "`%s`" % (tmp_path / "rejected") in report


def test_download_report_includes_filename_and_absolute_directory(tmp_path):
    candidate = {
        "id": "1", "url": "https://x.com/car/status/1", "author": "car",
        "author_name": "Car", "date": "2026-11-08", "likes": 10,
        "replies": 2, "reposts": 3, "views": 100, "text": "Car video",
    }
    downloads = [{"candidate": candidate, "video": make_video(tmp_path, 31), "frames": []}]
    args = SimpleNamespace(sort="likes", max_duration=300)

    finder.write_outputs(args, "汽车", ["car filter:media"], [candidate], downloads, [], [], {}, tmp_path)

    report = (tmp_path / "REPORT.md").read_text()
    assert "| 文件名 | 本地路径 | 作者 | 分辨率 | 时长 | 点赞 | 评论 | 链接 |" in report
    assert "`clip.mp4`" in report
    assert "`%s`" % (tmp_path / "videos") in report


FINALIZE_MODULE_PATH = MODULE_PATH.with_name("finalize.py")
FINALIZE_SPEC = importlib.util.spec_from_file_location("x_car_video_finalize", FINALIZE_MODULE_PATH)
finalizer = importlib.util.module_from_spec(FINALIZE_SPEC)
FINALIZE_SPEC.loader.exec_module(finalizer)


def publish_candidate(tweet_id):
    return {
        "id": tweet_id, "url": "https://x.com/car/status/%s" % tweet_id,
        "author": "car", "author_name": "Car", "date": "2026-11-08",
        "likes": 10, "replies": 2, "reposts": 3, "views": 100,
        "text": "Car video",
    }


def publish_item(tmp_path, tweet_id, directory="videos", rejected=False):
    directory_path = tmp_path / directory
    directory_path.mkdir(exist_ok=True)
    video_path = directory_path / ("clip_%s.mp4" % tweet_id)
    video_path.write_bytes(b"video")
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir(exist_ok=True)
    frame_path = frames_dir / ("%s_1.jpg" % tweet_id)
    frame_path.write_bytes(b"frame")
    item = {
        "candidate": publish_candidate(tweet_id),
        "video": {"file": str(video_path), "duration": 31, "width": 1920, "height": 1080},
        "frames": [{"file": str(frame_path), "at_sec": 4.65}],
    }
    if rejected:
        item["reason"] = "画面非汽车"
        item["frames"] = []
    return item


def test_finalize_uses_publish_titles_for_videos_frames_and_reports(tmp_path):
    selected = publish_item(tmp_path, "1")
    rejected = publish_item(tmp_path, "2", "rejected", rejected=True)
    old_video = Path(selected["video"]["file"])
    old_frame = Path(selected["frames"][0]["file"])
    manifest = {"downloads": [selected], "rejected": [rejected]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "REPORT.md").write_text("# X 汽车视频搜索报告\n")
    publish_path = tmp_path / "publish.json"
    publish_path.write_text(json.dumps({
        "1": {
            "title": "奇怪汽车：飞行/翻滚",
            "body": "可直接发布的入选正文",
            "topics": ["改装车", "#Goodwood 2026"],
        },
        "2": {
            "title": "淘汰视频标题",
            "body": "可直接发布的淘汰正文",
            "topics": ["#经典车"],
        },
    }, ensure_ascii=False))

    finalizer.finalize(tmp_path, publish_path)

    saved = json.loads((tmp_path / "manifest.json").read_text())
    selected = saved["downloads"][0]
    selected_path = Path(selected["video"]["file"])
    assert selected_path.name == "1.奇怪汽车：飞行-翻滚_1.mp4"
    assert selected_path.read_bytes() == b"video"
    assert not old_video.exists()
    assert not old_frame.exists()
    assert Path(selected["frames"][0]["file"]).name == "1.奇怪汽车：飞行-翻滚_1_1.jpg"
    assert selected["title"] == "奇怪汽车：飞行/翻滚"
    assert selected["recommendation"] == "可直接发布的入选正文"
    assert selected["topics"] == ["#改装车", "#Goodwood2026"]
    assert saved["downloads"][0]["video"]["file"] == str(selected_path)
    report = (tmp_path / "REPORT.md").read_text()
    assert "| # | 标题名 | 推荐正文 | 推荐话题 | 文件名 | 本地路径 | 时长 | 点赞 | 原帖 |" in report
    assert "`1.奇怪汽车：飞行-翻滚_1.mp4`" in report
    assert "`1.淘汰视频标题_2.mp4`" in report
    assert "#改装车 #Goodwood2026" in report
    assert "可直接发布的入选正文" in report
    assert "画面非汽车" in report

    finalizer.finalize(tmp_path, publish_path)
    report = (tmp_path / "REPORT.md").read_text()
    assert report.count(finalizer.START_MARKER) == 1
    assert report.count(finalizer.END_MARKER) == 1


def test_finalize_numbers_each_group_in_table_order(tmp_path):
    first = publish_item(tmp_path, "1")
    second = publish_item(tmp_path, "2")
    rejected = publish_item(tmp_path, "3", "rejected", rejected=True)
    manifest = {"downloads": [second, first], "rejected": [rejected]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    publish_path = tmp_path / "publish.json"
    publish_path.write_text(json.dumps({
        "1": {"title": "第一辆", "body": "第一条正文"},
        "2": {"title": "第二辆", "body": "第二条正文"},
        "3": {"title": "淘汰车辆", "body": "淘汰正文"},
    }, ensure_ascii=False))

    finalizer.finalize(tmp_path, publish_path)

    saved = json.loads((tmp_path / "manifest.json").read_text())
    assert Path(saved["downloads"][0]["video"]["file"]).name == "1.第二辆_2.mp4"
    assert Path(saved["downloads"][1]["video"]["file"]).name == "2.第一辆_1.mp4"
    assert Path(saved["rejected"][0]["video"]["file"]).name == "1.淘汰车辆_3.mp4"
    assert saved["downloads"][0]["topics"] == ["#汽车", "#改装车"]


def test_finalize_recovers_numbered_media_with_stale_manifest_paths(tmp_path):
    selected = publish_item(tmp_path, "1")
    old_video = Path(selected["video"]["file"])
    old_frame = Path(selected["frames"][0]["file"])
    old_video.rename(old_video.with_name("9.旧编号_1.mp4"))
    old_frame.rename(old_frame.with_name("9.旧编号_1_1.jpg"))
    (tmp_path / "manifest.json").write_text(json.dumps({"downloads": [selected], "rejected": []}))
    publish_path = tmp_path / "publish.json"
    publish_path.write_text(json.dumps({
        "1": {"title": "正确标题", "body": "正文", "topics": ["#改装车"]},
    }, ensure_ascii=False))

    finalizer.finalize(tmp_path, publish_path)

    saved = json.loads((tmp_path / "manifest.json").read_text())
    assert Path(saved["downloads"][0]["video"]["file"]).name == "1.正确标题_1.mp4"
    assert Path(saved["downloads"][0]["frames"][0]["file"]).name == "1.正确标题_1_1.jpg"


def test_finalize_rejects_missing_publish_content_before_renaming(tmp_path):
    import pytest

    selected = publish_item(tmp_path, "1")
    original = Path(selected["video"]["file"])
    (tmp_path / "manifest.json").write_text(json.dumps({"downloads": [selected], "rejected": []}))
    publish_path = tmp_path / "publish.json"
    publish_path.write_text("{}")

    with pytest.raises(ValueError, match="缺少"):
        finalizer.finalize(tmp_path, publish_path)

    assert original.is_file()
    assert not (tmp_path / "REPORT.md").exists()

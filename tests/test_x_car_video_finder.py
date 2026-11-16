import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_DIR = Path(__file__).parents[1] / ".agents/skills/xcar-xhs/scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
MODULE_PATH = SCRIPTS_DIR / "finder.py"
SPEC = importlib.util.spec_from_file_location("x_car_video_finder", MODULE_PATH)
finder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finder)

import topic_planner


def test_finder_defaults_to_chrome_default_for_x_and_downloads():
    parser = finder.build_parser()
    args = parser.parse_args(["--prompt", "汽车"])

    assert "默认 chrome" in parser.format_help()
    assert "默认 Default" in parser.format_help()
    assert args.web_session == "chrome"
    assert args.browser_profile == "Default"
    assert args.min_likes == 0
    assert args.min_reposts == 0
    assert finder.selection_target(args) == 3
    args.top = 1
    assert finder.selection_target(args) == 3
    args.top = 5
    assert finder.selection_target(args) == 5
    args.dry_run = True
    assert finder.selection_target(args) == 0
    assert finder.session_summary(args) == {
        "mode": "browser", "browser": "chrome", "profile": "Default",
        "cookies_file": None,
    }
    assert finder.ytdlp_session_args(args) == [
        "--cookies-from-browser", "chrome:Default",
    ]


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

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    report = (tmp_path / "REPORT.md").read_text()
    assert manifest["minimum_selected"] == 3
    assert manifest["selection_target"] == 3
    assert manifest["selection_complete"] is False
    assert "daily_publish_limit" not in manifest
    assert "核验入选数量：至少 3 条；未满足，必须继续搜索" in report
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
        "likes": 101, "replies": 2, "reposts": 3, "views": 100,
        "text": "Car video",
    }


def test_download_candidates_backfills_until_minimum_selection(tmp_path, monkeypatch):
    candidates = [publish_candidate(str(index)) for index in range(1, 5)]
    attempted = []

    def fake_download(candidate, videos_dir, args):
        attempted.append(candidate["id"])
        videos_dir.mkdir(parents=True, exist_ok=True)
        path = videos_dir / ("%s.mp4" % candidate["id"])
        path.write_bytes(b"video")
        return {
            "file": str(path), "duration": 301 if candidate["id"] == "1" else 30,
            "width": 1920, "height": 1080,
        }, None

    monkeypatch.setattr(finder, "download_video", fake_download)
    monkeypatch.setattr(finder, "extract_frames", lambda *args: [])
    args = SimpleNamespace(max_duration=300, frames=0)
    history = finder.PublishedHistory(tmp_path / "published.jsonl")

    downloads, rejected = finder.download_candidates(
        candidates, 3, history, tmp_path / "videos", tmp_path / "rejected",
        tmp_path / "frames", args, [])

    assert attempted == ["1", "2", "3", "4"]
    assert [item["candidate"]["id"] for item in downloads] == ["2", "3", "4"]
    assert [item["candidate"]["id"] for item in rejected] == ["1"]
    assert Path(rejected[0]["video"]["file"]).parent == tmp_path / "rejected"


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


def scored_publish(index, brand="Honda", model="S660", project=""):
    fact = "%s %s 的 660cc 发动机位于座椅后方" % (brand, model)
    return {
        "title": "%s 的 660cc 在哪？" % model,
        "body": fact + "，视频展示了对应结构。",
        "brand": brand,
        "model": model,
        "modification_project": project,
        "title_detail": "660cc",
        "verifiable_fact": fact,
        "fact_source": "https://example.com/facts/%s" % index,
        "discussion_question": "你更想看哪种机械布局？",
        "score_breakdown": {
            "first_three_seconds": 22,
            "knowledge_point": 22,
            "save_value": 16,
            "discussion_question": 10,
            "recent_diversity": 10,
        },
    }


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
            "brand": "Bugatti",
            "file_topic": "奇怪汽车：飞行/翻滚",
            "topics": ["改装车", "#Goodwood 2026"],
            "ai_disclosure": "素材来自品牌官方 X 账号；选题、素材初筛及文字由 AI 辅助整理。",
        },
        "2": {
            "title": "淘汰视频标题",
            "body": "可直接发布的淘汰正文",
            "brand": "Peralta",
            "file_topic": "淘汰视频标题",
            "topics": ["#经典车"],
        },
    }, ensure_ascii=False))

    finalizer.finalize(tmp_path, publish_path)

    saved = json.loads((tmp_path / "manifest.json").read_text())
    selected = saved["downloads"][0]
    selected_path = Path(selected["video"]["file"])
    assert selected_path.name == "1.Bugatti_奇怪汽车：飞行-翻滚.mp4"
    assert selected_path.read_bytes() == b"video"
    assert not old_video.exists()
    assert not old_frame.exists()
    assert Path(selected["frames"][0]["file"]).name == "1.Bugatti_奇怪汽车：飞行-翻滚_1.jpg"
    assert selected["title"] == "奇怪汽车：飞行/翻滚"
    assert selected["recommendation"] == "可直接发布的入选正文"
    assert selected["brand"] == "Bugatti"
    assert selected["file_topic"] == "奇怪汽车：飞行/翻滚"
    assert selected["topics"] == ["#改装车", "#Goodwood2026"]
    assert selected["ai_disclosure"] == ""
    assert saved["downloads"][0]["video"]["file"] == str(selected_path)
    report = (tmp_path / "REPORT.md").read_text()
    assert "| # | 标题名 | 推荐正文 | 推荐话题 | 文件名 | 本地路径 | 时长 | 点赞 | 原帖 |" in report
    assert "[奇怪汽车：飞行/翻滚](<%s>)" % selected_path.as_posix() in report
    assert "`1.Bugatti_奇怪汽车：飞行-翻滚.mp4`" in report
    assert "`1.Peralta_淘汰视频标题.mp4`" in report
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
    assert Path(saved["downloads"][0]["video"]["file"]).name == "1.汽车_第二辆.mp4"
    assert Path(saved["downloads"][1]["video"]["file"]).name == "2.汽车_第一辆.mp4"
    assert Path(saved["rejected"][0]["video"]["file"]).name == "1.汽车_淘汰车辆.mp4"
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
        "1": {
            "title": "正确标题",
            "body": "正文",
            "brand": "Bugatti",
            "file_topic": "正确标题",
            "topics": ["#改装车"],
        },
    }, ensure_ascii=False))

    finalizer.finalize(tmp_path, publish_path)

    saved = json.loads((tmp_path / "manifest.json").read_text())
    assert Path(saved["downloads"][0]["video"]["file"]).name == "1.Bugatti_正确标题.mp4"
    assert Path(saved["downloads"][0]["frames"][0]["file"]).name == "1.Bugatti_正确标题_1.jpg"


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


def test_finalize_blocks_new_manifest_with_fewer_than_three_selected(tmp_path):
    import pytest

    selected = publish_item(tmp_path, "1")
    original = Path(selected["video"]["file"])
    (tmp_path / "manifest.json").write_text(json.dumps({
        "minimum_selected": 3,
        "selection_complete": False,
        "downloads": [selected],
        "rejected": [],
    }))
    publish_path = tmp_path / "publish.json"
    publish_path.write_text(json.dumps({
        "1": {"title": "合格车辆", "body": "正文"},
    }, ensure_ascii=False))

    with pytest.raises(ValueError, match="至少要有 3 条"):
        finalizer.finalize(tmp_path, publish_path)

    assert original.is_file()
    assert not (tmp_path / "REPORT.md").exists()


def test_finalize_accepts_three_selected_and_marks_selection_complete(tmp_path):
    downloads = [publish_item(tmp_path, str(index)) for index in range(1, 4)]
    (tmp_path / "manifest.json").write_text(json.dumps({
        "minimum_selected": 3,
        "selection_target": 3,
        "selection_complete": False,
        "stats": {"selection_complete": False},
        "downloads": downloads,
        "rejected": [],
    }))
    publish_path = tmp_path / "publish.json"
    publish_path.write_text(json.dumps({
        "1": scored_publish(1, "Honda", "S660"),
        "2": scored_publish(2, "BMW", "Z1"),
        "3": scored_publish(3, "Toyota", "Sera"),
    }, ensure_ascii=False))

    from tests.candidate_fixtures import approve_item
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    finalizer.apply_publish_content(manifest['downloads'], finalizer.load_publish(publish_path))
    for item in manifest['downloads']:
        approve_item(item)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    finalizer.finalize(tmp_path, publish_path)

    saved = json.loads((tmp_path / "manifest.json").read_text())
    assert len(saved["downloads"]) == 3
    assert saved["selection_complete"] is True
    assert saved["stats"]["selection_complete"] is True


def test_auto_topic_plan_enforces_content_mix_and_five_episode_series():
    from collections import Counter

    records = []
    plans = []
    for index in range(20):
        plan = topic_planner.build_topic_plan(records)
        plans.append(plan)
        records.append({
            "source_id": str(index),
            "status": "published",
            "topic_pillar": plan["pillar"],
            "topic_series_id": plan["series_id"],
            "topic_series_episode": plan["series_episode"],
        })

    assert Counter(plan["pillar"] for plan in plans) == {
        "rare_models": 12,
        "mechanical_contrast": 5,
        "visual_experiment": 3,
    }
    rare = [plan for plan in plans if plan["pillar"] == "rare_models"]
    assert rare[0]["series_id"] == "rare_jdm"
    assert rare[0]["series_label"] == "JDM 稀有车型"
    assert "90 年代" not in rare[0]["prompt"]
    assert any('"Honda S660"' in query for query in rare[0]["queries"])
    assert [plan["series_episode"] for plan in rare[:5]] == [1, 2, 3, 4, 5]
    assert len({plan["series_id"] for plan in rare[:5]}) == 1
    assert len({plan["series_id"] for plan in rare[5:10]}) == 1
    assert rare[0]["series_id"] != rare[5]["series_id"]
    assert all(plan["score_threshold"] == 70 for plan in plans)
    assert all('-"car meet"' in query for plan in plans for query in plan["queries"])
    assert all("-Tesla" not in query for plan in plans for query in plan["queries"])


def test_auto_topic_plan_includes_mountain_driving_series():
    records = [{
        "source_id": str(index),
        "status": "published",
        "topic_pillar": "visual_experiment" if index < 10 else "rare_models",
    } for index in range(24)]

    plan = topic_planner.build_topic_plan(records)

    assert plan["strategy_version"] == 7
    assert plan["score_rubric"] == {
        "first_three_seconds": 25,
        "knowledge_point": 25,
        "save_value": 20,
        "discussion_question": 15,
        "recent_diversity": 15,
    }
    assert plan["source_engagement"] == "weak_reference_only"
    assert plan["pillar"] == "visual_experiment"
    assert plan["series_id"] == "mountain_driving"
    assert plan["series_label"] == "山路驾驶场景"
    assert any('"mountain road"' in query for query in plan["queries"])
    assert any('"touge POV"' in query for query in plan["queries"])
    assert all('-"mountain road"' not in query for query in plan["queries"])


def test_auto_topic_plan_is_stable_until_publication_is_recorded():
    initial = topic_planner.build_topic_plan([])

    assert topic_planner.build_topic_plan([]) == initial
    next_plan = topic_planner.build_topic_plan([{
        "status": "unconfirmed",
        "topic_pillar": initial["pillar"],
        "topic_series_id": initial["series_id"],
        "topic_series_episode": initial["series_episode"],
    }])

    assert next_plan["schedule_slot"] == 1
    assert next_plan["series_episode"] == 1


def test_auto_topic_main_runs_without_prompt_and_saves_plan(tmp_path, monkeypatch):
    out = tmp_path / "out"
    history_path = tmp_path / "published.jsonl"
    monkeypatch.setattr(sys, "argv", [
        "finder.py", "--auto-topic", "--dry-run", "--out", str(out),
        "--publish-history", str(history_path), "--min-likes", "5",
        "--since", "2026-11-01",
    ])
    monkeypatch.setattr(finder, "session_summary", lambda args: {})
    monkeypatch.setattr(finder, "resolve_xfetch", lambda value: "xfetch")
    monkeypatch.setattr(finder, "resolve_python", lambda value: "python")
    monkeypatch.setattr(finder, "prepare_x_auth", lambda args, env, py, xfetch: env)
    monkeypatch.setattr(finder, "auth_ok", lambda py, xfetch, env: (True, ""))
    searched = []

    def fake_search(py, xfetch, query, args, env, raw_dir):
        searched.append(query)
        return [{
            "id": "auto-1",
            "text": "Honda Beat kei car design details",
            "favorite_count": 100,
            "retweet_count": 100,
            "author": {"screen_name": "car", "name": "Car"},
            "media": [{"type": "video", "url": "https://example.com/video"}],
        }]

    monkeypatch.setattr(finder, "search_query", fake_search)

    finder.main()

    manifest = json.loads((out / "manifest.json").read_text())
    saved_plan = json.loads((out / "topic_plan.json").read_text())
    assert manifest["topic_plan"] == saved_plan
    assert manifest["prompt"] == saved_plan["prompt"]
    assert saved_plan["pillar"] == "rare_models"
    assert saved_plan["series_episode"] == 1
    assert manifest["queries"] == searched
    assert len(searched) == 3
    assert all("min_faves:5" in query and "since:2026-11-01" in query for query in searched)
    assert "20 条轮动的第 1 位" in (out / "REPORT.md").read_text()


def test_manual_mode_still_requires_prompt(monkeypatch):
    import pytest

    monkeypatch.setattr(sys, "argv", ["finder.py"])
    with pytest.raises(SystemExit):
        finder.main()


def test_explicit_explainer_candidate_is_filtered_before_download():
    candidate = publish_candidate("explainer")
    candidate["text"] = "真人出镜讲解这台 Honda Beat 的售价"

    allowed, skipped = finder.partition_forbidden_topics([candidate])

    assert allowed == []
    assert skipped[0]["candidate"]["id"] == "explainer"
    assert "真人讲解、口播或屏幕讲解" in skipped[0]["reason"]


def test_finalize_rejects_cappuccino_talking_head_sample(tmp_path):
    tweet_id = "1948914053335974008"
    item = publish_item(tmp_path, tweet_id)
    original_video = Path(item["video"]["file"])
    item["candidate"]["text"] = (
        "El legendario Suzuki Cappuccino a la venta en Argentina! "
        "Qué opinan del precio?"
    )
    item["recommendation"] = "视频还讲到 Monster Sport 涡轮套件与 ECU 调校。"
    item["verification"] = {
        "status": "accepted",
        "score": 77,
        "presentation": "talking_head_explainer",
    }
    (tmp_path / "manifest.json").write_text(json.dumps({"downloads": [item]}, ensure_ascii=False))
    publish_path = tmp_path / "publish.json"
    publish_path.write_text(json.dumps({
        tweet_id: {
            "title": "Cappuccino 轻跑有多好玩？",
            "body": "这台 Suzuki Cappuccino 的视频还讲到 Monster Sport 涡轮套件。",
            "brand": "Suzuki",
            "file_topic": "Cappuccino 改装轻跑",
            "topics": ["#SuzukiCappuccino", "#JDM"],
        },
    }, ensure_ascii=False))

    finalizer.finalize(tmp_path, publish_path)

    saved = json.loads((tmp_path / "manifest.json").read_text())
    assert saved["downloads"] == []
    assert saved["stats"]["explainer_rejected"] == 1
    rejected = saved["rejected"][0]
    assert rejected["candidate"]["id"] == tweet_id
    assert rejected["verification"]["status"] == "rejected"
    assert rejected["verification"]["rejection_policy"] == "explainer_video"
    assert "讲解视频禁选" in rejected["reason"]
    assert Path(rejected["video"]["file"]).parent == tmp_path / "rejected"
    assert Path(rejected["video"]["file"]).is_file()
    assert not original_video.exists()
    assert "讲解视频禁选" in (tmp_path / "REPORT.md").read_text()


def test_engagement_download_thresholds_are_inclusive_and_independent():
    boundary = publish_candidate("boundary")
    boundary.update({"likes": 1000, "reposts": 100})
    low_likes = publish_candidate("low-likes")
    low_likes.update({"likes": 999, "reposts": 1000})
    low_reposts = publish_candidate("low-reposts")
    low_reposts.update({"likes": 5000, "reposts": 99})

    allowed, skipped = finder.partition_engagement_candidates(
        [boundary, low_likes, low_reposts], 1000, 100)

    assert [item["id"] for item in allowed] == ["boundary"]
    assert {item["candidate"]["id"] for item in skipped} == {"low-likes", "low-reposts"}
    assert "点赞 999 < 1000" in skipped[0]["reason"]
    assert "转发 99 < 100" in skipped[1]["reason"]

    allowed, skipped = finder.partition_engagement_candidates([low_likes, low_reposts], 0, 0)
    assert len(allowed) == 2
    assert skipped == []


def test_total_engagement_must_be_strictly_greater_than_100():
    boundary = publish_candidate("total-100")
    boundary.update({"likes": 80, "replies": 10, "reposts": 10})
    above = publish_candidate("total-101")
    above.update({"likes": 80, "replies": 10, "reposts": 11})
    missing = publish_candidate("missing-counts")
    missing.update({"likes": None, "replies": None, "reposts": None})

    allowed, skipped = finder.partition_engagement_candidates(
        [boundary, above, missing], 0, 0)

    assert [item["id"] for item in allowed] == ["total-101"]
    assert [item["candidate"]["id"] for item in skipped] == ["total-100", "missing-counts"]
    assert "互动总量 100 ≤ 100" in skipped[0]["reason"]
    assert "互动总量 0 ≤ 100" in skipped[1]["reason"]


def test_batch_deduplicates_brand_and_keeps_highest_scored_item():
    items = []
    for source_id, brand, model, score in (
        ("1", "BMW", "Z1", 75),
        ("2", "宝马", "M1", 90),
        ("3", "Honda", "S660", 80),
    ):
        items.append({
            "candidate": {"id": source_id}, "title": model, "brand": brand, "model": model,
            "modification_project": "", "score": score,
        })
    manifest = {"minimum_selected": 3, "downloads": items, "rejected": [], "stats": {}}

    finalizer.reject_duplicate_downloads(manifest, [], [])

    assert [item["candidate"]["id"] for item in manifest["downloads"]] == ["2", "3"]
    assert manifest["rejected"][0]["candidate"]["id"] == "1"
    assert "同品牌每批最多一条" in manifest["rejected"][0]["reason"]
    assert manifest["stats"]["content_duplicate_rejected"] == 1


def test_new_series_vehicle_names_survive_car_filter_without_generic_car_word():
    import re
    car_re = re.compile(finder.CAR_LEXICON)
    for text in ['Honda Fit Magic Seats', 'N-VAN シートがフラット',
                 'エブリイ車中泊仕様から仕事仕様へ', 'ルーミーの車中泊',
                 'アトレー シート', 'Suzuki Jimny articulation',
                 'Volvo 850R', 'Stagea 260RS', 'Legnum VR4', 'DeLorean DMC-12',
                 'Opel GT flip over headlights', 'Alfa Romeo Montreal', 'Volkswagen restoration']:
        tweet = {'id': '1', 'text': text, 'media': [{'type': 'video', 'url': 'https://example.com/v'}]}
        assert finder.normalize(tweet, car_re, [], True) is not None, text
    unrelated = {'id': '2', 'text': 'Folding seats in a home theatre',
                 'media': [{'type': 'video', 'url': 'https://example.com/v'}]}
    assert finder.normalize(unrelated, car_re, [], True) is None

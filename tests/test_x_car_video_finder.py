import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).parents[1] / ".agents/skills/x-car-video-finder/scripts/finder.py"
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

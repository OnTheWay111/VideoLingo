#!/usr/bin/env python3
"""xcar-xhs 本地只读监控页与真实 Token usage 接收接口。"""

import argparse
import datetime
import json
import mimetypes
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from monitor_telemetry import (DEFAULT_EVENT_FILE, DEFAULT_USAGE_FILE, FLOW_STAGES,
                               read_jsonl, record_usage)
from publication_queue import default_queue_path
from publish_history import PublishedHistory, default_history_path
from xiaohongshu_publisher import latest_records


TIMEZONE = ZoneInfo("Asia/Shanghai")
SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[4] / "docs/design/xcar-monitor"
STAGE_LABELS = {
    "topic_strategy": "选题与策略", "search_filter": "搜索与筛选",
    "download_frames": "下载 / 抽帧协助", "visual_fact_review": "画面与事实审核",
    "copy_finalize_enqueue": "文案、定稿与入池", "plan_preflight": "计划与预检",
    "upload_submit": "上传与提交协助", "result_verify": "结果核验",
    "link_recovery": "链接补查与恢复",
}


def parse_time(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def local_date(value):
    parsed = parse_time(value)
    return parsed.astimezone(TIMEZONE).date().isoformat() if parsed else None


def local_time(value):
    parsed = parse_time(value)
    return parsed.astimezone(TIMEZONE).strftime("%H:%M:%S") if parsed else "—"


def read_json(path, default):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def item_view(entry, kind, usage_by_job):
    item = entry.get("item") or {}
    candidate = item.get("candidate") or {}
    job_id = str(item.get("candidate_job_id") or entry.get("candidate_job_id") or "")
    if kind == "candidate":
        occurred_at = entry.get("enqueued_at")
        status = entry.get("status") or "queued"
        note_url = entry.get("note_url") or ""
    else:
        occurred_at = entry.get("published_at") or entry.get("recorded_at")
        status = "published_linked" if entry.get("note_url") else "published_link_pending"
        note_url = entry.get("note_url") or ""
    job_usage = usage_by_job.get(job_id)
    video = item.get("video") or {}
    return {
        "source_id": str(entry.get("source_id") or candidate.get("id") or ""),
        "title": entry.get("title") or item.get("title") or "未命名视频",
        "brand": entry.get("brand") or item.get("brand") or "",
        "model": entry.get("model") or item.get("model") or "",
        "series": item.get("topic_series_id") or entry.get("topic_series_id") or "",
        "score": entry.get("score") if entry.get("score") is not None else item.get("score"),
        "occurred_at": occurred_at,
        "time": local_time(occurred_at),
        "status": status,
        "job_id": job_id,
        "job_token_usage": job_usage,
        "source_url": entry.get("source_url") or candidate.get("url") or "",
        "note_url": note_url,
        "video_file": entry.get("video_file") or video.get("file") or "",
    }


def pool_item_view(entry):
    item = entry.get("item") or {}
    candidate = item.get("candidate") or {}
    topics = item.get("topics") or []
    if isinstance(topics, str):
        topics = topics.split()
    def metric(*values):
        value = next((candidate for candidate in values if candidate not in (None, "")), None)
        return int(value) if value is not None else None
    source_url = candidate.get("url") or entry.get("source_url") or ""
    if urlparse(source_url).scheme not in ("http", "https"):
        source_url = ""
    return {
        "source_id": str(entry.get("source_id") or candidate.get("id") or ""),
        "status": entry.get("status") or "queued",
        "title": entry.get("title") or item.get("title") or "未命名视频",
        "body": item.get("body") or item.get("recommendation") or "",
        "topics": [str(topic) for topic in topics],
        "likes": metric(candidate.get("likes"), candidate.get("favorite_count")),
        "replies": metric(candidate.get("replies"), candidate.get("reply_count")),
        "reposts": metric(candidate.get("reposts"), candidate.get("retweet_count")),
        "source_url": source_url,
        "out_dir": entry.get("out_dir") or "",
        "directory_name": Path(entry.get("out_dir") or "").name,
        "enqueued_at": entry.get("enqueued_at"),
    }


class MonitorData:
    def __init__(self, queue_file=None, history_file=None, event_file=None, usage_file=None):
        self.history_file = Path(history_file or default_history_path()).resolve()
        self.queue_file = Path(queue_file or default_queue_path(self.history_file)).resolve()
        self.event_file = Path(event_file or DEFAULT_EVENT_FILE).resolve()
        self.usage_file = Path(usage_file or DEFAULT_USAGE_FILE).resolve()

    def queue_records(self):
        queue_data = read_json(self.queue_file, {"entries": []})
        if not isinstance(queue_data, dict) or not isinstance(queue_data.get("entries"), list):
            raise ValueError("发布队列必须是包含 entries 数组的 JSON 对象")
        return queue_data["entries"]

    def open_candidate_directory(self, source_id, opener=None):
        source_id = str(source_id or "").strip()
        if not source_id:
            raise ValueError("source_id 不能为空")
        entry = next((row for row in self.queue_records()
                      if str(row.get("source_id") or "") == source_id
                      and row.get("status") in ("queued", "planned")), None)
        if not entry:
            raise ValueError("候选池中没有该待发布视频")
        output_dir = Path(entry.get("out_dir") or "").resolve()
        folder = output_dir / "videos" if (output_dir / "videos").is_dir() else output_dir
        if not folder.is_dir():
            raise ValueError("Finder 目录不存在：%s" % folder)
        if opener:
            opener(folder)
        else:
            subprocess.run(["open", str(folder)], check=True, timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"source_id": source_id, "folder": str(folder)}

    def snapshot(self, day=None):
        selected_day = day or datetime.datetime.now(TIMEZONE).date().isoformat()
        datetime.date.fromisoformat(selected_day)
        errors = []
        try:
            queue = self.queue_records()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            queue, errors = [], ["候选池读取失败：" + str(exc)]
        try:
            history = latest_records(PublishedHistory(self.history_file).records())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            history = []
            errors.append("发布历史读取失败：" + str(exc))
        try:
            usage = read_jsonl(self.usage_file)
        except (OSError, ValueError) as exc:
            usage = []
            errors.append("Token 账本读取失败：" + str(exc))
        try:
            events = read_jsonl(self.event_file)
        except (OSError, ValueError) as exc:
            events = []
            errors.append("业务事件账本读取失败：" + str(exc))
        try:
            registry = read_json(self.history_file.with_name("candidate_jobs.json"), {"jobs": {}})
            jobs = registry.get("jobs") or {}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            jobs = {}
            errors.append("候选任务读取失败：" + str(exc))

        day_usage = [row for row in usage if local_date(row.get("completed_at")) == selected_day]
        usage_by_job = {}
        for row in day_usage:
            job_id = str(row.get("job_id") or "")
            if job_id:
                usage_by_job[job_id] = usage_by_job.get(job_id, 0) + int(row.get("total_tokens") or 0)
        candidates = [row for row in queue if local_date(row.get("enqueued_at")) == selected_day]
        pending_pool = [row for row in queue if row.get("status") in ("queued", "planned")]
        pending_pool.sort(key=lambda row: (row.get("status") == "planned",
                                           row.get("enqueued_at") or ""), reverse=True)
        published = [row for row in history
                     if row.get("status") == "published"
                     and local_date(row.get("published_at")) == selected_day]
        candidates.sort(key=lambda row: row.get("enqueued_at") or "", reverse=True)
        published.sort(key=lambda row: row.get("published_at") or "", reverse=True)
        event_counts = {}
        for row in events:
            if local_date(row.get("occurred_at")) != selected_day:
                continue
            key = (row.get("flow"), row.get("stage"))
            event_counts[key] = event_counts.get(key, 0) + 1
        token = {flow: self._usage_summary(flow, day_usage) for flow in FLOW_STAGES}
        links_available = sum(bool(row.get("note_url")) for row in published)
        return {
            "date": selected_day,
            "timezone": "Asia/Shanghai",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "sources": {
                "queue_file": str(self.queue_file), "history_file": str(self.history_file),
                "event_file": str(self.event_file), "usage_file": str(self.usage_file),
            },
            "quality": {"errors": errors, "business_events_collected": bool(events)},
            "summary": {
                "candidate_count": len(candidates), "published_count": len(published),
                "pending_pool_count": len(pending_pool),
                "published_links_available": links_available,
                "published_links_pending": len(published) - links_available,
            },
            "stages": {
                flow: [{"key": stage, "label": STAGE_LABELS[stage],
                        "count": event_counts.get((flow, stage)),
                        "collected": (flow, stage) in event_counts}
                       for stage in stages]
                for flow, stages in FLOW_STAGES.items()
            },
            "token": token,
            "active_tasks": self._active_tasks(jobs, queue, events),
            "pending_pool_items": [pool_item_view(row) for row in pending_pool],
            "candidate_items": [item_view(row, "candidate", usage_by_job) for row in candidates],
            "published_items": [item_view(row, "publish", usage_by_job) for row in published],
        }

    def _active_tasks(self, jobs, queue, events):
        tasks = []
        terminal = {"complete", "cancelled"}
        for job_id, job in jobs.items():
            if job.get("status") in terminal:
                continue
            checkpoint = job.get("checkpoint") or {}
            items = list((checkpoint.get("items") or {}).values())
            counts = {}
            for item in items:
                status = item.get("status") or "unknown"
                counts[status] = counts.get(status, 0) + 1
            status = job.get("status") or checkpoint.get("status") or "unknown"
            phase = checkpoint.get("phase") or "collecting"
            current = "copy_finalize_enqueue" if phase == "finalizing" else \
                      "visual_fact_review" if status == "awaiting_review" else \
                      "download_frames" if counts.get("downloaded") or counts.get("downloading") else \
                      "search_filter"
            task_events = [row for row in events if str(row.get("job_id") or "") == str(job_id)]
            completed = {row.get("stage") for row in task_events if row.get("status") not in ("failed", "blocked")}
            plan = job.get("plan") or checkpoint.get("plan") or {}
            tasks.append({
                "id": str(job_id), "flow": "candidate", "status": status,
                "needs_attention": status in ("awaiting_review", "blocked", "budget_exhausted",
                                                "needs_candidates", "exhausted"),
                "current_stage": current, "completed_stages": sorted(completed),
                "stages": [{"key": key, "label": STAGE_LABELS[key]} for key in FLOW_STAGES["candidate"]],
                "title": plan.get("series_label") or plan.get("prompt") or "候选生产任务",
                "pillar": plan.get("pillar_label") or "",
                "out_dir": job.get("out_dir") or "",
                "updated_at": job.get("updated_at") or checkpoint.get("updated_at"),
                "progress": {
                    "target": job.get("target") or checkpoint.get("target") or 0,
                    "approved": counts.get("approved", 0),
                    "awaiting_review": counts.get("awaiting_review", 0),
                    "rejected": counts.get("rejected", 0),
                    "pages": checkpoint.get("pages", 0),
                    "downloads": checkpoint.get("downloads", 0),
                },
                "error": (read_json(Path(job.get("out_dir") or "") / "candidate_receipt.json", {})
                          .get("error") if job.get("out_dir") else None),
            })
        for entry in queue:
            if entry.get("status") != "planned":
                continue
            item = entry.get("item") or {}
            job_id = str(item.get("candidate_job_id") or "")
            tasks.append({
                "id": "publish:" + str(entry.get("source_id") or ""), "flow": "publish",
                "status": "planned", "needs_attention": False,
                "current_stage": "plan_preflight", "completed_stages": [],
                "stages": [{"key": key, "label": STAGE_LABELS[key]} for key in FLOW_STAGES["publish"]],
                "title": entry.get("title") or "发布任务", "pillar": "",
                "out_dir": entry.get("out_dir") or "", "updated_at": entry.get("selected_at"),
                "progress": {"target": 1, "approved": 0, "awaiting_review": 0,
                             "rejected": 0, "pages": 0, "downloads": 0},
                "candidate_job_id": job_id, "error": None,
            })
        tasks.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
        return tasks

    @staticmethod
    def _usage_summary(flow, rows):
        scoped = [row for row in rows if row.get("flow") == flow]
        stages = []
        for stage in FLOW_STAGES[flow]:
            records = [row for row in scoped if row.get("stage") == stage]
            stages.append({
                "key": stage, "label": STAGE_LABELS[stage],
                "collected": bool(records), "request_count": len(records),
                "total_tokens": sum(int(row.get("total_tokens") or 0) for row in records) if records else None,
                "input_tokens": sum(int(row.get("input_tokens") or 0) for row in records) if records else None,
                "cached_input_tokens": sum(int(row.get("cached_input_tokens") or 0) for row in records) if records else None,
                "output_tokens": sum(int(row.get("output_tokens") or 0) for row in records) if records else None,
                "reasoning_tokens": sum(int(row.get("reasoning_tokens") or 0) for row in records) if records else None,
                "quality": "partial" if any(row.get("quality") == "partial" for row in records) else
                           "complete" if records else "uncollected",
            })
        collected = [stage for stage in stages if stage["collected"]]
        coverage = "uncollected" if not collected else "complete" if len(collected) == len(stages) else "partial"
        if any(stage["quality"] == "partial" for stage in collected):
            coverage = "partial"
        return {
            "coverage": coverage,
            "total_tokens": sum(stage["total_tokens"] for stage in collected) if collected else None,
            "request_count": sum(stage["request_count"] for stage in collected),
            "stages": stages,
        }


class Handler(BaseHTTPRequestHandler):
    data = None
    static_dir = None

    def log_message(self, form, *args):
        sys.stderr.write("[xcar-monitor] " + form % args + "\n")

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/overview":
            try:
                self.send_json(self.data.snapshot(parse_qs(parsed.query).get("date", [None])[0]))
            except (OSError, ValueError) as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        target = "index.html" if parsed.path in ("", "/") else parsed.path.lstrip("/")
        path = (self.static_dir / target).resolve()
        if self.static_dir not in path.parents and path != self.static_dir:
            self.send_error(403)
            return
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        route = urlparse(self.path).path
        if route not in ("/api/usage", "/api/open-folder"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 64 * 1024:
                raise ValueError("请求体必须为 1–65536 字节")
            payload = json.loads(self.rfile.read(length))
            if route == "/api/open-folder":
                self.send_json(self.data.open_candidate_directory(payload.get("source_id")))
            else:
                saved, added = record_usage(payload, self.data.usage_file)
                self.send_json({"added": added, "usage": saved}, 201 if added else 200)
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            self.send_json({"error": str(exc)}, 400)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--queue-file")
    parser.add_argument("--history-file")
    parser.add_argument("--event-file")
    parser.add_argument("--usage-file")
    parser.add_argument("--static-dir", default=str(DEFAULT_STATIC_DIR))
    parser.add_argument("--snapshot", action="store_true", help="只输出一次真实快照，不启动服务")
    parser.add_argument("--date")
    args = parser.parse_args(argv)
    data = MonitorData(args.queue_file, args.history_file, args.event_file, args.usage_file)
    if args.snapshot:
        print(json.dumps(data.snapshot(args.date), ensure_ascii=False, indent=2))
        return 0
    static_dir = Path(args.static_dir).resolve()
    if not (static_dir / "index.html").is_file():
        parser.error("静态页面不存在：%s" % static_dir)
    handler = type("MonitorHandler", (Handler,), {"data": data, "static_dir": static_dir})
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print("XCAR 监控页：http://%s:%d" % (args.host, args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

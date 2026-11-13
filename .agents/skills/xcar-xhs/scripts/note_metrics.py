#!/usr/bin/env python3
"""登记小红书公开笔记的 24/72 小时真实效果数据。"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

from content_policy import parse_datetime
from publish_history import PublishedHistory, default_history_path, locked_history, read_records

DEFAULT_METRICS_FILE = Path(__file__).resolve().parents[1] / "state" / "note_metrics.jsonl"
CHECKPOINTS = (24, 72)
METRIC_FIELDS = ("views", "likes", "favorites", "comments", "shares", "new_followers")


def default_metrics_path():
    value = os.environ.get("X_CAR_NOTE_METRICS_FILE")
    return Path(value).expanduser().resolve() if value else DEFAULT_METRICS_FILE


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def latest_published(records):
    latest = {}
    for record in records:
        source_id = str(record.get("source_id") or "")
        if source_id:
            latest[source_id] = record
    return [record for record in latest.values() if record.get("status") == "published"]


class NoteMetrics:
    def __init__(self, path=None):
        self.path = Path(path).expanduser().resolve() if path else default_metrics_path()

    def records(self):
        with locked_history(self.path) as handle:
            return read_records(handle)

    def add(self, publication, checkpoint, metrics, now=None):
        if checkpoint not in CHECKPOINTS:
            raise ValueError("效果记录节点只能是 24 或 72 小时")
        now = now or utc_now()
        published_at = parse_datetime(publication.get("published_at") or publication.get("recorded_at"))
        if not published_at:
            raise ValueError("发布记录缺少有效发布时间")
        due_at = published_at + datetime.timedelta(hours=checkpoint)
        if now < due_at:
            raise ValueError("%d 小时节点尚未到期，不能提前编造效果数据" % checkpoint)
        note_url = str(publication.get("note_url") or "").strip()
        if not note_url.startswith((
                "https://www.xiaohongshu.com/explore/",
                "https://www.xiaohongshu.com/discovery/item/")):
            raise ValueError("记录效果前必须补齐真实公开笔记地址")
        cleaned = {}
        for field in METRIC_FIELDS:
            value = metrics.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("%s 必须是非负整数，未知数据不能填 0 代替" % field)
            cleaned[field] = value
        source_id = str(publication.get("source_id") or "")
        with locked_history(self.path, create=True) as handle:
            records = read_records(handle)
            if any(str(record.get("source_id")) == source_id and record.get("checkpoint_hours") == checkpoint
                   for record in records):
                raise ValueError("该笔记的 %d 小时数据已经记录，不能重复覆盖" % checkpoint)
            views = cleaned["views"]
            record = {
                "source_id": source_id,
                "title": publication.get("title") or "",
                "note_url": note_url,
                "published_at": published_at.isoformat(timespec="seconds"),
                "checkpoint_hours": checkpoint,
                "due_at": due_at.isoformat(timespec="seconds"),
                "captured_at": now.isoformat(timespec="seconds"),
                **cleaned,
                "save_rate": round(cleaned["favorites"] / views, 6) if views else None,
                "save_rate_percent": round(cleaned["favorites"] / views * 100, 2) if views else None,
                "follows_per_1000_views": round(cleaned["new_followers"] / views * 1000, 3) if views else None,
            }
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def due(self, history, now=None):
        now = now or utc_now()
        existing = {(str(record.get("source_id")), record.get("checkpoint_hours"))
                    for record in self.records()}
        due, blocked = [], []
        for publication in latest_published(history.records()):
            published_at = parse_datetime(publication.get("published_at") or publication.get("recorded_at"))
            if not published_at:
                continue
            for checkpoint in CHECKPOINTS:
                source_id = str(publication["source_id"])
                if (source_id, checkpoint) in existing:
                    continue
                due_at = published_at + datetime.timedelta(hours=checkpoint)
                if now < due_at:
                    continue
                item = {
                    "source_id": source_id,
                    "title": publication.get("title") or "",
                    "note_url": publication.get("note_url") or "",
                    "checkpoint_hours": checkpoint,
                    "due_at": due_at.isoformat(timespec="seconds"),
                }
                if item["note_url"]:
                    due.append(item)
                else:
                    blocked.append({**item, "reason": "缺少真实公开笔记地址"})
        return {"due": due, "blocked": blocked, "metrics_file": str(self.path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-file", default=str(default_history_path()))
    parser.add_argument("--metrics-file", default=str(default_metrics_path()))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("due", help="列出已经到期的 24/72 小时采集任务")
    record = commands.add_parser("record", help="记录从公开笔记和创作者后台读取的真实数据")
    record.add_argument("--source-id", required=True)
    record.add_argument("--checkpoint", type=int, choices=CHECKPOINTS, required=True)
    for field in METRIC_FIELDS:
        record.add_argument("--" + field.replace("_", "-"), type=int, required=True)
    commands.add_parser("list")
    args = parser.parse_args(argv)
    history = PublishedHistory(args.history_file)
    store = NoteMetrics(args.metrics_file)
    try:
        if args.command == "due":
            result = store.due(history)
        elif args.command == "list":
            result = store.records()
        else:
            publication = next(
                (item for item in latest_published(history.records())
                 if str(item.get("source_id")) == str(args.source_id)), None)
            if not publication:
                raise ValueError("找不到已发布记录：%s" % args.source_id)
            metrics = {field: getattr(args, field) for field in METRIC_FIELDS}
            result = store.add(publication, args.checkpoint, metrics)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, "错误: %s\n" % exc)


if __name__ == "__main__":
    sys.exit(main())

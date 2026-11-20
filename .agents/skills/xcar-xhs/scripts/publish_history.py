#!/usr/bin/env python3
"""小红书发布历史的固定存储、查询和命令行维护。"""

import argparse
import contextlib
import datetime
import json
import os
import re
import sys
from pathlib import Path

from content_policy import compact_video_title

try:
    import fcntl
except ImportError:
    fcntl = None

DEFAULT_HISTORY_FILE = Path(__file__).resolve().parents[1] / "state" / "published_videos.jsonl"
DEFAULT_DRAFT_HISTORY_FILE = Path(__file__).resolve().parents[1] / "state" / "draft_videos.jsonl"
ACTIVE_STATUSES = {"published", "unconfirmed"}
RECORD_STATUSES = ACTIVE_STATUSES | {"failed", "draft_saved", "unconfirmed_draft"}


def default_history_path():
    value = os.environ.get("X_CAR_PUBLISH_HISTORY_FILE")
    return Path(value).expanduser().resolve() if value else DEFAULT_HISTORY_FILE


def default_draft_history_path():
    value = os.environ.get("X_CAR_DRAFT_HISTORY_FILE")
    return Path(value).expanduser().resolve() if value else DEFAULT_DRAFT_HISTORY_FILE


def normalize_title(title):
    return re.sub(r"\s+", "", compact_video_title(title or "")).casefold()


@contextlib.contextmanager
def locked_history(path, create=False):
    path = Path(path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() and not create:
        yield None
        return
    with path.open("a+" if create else "r", encoding="utf-8") as handle:
        if fcntl:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield handle
        finally:
            if fcntl:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_records(handle):
    if handle is None:
        return []
    handle.seek(0)
    records = []
    for line_number, line in enumerate(handle.read().splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("发布历史第 %d 行不是有效 JSON" % line_number) from exc
        if not isinstance(record, dict):
            raise ValueError("发布历史第 %d 行必须是 JSON 对象" % line_number)
        records.append(record)
    return records


def match_record(records, source_id=None, title=None):
    source_id = str(source_id) if source_id is not None else ""
    normalized_title = normalize_title(title)
    for record in reversed(records):
        matched_by = None
        if source_id and str(record.get("source_id") or "") == source_id:
            matched_by = "source_id"
        elif normalized_title and normalize_title(record.get("title")) == normalized_title:
            matched_by = "title"
        if not matched_by:
            continue
        if record.get("status", "published") not in ACTIVE_STATUSES:
            return None
        return {**record, "matched_by": matched_by}
    return None


class PublishedHistory:
    def __init__(self, path=None):
        self.path = Path(path).expanduser().resolve() if path else default_history_path()

    def records(self):
        with locked_history(self.path) as handle:
            return read_records(handle)

    def check(self, source_id=None, title=None):
        return match_record(self.records(), source_id, title)

    def add(self, source_id, title, source_url="", status="published", platform="xiaohongshu", **extra):
        if status not in RECORD_STATUSES:
            raise ValueError("发布状态必须是 published、unconfirmed 或 failed")
        if not str(source_id or "").strip() and not normalize_title(title):
            raise ValueError("source_id 和 title 至少填写一项")
        recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        record = {
            "source_id": str(source_id or ""),
            "title": title or "",
            "source_url": source_url or "",
            "platform": platform,
            "status": status,
            "recorded_at": recorded_at,
        }
        if status == "published":
            record["published_at"] = recorded_at
        record.update({key: value for key, value in extra.items() if value not in (None, "")})
        with locked_history(self.path, create=True) as handle:
            records = read_records(handle)
            existing = match_record(records, source_id, title)
            if existing and existing.get("status", "published") == status:
                return existing
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def update_note_url(self, source_id, note_url):
        if not str(note_url or "").strip():
            raise ValueError("公开笔记地址不能为空")
        with locked_history(self.path, create=True) as handle:
            records = read_records(handle)
            existing = match_record(records, source_id=source_id)
            if not existing:
                raise ValueError("找不到已发布或待确认记录：%s" % source_id)
            record = {key: value for key, value in existing.items() if key != "matched_by"}
            record["note_url"] = note_url.strip()
            record["note_url_pending"] = False
            record["public_link_status"] = "available"
            record["note_url_recorded_at"] = datetime.datetime.now(
                datetime.timezone.utc).isoformat(timespec="seconds")
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-file", default=str(default_history_path()))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("add", "check"):
        command = commands.add_parser(name)
        command.add_argument("--source-id", default="")
        command.add_argument("--title", default="")
        if name == "add":
            command.add_argument("--source-url", default="")
            command.add_argument("--status", choices=sorted(ACTIVE_STATUSES), default="published")
            command.add_argument("--note-url", default="")
            command.add_argument("--brand", default="")
            command.add_argument("--model", default="")
            command.add_argument("--modification-project", default="")
    note = commands.add_parser("attach-note", help="补登记真实公开笔记地址，不改变原发布时间")
    note.add_argument("--source-id", required=True)
    note.add_argument("--note-url", required=True)
    commands.add_parser("list")
    resolve = commands.add_parser("resolve", help="后台确认未发布后，将待确认记录更正为 failed")
    resolve.add_argument("--source-id", required=True)
    resolve.add_argument("--title", required=True)
    resolve.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    history = PublishedHistory(args.history_file)
    try:
        if args.command == "list":
            print(json.dumps(history.records(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "check":
            match = history.check(args.source_id, args.title)
            print(json.dumps({"published": bool(match), "match": match}, ensure_ascii=False, indent=2))
            return 1 if match else 0
        if args.command == "resolve":
            match = history.check(args.source_id, args.title)
            if not match:
                match = next((record for record in reversed(history.records())
                              if str(record.get("source_id")) == str(args.source_id)), None)
            if not match or match.get("status") not in ("unconfirmed", "unconfirmed_draft"):
                raise ValueError("只有 unconfirmed 或 unconfirmed_draft 记录可以更正为 failed")
            record = history.add(args.source_id, args.title, match.get("source_url", ""),
                                 "failed", resolution_reason=args.reason)
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0
        if args.command == "attach-note":
            record = history.update_note_url(args.source_id, args.note_url)
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0
        record = history.add(
            args.source_id, args.title, args.source_url, args.status,
            note_url=args.note_url, brand=args.brand, model=args.model,
            modification_project=args.modification_project)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(1, "错误: %s\n" % exc)


if __name__ == "__main__":
    sys.exit(main())

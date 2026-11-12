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

try:
    import fcntl
except ImportError:
    fcntl = None

DEFAULT_HISTORY_FILE = Path(__file__).resolve().parents[1] / "state" / "published_videos.jsonl"
ACTIVE_STATUSES = {"published", "unconfirmed"}


def default_history_path():
    value = os.environ.get("X_CAR_PUBLISH_HISTORY_FILE")
    return Path(value).expanduser().resolve() if value else DEFAULT_HISTORY_FILE


def normalize_title(title):
    return re.sub(r"\s+", "", title or "").casefold()


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
        if record.get("status", "published") not in ACTIVE_STATUSES:
            continue
        if source_id and str(record.get("source_id") or "") == source_id:
            return {**record, "matched_by": "source_id"}
        if normalized_title and normalize_title(record.get("title")) == normalized_title:
            return {**record, "matched_by": "title"}
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
        if status not in ACTIVE_STATUSES:
            raise ValueError("发布状态必须是 published 或 unconfirmed")
        if not str(source_id or "").strip() and not normalize_title(title):
            raise ValueError("source_id 和 title 至少填写一项")
        record = {
            "source_id": str(source_id or ""),
            "title": title or "",
            "source_url": source_url or "",
            "platform": platform,
            "status": status,
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        }
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
    commands.add_parser("list")
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
        record = history.add(args.source_id, args.title, args.source_url, args.status, note_url=args.note_url)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(1, "错误: %s\n" % exc)


if __name__ == "__main__":
    sys.exit(main())

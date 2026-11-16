#!/usr/bin/env python3
"""跨批次的小红书发布队列。"""

import datetime
import argparse
import contextlib
import copy
import json
import os
from pathlib import Path

from content_policy import content_duplicate, content_identity
from publish_history import locked_history
from publication_runtime import atomic_json
from candidate_review import validate_review, validate_publication_review, file_digest

DEFAULT_QUEUE_FILE = Path(__file__).resolve().parents[1] / "state" / "publish_queue.json"
WAITING_STATUSES = {"queued", "planned"}


def default_queue_path(history_path=None):
    value = os.environ.get("X_CAR_PUBLISH_QUEUE_FILE")
    if value:
        return Path(value).expanduser().resolve()
    if history_path:
        return Path(history_path).expanduser().resolve().with_name("publish_queue.json")
    return DEFAULT_QUEUE_FILE


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def read_queue(handle):
    if handle is None:
        return {"version": 1, "entries": []}
    handle.seek(0)
    text = handle.read().strip()
    if not text:
        return {"version": 1, "entries": []}
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ValueError("发布队列必须是包含 entries 数组的 JSON 对象")
    return data


@contextlib.contextmanager
def locked_queue(path, create=False):
    # 使用稳定的独立锁文件；数据原子替换后锁仍保护新 inode。
    path = Path(path)
    with locked_history(path.with_suffix(path.suffix + '.lock'), create=True):
        if not path.exists() and not create:
            yield None
        else:
            with path.open('a+' if create else 'r', encoding='utf-8') as handle:
                yield handle


def write_queue(handle, data):
    atomic_json(handle.name, data)


class PublicationQueue:
    def __init__(self, path=None, history_path=None):
        self.path = (Path(path).expanduser().resolve() if path else default_queue_path(history_path))

    def records(self):
        with locked_queue(self.path) as handle:
            return read_queue(handle)["entries"]

    def enqueue(self, items, out_dir):
        items = copy.deepcopy(items)
        for item in items:
            validate_review(item)
        added = []
        with locked_queue(self.path, create=True) as handle:
            data = read_queue(handle)
            entries = data["entries"]
            for item in items:
                source_id = str((item.get("candidate") or {}).get("id") or "")
                if not source_id:
                    raise ValueError("入队素材缺少原帖 ID")
                existing = next((entry for entry in entries if entry.get("source_id") == source_id), None)
                if existing:
                    continue
                duplicate = content_duplicate(
                    content_identity(item), entries, days=None, statuses=WAITING_STATUSES)
                if duplicate:
                    raise ValueError("素材 %s 与队列内容重复：%s（%s）" % (
                        source_id, duplicate["reason"], duplicate["title"] or duplicate["source_id"]))
                verification = item.get("verification") or {}
                entry = {
                    "revision": 1,
                    "source_id": source_id,
                    "out_dir": str(Path(out_dir).resolve()),
                    "status": "queued",
                    "enqueued_at": utc_now(),
                    "score": item.get("score", verification.get("score", 0)),
                    "title": item.get("title") or "",
                    "brand": item.get("brand") or "",
                    "model": item.get("model") or "",
                    "modification_project": item.get("modification_project") or "",
                    "item": item,
                }
                entries.append(entry)
                added.append(entry)
            if added:
                write_queue(handle, data)
        return added

    def select(self, latest_published=None, excluded_source_ids=()):
        excluded_source_ids = set(map(str, excluded_source_ids))
        self.audit()
        with locked_queue(self.path, create=True) as handle:
            data = read_queue(handle)
            entries = data["entries"]
            planned = next((entry for entry in entries if entry.get("status") == "planned"
                            and entry.get("source_id") not in excluded_source_ids), None)
            if planned:
                return planned
            waiting = [entry for entry in entries if entry.get("status") == "queued"
                       and entry.get("source_id") not in excluded_source_ids]
            waiting.sort(key=lambda entry: (-float(entry.get("score") or 0), entry.get("enqueued_at") or ""))
            latest_identity = content_identity(latest_published or {})
            latest_series = (latest_published or {}).get("topic_series_id") or ""
            selected = None
            for entry in waiting:
                identity = content_identity(entry)
                same_brand = (latest_identity.get("brand") and identity.get("brand") == latest_identity["brand"])
                same_project = (
                    latest_identity.get("modification_project")
                    and identity.get("brand") == latest_identity.get("brand")
                    and identity.get("modification_project") == latest_identity["modification_project"]
                )
                same_series = latest_series and (entry.get("item") or {}).get("topic_series_id") == latest_series
                if same_brand or same_project or same_series:
                    continue
                selected = entry
                break
            if selected:
                selected["status"] = "planned"
                selected["selected_at"] = utc_now()
                write_queue(handle, data)
            return selected

    def release(self, source_id, allow_unconfirmed=False):
        source_id = str(source_id)
        with locked_queue(self.path, create=True) as handle:
            if handle is None:
                return None
            data = read_queue(handle)
            for entry in data["entries"]:
                if entry.get("source_id") == source_id and entry.get("status") in (
                        {"planned", "unconfirmed"} if allow_unconfirmed else {"planned"}):
                    entry["status"] = "queued"
                    entry.pop("selected_at", None)
                    write_queue(handle, data)
                    return entry
        return None

    def complete(self, source_id, status, note_url=""):
        if status not in ("published", "unconfirmed"):
            raise ValueError("队列完成状态必须是 published 或 unconfirmed")
        source_id = str(source_id)
        with locked_queue(self.path, create=True) as handle:
            if handle is None:
                return None
            data = read_queue(handle)
            for entry in data["entries"]:
                if entry.get("source_id") == source_id:
                    entry["status"] = status
                    entry["completed_at"] = utc_now()
                    if note_url:
                        entry["note_url"] = note_url
                    write_queue(handle, data)
                    return entry
        return None

    def attach_note(self, source_id, note_url):
        source_id = str(source_id)
        with locked_queue(self.path, create=True) as handle:
            if handle is None:
                return None
            data = read_queue(handle)
            for entry in data["entries"]:
                if entry.get("source_id") == source_id:
                    entry["note_url"] = note_url
                    write_queue(handle, data)
                    return entry
        return None


    def update_candidate(self, source_id, item, expected_revision, out_dir):
        validate_review(item)
        if str((item.get('candidate') or {}).get('id')) != str(source_id):
            raise ValueError('候选 ID 不匹配')
        with locked_queue(self.path, create=True) as handle:
            data = read_queue(handle)
            entry = next((e for e in data['entries'] if e['source_id'] == str(source_id)), None)
            if not entry or entry['status'] not in ('queued', 'needs_review', 'invalid', 'withdrawn'):
                raise ValueError('只能更新尚未进入发布计划的候选')
            if entry.get('revision', 1) != expected_revision:
                raise ValueError('候选版本冲突，请重新读取后更新')
            duplicate = content_duplicate(content_identity(item),
                [e for e in data['entries'] if e['source_id'] != str(source_id)],
                days=None, statuses=WAITING_STATUSES)
            if duplicate:
                raise ValueError('更新后与排队内容重复：' + duplicate['reason'])
            entry.update(item=copy.deepcopy(item), out_dir=str(Path(out_dir).resolve()), status='queued',
                         revision=expected_revision + 1, updated_at=utc_now(), score=item['score'])
            for key in ('title', 'brand', 'model', 'modification_project'):
                entry[key] = item.get(key, '')
            entry.pop('maintenance_reason', None)
            write_queue(handle, data)
            return entry

    def withdraw(self, source_id, expected_revision, reason):
        if not reason.strip():
            raise ValueError('撤回必须说明原因')
        with locked_queue(self.path, create=True) as handle:
            data = read_queue(handle)
            entry = next((e for e in data['entries'] if e['source_id'] == str(source_id)), None)
            if not entry or entry['status'] not in ('queued', 'needs_review', 'invalid'):
                raise ValueError('只能撤回未进入发布计划的候选')
            if entry.get('revision', 1) != expected_revision:
                raise ValueError('候选版本冲突')
            entry.update(status='withdrawn', revision=expected_revision + 1,
                         maintenance_reason=reason, updated_at=utc_now())
            write_queue(handle, data)
            return entry

    def audit(self, max_age_days=30, now=None):
        now = now or datetime.datetime.now(datetime.timezone.utc)
        changed = []
        with locked_queue(self.path) as handle:
            data = read_queue(handle)
            for entry in data['entries']:
                if entry.get('status') != 'queued':
                    continue
                item = entry.get('item') or {}
                path = Path((item.get('video') or {}).get('file') or '/nonexistent')
                status, reason = '', ''
                if not path.is_file() or path.stat().st_size == 0:
                    status, reason = 'invalid', '视频文件缺失或为空'
                else:
                    try:
                        validate_publication_review(item)
                        reviewed_at = item['verification'].get('reviewed_at') or entry['enqueued_at']
                        checked = datetime.datetime.fromisoformat(reviewed_at.replace('Z', '+00:00'))
                        if checked.tzinfo is None:
                            checked = checked.replace(tzinfo=datetime.timezone.utc)
                        if max_age_days > 0 and (now - checked).total_seconds() > max_age_days * 86400:
                            status, reason = 'needs_review', '核验已超过复核期限'
                    except (OSError, ValueError, KeyError) as exc:
                        status, reason = 'needs_review', str(exc)
                if status:
                    entry.update(status=status, maintenance_reason=reason, updated_at=utc_now(),
                                 revision=entry.get('revision', 1) + 1)
                    changed.append(copy.deepcopy(entry))
            if changed:
                write_queue(handle, data)
        return changed

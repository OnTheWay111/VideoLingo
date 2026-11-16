#!/usr/bin/env python3
"""xcar-xhs 业务事件与模型 Token 用量账本。"""

import argparse
import datetime
import json
import os
import sys
import uuid
from pathlib import Path

from publish_history import locked_history


STATE_DIR = Path(__file__).resolve().parents[1] / "state"
DEFAULT_EVENT_FILE = STATE_DIR / "monitor_events.jsonl"
DEFAULT_USAGE_FILE = STATE_DIR / "token_usage.jsonl"

CANDIDATE_STAGES = (
    "topic_strategy", "search_filter", "download_frames",
    "visual_fact_review", "copy_finalize_enqueue",
)
PUBLISH_STAGES = (
    "plan_preflight", "upload_submit", "result_verify", "link_recovery",
)
FLOW_STAGES = {"candidate": CANDIDATE_STAGES, "publish": PUBLISH_STAGES}


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def _read_jsonl(handle):
    if handle is None:
        return []
    handle.seek(0)
    records = []
    for number, line in enumerate(handle, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("账本第 %d 行不是有效 JSON" % number) from exc
        if not isinstance(value, dict):
            raise ValueError("账本第 %d 行必须是 JSON 对象" % number)
        records.append(value)
    return records


def read_jsonl(path):
    with locked_history(Path(path)) as handle:
        return _read_jsonl(handle)


def _append_unique(path, record, identity_field, strict=True):
    path = Path(path)
    with locked_history(path, create=True) as handle:
        records = _read_jsonl(handle)
        identity = str(record[identity_field])
        existing = next((item for item in records
                         if str(item.get(identity_field)) == identity), None)
        if existing:
            if strict:
                comparable = set(existing) | set(record)
                if identity_field == "request_id":
                    comparable -= {"started_at", "completed_at"}
                if any(existing.get(key) != record.get(key) for key in comparable):
                    raise ValueError("%s 已存在且内容不同" % identity_field)
            return existing, False
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record, True


def record_event(flow, stage, job_id="", source_id="", status="complete",
                 occurred_at=None, event_id=None, details=None, path=DEFAULT_EVENT_FILE):
    validate_stage(flow, stage)
    record = {
        "event_id": event_id or uuid.uuid4().hex,
        "flow": flow,
        "stage": stage,
        "job_id": str(job_id or ""),
        "source_id": str(source_id or ""),
        "status": status,
        "occurred_at": occurred_at or utc_now(),
        "details": details or {},
    }
    return _append_unique(path, record, "event_id", strict=False)[0]


def validate_stage(flow, stage):
    if flow not in FLOW_STAGES:
        raise ValueError("flow 必须是 candidate 或 publish")
    if stage not in FLOW_STAGES[flow]:
        raise ValueError("%s 不是 %s 的有效阶段" % (stage, flow))


def normalize_usage(value):
    flow = str(value.get("flow") or "")
    stage = str(value.get("stage") or "")
    validate_stage(flow, stage)
    request_id = str(value.get("request_id") or "").strip()
    if not request_id:
        raise ValueError("request_id 不能为空")
    numeric = {}
    for key in ("input_tokens", "cached_input_tokens", "output_tokens",
                "reasoning_tokens", "total_tokens"):
        raw = value.get(key)
        if raw in (None, ""):
            numeric[key] = None
            continue
        number = int(raw)
        if number < 0:
            raise ValueError("Token 数不能为负数")
        numeric[key] = number
    if numeric["input_tokens"] is not None and numeric["cached_input_tokens"] is not None:
        if numeric["cached_input_tokens"] > numeric["input_tokens"]:
            raise ValueError("cached_input_tokens 不能大于 input_tokens")
    if numeric["total_tokens"] is None:
        if numeric["input_tokens"] is None or numeric["output_tokens"] is None:
            raise ValueError("缺少 total_tokens 时必须同时提供 input_tokens 和 output_tokens")
        numeric["total_tokens"] = numeric["input_tokens"] + numeric["output_tokens"]
    quality = str(value.get("quality") or "complete")
    if quality not in ("complete", "partial"):
        raise ValueError("quality 必须是 complete 或 partial")
    return {
        "request_id": request_id,
        "flow": flow,
        "stage": stage,
        "job_id": str(value.get("job_id") or ""),
        "source_id": str(value.get("source_id") or ""),
        "attempt": int(value.get("attempt") or 1),
        "provider": str(value.get("provider") or ""),
        "model": str(value.get("model") or ""),
        "usage_source": str(value.get("usage_source") or "provider_response"),
        "quality": quality,
        "started_at": value.get("started_at") or None,
        "completed_at": value.get("completed_at") or utc_now(),
        **numeric,
    }


def record_usage(value, path=DEFAULT_USAGE_FILE):
    record = normalize_usage(value)
    return _append_unique(path, record, "request_id")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usage-file", default=str(DEFAULT_USAGE_FILE))
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record", help="记录一次模型调用返回的真实 usage")
    record.add_argument("--request-id", required=True)
    record.add_argument("--flow", choices=tuple(FLOW_STAGES), required=True)
    record.add_argument("--stage", required=True)
    record.add_argument("--job-id", default="")
    record.add_argument("--source-id", default="")
    record.add_argument("--attempt", type=int, default=1)
    record.add_argument("--provider", default="")
    record.add_argument("--model", default="")
    record.add_argument("--usage-source", default="provider_response")
    record.add_argument("--quality", choices=("complete", "partial"), default="complete")
    record.add_argument("--started-at")
    record.add_argument("--completed-at")
    record.add_argument("--input-tokens", type=int)
    record.add_argument("--cached-input-tokens", type=int)
    record.add_argument("--output-tokens", type=int)
    record.add_argument("--reasoning-tokens", type=int)
    record.add_argument("--total-tokens", type=int)
    commands.add_parser("list")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            print(json.dumps(read_jsonl(args.usage_file), ensure_ascii=False, indent=2))
            return 0
        value = vars(args).copy()
        for key in ("command", "usage_file"):
            value.pop(key, None)
        saved, added = record_usage(value, args.usage_file)
        print(json.dumps({"added": added, "usage": saved}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(2, "错误: %s\n" % exc)


if __name__ == "__main__":
    sys.exit(main())

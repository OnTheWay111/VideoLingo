#!/usr/bin/env python3
"""小红书汽车账号的禁选主题规则。"""

import re

FORBIDDEN_TOPIC_RE = re.compile(
    r"(?i)(?:海外玩车现场|地下车聚|车聚|车展|汽车展|车主故事|特色驾驶场景|山路驾驶|公路驾驶|"
    r"第一视角驾驶|驾驶\s*POV|\bcar\s*(?:meet|show)\b|\bauto\s+show\b|\bmotor\s+show\b|"
    r"\bowner(?:'s)?\s+story\b|\bdriving\s+pov\b|\bpov\s+drive\b|\broad\s+trip\b|"
    r"\bgoodwood\b|\bfestival\s+of\s+speed\b|\bsema\b)"
)


def forbidden_topic_match(item):
    candidate = item.get("candidate") or {}
    values = [
        item.get("text"), candidate.get("text"), item.get("title"),
        item.get("recommendation"), item.get("file_topic"),
    ]
    topics = item.get("topics") or []
    if isinstance(topics, list):
        values.extend(str(topic) for topic in topics)
    haystack = "\n".join(str(value) for value in values if value)
    match = FORBIDDEN_TOPIC_RE.search(haystack)
    return match.group(0) if match else None


def partition_forbidden_topics(candidates):
    allowed, skipped = [], []
    for candidate in candidates:
        match = forbidden_topic_match(candidate)
        if match:
            skipped.append({
                "candidate": candidate,
                "reason": "小红书禁选：海外玩车现场、车聚车展、车主故事或特色驾驶场景（匹配“%s”）" % match,
            })
        else:
            allowed.append(candidate)
    return allowed, skipped

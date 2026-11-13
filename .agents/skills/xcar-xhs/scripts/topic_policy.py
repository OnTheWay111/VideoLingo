#!/usr/bin/env python3
"""小红书汽车账号的禁选主题与讲解视频规则。"""

import re

FORBIDDEN_TOPIC_RE = re.compile(
    r"(?i)(?:海外玩车现场|地下车聚|车聚|车展|汽车展|车主故事|特色驾驶场景|山路驾驶|公路驾驶|"
    r"第一视角驾驶|驾驶\s*POV|\bcar\s*(?:meet|show)\b|\bauto\s+show\b|\bmotor\s+show\b|"
    r"\bowner(?:'s)?\s+story\b|\bdriving\s+pov\b|\bpov\s+drive\b|\broad\s+trip\b|"
    r"\bgoodwood\b|\bfestival\s+of\s+speed\b|\bsema\b)"
)

EXPLAINER_RE = re.compile(
    r"(?i)(?:真人(?:出镜|讲解|解说)|出镜(?:讲解|解说)|口播|屏幕讲解|讲解视频|解说视频|"
    r"视频.{0,8}(?:讲到|讲解|解说)|\btalking[\s_-]*head[\s_-]*(?:explainer|commentary)?\b|"
    r"\bpresenter[\s_-]*(?:led|explainer)\b|\bscreen[\s_-]*(?:explainer|commentary)\b|"
    r"\bexplainer\s+video\b)"
)


def content_values(item):
    candidate = item.get("candidate") or {}
    values = [
        item.get("text"), candidate.get("text"), item.get("title"),
        item.get("recommendation"), item.get("file_topic"),
    ]
    topics = item.get("topics") or []
    if isinstance(topics, list):
        values.extend(str(topic) for topic in topics)
    return values


def explainer_video_match(item):
    values = content_values(item)
    values.append(item.get("presentation"))
    for key in ("verification", "visual_review"):
        review = item.get(key) or {}
        if isinstance(review, dict):
            values.append(review.get("presentation"))
    haystack = "\n".join(str(value) for value in values if value)
    match = EXPLAINER_RE.search(haystack)
    return match.group(0) if match else None


def forbidden_topic_match(item):
    haystack = "\n".join(str(value) for value in content_values(item) if value)
    match = FORBIDDEN_TOPIC_RE.search(haystack)
    if match:
        return match.group(0)
    return explainer_video_match(item)


def partition_forbidden_topics(candidates):
    allowed, skipped = [], []
    for candidate in candidates:
        explainer = explainer_video_match(candidate)
        if explainer:
            skipped.append({
                "candidate": candidate,
                "reason": "小红书禁选：真人讲解、口播或屏幕讲解视频（匹配“%s”）" % explainer,
            })
            continue
        haystack = "\n".join(str(value) for value in content_values(candidate) if value)
        match = FORBIDDEN_TOPIC_RE.search(haystack)
        if match:
            skipped.append({
                "candidate": candidate,
                "reason": "小红书禁选：海外玩车现场、车聚车展、车主故事或特色驾驶场景（匹配“%s”）" % match.group(0),
            })
        else:
            allowed.append(candidate)
    return allowed, skipped

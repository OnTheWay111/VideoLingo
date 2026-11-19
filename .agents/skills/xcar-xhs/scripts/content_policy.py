#!/usr/bin/env python3
"""汽车内容评分、可核验文案和车型级身份规则。"""

import datetime
import math
import re

SCORE_THRESHOLD = 70
MIN_TOTAL_ENGAGEMENT = 100
BRAND_SERIES = "brand_models"
SCORE_RUBRIC = {
    "first_three_seconds": 25,
    "knowledge_point": 25,
    "save_value": 20,
    "discussion_question": 15,
    "recent_diversity": 15,
}
BRAND_ALIASES = {
    "bmw": "bmw", "宝马": "bmw",
    "mclaren": "mclaren", "迈凯伦": "mclaren",
    "mercedes": "mercedes-benz", "mercedesbenz": "mercedes-benz", "奔驰": "mercedes-benz",
    "ferrari": "ferrari", "法拉利": "ferrari",
    "porsche": "porsche", "保时捷": "porsche",
    "lamborghini": "lamborghini", "兰博基尼": "lamborghini",
    "bugatti": "bugatti", "布加迪": "bugatti",
    "toyota": "toyota", "丰田": "toyota",
    "honda": "honda", "本田": "honda",
    "mazda": "mazda", "马自达": "mazda",
    "nissan": "nissan", "日产": "nissan",
    "suzuki": "suzuki", "铃木": "suzuki",
    "autozam": "autozam",
}


def _count(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def engagement_counts(item):
    """兼容旧候选的三项口径；品牌车型任务另计 X 收藏。"""
    candidate = item.get("candidate") if isinstance(item, dict) else None
    candidate = candidate if isinstance(candidate, dict) else (item or {})
    counts = {
        "likes": _count(candidate.get("likes", candidate.get("favorite_count"))),
        "replies": _count(candidate.get("replies", candidate.get("reply_count"))),
        "reposts": _count(candidate.get("reposts", candidate.get("retweet_count"))),
    }
    if BRAND_SERIES in (candidate.get("topic_series_id"), (item or {}).get("topic_series_id")):
        counts["bookmarks"] = _count(candidate.get("bookmarks", candidate.get("bookmark_count")))
    return counts


def engagement_total(item):
    return sum(engagement_counts(item).values())


def engagement_rejection_reason(item, minimum=MIN_TOTAL_ENGAGEMENT):
    """总互动量必须严格大于 minimum；等于门槛也拒绝。"""
    counts = engagement_counts(item)
    total = sum(counts.values())
    if total > minimum:
        return ""
    return "互动总量 %d ≤ %d（点赞 %d + 评论 %d + 转发 %d%s）" % (
        total, minimum, counts["likes"], counts["replies"], counts["reposts"],
        (" + 收藏 %d" % counts["bookmarks"]) if "bookmarks" in counts else "")


def normalized(value):
    return re.sub(r"[\s_\-–—/\\.,，。:：()（）\[\]{}]+", "", str(value or "")).casefold()


MATERIAL_SOURCE_RE = re.compile(r"素材(?:来自|来源)[^；。\n]*[；;]?")
PRESET_AI_DISCLOSURE_RE = re.compile(r"(?:[；;]\s*)?选题、素材初筛及文字由\s*AI\s*辅助整理[。；;]?")


def strip_material_source_disclosure(value):
    return MATERIAL_SOURCE_RE.sub("", str(value or "")).strip()


def strip_preset_ai_disclosure(value):
    text = PRESET_AI_DISCLOSURE_RE.sub("", str(value or ""))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("；; \t\r\n")


def validate_no_material_source_copy(value):
    if MATERIAL_SOURCE_RE.search(str(value or "")):
        raise ValueError("小红书文案不得包含素材来源说明")


def normalize_brand(value):
    key = normalized(value)
    return BRAND_ALIASES.get(key, key)


def normalize_project(value):
    text = str(value or "").strip().casefold()
    if not text or normalized(text) in {"无", "原厂", "原装", "stock", "none", "original"}:
        return ""
    if re.search(r"转子|rotary|wankel", text, re.I):
        return "rotary"
    if re.search(r"宽体|wide\s*body", text, re.I):
        return "widebody"
    if re.search(r"发动机移植|引擎移植|动力移植|engine\s*swap|motor\s*swap|powertrain\s*swap", text, re.I):
        return "engine-swap"
    if re.search(r"电动化|纯电移植|electric\s*(?:conversion|swap)", text, re.I):
        return "electric-conversion"
    return normalized(text)


def content_identity(item):
    if isinstance(item.get("item"), dict):
        item = item["item"]
    return {
        "brand": normalize_brand(item.get("brand")),
        "model": normalized(item.get("model")),
        "modification_project": normalize_project(item.get("modification_project")),
    }


def parse_datetime(value):
    if isinstance(value, datetime.datetime):
        result = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            result = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=datetime.timezone.utc)
    return result


def content_duplicate(identity, records, now=None, days=30, statuses=None):
    if not identity.get("brand") or not (identity.get("model") or identity.get("modification_project")):
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    for record in reversed(records):
        if statuses and record.get("status", "published") not in statuses:
            continue
        other = content_identity(record)
        if other.get("brand") != identity["brand"]:
            continue
        matched_by = ""
        if identity.get("model") and identity["model"] == other.get("model"):
            matched_by = "model"
        elif (identity.get("modification_project")
              and identity["modification_project"] == other.get("modification_project")):
            matched_by = "modification_project"
        if not matched_by:
            continue
        timestamp = parse_datetime(
            record.get("published_at") or record.get("recorded_at")
            or record.get("enqueued_at") or record.get("selected_at"))
        if days is not None and timestamp:
            age_days = (now - timestamp).total_seconds() / 86400
            if age_days > days:
                continue
        label = "同车型" if matched_by == "model" else "同品牌同改装项目"
        return {
            "matched_by": matched_by,
            "source_id": str(record.get("source_id") or ""),
            "title": record.get("title") or (record.get("item") or {}).get("title") or "",
            "reason": "%s%s内重复" % (label, "%d 天" % days if days is not None else "排队期"),
        }
    return None


def validate_score(item):
    verification = item.get("verification") or {}
    breakdown = item.get("score_breakdown") or verification.get("score_breakdown")
    if not isinstance(breakdown, dict) or set(breakdown) != set(SCORE_RUBRIC):
        raise ValueError("评分必须完整包含首三秒、知识点、收藏价值、讨论问题和近期多样性五项")
    total = 0
    for key, maximum in SCORE_RUBRIC.items():
        value = breakdown[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("评分项 %s 必须是有限数字" % key)
        if not 0 <= value <= maximum:
            raise ValueError("评分项 %s 必须在 0–%d 分之间" % (key, maximum))
        total += value
    score = item.get("score", verification.get("score"))
    if score is None:
        score = total
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise ValueError("总分必须是有限数字")
    if abs(score - total) > 0.001:
        raise ValueError("总分必须等于五项评分之和")
    if score < SCORE_THRESHOLD:
        raise ValueError("选题总分必须达到 %d 分" % SCORE_THRESHOLD)
    return score, dict(breakdown)


def validate_verifiable_content(item):
    title = str(item.get("title") or "").strip()
    body = str(item.get("recommendation") or item.get("body") or "").strip()
    title_detail = str(item.get("title_detail") or "").strip()
    fact = str(item.get("verifiable_fact") or "").strip()
    source = str(item.get("fact_source") or "").strip()
    question = str(item.get("discussion_question") or "").strip()
    brand = normalize_brand(item.get("brand"))
    model = normalized(item.get("model"))
    if (not brand or not model or brand in {"汽车", "车辆", "car", "cars"}
            or model in {"汽车", "车辆", "车型", "跑车", "超跑", "轿车", "car", "cars", "jdm"}):
        raise ValueError("必须填写可识别的 brand 和 model，不能只用宽泛品牌或车型类别")
    if not title_detail or title_detail not in title:
        raise ValueError("title_detail 必须原样出现在标题中，提供具体可核验细节")
    if not fact or fact not in body:
        raise ValueError("verifiable_fact 必须原样出现在正文中，保证知识点可直接复核")
    if not re.match(r"^https?://\S+$", source):
        raise ValueError("fact_source 必须是可访问的 HTTP(S) 来源地址")
    if not question or not re.search(r"[?？]", question):
        raise ValueError("discussion_question 必须是一个明确的问题")
    return {
        "title_detail": title_detail,
        "verifiable_fact": fact,
        "fact_source": source,
        "discussion_question": question,
    }

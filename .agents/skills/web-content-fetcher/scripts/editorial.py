"""公众号选题的主题相关性与发布适配预筛选。"""

import re


NEWS_MARKERS = (
    "发布", "推出", "上线", "更新", "修复", "新功能", "新版", "事故", "故障", "漏洞",
    "冲突", "报告", "调查", "售价", "补货", "release", "launch", "update", "now has",
    "new ", "bug", "issue", "security", "survey", "improved", "remote ssh",
)
PRACTICE_MARKERS = (
    "教程", "实战", "指南", "对比", "评测", "原理", "分析", "复盘", "回测", "管理会话",
    "mcp", "cli", "desktop", "workflow", "guide", "tutorial", "benchmark", "review",
    "api", "agent", "command center", "remote ssh",
)
ENGAGEMENT_MARKERS = (
    "reply below", "你会入吗", "兄弟们", "不藏了", "评论区", "告诉我", "你怎么看",
    "which model should", "投票", "你会买吗",
)
HYPE_MARKERS = (
    "稳赚", "月入", "年入", "躺赚", "每月稳赚", "一年下来", "万美元", "被动收入",
    "make $", "guaranteed profit", "passive income", "profit every month",
)
PROMOTION_MARKERS = ("付费", "paid", "standard and pro", "pricing", "订阅制")
OPERATOR_PATTERN = re.compile(
    r"\b(?:filter|since|until|from|to|lang|min_faves|min_retweets):[^\s()]+",
    re.IGNORECASE,
)


def _dedupe(values):
    result = []
    for value in values:
        value = value.strip()
        if value and value.casefold() not in {item.casefold() for item in result}:
            result.append(value)
    return result


def topic_aliases(topic="", aliases=None, queries=None):
    values = list(aliases or [])
    if topic:
        values.append(topic)
    for query in queries or []:
        quoted = re.findall(r'"([^"]+)"', query)
        if quoted:
            values.extend(quoted)
            continue
        cleaned = OPERATOR_PATTERN.sub(" ", query)
        cleaned = re.sub(r"\b(?:OR|AND|NOT)\b|[()\-]+", " ", cleaned, flags=re.IGNORECASE)
        values.extend(token for token in cleaned.split() if len(token) <= 80)
    values = _dedupe(values)
    if any("codex" in value.casefold() for value in values):
        values = _dedupe(values + ["Codex"])
    return values


def _lead_text(text):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return "\n".join(lines[:2]).casefold()


def _matched_aliases(text, aliases):
    folded = (text or "").casefold()
    return [alias for alias in aliases if alias.casefold() in folded]


def evaluate_item(item, aliases, min_score=70):
    text = item.get("text") or item.get("title") or ""
    matched = _matched_aliases(text, aliases)
    lead_matched = _matched_aliases(_lead_text(text), aliases)
    evaluated = dict(item)
    evaluated["topic_matches"] = matched
    evaluated["topic_in_lead"] = bool(lead_matched)

    if aliases and not matched:
        evaluated.update({
            "wechat_score": 0,
            "wechat_reasons": ["正文没有明确提到主题"],
            "selection": "rejected",
            "reject_reasons": ["主题缺失"],
        })
        return evaluated
    if aliases and not lead_matched:
        evaluated.update({
            "wechat_score": 0,
            "wechat_reasons": ["主题未出现在前两句，属于背景或附带提及"],
            "selection": "rejected",
            "reject_reasons": ["非主题主导"],
        })
        return evaluated

    score = 50
    reasons = ["前两句聚焦主题"] if aliases else ["未提供主题词，仅评估发布适配"]
    length = len(text)
    if length >= 120:
        score += 5
        reasons.append("包含基本细节")
    if length >= 300:
        score += 10
        reasons.append("信息量较完整")
    if length >= 800:
        score += 10
        reasons.append("适合深度展开")

    folded = text.casefold()
    has_news = any(marker.casefold() in folded for marker in NEWS_MARKERS)
    has_practice = any(marker.casefold() in folded for marker in PRACTICE_MARKERS)
    if has_news:
        score += 15
        reasons.append("有明确新闻点")
    if has_practice:
        score += 10
        reasons.append("有技术或实用信息")
    if not has_news and not has_practice:
        score -= 10
        reasons.append("缺少明确新闻点或实用信息")
    if any(marker.casefold() in folded for marker in ENGAGEMENT_MARKERS):
        score -= 20
        reasons.append("含引流或互动话术")
    if any(marker.casefold() in folded for marker in HYPE_MARKERS):
        score -= 35
        reasons.append("含高收益宣传风险")
    if any(marker.casefold() in folded for marker in PROMOTION_MARKERS):
        score -= 5
        reasons.append("含产品推广信息")

    score = max(0, min(100, score))
    evaluated["wechat_score"] = score
    evaluated["wechat_reasons"] = reasons
    if score >= min_score:
        evaluated["selection"] = "selected"
        evaluated["reject_reasons"] = []
    else:
        evaluated["selection"] = "rejected"
        evaluated["reject_reasons"] = ["公众号适配分 %d 低于 %d" % (score, min_score)]
    return evaluated


def select_wechat_items(items, topic="", aliases=None, queries=None, min_score=70):
    resolved_aliases = topic_aliases(topic, aliases, queries)
    selected, rejected = [], []
    for item in items:
        evaluated = evaluate_item(item, resolved_aliases, min_score)
        if evaluated["selection"] == "selected":
            selected.append(evaluated)
        else:
            rejected.append(evaluated)
    return selected, rejected, resolved_aliases

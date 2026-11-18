#!/usr/bin/env python3
"""小红书汽车账号的禁选主题与讲解视频规则。"""

import re

from candidate_review import ACTION_SERIES
from mechanism_duplicates import AMBIGUOUS, MECHANISMS, fold

FORBIDDEN_TOPIC_RE = re.compile(
    r"(?i)(?:海外玩车现场|地下车聚|车聚|车展|汽车展|车主故事|"
    r"\bcar\s*(?:meet|show)\b|\bauto\s+show\b|\bmotor\s+show\b|"
    r"\bowner(?:'s)?\s+story\b|\bgoodwood\b|\bfestival\s+of\s+speed\b|\bsema\b)"
)

DRIVING_TOPIC_RE = re.compile(
    r"(?i)(?:特色驾驶场景|公路驾驶|第一视角驾驶|驾驶\s*POV|"
    r"\bdriving\s+pov\b|\bpov\s+drive\b|\broad\s+trip\b)"
)

MOUNTAIN_DRIVING_RE = re.compile(
    r"(?i)(?:山路|峠|\btouge\b|\bmountain[\s_-]*(?:road|pass|drive|driving)\b|"
    r"\b(?:road|drive|driving)[\s_-]*(?:in|on|through)?[\s_-]*(?:the[\s_-]*)?mountains?\b|"
    r"\bcanyon[\s_-]*(?:road|drive|driving)\b)"
)

EXPLAINER_RE = re.compile(
    r"(?i)(?:真人(?:出镜|讲解|解说)|出镜(?:讲解|解说)|口播|屏幕讲解|讲解视频|解说视频|"
    r"视频.{0,8}(?:讲到|讲解|解说)|\btalking[\s_-]*head[\s_-]*(?:explainer|commentary)?\b|"
    r"\bpresenter[\s_-]*(?:led|explainer)\b|\bscreen[\s_-]*(?:explainer|commentary)\b|"
    r"\bexplainer\s+video\b)"
)


COLOR_TECH = re.compile(r'(?<![a-z0-9])(?:ix[ -]+flow|i[ -]+vision[ -]+dee)(?![a-z0-9])')
EXTERIOR_INK = re.compile(
    r'(?:e[ -]?ink|electronic ink).{0,30}(?:bodywork|body panels?|exterior)|'
    r'(?:exterior|bodywork|body panels?).{0,30}(?:e[ -]?ink|electronic ink)|'
    r'(?:电子墨水|电子纸).{0,8}车身|车身.{0,8}(?:电子墨水|电子纸)')
COLOR_CHANGE = re.compile(
    r'\bcolou?r[ -]chang(?:e|es|ing)\b|\bchang(?:e|es|ing) (?:the |its )?colou?rs?\b|'
    r'\bcambia(?:r)? de color\b|(?:车身|电子墨水|表面)(?:可变色|变色|颜色(?:可)?(?:变化|切换))|改变车身颜色|'
    r'\b(?:switch\w*|chang\w*|turn\w*) from (?:black|white|grey|gray) to (?:black|white|grey|gray)\b')
NON_BODY_COLOR = re.compile(
    r'\bcolou?r[ -]changing (?:phone|screen|display|jacket|lights?|wheels?)\b|'
    r'\b(?:phone|screen|display|jacket|lights?) (?:can )?changes? colou?r|手机变色|屏幕变色|灯光变色')
# 其他部件、位移或车型比较意味着可能有独立选题；交回画面审核。
OTHER_SUBJECT = re.compile(
    r'\b(?:doors?|hood|bonnet|roof|seats?|wings?|spoilers?|flaps?|wheels?|grille|steering|'
    r'suspension|headlights?|airbrakes?|actuators?|mirrors?|wipers?|pedals?|axles?|motors?|interior|dashboard|unfold\w*|fold\w*|deploy\w*|rotat\w*|retract\w*|'
    r'lift\w*|open\w*|articulat\w*|extend\w*|pivot\w*|telescop\w*|morph\w*|shape|bounc\w*|slide[sd]?|sliding|moving|moves?|cannot|'
    r'versus|vs|compare\w*|another (?:car|vehicle)|puertas?|techo|aleron)\b|'
    r'车门|机盖|顶篷|尾翼|座椅|格栅|车灯|车轮|悬[架挂]|转向|折叠|升降|展开|伸缩|翻转|开启|座舱|内饰|另一[辆款]|后视镜|雨刷|伸出|转动|变形|弹跳|位移|不能')


def color_only_match(text, series_id):
    if series_id != ACTION_SERIES:
        return None
    caption = fold(re.sub(r'https?://\S+|@\w+|#(?!ix\s+flow\b)\w+', ' ', text, flags=re.I)).replace('#', '')
    technology = COLOR_TECH.search(caption) or EXTERIOR_INK.search(caption)
    color = COLOR_CHANGE.search(caption)
    if (not technology or not color or NON_BODY_COLOR.search(caption)
            or OTHER_SUBJECT.search(caption) or AMBIGUOUS.search(caption)
            or any(re.search(pattern, caption) for pattern in MECHANISMS.values())):
        return None
    # 提问本身不能证明发生了变色；后续电耗或维修问题不影响前面的明确陈述。
    clause = next((part.strip() for part in re.findall(r'[^.!?。！？]+[.!?。！？]?', caption)
                   if COLOR_CHANGE.search(part)), '')
    if clause.endswith(('?', '？')):
        return None
    return {'technology_evidence': technology.group(), 'color_evidence': color.group()}


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


def forbidden_scene_match(item):
    haystack = "\n".join(str(value) for value in content_values(item) if value)
    match = FORBIDDEN_TOPIC_RE.search(haystack)
    if match:
        return match.group(0)
    if not MOUNTAIN_DRIVING_RE.search(haystack):
        match = DRIVING_TOPIC_RE.search(haystack)
        if match:
            return match.group(0)
    return None


def forbidden_topic_match(item):
    return forbidden_scene_match(item) or explainer_video_match(item)


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
        forbidden = forbidden_scene_match(candidate)
        if forbidden:
            skipped.append({
                "candidate": candidate,
                "reason": "小红书禁选：海外玩车现场、车聚车展、车主故事或非山路特色驾驶场景（匹配“%s”）" % forbidden,
            })
        else:
            allowed.append(candidate)
    return allowed, skipped

#!/usr/bin/env python3
"""小红书汽车账号的禁选主题与讲解视频规则。"""

import re

from candidate_review import ACTION_SERIES
from mechanism_duplicates import MECHANISMS, fold

FORBIDDEN_TOPIC_RE = re.compile(
    r"(?i)(?:海外玩车现场|地下车聚|车聚|车展|汽车展|车主故事|"
    r"\bcar\s*(?:meet|show)\b|\bauto\s+show\b|\bmotor\s+show\b|"
    r"\bowner(?:'s)?\s+story\b|\bgoodwood\b|\bfestival\s+of\s+speed\b|\bsema\b)"
)

EXPLAINER_RE = re.compile(
    r"(?i)(?:真人(?:出镜|讲解|解说)|出镜(?:讲解|解说)|口播|屏幕讲解|讲解视频|解说视频|"
    r"视频.{0,8}(?:讲到|讲解|解说)|\btalking[\s_-]*head[\s_-]*(?:explainer|commentary)?\b|"
    r"\bpresenter[\s_-]*(?:led|explainer)\b|\bscreen[\s_-]*(?:explainer|commentary)\b|"
    r"\bexplainer\s+video\b)"
)


# Dee 也可能是人名。简称需要由宝马直接引出，完整车型名可独立识别。
DEE_MODEL = (r'(?:i[ -]+vision[ -]+dee|(?:bmw|宝马)\s*(?:(?:just )?'
             r"(?:announced|introduces?|unveils?|calls it)\s*[:：-]?\s*|[’']s\s+)?dee)")
NAMED_DEE_ZH = (
    r'宝马(?:有|推出|发布|造了)(?:一[辆台款])?(?:'
    r'(?:叫|名叫|名为)\s*["“\']?dee["”\']?\s*的(?:能|会|可)?变色(?:概念)?车'
    r'|(?:能|会|可)?变色(?:概念)?车(?:叫|名叫|名为)\s*["“\']?dee)')
DEE_NAMED_CAR = (
    r'bmw\s+(?:has|made|built|introduces?|unveils?|unveiled)\s+(?:(?:a|the|its)\s+)?'
    r'(?:new\s+)?colou?r[ -]changing\s+(?:concept\s+)?(?:car|vehicle)\s+'
    r'(?:called|named)\s+["“\']?dee|' + NAMED_DEE_ZH)
COLOR_TECH = re.compile(
    r'(?<![a-z0-9])(?:ix[ -]+flow|' + DEE_MODEL + '|' + DEE_NAMED_CAR + r')(?![a-z0-9])')
EXTERIOR_INK = re.compile(
    r'(?:e[ -]?ink|electronic ink).{0,30}(?:bodywork|body panels?|exterior)|'
    r'(?:exterior|bodywork|body panels?).{0,30}(?:e[ -]?ink|electronic ink)|'
    r'(?:电子墨水|电子纸).{0,8}车身|车身.{0,8}(?:电子墨水|电子纸)')
COLOR_CHANGE = re.compile(NAMED_DEE_ZH + r'|'
    r'\bcolou?r[ -]chang(?:e|es|ing)\b|\b(?:chang(?:e|es|ed|ing)|switch(?:es|ed|ing)?) (?:the |its )?(?:(?:body|exterior) )?colou?rs?\b|'
    r'\bcambia(?:r)? de color\b|(?:车身|电子墨水|表面)(?:可变色|变色|颜色(?:可)?(?:变化|切换))|改变车身颜色|'
    r'\b(?:switch\w*|chang\w*|turn\w*) from (?:black|white|grey|gray) to (?:black|white|grey|gray)\b')
# 仅有 E Ink 时，限于直接修饰 Dee 的整车称谓；附属屏幕、仪表等另行审核。
DEE_INK_FEATURE = re.compile(DEE_MODEL + r'\s+(?:with\s+|w/\s*)e[ -]?ink(?=\s*(?:[!.,;。！]|$))')
EXTERIOR_COLOR = re.compile(
    r'\bconfigurable (?:exterior|bodywork) (?:in|with) (?:up to )?\d+ (?:different )?colou?rs?\b|'
    r'\b(?:individually )?configurable (?:exterior|body) colou?rs?\b|'
    r'\b(?:exterior|body) colou?rs? (?:is |are |can be )?(?:individually )?(?:configurable|configured)\b|'
    r'可(?:自由|独立)?配置(?:的)?车身颜色|车身(?:的)?(?:颜色|色彩)(?:可(?:以)?|支持)(?:自由|单独|独立|自定义)?(?:配置|调节|设置)|'
    r'\b(?:\d+|full)[ -]colou?r e[ -]?ink\b|'
    r'\be[ -]?ink (?:technology )?to (?:transform|change) (?:its|the) exterior appearance\b|'
    r'\b(?:carro|veiculo|carroceria)(?: eletric[oa])? (?:que )?(?:muda|mudam|pode mudar) de cor\b|'
    r'\b(?:mudar|alterar) (?:a )?cor da carroceria\b|'
    r'(?:(?:ي|ت)تغير لون (?:المركبة|السيارة)|(?:السيارة|المركبة) (?:ال)?متغيرة اللون|'
    r'(?:ال)?متغيرة اللون(?=\s+(?:bmw|ix flow))|(?:تغيير|التحكم في) لونها الخارجي)')
NON_BODY_COLOR = re.compile(
    r'\bcolou?r[ -]changing (?:phone|screen|display|jacket|lights?|wheels?)\b|'
    r'\b(?:phone|screen|display|jacket|lights?) (?:can )?changes? colou?r|手机变色|屏幕变色|灯光变色')
# 其他部件、位移或车型比较意味着可能有独立选题；交回画面审核。
OTHER_SUBJECT = re.compile(
    r'\b(?:doors?|hood|bonnet|roof|seats?|wings?|spoilers?|flaps?|wheels?|grille|steering|'
    r'suspension|headlights?|airbrakes?|actuators?|mirrors?|wipers?|pedals?|axles?|motors?|interior|dashboard|unfold\w*|fold\w*|deploy\w*|rotat\w*|retract\w*|'
    r'lift\w*|open\w*|articulat\w*|extend\w*|pivot\w*|telescop\w*|morph\w*|shape|bounc\w*|slide[sd]?|sliding|moving|moves?|'
    r'versus|vs|compare\w*|another (?:car|vehicle)|puertas?|techo|aleron)\b|'
    r'车门|机盖|顶篷|尾翼|座椅|格栅|车灯|车轮|悬[架挂]|转向|折叠|升降|展开|伸缩|翻转|开启|座舱|内饰|另一[辆款]|后视镜|雨刷|伸出|转动|变形|弹跳|位移|抬起|落下|上翻|下翻|移动|收起|降下|弹出|弹起')


# 跨句扫描动作与主题冲突。葡语“轮圈也能变色”本身没有机械动作。
MIXED_OR_UNCERTAIN = re.compile(
    r'\b(?:active aero(?:dynamics)?|revolv\w*|spin(?:s|ning)?|rais(?:e[sd]?|ing)|lowers?|rises?|kneels?|'
    r'portas?|puertas?|teto|techo|capo|aerofolio|suspensao|volante|'
    r'abre\w*|abr[ei]r\w*|gira\w*|rotacion\w*|recolh\w*|retrat\w*|dobra\w*|'
    r'desdobr\w*|levant\w*|desplieg\w*|pliega\w*|ester[cz]\w*|vira\w*|movem|moviment\w*|mueve\w*|'
    r'comparad\w*|outr[oa] (?:carro|veiculo))\b|'
    r'ابواب|الباب|باب السيارة|سقف|جناح|عجلات.{0,12}(?:تدور|تلتف)|فتح|انفتاح|'
    r'يرتفع|ترتفع|يطو[يى]|تطو[يى]|تتحرك|يتحرك|تدور|يدور|دوران|توجيه|'
    r'مقارنة')
COLOR_TOPIC_SWITCH = re.compile(
    r'\b(?:unlike|whereas|compared (?:to|with)|instead of|wish(?:es)?|if only)\b|对比|相比|而非|要是|假如')
UNCERTAIN_COLOR = re.compile(
    r'\b(?:what if|could|might|suppose|hypothetically|هل)\b|是否|能否|假设|'
    r'\bthe future of (?:the )?colou?r[ -]changing\b')
COLOR_EVIDENCE = re.compile('|'.join(p.pattern for p in (COLOR_CHANGE, EXTERIOR_COLOR, DEE_INK_FEATURE)))
# 否定要直接作用在命中的变色短语上。允许助动词、冠词和少量修饰副词介于两者之间。
COLOR_NEGATION_BEFORE = re.compile(
    r"\b(?:(?:not(?!\s+(?:only|just|merely)\b)|no|never|cannot|without|non(?=-)|"
    r"(?:do(?:es)?|did|is|are|can|could|will|would|has|have|was|were)n['’]t|"
    r"nao(?!\s+(?:so|apenas)\b)|sem|nunca|لا|ليس\w*|لن|لم|بدون)[\s-]+"
    r"(?:\b(?:really|actually|yet|ever|even|quite|fully|electronically|be|a|an|any|the|e|um|uma|es|un|una|"
    r"consegue|pode|puede|lo|يزال|يمكن ان|have|support|able to|capable of|evidence of|sign of)\s+)*|no longer\s+)$|"
    r'(?:并非|不是|没有|不会|不能|无法|未能|不)(?:真的|实际|可|能|支持|实现|进行|任何)?\s*$')
COLOR_NEGATION_AFTER = re.compile(
    r"^\s*(?:is|are|was|were)\s+(?:not\s+(?:possible|real|supported|available)|"
    r"impossible|unsupported|unavailable|fake)\b|^\s*(?:不可用|未实现|无法实现|是假的)")
SECONDARY_DISPLAY = re.compile(
    r'\b(?:screen|dashboard|tablet|phone|jacket|infotainment|touchscreen|smartwatch|monitor|'
    r'tela|painel de instrumentos|celular|pantalla)\b|\be[ -]?ink (?:digital )?display\b|'
    r'屏幕|仪表|手机|شاشة|هاتف')


def color_only_match(text, series_id):
    if series_id != ACTION_SERIES:
        return None
    source = re.sub(r'https?://\S+|@\w+', ' ', text, flags=re.I)
    caption = fold(re.sub(r'#(?!ix\s+flow\b)\w+', ' ', source, flags=re.I)).replace('#', '')
    technology = COLOR_TECH.search(caption) or EXTERIOR_INK.search(caption)
    if not technology:
        return None
    # 标签不能单独提供车型或变色证据，但不能删掉标签中的第二个机构。
    context = fold(re.sub(r'#(\w+)', lambda m: re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', m[1]).replace('_', ' '),
                          source))
    if (OTHER_SUBJECT.search(context) or COLOR_TOPIC_SWITCH.search(caption) or MIXED_OR_UNCERTAIN.search(context)
            or NON_BODY_COLOR.search(caption) or SECONDARY_DISPLAY.search(context)
            or any(re.search(pattern, context) for pattern in MECHANISMS.values())):
        return None
    confirmed = None
    for clause in re.findall(r'[^.!?。！？؟]+[.!?。！？؟]?', caption):
        for color in COLOR_EVIDENCE.finditer(clause):
            if (COLOR_NEGATION_BEFORE.search(clause[:color.start()])
                    or COLOR_NEGATION_AFTER.search(clause[color.end():])):
                return None
            if not confirmed and not clause.endswith(('?', '？', '؟')) and not UNCERTAIN_COLOR.search(clause):
                confirmed = color
    if confirmed:
        return {'technology_evidence': technology.group(), 'color_evidence': confirmed.group()}
    return None


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
                "reason": "小红书禁选：海外玩车现场、车聚车展、车主故事（匹配“%s”）" % forbidden,
            })
        else:
            allowed.append(candidate)
    return allowed, skipped

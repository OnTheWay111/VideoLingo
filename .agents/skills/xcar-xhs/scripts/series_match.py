"""系列初筛只拦截原帖明示的冲突；词表命中用于安排下载，最后由画面核验。"""
import re
from functools import lru_cache

from content_policy import BRAND_SERIES
from race_humor import RACE_TERMS, SERIES_ID as RACE_HUMOR, assess_race
from topic_planner import PILLARS


# 每个系列给审核员同一个可回答的问题。关键词仅为正向线索，缺词不能证明不匹配。
SERIES = {
    RACE_HUMOR: ('哪场正赛中发生了什么自然插曲，连续画面和赛事来源如何对上？', RACE_TERMS),
    'rare_jdm': ('哪台日系车的哪个设计值得辨认？', r'kei car|jdm|軽自動車|日系'),
    'limited_supercars': ('具体限量版本或独有设计是什么？', r'limited edition|one[- ]off|one of|only \d+|限量|孤品'),
    'screen_game_cars': ('哪台实车对应哪部作品，是原车、同款还是复刻？', r'picture car|movie car|screen.used|replica|电影|复刻'),
    'rare_performance_wagons': ('哪个细节能辨认这台原厂性能旅行版？', r'wagon|estate|avant|旅行车'),
    'regional_production_cars': ('能辨认哪台量产车、在哪个市场销售？', r'\bute\b|australian|market|production|市场|量产'),
    'rally_road_cars': ('画面中哪个细节和事实来源能辨认具体公路版本？', r'stradale|road[- ](?:car|version|going|legal)|street[- ]legal|homologation special|公路版|市售版|量产版'),
    'engine_swaps': ('哪台车装上哪种不同的动力总成？', r'\bswap(?:ped)?\b|conversion|移植|换装'),
    'custom_fabrication': ('哪个定制部件展示了什么工艺？', r'fabricat|widebody|coachbuilt|焊接|宽体|定制'),
    'car_mechanisms': ('哪台车的哪个部件在前三秒如何运动？', r'fold|rotat|retract|deploy|open|lift|lower|翻转|开启|升降|折叠'),
    'classic_restoration': ('同一台车整备前后发生了什么变化？', r'restor|first start|before and after|dry ice|修复|整备'),
    'offroad_equipment': ('哪种越野装备正在发挥作用？', r'differential|sway bar|articulation|cross axle|差速锁|交叉轴'),
    'microcars': ('这台微型车的尺寸或结构特别在哪里？', r'microcar|bubble car|three.wheel|微型车|三轮'),
    'experimental_vehicles': ('哪台实验车在验证什么设计？', r'prototype|concept|experimental|amphibious|原型|概念|实验'),
    'mountain_driving': ('能辨认哪台车、哪种山路场景？', r'mountain|canyon|touge|山路|山口|峠'),
    'small_car_space': ('座椅、车门或地板怎样改变可用空间？', r'fold|flat floor|sliding door|camper|折叠|侧滑门|空间'),
}
REAL_FOOTAGE_SERIES = {BRAND_SERIES, 'rally_road_cars', 'regional_production_cars', 'car_mechanisms', 'screen_game_cars',
                      'classic_restoration', 'offroad_equipment', 'mountain_driving', 'rare_performance_wagons'}
ROAD = re.compile(SERIES['rally_road_cars'][1], re.I)
RACE = re.compile(
    r'\brally[ -]?1\b|\b(?:s1 e2|polo r wrc|206 wrc|xsara wrc)\b|'
    r'\b(?:rally|final|special) stage\b|\bstage (?:win|drama|live|onboard)\b|'
    r'\b(?:wrc|rally)\s*(?:live|onboard)\b|拉力赛段|赛段计时|赛车组别', re.I)
VIRTUAL = re.compile(r'\b(?:gameplay footage|in.game footage|sim racing clip|ai.generated (?:video|footage)|'
                     r'rendered in (?:blender|unreal))\b|游戏录屏|游戏实机画面|AI\s*生成画面', re.I)
GAMEPLAY = re.compile(
    r'\bgameplay (?:video|footage|clip)\b|'
    r'\b(?:gt7|gran turismo \d|forza horizon \d)\b.{0,35}\b(?:gameplay|daily race|free update)\b|'
    r'游戏录屏|ゲームプレイ', re.I)
MIXED = re.compile(r'\b(?:not|versus|vs\.?|compared|alongside|replica of|real life|real car)\b|并非|不是|对比|与实车', re.I)
DETAIL = re.compile(r'\b(?:door|roof|engine|tailgate|chassis|interior|wing|seat|suspension)s?\b|'
                    r'车门|车顶|底盘|尾门|发动机|座椅|尾翼|悬架', re.I)


@lru_cache(maxsize=None)
def named_models(series):
    for pillar in PILLARS.values():
        for spec in pillar['series']:
            if spec['id'] == series:
                names = {s.casefold() for q in spec['query_groups'] for s in re.findall(r'"([^"]+)"', q)
                         if re.search(r'\d|[A-Z][a-z]+ [A-Z]', s)}
                if names:
                    return re.compile(r'(?<!\w)(?:' + '|'.join(map(re.escape, sorted(names))) + r')(?!\w)', re.I)
    return None


def assess_series(series, text):
    """返回（匹配优先级，明确冲突原因）；模糊原帖继续参与画面审核。"""
    if series not in SERIES and series != BRAND_SERIES:
        return 0, ''
    caption = re.sub(r'https?://\S+|@\w+', ' ', text or '')
    if series == RACE_HUMOR:
        return assess_race(caption)
    road = ROAD.search(caption)
    mixed = MIXED.search(caption)
    if series != 'experimental_vehicles' and GAMEPLAY.search(caption) and not mixed:
        return -1, '原帖明确为游戏演示，缺少实车依据'
    if series in REAL_FOOTAGE_SERIES and VIRTUAL.search(caption) and not mixed:
        return -1, '原帖明确标注虚拟画面，缺少该系列要求的实车展示'
    identifiable_road_model = re.search(r'\b(?:22b(?:-sti)?|gt-four (?:rc|carlos sainz))\b', caption, re.I)
    if series == 'rally_road_cars' and not (road or mixed or identifiable_road_model):
        race = RACE.search(caption)
        if race or (re.search(r'\bwrc\b', caption, re.I) and re.search(r'\bonboard\b', caption, re.I)):
            return -1, '原帖明确为拉力赛车或赛事转播，未给出公路版依据'
    if series == BRAND_SERIES:
        return 0, ''
    names = named_models(series)
    model = bool(names and names.search(caption))
    cue = bool(re.search(SERIES[series][1], caption, re.I))
    return int(model or cue) + int(model and (cue or bool(DETAIL.search(caption)))), ''


def reviewed_series_conflict(item):
    series = item.get('topic_series_id')
    caption = (item.get('candidate') or {}).get('text', '')
    if series == 'rally_road_cars' and VIRTUAL.search(item.get('ai_disclosure') or ''):
        return '拉力公路版须核验实车，该素材披露为生成画面'
    return assess_series(series, caption)[1]

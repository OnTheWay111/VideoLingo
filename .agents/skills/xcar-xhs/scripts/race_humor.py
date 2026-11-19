"""正赛自然趣事：原帖初筛，审核时核对赛事与连续画面。"""
import re
from urllib.parse import urlsplit

SERIES_ID = 'race_humor'
RACE_TERMS = r'\b(?:f1|formula (?:1|one)|nascar|indycar|fiawec|wec|wrc|grand prix|race day|race lap|pit stop)\b|正赛|大奖赛|计时赛段'
MOMENT = re.compile(r'wrong (?:pit|box|garage)|pit (?:box|stop) (?:mix.up|confusion)|'
                    r'unexpected (?:visitor|passenger)|funny moment|team radio|进错|走错|乌龙|意外访客|趣事', re.I)
STAGED = re.compile(r'\b(?:parody|prank|spoof|staged|skit|reenactment|re-enactment|'
                    r'meme edit|fake (?:radio|race)|voice.?over parody|ai.generated|sora|'
                    r'gameplay|sim racing|iracing|assetto corsa|f1 2[0-9] game)\b|'
                    r'恶搞|摆拍|整蛊|重演|虚构|游戏录屏|模拟赛车|搞笑配音|后期配音|AI\s*生成', re.I)
OFF_SESSION = re.compile(r'\b(?:free practice|fp[123]|qualifying|pre.race|post.race|'
                         r'after the race|pit.stop practice|demonstration run|publicity stunt)\b|'
                         r'练习赛|排位赛|赛前彩排|赛后采访|广告拍摄', re.I)
RACE_FIELDS = ('race_event', 'race_phase', 'race_source_url', 'incident_evidence')


def assess_race(text):
    if STAGED.search(text):
        return -1, '原帖明示恶搞、表演、模拟或生成画面'
    if OFF_SESSION.search(text):
        return -1, '原帖指向正赛以外的场景，无法单独确认为比赛中发生'
    return int(bool(re.search(RACE_TERMS, text, re.I))) + int(bool(MOMENT.search(text))), ''


def validate_race_evidence(item, editorial):
    _, conflict = assess_race((item.get('candidate') or {}).get('text', ''))
    if conflict or STAGED.search(item.get('ai_disclosure') or ''):
        raise ValueError(conflict or '正赛趣事须核验真实比赛画面')
    for field in RACE_FIELDS:
        if not isinstance(editorial.get(field), str) or not editorial[field].strip():
            raise ValueError('正赛趣事缺少核验依据：' + field)
    if editorial['race_phase'] not in ('race', 'sprint_race', 'timed_rally_stage'):
        raise ValueError('只接受正赛、冲刺正赛或正式计时拉力赛段')
    source = urlsplit(editorial['race_source_url'])
    if source.scheme not in ('http', 'https') or not source.hostname:
        raise ValueError('须提供可复核该场比赛和该插曲的来源链接')

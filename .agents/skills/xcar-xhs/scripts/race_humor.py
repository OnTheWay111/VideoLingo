"""正赛自然趣事：原帖初筛，审核时核对赛事与连续画面。"""
import re
import unicodedata
from urllib.parse import urlsplit

SERIES_ID = 'race_humor'
RACE_TERMS = r'\b(?:f1|formula (?:1|one)|nascar|indycar|fiawec|wec|wrc|grand prix|race day|race lap|pit stop)\b|正赛|大奖赛|计时赛段'
MOMENT = re.compile(r'wrong (?:pit|box|garage)|pit (?:box|stop) (?:mix.up|confusion)|'
                    r'unexpected (?:visitor|passenger)|funny moment|team radio|进错|走错|乌龙|意外访客|趣事', re.I)
STAGED = re.compile(r'\b(?:parody|prank|spoof|staged|scripted skit|sketch comedy|skit|reenactment|re-enactment|'
                    r'meme edit|fake (?:radio|race)|voice.?over parody|ai.generated|sora|'
                    r'gameplay|sim racing|iracing|assetto corsa|f1 2[0-9] game)\b|'
                    r'恶搞|摆拍|整蛊|重演|虚构|游戏录屏|模拟赛车|搞笑配音|后期配音|AI\s*生成', re.I)
OFF_SESSION = re.compile(r'\b(?:(?:free |open |pit stop )?practice(?: session)?|fp[ -]?[123]|q[123]|quali(?:fying)?|sprint shootout|pre.race|post.race|'
                         r'after the race|pit.stop practice|demonstration run|publicity stunt)\b|'
                         r'练习(?:赛)?|排位(?:赛)?|赛前彩排|赛后采访|广告拍摄', re.I)
RACE_FIELDS = ('race_event', 'race_phase', 'race_source_url', 'incident_evidence')


def assess_race(text):
    text = re.sub(r'https?://\S+|@\w+', ' ', text or '')
    # Negated labels do not identify the footage as staged or off-session.
    text = re.sub(r"\b(?:not|wasn't|isn't|never)\s+(?:(?:from|during|a|an|the)\s+){0,3}"
                  r"(?:parody|prank|staged|fake|fp[1-3]|practice|qualifying)\b|"
                  r"(?:并非|不是|没有)(?:恶搞|摆拍|排位赛?|练习赛?)", ' ', text, flags=re.I)
    if STAGED.search(text):
        return -1, '原帖明示恶搞、表演、模拟或生成画面'
    if (re.search(r'\bI (?:just drove|had the privilege to (?:pilot|drive))\b', text, re.I)
            and re.search(r'racing school|racingschool|driving experience|track day', text, re.I)):
        return -1, '原帖明确以个人试驾体验为主体'
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
    if 'race_incident' in editorial:
        profile = validate_incident(editorial['race_incident'])
        years = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', editorial['race_event'])
        if years and profile['key'].split('-')[1] not in years:
            raise ValueError('race_incident 年份与已核实的正赛不一致')
    source = urlsplit(editorial['race_source_url'])
    if source.scheme not in ('http', 'https') or not source.hostname:
        raise ValueError('须提供可复核该场比赛和该插曲的来源链接')

# Reviewed events from before structured incident descriptions were introduced.
# Each group requires one literal alias; a driver, venue or prop alone is insufficient.
LEGACY_INCIDENTS = (
    ('indycar-2024-barber-falling-mannequin',
     [['mannequin', 'georgina', '人偶'], ['barber', 'barber motorsports park', '巴伯'],
      ['fell', 'falling', 'falls', 'dropped', '掉落', '落下', 'tombé']]),
    ('f1-2013-malaysia-hamilton-wrong-pit',
     [['hamilton', '汉密尔顿'], ['malaysia', 'sepang', '雪邦', '马来西亚'],
      ['wrong pit', 'wrong garage', 'wrong box', 'wrong team', '误入', '进错', '拐向了前东家']]),
    ('f1-2011-china-button-wrong-pit',
     [['button', '巴顿'], ['china', 'chinese gp', 'shanghai', '中国', '上海'],
      ['wrong pit', 'wrong box', 'wrong garage', 'se metió al box de red bull', '停到了红牛', '误入', '进错']]),
)


def folded(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))


def has_alias(text, alias):
    term = folded(alias)
    if not term.isascii():
        return term in text
    return re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text) is not None


def validate_incident(profile):
    if not isinstance(profile, dict) or not re.fullmatch(
            r'[a-z0-9]+-(?:19|20)\d\d-(?:[a-z0-9]+-)+[a-z0-9]+', str(profile.get('key', ''))):
        raise ValueError('race_incident.key 须为锦标赛-年份-分站-插曲的稳定英文标识')
    groups = profile.get('anchors')
    if not isinstance(groups, list) or not 2 <= len(groups) <= 6 or any(
            not isinstance(g, list) or not g or any(not isinstance(t, str) or len(t.strip()) < 2
                                                    for t in g) for g in groups):
        raise ValueError('race_incident.anchors 须提供独立的身份和动作词组，每组列出明确的同义写法')
    return profile


def matches_profile(text, profile):
    text = folded(re.sub(r'https?://\S+|@\w+', ' ', text or ''))
    years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', text))
    if years and years != {profile['key'].split('-')[1]}:
        return False
    return all(any(has_alias(text, alias) for alias in group) for group in profile['anchors'])


def incident_profile(item):
    if item.get('topic_series_id') != SERIES_ID:
        return None
    editorial = (item.get('verification') or {}).get('editorial') or {}
    profile = editorial.get('race_incident') or item.get('race_incident')
    if profile:
        return validate_incident(profile)
    evidence = ' '.join(str(x or '') for x in (
        (item.get('candidate') or {}).get('text'), editorial.get('race_event'),
        editorial.get('incident_evidence'), item.get('title'), item.get('verifiable_fact')))
    for key, groups in LEGACY_INCIDENTS:
        profile = {'key': key, 'anchors': groups}
        if key.split('-')[1] in evidence and matches_profile(evidence, profile):
            return profile
    return None


def race_index(records, queued, approved, reserved=()):
    index = {}
    for rows, origin, statuses in ((records, '发布历史', {'published', 'unconfirmed'}),
                                    (queued, '候选池', {'queued', 'planned', 'published', 'unconfirmed'}),
                                    (approved, '本批审核', None), (reserved, '其他在制任务已核验', None)):
        for row in rows:
            if statuses and row.get('status', 'published') not in statuses:
                continue
            item = row.get('item') or row
            profile = incident_profile(item)
            if profile:
                source = row.get('source_id') or (item.get('candidate') or {}).get('id')
                if not source:
                    continue
                index.setdefault(profile['key'], dict(profile, origin=origin, source_id=str(source)))
    return index


def caption_incident(text, index):
    if re.search(r'\b(?:unlike|reminds? me of|reminiscent of|compared (?:to|with)|this time|whereas)\b|'
                 r'先是.{0,50}这次|不同于|让人想起|这次却', text, re.I):
        return None
    hits = [p for p in index.values() if matches_profile(text, p)]
    if len(hits) == 1:
        p = hits[0]
        return {field: p[field] for field in ('key', 'origin', 'source_id')}
    return None


def covered_queries(queries, items, events):
    covered = {}
    qualified = {sid: incident_profile(e['item']) for sid, e in items.items()
                 if e['status'] == 'approved' and e.get('series_id') == SERIES_ID}
    for query in queries:
        if query.get('series_id') != SERIES_ID:
            continue
        profile = next((qualified[sid] for ids in query.get('page_sources', {}).values()
                        for sid in ids if qualified.get(sid)), None)
        if not profile:
            # A mixed OR query can still find an independent event.
            positive = re.sub(r'(?:^|\s)(?:-"[^"]*"|-\S+|\w+:\S+)', ' ', query['query'])
            if not re.search(r'\bOR\b', positive, re.I):
                profile = caption_incident(positive, events)
        if profile:
            covered[query['query']] = profile['key']
    # Video and media tabs for the same words target the same event.
    def terms(query):
        return re.sub(r'\s+filter:(?:videos|media)\b', '', query, flags=re.I).strip()
    equivalent = {terms(q): key for q, key in covered.items()}
    for query in queries:
        key = equivalent.get(terms(query['query']))
        if key and query.get('series_id') == SERIES_ID:
            covered[query['query']] = key
    return covered

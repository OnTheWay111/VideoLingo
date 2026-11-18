"""仅凭原帖和近期已审核选题共有的明确车型、机构，跳过重复下载。"""
from datetime import timedelta
import re
import unicodedata

from content_policy import BRAND_ALIASES, normalize_brand, parse_datetime
from publication_queue import WAITING_STATUSES
from publish_history import ACTIVE_STATUSES


# 同类机构的不同实现分别匹配，如鹰翼门、鸥翼门、剪刀门。
MECHANISMS = {
    '鹰翼门': r'鹰翼(?:车)?门|falcon[ -]?wing(?:ed)?(?:[ -]doors?)?',
    '鸥翼门': r'鸥翼(?:式)?(?:车)?门|gull[ -]?wing(?:ed)?[ -]doors?',
    '下滑车门': r'下滑(?:式)?(?:车)?门|车门.{0,7}(?:下滑|下沉)|(?:vertically )?sliding doors?|doors? (?:slide|sliding|drop|sink)s? (?:down|into)',
    '剪刀门': r'剪刀(?:式)?(?:车)?门|scissor doors?',
    '蝴蝶门': r'蝴蝶(?:式)?(?:车)?门|butterfly doors?',
    '后轮转向': r'后轮转向|四轮转向|rear[ -](?:wheel|axle)[ -]steer(?:ing)?|rear steering wheels?|four[ -]wheel steer(?:ing)?',
    '弹出式头灯': r'跳灯|翻灯|pop[ -]?up (?:head)?lights?|flip[ -]?up (?:head)?lights?',
    '侧翻头灯': r'侧翻头灯|头灯.{0,5}侧向翻转|rotating headlights?',
    '车门藏伞': r'门.{0,9}(?:雨伞|藏伞)|umbrellas?.{0,25}\bdoors?\b|\bdoors?\b.{0,25}umbrellas?',
    '可拆后翼子板': r'可拆后翼子板|(?:拆|卸).{0,4}后翼子板|(?:remov\w*|detach\w*).{0,24}rear (?:fender|wing)|rear (?:fender|wing).{0,24}(?:remov\w*|detach\w*)',
    '液气悬架': r'液气悬[架挂]|hydro[ -]?pneumatic suspension',
    'Targa收顶': r'targa.{0,12}(?:收顶|后窗抬升)|targa roof',
    '原地掉头': r'原地(?:掉头|转向)|坦克掉头|tank turn|g[ -]turn',
    '收折硬顶': r'折叠硬顶|翻转硬顶|retractable hard[ -]?top|folding hard[ -]?top',
    '主动尾翼': r'主动尾翼|可伸缩尾翼|active (?:rear )?(?:spoiler|wing)|retractable (?:rear )?spoiler',
}
# 允许脱离品牌独立识别的专名，或同一确切版本的常见写法。
MODEL_ALIASES = {
    ('tesla', 'model x'): ('Model X',),
    ('tesla', 'cybertruck'): ('Cybertruck', '赛博皮卡'),
    ('mercedes-benz', '300 sl coupe'): ('Mercedes 300 SL Gullwing', '奔驰300SL鸥翼'),
    ('mercedes-benz', 'g-class electric prototype'): ('Mercedes EQG', 'Mercedes-Benz EQG',
                                                    'electric Mercedes G Wagon', 'Mercedes-Benz electric G Wagon'),
    ('rollsroyce', 'ghost'): ('古思特',),
}
MAKE_ALIASES = {'tesla': ('特斯拉',), 'citroen': ('雪铁龙',), 'rollsroyce': ('劳斯莱斯',)}
AMBIGUOUS = re.compile(r"\b(?:vs\.?|versus|unlike|whereas|compared (?:to|with)|instead of|without|not|no|never|lacks?|(?:doesn|isn|can)['’]t|wish(?:es)?|if only)\b|对比|相比|而非|并非|没有|要是|假如")
CLAUSE = re.compile(r'[.!?;。！？；\n]|\b(?:but|while)\b')


def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                   if not unicodedata.combining(c))


def name_pattern(name):
    # 英数字边界避免 Model X 命中 Model XL、BMW Z1 命中 BMW Z10。
    words = re.findall(r'[a-z]+|[0-9]+|[^\W\da-z_]+', fold(name))
    return r'[\s-]*'.join(re.escape(word) for word in words)


def recent_mechanisms(history_records, queue_records, now, approved=()):
    history_records = {r['source_id']: r for r in history_records if r.get('source_id')}.values()
    index = []
    reviewed = ({'item': item, 'source_id': item['candidate']['id'], 'status': 'approved',
                 'reviewed_at': item['verification']['reviewed_at']} for item in approved)
    for records, statuses, date_field in (
        (history_records, ACTIVE_STATUSES, 'published_at'),
        (queue_records, WAITING_STATUSES, 'enqueued_at'),
        (reviewed, {'approved'}, 'reviewed_at'),
    ):
        for record in records:
            if record.get('status', 'published') not in statuses:
                continue
            timestamp = parse_datetime(record.get(date_field) or record.get('recorded_at'))
            if timestamp is None or not timedelta() <= now - timestamp <= timedelta(days=30):
                continue
            item = record.get('item') or record
            brand, model = item.get('brand'), item.get('model')
            if not record.get('source_id') or not brand or not model:
                continue
            # 只使用已核验的本期选题，不从讨论题、下期预告或标签推断机构。
            topic = fold(' '.join(str(item.get(k) or '') for k in ('title', 'file_topic', 'verifiable_fact')))
            mechanisms = {label: pattern for label, pattern in MECHANISMS.items() if re.search(pattern, topic)}
            if not mechanisms:
                continue
            make = normalize_brand(fold(brand))
            makes = {brand, *MAKE_ALIASES.get(make, ()), *(k for k, v in BRAND_ALIASES.items() if v == make)}
            names = [alias + ' ' + model for alias in makes]
            names.extend(MODEL_ALIASES.get((make, fold(model)), ()))
            models = '|'.join(name_pattern(name) for name in names)
            index.append((re.compile(r'(?<![a-z0-9])(?:' + models + r')(?![a-z0-9])'), mechanisms,
                          {'source_id': record['source_id'], 'brand': brand, 'model': model,
                           'status': record.get('status', 'published'), 'matched_at': timestamp.isoformat()}))
    return index


def match_recent_mechanism(text, index):
    # 查询词、作者账号、链接和孤立标签不构成原帖证据。比较或否定句留给画面审核。
    caption = fold(re.sub(r'https?://\S+|[@#]\w+', ' ', text))
    if re.search(r'\b(?:vs|versus|unlike|whereas)\b|对比|相比', caption):
        return None
    for clause in CLAUSE.split(caption):
        if AMBIGUOUS.search(clause.replace('not just ', '').replace('not only ', '')):
            continue
        for model, mechanisms, record in index:
            subject = model.search(clause)
            if not subject:
                continue
            for label, pattern in mechanisms.items():
                action = re.search(pattern, clause)
                if not action:
                    continue
                first, last = sorted((subject, action), key=lambda m: m.start())
                gap = clause[first.end():last.start()]
                if len(gap) <= 80 and not re.search(r'\b(?:and|or|than|nor|alongside|beside|next to|behind|in front of|versus)\b|以及|相比|对比|、', gap):
                    return {**record, 'mechanism': label, 'model_evidence': subject.group(),
                            'mechanism_evidence': action.group()}
    return None

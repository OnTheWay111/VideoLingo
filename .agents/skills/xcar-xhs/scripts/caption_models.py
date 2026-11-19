"""用原帖明确的车型主体和已有审核身份做下载前去重。"""
import re
from functools import lru_cache

from content_policy import BRAND_ALIASES, content_duplicate, content_identity, normalize_brand
from mechanism_duplicates import MAKE_ALIASES, MODEL_ALIASES, fold, name_pattern
from publication_queue import WAITING_STATUSES
from publish_history import ACTIVE_STATUSES

# 只认车名或以车为主语的描述。比较、移植动力、模型和未明版本留给画面审核。
AMBIGUOUS = re.compile(r"\b(?:vs|versus|compared|unlike|replica|inspired|based on|powered by|"
                       r"engine swap|donor|scale model|tribute|not|or|and|alongside|comparison|lookalike)\b|对比|相比|复刻|仿制|移植|并非|模型")
LEAD = r"(?:^|(?<=[.!?。！？]))[\W_]*(?:(?:first|last|one|two|three|four|five|six|seven|eight|nine|ten|\d+|check out|look at|footage of|a closer look at|this|the|a|an|my|rare|classic|meet|watch|spotted|car|19\d\d|20\d\d)\b[\W_]+|这台|这辆)*"
TAIL = (r"(?=$|[\s]*[.,!?:。！？，：]|\s+(?:opens?|has|is|was|at|in|with|on|"
        r"cars?|starts?|arrives?|leaves?|drives?|rolling|driving|starting|walkaround|details)\b|的|正在|驶过|启动|亮相|展示|\s*現在)")


@lru_cache(maxsize=1024)
def subject_pattern(brand, model):
    make = normalize_brand(fold(brand))
    makes = {brand, *MAKE_ALIASES.get(make, ()), *(k for k, v in BRAND_ALIASES.items() if v == make)}
    if make in ('jaguar', '捷豹'):
        makes.update(('Jaguar', '捷豹'))
    names = [m + ' ' + model for m in makes]
    names.extend(MODEL_ALIASES.get((make, fold(model)), ()))
    # 无品牌只识别型号式专名。纯数字和常用词须有厂牌。
    if (re.fullmatch(r'[a-z]{2,}[ -]?\d{2,}[a-z]*', fold(model))
            or (re.search(r'\d', model) and re.search(r'\s[a-zA-Z]{4,}', model))):
        names.append(model)
    model_subject = LEAD + '(?:' + '|'.join(name_pattern(n) for n in names) + ')' + TAIL
    if re.search(r'\d', model) and re.search(r'\s[a-zA-Z]{4,}', model):
        # 完整版本名后有明确的主谓结构时，可识别叙述中段的车型。
        model_subject += (r'|(?<![a-z0-9])' + name_pattern(model)
                          + r'(?=\s+cars?\s+(?:recently |have |has |were |are |started |rolled ))')
    return re.compile(model_subject)


def model_index(history, queue, now):
    index = []
    for label, rows, days, statuses in (('候选池', queue, None, WAITING_STATUSES),
                                       ('发布历史', history, 30, ACTIVE_STATUSES)):
        for row in rows:
            item = row.get('item') or row
            brand, model = item.get('brand'), item.get('model')
            if not brand or not model or not row.get('source_id'):
                continue
            match = content_duplicate(content_identity(item), [row], now=now, days=days, statuses=statuses)
            if match:
                index.append((subject_pattern(brand, model), dict(source_id=row['source_id'], origin=label,
                              brand=brand, model=model)))
    return index


def caption_duplicate(text, index):
    caption = fold(re.sub(r'https?://\S+|@\w+', ' ', text or '')).replace('#', ' ').strip()
    caption = re.sub(r'[\W_]+$', '', caption)  # 单车型名后的 emoji 和标点不改变身份
    caption = re.sub(r'\s+(?:19|20)\d{2}$', '', caption)  # 车型后的单独年份不构成不同版本
    if not caption or AMBIGUOUS.search(caption):
        return None
    matches = {}
    for pattern, record in index:
        match = pattern.search(caption)
        if match and not re.match(r'\s*[,/、，&+]\s*\w', caption[match.end():]):
            identity = (normalize_brand(record['brand']), fold(record['model']))
            matches.setdefault(identity, {**record, 'evidence': match.group().strip()})
    return next(iter(matches.values())) if len(matches) == 1 else None

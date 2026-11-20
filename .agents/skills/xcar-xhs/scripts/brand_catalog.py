"""核对品牌历代车型名录，列出各车型的候选池覆盖和断点。"""
import argparse
from copy import deepcopy
import datetime
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from content_policy import (BRAND_SERIES, MIN_TOTAL_ENGAGEMENT, SCORE_RUBRIC, SCORE_THRESHOLD,
                            engagement_counts, normalize_brand, normalized)
from finalize import candidate_result
from mechanism_duplicates import fold
from publication_queue import PublicationQueue, WAITING_STATUSES
from publish_history import default_history_path
from topic_planner import PILLARS, STRATEGY_VERSION
from xiaohongshu_publisher import validate_content


DEFAULT_MODEL_PAGE_LIMIT = 3
EXPANSION_CATEGORIES = {
    'one_off', 'prototype', 'concept_car', 'hybrid_concept', 'race_car', 'homologation',
    'limited_edition', 'special_edition', 'sports_car',
    'unusual_structure', 'regional_exclusive', 'factory_special_purpose',
}


def names(value, label):
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(label + ' 必须是字符串数组')
    return [v.strip() for v in value]


def source_url(value):
    url = urlsplit(value if isinstance(value, str) else '')
    if url.scheme not in ('http', 'https') or not url.netloc:
        raise ValueError('名录须提供可核对的来源 URL')
    return value


def read_expansion(value, model):
    if not isinstance(value, dict):
        raise ValueError('车型 expansion 必须是 JSON 对象：' + model)
    category = value.get('category')
    if not isinstance(category, str) or category not in EXPANSION_CATEGORIES:
        raise ValueError('扩展车型类别无效：' + model)
    reason, visual_cue = value.get('reason'), value.get('visual_cue')
    if not all(isinstance(field, str) and field.strip() for field in (reason, visual_cue)):
        raise ValueError('扩展车型须填写稀有依据和画面辨认点：' + model)
    return dict(category=category, reason=reason.strip(), visual_cue=visual_cue.strip())


def read_catalog(path):
    catalog = json.loads(Path(path).read_text())
    if not isinstance(catalog, dict):
        raise ValueError('名录必须是 JSON 对象')
    brand = catalog.get('brand')
    as_of = datetime.date.fromisoformat(catalog.get('as_of') or '')
    founded = catalog.get('founded_year')
    if (not isinstance(brand, str) or not normalized(brand) or type(founded) != int
            or not 1 <= founded <= as_of.year or as_of > datetime.date.today()):
        raise ValueError('请核对品牌、成立年及名录截止日期')
    source_url(catalog.get('source'))
    catalog['coverage_gaps'] = names(catalog.get('coverage_gaps'), 'coverage_gaps')
    brand_aliases = names(catalog.get('brand_aliases', []), 'brand_aliases')
    if not isinstance(catalog.get('models'), list) or not catalog['models']:
        raise ValueError('名录至少需要一款有来源的车型')
    seen, targets, expansions = set(), [], {}
    for row in catalog['models']:
        if not isinstance(row, dict):
            raise ValueError('车型条目必须是 JSON 对象')
        model = row.get('model')
        year = row.get('first_year')
        if (not isinstance(model, str) or not normalized(model) or type(year) != int
                or not founded <= year <= as_of.year):
            raise ValueError('请核对车型正式名称和首次推出年份')
        aliases = names(row.get('aliases', []), 'aliases')
        keys = {normalized(v) for v in [model, *aliases]}
        if '' in keys or keys & seen:
            raise ValueError('车型正式名或别名与其他条目重叠：' + model)
        seen.update(keys)
        model = model.strip()
        targets.append(dict(brand=brand.strip(), brand_aliases=brand_aliases, model=model,
                            first_year=year, aliases=aliases, source=source_url(row.get('source'))))
        if 'expansion' in row:
            expansions[model] = read_expansion(row['expansion'], model)
    catalog['models'] = sorted(targets, key=lambda t: (t['first_year'], t['model']))
    catalog['model_expansions'] = expansions
    return catalog


def model_dir(root, target):
    identity = [normalize_brand(target['brand']), normalized(target['model'])]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20]
    return Path(root).resolve() / 'models' / key


def matches_identity(target, item):
    makes = {normalize_brand(v) for v in [target['brand'], *target['brand_aliases']]}
    models = {normalized(v) for v in [target['model'], *target['aliases']]}
    return normalize_brand(item.get('brand')) in makes and normalized(item.get('model')) in models


def matches_caption(target, text):
    text = fold(re.sub(r'https?://\S+|@\w+', ' ', text))
    for name in [target['model'], *target['aliases']]:
        words = re.findall(r'[a-z]+|[0-9]+|[^\W\da-z_]+', fold(name))
        pattern = r'[\s._/–—-]*'.join(map(re.escape, words))
        if pattern and re.search(r'(?<![a-z0-9])' + pattern + r'(?![a-z0-9])', text):
            return True
    return False


def model_plan(target):
    # 默认三页优先覆盖正式名、常用别名和媒体检索。
    makes = [target['brand'], *target['brand_aliases']]
    models = [target['model'], *target['aliases']]
    def phrase(value):
        return '"' + value.replace('"', ' ').strip() + '"'
    brand_terms = '(' + ' OR '.join(map(phrase, makes)) + ')'
    queries = [brand_terms + ' ' + phrase(model) + ' filter:videos' for model in models]
    queries.insert(2, brand_terms + ' (' + ' OR '.join(map(phrase, models)) + ') filter:media')
    return dict(mode='brand_catalog', brand_target=target, series_id=BRAND_SERIES,
                series_label='品牌历代车型', pillar='rare_models', pillar_label=PILLARS['rare_models']['label'],
                strategy_version=STRATEGY_VERSION, series_episode=None, score_threshold=SCORE_THRESHOLD,
                score_rubric=SCORE_RUBRIC, target_duration_seconds=PILLARS['rare_models']['duration'],
                queries=list(dict.fromkeys(queries)), verified_selection_count=1,
                prompt=f"品牌历代车型｜{target['brand']} {target['model']}")


def coverage(catalog, root, entries):
    expansions = catalog.get('model_expansions', {})
    rows = []
    for target in catalog['models']:
        out = model_dir(root, target)
        path = out / 'candidate_receipt.json'
        if (out / 'collection' / 'candidate_receipt.json').exists():
            path = out / 'collection' / 'candidate_receipt.json'
        receipt = json.loads(path.read_text()) if path.exists() else {}
        row = {**target, 'out': str(out), 'status': receipt.get('status', 'pending')}
        if target['model'] in expansions:
            row['expansion'] = expansions[target['model']]
        row.update({key: receipt[key] for key in ('pages_used', 'page_limit', 'budget') if key in receipt})
        if row['status'] == 'complete':
            row['status'] = 'missing_from_pool'
        if receipt.get('error'):
            row['reason'] = receipt['error']
        for entry in entries:
            item = entry.get('item') or {}
            if entry.get('status') not in WAITING_STATUSES or not matches_identity(target, item):
                continue
            row.update(status='pool_conflict', source_id=entry['source_id'])
            if entry['status'] != 'queued':
                row['reason'] = '同车型已进入发布计划'
                continue
            counts = engagement_counts({**item, 'topic_series_id': BRAND_SERIES})
            if sum(counts.values()) <= MIN_TOTAL_ENGAGEMENT:
                row['reason'] = '已有候选的四项互动未超过 100'
                continue
            try:
                if entry.get("collection_only"):
                    if not Path(item["video"]["file"]).is_file():
                        raise ValueError("视频文件缺失")
                else:
                    validate_content(deepcopy(item), publication=True)
            except (OSError, KeyError, ValueError) as exc:
                row['reason'] = str(exc)
                continue
            origin = 'this_job' if entry.get('out_dir') in (str(out), str(out / 'collection')) else 'existing_pool'
            result = candidate_result(item)
            row.update(status='queued', origin=origin, source_url=item['candidate'].get('url'),
                       video=item['video']['file'], engagement=counts, total_engagement=sum(counts.values()),
                       title=result['title'], body=result['body'], topics=result['topics'])
            row.pop('reason', None)
            break
        rows.append(row)
    count = sum(row['status'] == 'queued' for row in rows)
    return dict(brand=catalog['brand'], founded_year=catalog['founded_year'], as_of=catalog['as_of'],
                source=catalog['source'], coverage_gaps=catalog['coverage_gaps'], queued=count, total=len(rows),
                status='complete' if count == len(rows) and not catalog['coverage_gaps'] else 'incomplete',
                models=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brand', required=True)
    parser.add_argument('--out', type=Path, required=True, help='品牌任务根目录，名录存为 brand_catalog.json')
    parser.add_argument('--history-file', default=str(default_history_path()))
    args = parser.parse_args()
    path = args.out / 'brand_catalog.json'
    if not path.exists():
        print(json.dumps(dict(status='awaiting_catalog', brand=args.brand, catalog=str(path),
                              action='按 Skill 查证品牌车型并保存名录'), ensure_ascii=False))
        return 3
    try:
        catalog = read_catalog(path)
        if normalize_brand(args.brand) not in {normalize_brand(v) for v in [catalog['brand'], *catalog.get('brand_aliases', [])]}:
            raise ValueError('目录内名录的品牌与请求不符')
        result = coverage(catalog, args.out, PublicationQueue(history_path=args.history_file).records())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())

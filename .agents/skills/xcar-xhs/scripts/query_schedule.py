"""候选检索按近期实际产出安排请求，兼容旧批次留存的搜索页。"""
import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path

from candidate_review import ACTION_SERIES
from content_policy import parse_datetime
from series_match import reviewed_series_conflict

RECENT_BATCHES = 12
COOLDOWN = timedelta(days=1)
SEARCH_SETTINGS = {'type': 'top', 'limit': 25, 'since': None, 'min_likes': 0, 'min_reposts': 0}
YIELD_VERSION = 2


POP_UP_HEADLIGHT_MODELS = '("Mazda MX-5" OR "马自达 MX-5" OR "Corvette C5" OR "Nissan 300ZX" OR "日产 300ZX")'
SIDE_TAILGATE_MODELS = '("Ford F-150" OR "福特 F-150" OR "Honda Ridgeline" OR "GMC Sierra")'
BROAD_MECHANISM_QUERIES = {
    '跳灯': POP_UP_HEADLIGHT_MODELS,
    '翻灯': POP_UP_HEADLIGHT_MODELS,
    '跳灯 翻灯': POP_UP_HEADLIGHT_MODELS,
    '翻灯 跳灯': POP_UP_HEADLIGHT_MODELS,
    '尾门': SIDE_TAILGATE_MODELS,
    '尾门 侧开': SIDE_TAILGATE_MODELS,
    '侧开 尾门': SIDE_TAILGATE_MODELS,
    '侧开尾门': SIDE_TAILGATE_MODELS,
}
QUERY_MODIFIERS = re.compile(r'(?<!\S)(?:-?[A-Za-z_]+:\S+|-(?:"[^"]*"|\S+))')


def narrow_mechanism_query(query, series_id):
    """只有补充词缺少车型或其他限定时，才给已知的宽泛词加车型。"""
    if series_id == ACTION_SERIES:
        positive = QUERY_MODIFIERS.sub(' ', query).replace('"', '')
        models = BROAD_MECHANISM_QUERIES.get(' '.join(positive.split()))
        if models:
            return f'{query} {models}'
    return query


def search_text(query, settings):
    if settings.get('min_likes') and 'min_faves:' not in query:
        query += ' min_faves:%d' % settings['min_likes']
    if settings.get('since') and 'since:' not in query:
        query += ' since:' + settings['since']
    return query


def query_key(query):
    return '%s\n%s' % (query.get('series_id') or '', query['query'])


def historical_pages(job):
    """按已记录的首次来源归因；旧任务从留存原始页恢复，坏页不计为零产出。"""
    state = job['checkpoint']
    recorded, legacy = [], []
    for query in state['queries']:
        series = query.get('series_id') or state['plan']['series_id']
        key = query_key(dict(query, series_id=series))
        for page in range(1, query['pages'] + 1):
            ids = query.get('page_sources', {}).get(str(page))
            if ids is not None:
                recorded.append((key, page, set(map(str, ids))))
            else:
                text = search_text(query['query'], state.get('settings', {}))
                hashed = hashlib.sha256(text.encode()).hexdigest()[:16]
                path = Path(job['out_dir']) / 'raw' / f'{hashed}_page{page}.jsonl'
                try:
                    legacy.append((path.stat().st_mtime_ns, path, key, page))
                except OSError:
                    continue
    # 新格式有确切来源。混合检查点不能把同一个 ID 再归给旧页。
    seen = set().union(*(ids for _, _, ids in recorded))
    for _, path, key, page in sorted(legacy):
        try:
            ids = {str(row['id']) for line in path.read_text().split('\n') if line.strip()
                   for row in (json.loads(line),) if row.get('id')}
        except (OSError, ValueError, AttributeError):
            continue
        recorded.append((key, page, ids - seen))
        seen.update(ids)
    return recorded


def prior_query_yield(jobs, state, excluded_ids=()):
    """冻住任务开始前的历史，恢复时沿用同一排序依据。"""
    created = parse_datetime(state['created_at'])
    series = set(state['plan']['series_id'].split('+'))
    settings = state.get('settings', {})
    history = []
    for job in jobs:
        checkpoint = job.get('checkpoint') or {}
        if checkpoint.get('job_id') == state['job_id']:
            continue
        if job.get('status') not in ('complete', 'exhausted', 'budget_exhausted') or not checkpoint.get('queries'):
            continue
        if not series.intersection((checkpoint.get('plan', {}).get('series_id') or '').split('+')):
            continue
        if any(checkpoint.get('settings', {}).get(k, default) != settings.get(k, default)
               for k, default in SEARCH_SETTINGS.items()):
            continue
        updated = parse_datetime(job.get('updated_at'))
        if updated and timedelta() <= created - updated < COOLDOWN:
            history.append((updated, job))
    stats = {}
    credited = set(excluded_ids)
    for _, job in sorted(history, key=lambda pair: pair[0])[-RECENT_BATCHES:]:
        checkpoint = job['checkpoint']
        items = checkpoint['items']
        eligible = {sid for sid, row in items.items() if row['status'] == 'approved'
                    and not reviewed_series_conflict(row.get('item') or {})}
        if 'enqueue' in checkpoint:
            eligible.intersection_update(checkpoint['enqueue'].get('source_ids', []))
        for key, _, ids in historical_pages(job):
            origin_series = key.split('\n', 1)[0]
            new = {sid for sid in (eligible & ids) - credited
                   if items[sid].get('series_id', origin_series) == origin_series}
            if not new and any(items.get(sid, {}).get('status') in
                               ('discovered', 'downloaded', 'downloading', 'awaiting_review') for sid in ids):
                continue  # 尚未裁定的页保留探索机会
            credited.update(new)
            entry = stats.setdefault(key, {'samples': 0, 'qualified': 0, 'dry_streak': 0})
            entry['samples'] += 1
            entry['qualified'] += len(new)
            entry['dry_streak'] = 0 if new else entry['dry_streak'] + 1
    return stats


def select_query(queries, history, items):
    """仅对首页及最近一页均有审核通过素材的词续页。"""
    def priority(query):
        pages = query['pages']
        if pages:
            # 先探第二页。第三页起让给尚未尝试的首页。
            return (0 if pages == 1 else 2, pages)
        stats = history.get(query_key(query))
        if stats and stats['dry_streak'] >= 2:
            return (3, stats['dry_streak'])
        if stats and stats['qualified']:
            return (1, -stats['qualified'] / stats['samples'])
        return (2, 0)

    available = []
    for query in queries:
        if query['done']:
            continue
        if query['pages']:
            sources = query.get('page_sources', {})
            required_pages = {'1', str(query['pages'])}
            if not all(any(items.get(source_id, {}).get('status') == 'approved'
                           for source_id in sources.get(page, ())) for page in required_pages):
                continue
        available.append(query)
    return min(available, key=priority, default=None)

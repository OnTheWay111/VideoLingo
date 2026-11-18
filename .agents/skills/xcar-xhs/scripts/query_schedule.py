"""候选检索按近期实际产出安排请求，兼容旧批次留存的搜索页。"""
import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path

from candidate_review import ACTION_SERIES
from content_policy import parse_datetime

RECENT_BATCHES = 12
COOLDOWN = timedelta(days=1)
SEARCH_SETTINGS = {'type': 'top', 'limit': 25, 'since': None, 'min_likes': 0, 'min_reposts': 0}
CANDIDATE_STATUSES = {'downloading', 'downloaded', 'awaiting_review', 'approved',
                      'rejected', 'frame_failed', 'download_failed'}


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


def candidate_count(source_ids, items):
    return sum(items.get(source_id, {}).get('status') in CANDIDATE_STATUSES for source_id in source_ids)


def historical_counts(job):
    """优先读取新检查点。旧检查点按原始页保存顺序归因，每个原帖只计一次。"""
    checkpoint = job['checkpoint']
    items = checkpoint['items']
    settings = checkpoint.get('settings', {})
    recorded, legacy = [], []
    for query in checkpoint['queries']:
        if not query['pages']:
            continue
        key = query_key(query)
        first = query.get('page_sources', {}).get('1')
        if first is not None:
            recorded.append((key, candidate_count(first, items)))
            continue
        hashed = hashlib.sha256(search_text(query['query'], settings).encode()).hexdigest()[:16]
        path = Path(job['out_dir']) / 'raw' / (hashed + '_page1.jsonl')
        try:
            if path.is_file():
                legacy.append((path.stat().st_mtime_ns, path, key))
        except OSError:
            continue
    seen = set()
    for _, path, key in sorted(legacy):
        try:
            ids = {str(row['id']) for line in path.read_text().split('\n') if line.strip()
                   for row in (json.loads(line),) if row.get('id')}
        except (OSError, ValueError, AttributeError):
            continue  # 缺失或损坏的原始页不作为低产证据。
        recorded.append((key, candidate_count(ids - seen, items)))
        seen.update(ids)
    return recorded


def prior_query_yield(jobs, state):
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
    for _, job in sorted(history, key=lambda pair: pair[0])[-RECENT_BATCHES:]:
        for key, count in historical_counts(job):
            entry = stats.setdefault(key, {'samples': 0, 'candidates': 0, 'dry_streak': 0})
            entry['samples'] += 1
            entry['candidates'] += count
            entry['dry_streak'] = 0 if count else entry['dry_streak'] + 1
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
        if stats and stats['candidates']:
            return (1, -stats['candidates'] / stats['samples'])
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

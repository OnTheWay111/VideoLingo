"""只读回放历史首页与已下载素材，比较等预算下已知的合格产出。"""
import argparse
import json
from collections import Counter
from datetime import date, timedelta, timezone
from pathlib import Path

from content_policy import parse_datetime
from publish_history import default_history_path
from query_schedule import historical_pages, prior_query_yield, query_key, select_query
from series_match import assess_series


def choose_first_pages(queries, history, budget):
    pending = [dict(q, pages=0, done=False) for q in queries]
    keys = []
    for _ in range(min(budget, len(pending))):
        selected = select_query(pending, history, {})
        keys.append(query_key(selected))
        selected['done'] = True
    return keys


def replay(jobs, entries, day, budget):
    totals = Counter(jobs=0, historical_download_rows=0, skippable_download_rows=0,
                     known_qualified_skipped=0, paired_jobs=0)
    first_pages = {name: Counter(pages=0, known_qualified=0) for name in ('baseline', 'revised')}
    corrections = [e for e in entries if e['status'] in ('withdrawn', 'invalid', 'needs_review')]
    for job in jobs:
        state = job.get('checkpoint') or {}
        created = parse_datetime(state.get('created_at'))
        if not created or created.astimezone(timezone(timedelta(hours=8))).date() != day:
            continue
        totals['jobs'] += 1
        items = state['items']
        qualified = {sid for sid, row in items.items() if row['status'] == 'approved'}
        if 'enqueue' in state:
            qualified.intersection_update(state['enqueue'].get('source_ids', []))
        # 当前撤回/失效用于评价标签。排名只使用任务创建之前已经知道的状态。
        qualified.difference_update(e['source_id'] for e in corrections)
        for sid, row in items.items():
            item = row.get('item') or {}
            if not item.get('video') and row['status'] not in ('download_failed', 'frame_failed'):
                continue
            totals['historical_download_rows'] += 1
            series = row.get('series_id') or item.get('topic_series_id') or state['plan']['series_id']
            if assess_series(series, item['candidate'].get('text', ''))[1]:
                totals['skippable_download_rows'] += 1
                totals['known_qualified_skipped'] += int(sid in qualified)

        # 仅回放当时保存过旧排序依据、且尚未迁移到新版的任务。
        if 'query_yield' not in state or state.get('query_yield_version'):
            continue
        pages = {key: ids for key, page, ids in historical_pages(job) if page == 1}
        observed = []
        for q in state['queries']:
            q = dict(q, series_id=q.get('series_id') or state['plan']['series_id'])
            if query_key(q) in pages:
                observed.append(q)
        if not observed:
            continue
        totals['paired_jobs'] += 1
        old = {key: {**stats, 'qualified': stats['candidates']} for key, stats in state['query_yield'].items()}
        known_exclusions = {e['source_id'] for e in corrections
                            if e.get('updated_at') and parse_datetime(e['updated_at']) <= created}
        new = prior_query_yield(jobs, state, known_exclusions)
        for name, history in (('baseline', old), ('revised', new)):
            keys = choose_first_pages(observed, history, budget)
            first_pages[name]['pages'] += len(keys)
            discovered = set().union(*(pages[k] for k in keys))
            first_pages[name]['known_qualified'] += len(discovered & qualified)

    return {'date': day.isoformat(), 'first_pages_per_job': budget,
            'cohort': dict(totals), 'paired_first_pages': first_pages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    state_dir = default_history_path().parent
    parser.add_argument('--jobs', type=Path, default=state_dir / 'candidate_jobs.json')
    parser.add_argument('--queue', type=Path, default=state_dir / 'publish_queue.json')
    parser.add_argument('--date', type=date.fromisoformat, required=True, help='上海时区的任务创建日期')
    parser.add_argument('--first-pages', type=int, default=3)
    args = parser.parse_args()
    if args.first_pages < 1:
        parser.error('--first-pages 必须为正整数')
    jobs = list(json.loads(args.jobs.read_text())['jobs'].values())
    entries = json.loads(args.queue.read_text())['entries']
    print(json.dumps(replay(jobs, entries, args.date, args.first_pages), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

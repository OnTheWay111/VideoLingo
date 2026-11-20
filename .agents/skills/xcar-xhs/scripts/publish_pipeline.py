#!/usr/bin/env python3
"""定稿后一次调用：恢复、入队、预检、逐条发布、补链接和输出回执。"""
import argparse
import json
import sys
from pathlib import Path

from chrome_xiaohongshu import ChromeSession, recover_note_urls, recover_submission, release_unsubmitted, run_locked
from note_metrics import NoteMetrics
from publication_runtime import SubmissionJournal, atomic_json, execution_lock
from publish_history import PublishedHistory, default_history_path
from monitor_telemetry import record_event
from xiaohongshu_publisher import build_plan, ensure_publish_available, save_plan, latest_records


def publication_result(plan, result):
    topics = plan['topics']
    body = plan['full_text'].removesuffix('\n\n' + ' '.join(topics))
    row = {**result, 'brand': plan['brand'], 'body': body, 'topics': topics}
    if result['status'] != 'skipped':
        row['publish_result'] = '成功' if result['status'] == 'published' else '失败'
    return row


def interrupted_result(plan, history, error):
    result = {'source_id': plan['source_id'], 'title': plan['title'], 'status': 'error', 'error': str(error)}
    try:
        attempt = SubmissionJournal(history.path).read() or {}
    except (OSError, ValueError):
        attempt = {}
    if (attempt.get('plan') or {}).get('source_id') == plan['source_id']:
        if attempt.get('status') in ('confirmed', 'recorded'):
            result.update(attempt.get('result') or {})
            if result['status'] == 'published':
                result['note_url_pending'] = not result.get('note_url')
        elif attempt.get('status') == 'submitting':
            result['status'] = 'unconfirmed'
    return publication_result(plan, result)


def run_pipeline(out_dir, history, max_items=1, profile='Default', headless=True,
                 timeout=180, action='publish', tweet_id=None):
    if timeout <= 0:
        raise ValueError('timeout 必须大于零')
    if max_items < 1 or (action != 'publish' and max_items != 1):
        raise ValueError('发布数量必须大于零；预演和草稿仅处理一条')
    out_dir = Path(out_dir).resolve()
    if not out_dir.is_dir():
        raise ValueError('定稿输出目录不存在')
    results = []
    summary = {'status': 'complete', 'results': results, 'published': 0}
    active_plan = None
    with execution_lock(history.path, profile):
        try:
            if action == 'preview':
                plan = build_plan(out_dir, tweet_id, history, action)
                save_plan(out_dir, plan)
                summary['plan'] = plan
                return summary
            recover_submission(history)
            if action == "publish":
                ensure_publish_available(history, tweet_id)
            with ChromeSession(profile, headless, timeout) as chrome:
                chrome.navigate_publish()  # 同一会话完成预检和发布。
                if action == 'publish':
                    # 历史链接补查独立于发布，避免旧记录拖慢或阻断新素材。
                    summary['pending_links'] = [
                        {'source_id': r['source_id'], 'title': r.get('title', '')}
                        for r in latest_records(history.records())
                        if r.get('status') == 'published' and not r.get('note_url')]
                    ensure_publish_available(history, tweet_id)
                for _ in range(max_items):
                    plan = build_plan(out_dir, tweet_id, history, action)
                    if action == 'publish' and plan['status'] == 'ready':
                        active_plan = plan
                    save_plan(out_dir, plan)
                    if plan['status'] != 'ready':
                        summary.update(status='skipped', plan=plan)
                        break
                    source_id = str(plan.get('source_id') or '')
                    job_id = str(plan.get('candidate_job_id') or '')
                    record_event('publish', 'plan_preflight', job_id, source_id,
                                 event_id='publish:%s:plan_preflight' % source_id,
                                 path=history.path.with_name('monitor_events.jsonl'))
                    result = run_locked(out_dir, history, profile, headless,
                                        timeout=timeout, session=chrome)
                    if action == 'publish':
                        results.extend(publication_result(plan, row) for row in result['results'])
                    else:
                        results.extend(result['results'])
                    active_plan = None
                    summary['published'] += result['published']
                    record_event('publish', 'upload_submit', job_id, source_id,
                                 status=result.get('status', 'unknown'),
                                 event_id='publish:%s:upload_submit' % source_id,
                                 path=history.path.with_name('monitor_events.jsonl'))
                    record_event('publish', 'result_verify', job_id, source_id,
                                 status=result.get('status', 'unknown'),
                                 event_id='publish:%s:result_verify' % source_id,
                                 path=history.path.with_name('monitor_events.jsonl'))
                    if result['status'] != 'complete':
                        summary['status'] = 'incomplete'
                        break
                    pending = [r for r in result['results'] if r.get('note_url_pending')]
                    if pending:
                        summary.setdefault('pending_links', []).extend(pending)
                    record_event('publish', 'link_recovery', job_id, source_id,
                                 status='pending' if pending else 'complete',
                                 event_id='publish:%s:link_recovery' % source_id,
                                 path=history.path.with_name('monitor_events.jsonl'))
                    # 历史已跳过的计划不能在同次运行中反复执行。
                    if not result['published'] and action == 'publish':
                        summary['status'] = 'skipped'
                        break
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            if active_plan:
                row = interrupted_result(active_plan, history, exc)
                results.append(row)
                summary['published'] += int(row['status'] == 'published')
            if action != 'preview':
                release_unsubmitted(out_dir, history)
            summary.update(status='blocked', error=str(exc))
        finally:
            try:
                summary['metrics'] = NoteMetrics(history.path.with_name('note_metrics.jsonl')).due(history)
            except (OSError, ValueError, KeyError) as exc:
                summary['metrics_error'] = str(exc)
            atomic_json(out_dir / 'publication_pipeline_receipt.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--history-file', default=str(default_history_path()))
    parser.add_argument('--max-items', type=int, default=1, help='本次最多发布几条，不是每日限制')
    parser.add_argument('--browser-profile', default='Default')
    parser.add_argument('--headed', action='store_true', help='显示隔离 Chrome，默认无头')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--action', choices=('publish', 'preview', 'draft'), default='publish')
    parser.add_argument('--tweet-id', help='preview/draft 必填')
    args = parser.parse_args(argv)
    try:
        summary = run_pipeline(args.out, PublishedHistory(args.history_file), args.max_items,
                               args.browser_profile, not args.headed, args.timeout,
                               args.action, args.tweet_id)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary['status'] in ('complete', 'skipped') else 2
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'blocked', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())

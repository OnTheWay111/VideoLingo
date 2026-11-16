"""xcar-xhs 监控聚合与 Token 用量账本测试。"""

import datetime
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / '.agents/skills/xcar-xhs/scripts'))

from monitor import MonitorData
from monitor_telemetry import record_event, record_usage


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def test_snapshot_uses_shanghai_day_and_real_records(tmp_path):
    queue = tmp_path / 'publish_queue.json'
    history = tmp_path / 'published_videos.jsonl'
    events = tmp_path / 'monitor_events.jsonl'
    usage = tmp_path / 'token_usage.jsonl'
    jobs = tmp_path / 'candidate_jobs.json'
    write_json(queue, {'version': 1, 'entries': [{
        'source_id': 'candidate-1', 'status': 'queued',
        'enqueued_at': '2026-11-15T16:15:00+00:00', 'title': '真实候选', 'score': 88,
        'item': {'candidate_job_id': 'job-1', 'candidate': {
            'id': 'candidate-1', 'url': 'https://x.com/car/status/candidate-1'}},
    }]})
    history.write_text(json.dumps({
        'source_id': 'published-1', 'status': 'published', 'title': '真实发布',
        'published_at': '2026-11-16T01:00:00+00:00', 'note_url_pending': True,
    }, ensure_ascii=False) + '\n', encoding='utf-8')
    write_json(jobs, {'jobs': {'job-1': {
        'status': 'awaiting_review', 'target': 3, 'updated_at': '2026-11-16T02:00:00+00:00',
        'plan': {'series_label': 'JDM 稀有车型'},
        'checkpoint': {'phase': 'collecting', 'pages': 2, 'downloads': 3,
                       'items': {'candidate-1': {'status': 'awaiting_review'}}},
    }}})
    record_event('candidate', 'search_filter', 'job-1', event_id='event-1',
                 occurred_at='2026-11-16T00:00:00+00:00', path=events)
    record_usage({
        'request_id': 'request-1', 'flow': 'candidate', 'stage': 'visual_fact_review',
        'job_id': 'job-1', 'input_tokens': 100, 'cached_input_tokens': 40,
        'output_tokens': 20, 'reasoning_tokens': 5,
        'completed_at': '2026-11-16T00:30:00+00:00',
    }, usage)

    snapshot = MonitorData(queue, history, events, usage).snapshot('2026-11-16')

    assert snapshot['summary'] == {
        'candidate_count': 1, 'published_count': 1,
        'published_links_available': 0, 'published_links_pending': 1,
    }
    assert snapshot['token']['candidate']['total_tokens'] == 120
    assert snapshot['token']['candidate']['coverage'] == 'partial'
    assert snapshot['candidate_items'][0]['job_token_usage'] == 120
    assert snapshot['stages']['candidate'][1]['count'] == 1
    assert snapshot['active_tasks'][0]['current_stage'] == 'visual_fact_review'
    assert snapshot['active_tasks'][0]['progress']['awaiting_review'] == 1


def test_usage_is_idempotent_and_never_double_counts_subsets(tmp_path):
    path = tmp_path / 'usage.jsonl'
    value = {
        'request_id': 'same-request', 'flow': 'publish', 'stage': 'result_verify',
        'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 25,
        'reasoning_tokens': 10, 'total_tokens': 125,
        'completed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    assert record_usage(value, path)[1] is True
    assert record_usage(value, path)[1] is False
    retry_without_original_time = dict(value)
    retry_without_original_time.pop('completed_at')
    assert record_usage(retry_without_original_time, path)[1] is False
    snapshot = MonitorData(tmp_path / 'queue.json', tmp_path / 'history.jsonl',
                           tmp_path / 'events.jsonl', path).snapshot()
    assert snapshot['token']['publish']['total_tokens'] == 125
    assert snapshot['token']['publish']['stages'][2]['cached_input_tokens'] == 80
    assert snapshot['token']['publish']['stages'][2]['reasoning_tokens'] == 10


def test_usage_rejects_invalid_stage_and_cached_over_input(tmp_path):
    with pytest.raises(ValueError, match='有效阶段'):
        record_usage({'request_id': 'bad', 'flow': 'candidate', 'stage': 'publish',
                      'input_tokens': 1, 'output_tokens': 1}, tmp_path / 'usage.jsonl')
    with pytest.raises(ValueError, match='不能大于'):
        record_usage({'request_id': 'bad-2', 'flow': 'candidate', 'stage': 'search_filter',
                      'input_tokens': 1, 'cached_input_tokens': 2, 'output_tokens': 1},
                     tmp_path / 'usage.jsonl')

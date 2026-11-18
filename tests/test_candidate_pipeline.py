"""候选生产测试全部使用临时素材与假 X 后端，不搜索真实账号、不发布。"""
import copy
import datetime
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / '.agents/skills/xcar-xhs/scripts'))
import candidate_pipeline as pipeline
import candidate_pool
import candidate_review
import finalize
import publication_queue
from publish_history import PublishedHistory
from topic_planner import build_topic_plan
from tests.candidate_fixtures import approve_item
from tests.test_xiaohongshu_publisher import finalized_output
from tests.test_x_car_video_finder import scored_publish


def args_for(tmp_path, **values):
    args = pipeline.finder.build_parser().parse_args([])
    defaults = dict(out=str(tmp_path / 'batch'), publish_history=str(tmp_path / 'history.jsonl'),
                    review_file=None, max_pages=12, max_downloads=15, max_seconds=60,
                    cancel=False, retry_failed=False, top=3)
    defaults.update(values)
    for key, value in defaults.items():
        setattr(args, key, value)
    return args


def tweet(source_id):
    return {'id': str(source_id), 'url': 'https://x.com/car/status/' + str(source_id),
            'text': 'Rare car design details', 'favorite_count': 101,
            'reply_count': 1, 'retweet_count': 1,
            'author': {'screen_name': 'car'}, 'media': [{'type': 'video', 'url': 'https://example.com/v'}]}


def test_pipeline_skips_total_engagement_at_or_below_100_before_download(tmp_path):
    args = args_for(tmp_path, max_pages=1)
    low = tweet('1')
    low.update(favorite_count=80, reply_count=10, retweet_count=10)
    backend = FakeBackend([([low], None)])

    result = pipeline.run_pipeline(args, backend)

    assert result['status'] == 'budget_exhausted'
    assert backend.downloads == []
    state = json.loads((Path(args.out) / 'candidate_state.json').read_text())
    assert state['items']['1']['status'] == 'skipped'
    assert '互动总量 100 ≤ 100' in state['items']['1']['reason']


def test_query_expansion_preserves_model_action_and_negative_constraints():
    query = '("Honda N-VAN" OR "Daihatsu Atrai") "folding seats" filter:media ' + pipeline.NEGATIVE_FILTERS
    assert pipeline.make_queries({'queries': [query, query]}) == [query]


def test_resume_archives_unscoped_queries_without_losing_progress_or_explicit_queries():
    original = '("Honda N-VAN" OR "Daihatsu Atrai") "folding seats" filter:media ' + pipeline.NEGATIVE_FILTERS
    bare = '"folding seats" filter:media ' + pipeline.NEGATIVE_FILTERS
    forbidden = '"car show" filter:media ' + pipeline.NEGATIVE_FILTERS
    explicit = '"Honda N-VAN" filter:media ' + pipeline.NEGATIVE_FILTERS
    queries = [{'query': q, 'pages': 1, 'cursor': 'saved', 'done': False}
               for q in [original, bare, forbidden, explicit]]
    state = {'plan': {'queries': [original]}, 'queries': queries, 'items': {'known': {'status': 'approved'}}}
    pipeline.retire_unscoped_queries(state, [explicit])
    assert [q['query'] for q in state['queries']] == [original, explicit]
    assert [q['query'] for q in state['retired_queries']] == [bare, forbidden]
    assert all(q['cursor'] == 'saved' and q['pages'] == 1 for q in state['retired_queries'])
    assert state['items'] == {'known': {'status': 'approved'}}
    pipeline.retire_unscoped_queries(state, [explicit])
    assert len(state['retired_queries']) == 2


class FakeBackend:
    def __init__(self, pages=None):
        self.pages = pages or [([tweet(1), tweet(2), tweet(3)], None)]
        self.searches = []
        self.downloads = []
        self.frame_calls = []
    def search(self, query, cursor, page, out):
        self.searches.append((query, cursor, page))
        return self.pages.pop(0) if self.pages else ([], None)
    def download(self, candidate, out):
        self.downloads.append(candidate['id'])
        path = out / 'videos' / (candidate['id'] + '.mp4')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('video-' + candidate['id']).encode())
        return {'file': str(path), 'duration': 30, 'size_bytes': path.stat().st_size}, None
    def frames(self, item, out):
        self.frame_calls.append(item['candidate']['id'])
        frames = []
        for index, at_sec in enumerate((0.5, 12, 24)):
            path = out / 'frames' / ('%s_%d.jpg' % (item['candidate']['id'], index))
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'fake frame')
            frames.append({'file': str(path), 'at_sec': at_sec})
        return frames


def review_all(out, reject=()):
    request = json.loads((Path(out) / 'review_request.json').read_text())
    brands = [('Honda', 'S660'), ('BMW', 'Z1'), ('Toyota', 'Sera'), ('Nissan', 'Figaro')]
    for source_id, result in request['items'].items():
        result['decision'] = 'rejected' if source_id in reject else 'approved'
        result['reason'] = '测试素材不符合系列' if source_id in reject else ''
        brand, model = brands[(int(source_id) - 1) % len(brands)]
        content = scored_publish(source_id, brand, model)
        content.update(file_topic=model, topics=['#汽车设计', '#' + model], ai_disclosure='')
        result['publish'] = content
        item = {'candidate': {'id': source_id}, 'video': result['video'], 'title': content['title']}
        proof = approve_item(item)['verification']
        proof.pop('content_sha256')
        result['verification'] = proof
    path = Path(out) / 'review.json'
    path.write_text(json.dumps(request, ensure_ascii=False))
    return str(path)


def test_planner_counts_latest_success_only_and_uses_publication_order():
    first = {'source_id': '1', 'status': 'published', 'topic_pillar': 'rare_models',
             'title': 'old', 'published_at': '2026-01-01T00:00:00Z'}
    second = {**first, 'source_id': '2', 'title': 'new', 'published_at': '2026-01-02T00:00:00Z'}
    events = [first, second, {**first, 'note_url': 'https://example.com/n'},
              {'source_id': '3', 'status': 'unconfirmed'}, {'source_id': '3', 'status': 'failed'}]
    plan = build_topic_plan(events)
    assert plan['schedule_slot'] == 3
    assert plan['recent_titles'] == ['old', 'new']
    assert plan == build_topic_plan([first, second])


def test_inventory_and_reservations_change_production_topic():
    queue = [{'status': 'queued', 'item': {'topic_pillar': 'rare_models', 'topic_series_id': 'rare_jdm'}}] * 10
    queue += [{'status': 'queued', 'item': {'topic_pillar': 'visual_experiment', 'topic_series_id': 'microcars'}}] * 10
    plan = build_topic_plan([], queue)
    assert plan['pillar'] == 'mechanical_contrast'
    reservation = {'status': 'awaiting_review', 'target': 3, 'plan': plan}
    assert build_topic_plan([], queue, [reservation])['series_id'] != plan['series_id']


def test_pipeline_review_then_enqueue_only_idempotent(tmp_path):
    args = args_for(tmp_path)
    backend = FakeBackend()
    first = pipeline.run_pipeline(args, backend)
    assert first['status'] == 'awaiting_review'
    assert len(backend.downloads) == 3
    assert not Path(tmp_path / 'publish_queue.json').exists()
    args.review_file = review_all(args.out)
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'complete', result
    assert result['enqueue']['added'] == 3
    assert result['published'] == 0
    entries = publication_queue.PublicationQueue(history_path=args.publish_history).records()
    assert {e['status'] for e in entries} == {'queued'}
    assert all(not e['item'].get('topic_series_episode') for e in entries)
    assert not (Path(args.out) / 'xiaohongshu_publish_plan.json').exists()
    assert not Path(args.publish_history).exists()
    pipeline.run_pipeline(args, backend)
    assert len(backend.downloads) == 3
    assert len(publication_queue.PublicationQueue(history_path=args.publish_history).records()) == 3


def test_rejection_backfills_without_redownload(tmp_path):
    args = args_for(tmp_path)
    backend = FakeBackend([([tweet(i) for i in range(1, 5)], 'next')])
    pipeline.run_pipeline(args, backend)
    args.review_file = review_all(args.out, reject=('2',))
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'awaiting_review', result
    assert backend.downloads == ['1', '2', '3', '4']
    args.review_file = review_all(args.out)
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'complete', result
    saved = json.loads((Path(args.out) / 'manifest.json').read_text())
    assert [i['candidate']['id'] for i in saved['rejected']] == ['2']
    assert Path(saved['rejected'][0]['video']['file']).parent.name == 'rejected'


def test_missing_review_evidence_and_changed_video_block(tmp_path):
    args = args_for(tmp_path)
    backend = FakeBackend()
    pipeline.run_pipeline(args, backend)
    args.review_file = review_all(args.out)
    review = json.loads(Path(args.review_file).read_text())
    review['items']['1']['verification']['fact_evidence'] = ''
    Path(args.review_file).write_text(json.dumps(review))
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'blocked'
    assert 'fact_evidence' in result['error']
    args.review_file = review_all(args.out)
    Path(review['items']['1']['video']['file']).write_bytes(b'changed')
    renewed = pipeline.run_pipeline(args, backend)
    assert renewed['status'] == 'awaiting_review'
    assert len(backend.downloads) == 3  # 只重新抽帧，不重下
    request = json.loads((Path(args.out) / 'review_request.json').read_text())
    assert request['items']['1']['video_sha256'] != review['items']['1']['video_sha256']
    assert not (tmp_path / 'publish_queue.json').exists()


def test_cursor_and_page_budget_resume(tmp_path):
    args = args_for(tmp_path, max_pages=1)
    backend = FakeBackend([([tweet(1)], 'cursor1'), ([tweet(2), tweet(3)], None)])
    pipeline.run_pipeline(args, backend)
    args.review_file = review_all(args.out)
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'budget_exhausted'
    state_path = Path(args.out) / 'candidate_state.json'
    state = json.loads(state_path.read_text())
    assert state['queries'][0]['cursor'] == 'cursor1'
    # 指定只剩该查询，验证恢复使用原 cursor 而不是从首页开始。
    state['queries'] = state['queries'][:1]
    state_path.write_text(json.dumps(state))
    registry_path = tmp_path / 'candidate_jobs.json'
    registry = json.loads(registry_path.read_text())
    registry['jobs'][state['job_id']]['checkpoint'] = state
    registry_path.write_text(json.dumps(registry))
    args.max_pages = 2
    assert pipeline.run_pipeline(args, backend)['status'] == 'awaiting_review'
    assert backend.searches[-1][1:] == ('cursor1', 2)
    assert backend.downloads == ['1', '2', '3']


def test_failed_download_requires_explicit_retry(tmp_path):
    args = args_for(tmp_path, max_pages=1)
    backend = FakeBackend([([tweet(1)], None)])
    real = backend.download
    backend.download = lambda *args: (None, 'temporary error')
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'budget_exhausted'
    backend.download = real
    assert pipeline.run_pipeline(args, backend)['status'] == 'budget_exhausted'
    assert backend.downloads == []
    args.retry_failed = True
    assert pipeline.run_pipeline(args, backend)['status'] == 'awaiting_review'
    assert backend.downloads == ['1']


def test_rejected_source_cache_and_queue_prevent_download(tmp_path):
    args = args_for(tmp_path, max_pages=1)
    registry = {'jobs': {}, 'rejected': {'1': {'reason': '已核验不合格'}}}
    (tmp_path / 'candidate_jobs.json').write_text(json.dumps(registry))
    old = tmp_path / 'old'; old.mkdir()
    item = finalized_output(old, '2')
    publication_queue.PublicationQueue(history_path=args.publish_history).enqueue([item], old)
    backend = FakeBackend()
    assert pipeline.run_pipeline(args, backend)['status'] == 'awaiting_review'
    assert backend.downloads == ['3']


def test_other_active_job_reserves_downloaded_sources_and_cancel_releases(tmp_path):
    args = args_for(tmp_path)
    backend = FakeBackend()
    pipeline.run_pipeline(args, backend)
    other = args_for(tmp_path, out=str(tmp_path / 'second'), max_pages=1)
    second_backend = FakeBackend()
    result = pipeline.run_pipeline(other, second_backend)
    assert result['status'] == 'budget_exhausted'
    assert second_backend.downloads == []
    args.cancel = True
    assert pipeline.run_pipeline(args, backend)['status'] == 'cancelled'


def test_pool_version_update_withdraw_and_audit(tmp_path):
    out = tmp_path / 'out'; out.mkdir()
    item = finalized_output(out)
    queue = publication_queue.PublicationQueue(tmp_path / 'pool.json')
    queue.enqueue([item], out)
    changed = copy.deepcopy(item)
    changed['title'] = 'S660 的 660cc 布局'
    approve_item(changed)
    with pytest.raises(ValueError, match='版本冲突'):
        queue.update_candidate('123', changed, 9, out)
    assert queue.update_candidate('123', changed, 1, out)['revision'] == 2
    assert queue.withdraw('123', 2, '更换选题')['status'] == 'withdrawn'
    with pytest.raises(ValueError):
        queue.withdraw('123', 2, '重复撤回')
    queue.update_candidate('123', changed, 3, out)
    Path(changed['video']['file']).unlink()
    assert queue.audit()[0]['status'] == 'invalid'


def test_missing_review_cannot_enqueue_and_no_plan_created(tmp_path):
    out = tmp_path / 'out'; out.mkdir()
    item = finalized_output(out)
    item.pop('verification')
    queue = publication_queue.PublicationQueue(tmp_path / 'pool.json')
    with pytest.raises(ValueError, match='核验'):
        queue.enqueue([item], out)
    assert queue.records() == []


def test_finalization_recovers_after_first_rename(tmp_path, monkeypatch):
    args = args_for(tmp_path)
    backend = FakeBackend()
    pipeline.run_pipeline(args, backend)
    args.review_file = review_all(args.out)
    real_rename = Path.rename
    calls = []
    def interrupt(self, target):
        value = real_rename(self, target)
        calls.append(str(self))
        if len(calls) == 1:
            raise OSError('simulated crash after rename')
        return value
    monkeypatch.setattr(Path, 'rename', interrupt)
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'blocked'
    assert (Path(args.out) / 'finalize_transaction.json').exists()
    monkeypatch.setattr(Path, 'rename', real_rename)
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'complete', result
    assert len(backend.downloads) == 3
    manifest = json.loads((Path(args.out) / 'manifest.json').read_text())
    assert all(Path(item['video']['file']).is_file() for item in manifest['downloads'])


def test_enqueue_success_receipt_crash_is_idempotent(tmp_path, monkeypatch):
    args = args_for(tmp_path)
    backend = FakeBackend()
    pipeline.run_pipeline(args, backend)
    args.review_file = review_all(args.out)
    real = pipeline.enqueue_finalized
    def crash(*values, **kw):
        real(*values, **kw)
        raise OSError('receipt crash')
    monkeypatch.setattr(pipeline, 'enqueue_finalized', crash)
    assert pipeline.run_pipeline(args, backend)['status'] == 'blocked'
    monkeypatch.setattr(pipeline, 'enqueue_finalized', real)
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'complete', result
    assert len(publication_queue.PublicationQueue(history_path=args.publish_history).records()) == 3
    assert len(backend.downloads) == 3


def test_frame_sampling_covers_first_three_seconds(tmp_path, monkeypatch):
    samples = []
    monkeypatch.setattr(pipeline.finder, 'probe_video', lambda _: {'duration': 30})
    def run(cmd, **kwargs):
        samples.append(float(cmd[cmd.index('-ss') + 1]))
        Path(cmd[-2]).write_bytes(b'frame')
        from types import SimpleNamespace
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(pipeline.finder, 'run', run)
    frames = pipeline.finder.extract_frames({'id': '1'}, 'fake.mp4', tmp_path, 3)
    assert len(frames) == 3
    assert samples[0] <= 3
    assert samples[-1] > 3


def test_frame_failure_retry_does_not_download_again(tmp_path):
    args = args_for(tmp_path, max_pages=1)
    backend = FakeBackend([([tweet(1)], None)])
    real_frames = backend.frames
    backend.frames = lambda *args: []
    assert pipeline.run_pipeline(args, backend)['status'] == 'budget_exhausted'
    assert backend.downloads == ['1']
    args.retry_failed = True
    backend.frames = real_frames
    assert pipeline.run_pipeline(args, backend)['status'] == 'awaiting_review'
    assert backend.downloads == ['1']


def test_unconfirmed_publication_does_not_block_candidate_production(tmp_path):
    args = args_for(tmp_path)
    history = PublishedHistory(args.publish_history)
    history.add('999', '之前的发布待确认', status='unconfirmed')
    original = Path(args.publish_history).read_bytes()
    backend = FakeBackend()
    pipeline.run_pipeline(args, backend)
    args.review_file = review_all(args.out)
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'complete', result
    assert Path(args.publish_history).read_bytes() == original


def test_registry_recovers_missing_local_state_without_new_search(tmp_path):
    args = args_for(tmp_path)
    backend = FakeBackend()
    pipeline.run_pipeline(args, backend)
    args.review_file = review_all(args.out)
    (Path(args.out) / 'candidate_state.json').unlink()
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'complete', result
    assert len(backend.searches) == 1
    assert len(backend.downloads) == 3


def test_pool_write_failure_preserves_original_file(tmp_path, monkeypatch):
    out = tmp_path / 'out'; out.mkdir()
    item = finalized_output(out)
    queue = publication_queue.PublicationQueue(tmp_path / 'pool.json')
    queue.enqueue([item], out)
    original = queue.path.read_bytes()
    def fail(*args):
        raise OSError('simulated disk error')
    monkeypatch.setattr(publication_queue, 'atomic_json', fail)
    with pytest.raises(OSError):
        queue.withdraw('123', 1, 'test')
    assert queue.path.read_bytes() == original


def test_audit_holds_stale_or_changed_content_and_select_ignores_it(tmp_path):
    out = tmp_path / 'out'; out.mkdir()
    item = finalized_output(out)
    queue = publication_queue.PublicationQueue(tmp_path / 'pool.json')
    queue.enqueue([item], out)
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=31)
    assert queue.audit(now=future)[0]['status'] == 'needs_review'
    assert queue.select() is None
    approve_item(item)
    queue.update_candidate('123', item, 2, out)
    Path(item['video']['file']).write_bytes(b'changed video')
    assert queue.audit()[0]['status'] == 'needs_review'


def test_planned_entry_cannot_be_rewritten(tmp_path):
    out = tmp_path / 'out'; out.mkdir()
    item = finalized_output(out)
    queue = publication_queue.PublicationQueue(tmp_path / 'pool.json')
    queue.enqueue([item], out)
    assert queue.select()['status'] == 'planned'
    with pytest.raises(ValueError, match='未进入发布计划'):
        queue.update_candidate('123', item, 1, out)
    with pytest.raises(ValueError, match='未进入发布计划'):
        queue.withdraw('123', 1, 'test')


def test_low_score_is_rejected_and_replenished(tmp_path):
    args = args_for(tmp_path)
    backend = FakeBackend([([tweet(i) for i in range(1, 5)], None)])
    pipeline.run_pipeline(args, backend)
    args.review_file = review_all(args.out)
    data = json.loads(Path(args.review_file).read_text())
    data['items']['1']['publish']['score_breakdown']['knowledge_point'] = 0
    Path(args.review_file).write_text(json.dumps(data))
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'awaiting_review', result
    state = json.loads((Path(args.out) / 'candidate_state.json').read_text())
    assert state['items']['1']['status'] == 'rejected'
    assert backend.downloads == ['1', '2', '3', '4']


def test_finalization_shortage_resumes_collection(tmp_path, monkeypatch):
    args = args_for(tmp_path)
    backend = FakeBackend([([tweet(i) for i in range(1, 5)], None)])
    pipeline.run_pipeline(args, backend)
    args.review_file = review_all(args.out)
    real_finalize = pipeline.finalize
    def racing_rejection(out, publish_file):
        path = Path(out) / 'manifest.json'
        manifest = json.loads(path.read_text())
        item = manifest['downloads'].pop()
        item['reason'] = '模拟入池并发去重'
        manifest['rejected'].append(item)
        path.write_text(json.dumps(manifest))
        raise ValueError('核验入选名单至少要有 3 条')
    monkeypatch.setattr(pipeline, 'finalize', racing_rejection)
    assert pipeline.run_pipeline(args, backend)['status'] == 'needs_candidates'
    monkeypatch.setattr(pipeline, 'finalize', real_finalize)
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'awaiting_review', result
    assert backend.downloads == ['1', '2', '3', '4']


def test_cli_cancel_no_network_or_publish(tmp_path):
    assert pipeline.main(['--out', str(tmp_path / 'job'), '--history-file', str(tmp_path / 'h.jsonl'), '--cancel']) == 0
    assert not (tmp_path / 'job' / 'xiaohongshu_publish_plan.json').exists()
    assert not (tmp_path / 'h.jsonl').exists()


def test_failed_ffmpeg_does_not_reuse_old_frame(tmp_path, monkeypatch):
    from types import SimpleNamespace
    (tmp_path / '1_1.jpg').write_bytes(b'old frame')
    monkeypatch.setattr(pipeline.finder, 'probe_video', lambda _: {'duration': 30})
    monkeypatch.setattr(pipeline.finder, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1))
    assert pipeline.finder.extract_frames({'id': '1'}, 'fake.mp4', tmp_path, 1) == []
    assert not (tmp_path / '1_1.jpg').exists()


def test_pure_enqueue_cli_requires_finalized_three_and_never_plans(tmp_path):
    args = args_for(tmp_path)
    backend = FakeBackend()
    pipeline.run_pipeline(args, backend)
    assert candidate_pool.main(['--history-file', args.publish_history, 'enqueue', '--out', args.out]) == 2
    args.review_file = review_all(args.out)
    assert pipeline.run_pipeline(args, backend)['status'] == 'complete'
    assert candidate_pool.main(['--history-file', args.publish_history, 'enqueue', '--out', args.out]) == 0
    assert not (Path(args.out) / 'xiaohongshu_publish_plan.json').exists()
    manifest_path = Path(args.out) / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['downloads'] = manifest['downloads'][:1]
    manifest_path.write_text(json.dumps(manifest))
    assert candidate_pool.main(['--history-file', args.publish_history, 'enqueue', '--out', args.out]) == 2


def test_downloading_reservation_survives_hard_interruption(tmp_path):
    args = args_for(tmp_path)
    backend = FakeBackend([([tweet(1)], None)])
    real = backend.download
    backend.download = lambda *args: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        pipeline.run_pipeline(args, backend)
    registry = json.loads((tmp_path / 'candidate_jobs.json').read_text())
    job = next(iter(registry['jobs'].values()))
    assert job['source_ids'] == ['1']
    assert job['checkpoint']['items']['1']['status'] == 'downloading'
    backend.download = real
    assert pipeline.run_pipeline(args, backend)['status'] == 'awaiting_review'
    assert backend.downloads == ['1']


def test_search_timeout_preserves_cursor_and_returns_structured_block(tmp_path):
    import subprocess
    args = args_for(tmp_path)
    backend = FakeBackend()
    backend.search = lambda *args: (_ for _ in ()).throw(subprocess.TimeoutExpired('xfetch', 1))
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'blocked'
    assert result['published'] == 0
    state = json.loads((Path(args.out) / 'candidate_state.json').read_text())
    assert state['pages'] == 1
    assert state['queries'][0]['cursor'] is None
    assert state['queries'][0]['done'] is False


def test_missing_media_can_be_explicitly_redownloaded_and_reviewed(tmp_path):
    args = args_for(tmp_path)
    backend = FakeBackend()
    pipeline.run_pipeline(args, backend)
    request = json.loads((Path(args.out) / 'review_request.json').read_text())
    Path(request['items']['1']['video']['file']).unlink()
    args.retry_failed = True
    result = pipeline.run_pipeline(args, backend)
    # 其余条目仍待审核，本次先报告待审；审核后会补齐丢失的媒体。
    assert result['status'] == 'awaiting_review'
    args.review_file = review_all(args.out)
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'awaiting_review', result
    assert backend.downloads.count('1') == 2


def test_mixed_refill_keeps_origin_through_review_and_queue(tmp_path):
    args = args_for(tmp_path, series='regional_production_cars,rally_road_cars,car_mechanisms')
    backend = FakeBackend([([tweet(i)], None) for i in range(1, 4)])
    expected = args.series.split(',')
    for series in expected:
        receipt = pipeline.run_pipeline(args, backend)
        assert receipt['status'] == 'awaiting_review'
        request = json.loads(Path(receipt['review_request']).read_text())
        assert [row['series_id'] for row in request['items'].values()] == [series]
        args.review_file = review_all(args.out)
    assert pipeline.run_pipeline(args, backend)['enqueue']['added'] == 3
    assert all('filter:videos' in query for query, _, _ in backend.searches)
    entries = publication_queue.PublicationQueue(history_path=args.publish_history).records()
    assert {e['item']['topic_series_id'] for e in entries} == set(expected)


def test_model_only_text_accepts_specific_aliases():
    finder = pipeline.finder
    for text, expected in [('Tatra603', True), ('太脱拉603的发动机', True),
                           ('AZ-1', True), ('Berlin Fashion 603', False), ('Tatra6030', False)]:
        row = dict(tweet('123'), text=text, author={})
        assert finder.car_related(row, pipeline.re.compile(finder.CAR_LEXICON)) is expected

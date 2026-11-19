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
        if 'editorial' in result['verification']:
            proof['editorial'] = {
                **({'road_version_detail': '测试公路版保险杠与量产座舱'}
                   if result['series_id'] == 'rally_road_cars' else {}),
                'opening_action_at_sec': 0.5,
                'discussion_basis': '窄车位打开车门的空间取舍',
                'follow_up_topic': 'Toyota Sera 蝴蝶门的开启空间',
            }
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
    reservation = {'status': 'awaiting_review', 'target': 3, 'plan': plan, 'source_ids': ['in-progress']}
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


def test_mechanisms_rejects_late_action_and_keeps_next_topic_in_queue(tmp_path):
    args = args_for(tmp_path, series='car_mechanisms')
    backend = FakeBackend([([tweet(i) for i in range(1, 5)], None)])
    assert pipeline.run_pipeline(args, backend)['status'] == 'awaiting_review'
    args.review_file = review_all(args.out)
    path = Path(args.review_file)
    review = json.loads(path.read_text())
    review['items']['2']['verification']['editorial']['opening_action_at_sec'] = None
    path.write_text(json.dumps(review))

    assert pipeline.run_pipeline(args, backend)['status'] == 'awaiting_review'
    args.review_file = review_all(args.out)
    result = pipeline.run_pipeline(args, backend)

    assert result['status'] == 'complete', result
    assert result['published'] == 0
    assert backend.downloads == ['1', '2', '3', '4']
    saved = json.loads((Path(args.out) / 'manifest.json').read_text())
    assert saved['rejected'][0]['reason'] == '汽车机关图鉴前 3 秒未见关键动作'
    queue = publication_queue.PublicationQueue(history_path=args.publish_history).records()
    assert {e['source_id'] for e in queue} == {'1', '3', '4'}
    for entry in queue:
        item = entry['item']
        assert item['verification']['editorial']['follow_up_topic'] == 'Toyota Sera 蝴蝶门的开启空间'
        item['verification']['editorial']['follow_up_topic'] = '另一选题'
        with pytest.raises(ValueError, match='文案已变化'):
            candidate_review.validate_review(item)


def test_candidate_sort_prefers_opening_action_then_discussion():
    def item(at, discussion, total):
        return {'score': total, 'score_breakdown': {'discussion_question': discussion},
                'verification': {'editorial': {'opening_action_at_sec': at}}}

    strongest = item(1, 14, 75)
    action = item(0, 11, 86)
    static = item(None, 15, 95)
    assert sorted([static, strongest, action], key=candidate_review.candidate_sort_key) == [strongest, action, static]


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


def test_search_prioritizes_productive_cursor_and_stops_after_repeated_page(tmp_path):
    args = args_for(tmp_path, series='car_mechanisms', max_pages=3)
    backend = FakeBackend([
        ([tweet(1)], 'first-cursor'),
        ([tweet(1)], 'repeated-page-cursor'),
        ([tweet(2), tweet(3)], None),
    ])
    assert pipeline.run_pipeline(args, backend)['status'] == 'awaiting_review'
    args.review_file = review_all(args.out)
    assert pipeline.run_pipeline(args, backend)['downloads_attempted'] == 3
    first, second, third = backend.searches
    assert first[2] == 1
    assert second == (first[0], 'first-cursor', 2)
    assert third[0] != first[0] and third[1:] == (None, 1)
    assert backend.downloads == ['1', '2', '3']
    args.review_file = review_all(args.out)
    assert pipeline.run_pipeline(args, backend)['status'] == 'complete'
    assert len(backend.searches) == 3


def test_search_waits_for_homepage_review_and_skips_cursor_after_rejection(tmp_path):
    args = args_for(tmp_path, series='car_mechanisms', max_pages=2)
    backend = FakeBackend([([tweet(1)], 'first-cursor'), ([tweet(2)], None)])
    assert pipeline.run_pipeline(args, backend)['status'] == 'awaiting_review'
    assert pipeline.run_pipeline(args, backend)['status'] == 'awaiting_review'
    assert len(backend.searches) == 1

    args.review_file = review_all(args.out, reject=('1',))
    assert pipeline.run_pipeline(args, backend)['status'] == 'awaiting_review'
    first, second = backend.searches
    assert second[0] != first[0] and second[1:] == (None, 1)
    assert backend.downloads == ['1', '2']


def test_cursor_requires_approval_on_both_first_and_latest_pages():
    query = {'query': 'demo', 'pages': 2, 'done': False, 'cursor': 'saved'}
    items = {'1': {'status': 'approved'}, '2': {'status': 'rejected'}}
    assert pipeline.select_query([query], {}, items) is None
    query['page_sources'] = {'1': ['1'], '2': ['2']}
    assert pipeline.select_query([query], {}, items) is None
    items['2']['status'] = 'approved'
    assert pipeline.select_query([query], {}, items) == query
    items['1']['status'] = 'rejected'
    assert pipeline.select_query([query], {}, items) is None


def test_candidate_search_narrows_generic_mechanism_words(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, 'make_queries', lambda plan: [])
    lamps = '跳灯 翻灯 min_faves:15 filter:videos -"car show"'
    tailgate = '"尾门 侧开" min_faves:15 filter:videos'
    scoped = '"Honda Ridgeline" "双向尾门" filter:videos'
    args = args_for(tmp_path, series='car_mechanisms', query=[lamps, tailgate, scoped], max_pages=3)
    backend = FakeBackend([([], None)] * 3)
    assert pipeline.run_pipeline(args, backend)['status'] == 'exhausted'
    first, second, third = backend.searches
    assert first[0].startswith(lamps + ' (') and '"Mazda MX-5"' in first[0]
    assert second[0].startswith(tailgate + ' (') and '"Honda Ridgeline"' in second[0]
    assert third[0] == scoped


def test_narrowing_preserves_existing_search_and_discards_incompatible_cursor():
    broad = {'query': '跳灯 翻灯 filter:videos', 'pages': 1, 'cursor': 'old', 'done': False,
             'series_id': 'car_mechanisms', 'page_sources': {'1': ['1']}}
    state = {'plan': {'series_id': 'car_mechanisms'}, 'queries': [broad]}
    pipeline.narrow_mechanism_queries(state)
    precise, = state['queries']
    assert precise['pages'] == 0 and precise['cursor'] is None
    assert state['retired_queries'][0]['page_sources'] == broad['page_sources']

    precise.update(pages=1, cursor='new', page_sources={'1': ['2']})
    state['queries'].append(dict(broad, pages=0, cursor=None))
    pipeline.narrow_mechanism_queries(state)
    assert state['queries'] == [precise]
    assert len(state['retired_queries']) == 1


def test_search_learns_from_compatible_prior_batches_and_keeps_dry_queries_as_fallback(tmp_path):
    import hashlib

    series = 'car_mechanisms'
    dry = {'query': 'dry', 'series_id': series, 'pages': 1, 'done': True}
    useful = {'query': 'useful', 'series_id': series, 'pages': 1, 'done': True}
    current = {'job_id': 'current', 'created_at': '2026-11-18T12:00:00+00:00',
               'settings': {'type': 'top'}, 'plan': {'series_id': series}}
    invalid = {'query': 'bad archive', 'series_id': series, 'pages': 1, 'done': True}

    def archived(name, updated, mode='top', observed=True):
        root = tmp_path / name
        raw = root / 'raw'
        raw.mkdir(parents=True)
        observations = {'dry': '', 'useful': json.dumps(tweet(1)) + '\n', 'bad archive': 'broken\n'}
        for query, contents in observations.items():
            key = hashlib.sha256(query.encode()).hexdigest()[:16]
            (raw / (key + '_page1.jsonl')).write_text(contents)
        queries = [dry, useful, invalid]
        if observed:
            queries = [dict(dry, page_sources={'1': ['old']}),
                       dict(useful, page_sources={'1': ['1']}), invalid]
        return {'status': 'complete', 'updated_at': updated, 'out_dir': str(root), 'checkpoint': {
            'job_id': name, 'queries': queries, 'items': {'1': {'status': 'approved'}, 'old': {'status': 'skipped'}},
            'plan': current['plan'], 'settings': {'type': mode}}}

    jobs = [
        archived('expired', '2026-11-17T10:00:00+00:00'),
        archived('first', '2026-11-18T10:00:00+00:00', observed=False),
        archived('second', '2026-11-18T11:00:00+00:00'),
        archived('different_mode', '2026-11-18T11:30:00+00:00', mode='latest'),
        archived('future', '2026-11-18T12:01:00+00:00'),
    ]
    history = pipeline.prior_query_yield(jobs, current)
    assert history == {
        'car_mechanisms\ndry': {'samples': 2, 'qualified': 0, 'dry_streak': 2},
        'car_mechanisms\nuseful': {'samples': 2, 'qualified': 1, 'dry_streak': 1},
    }
    fresh = [dict(query, pages=0, done=False) for query in (dry, dict(dry, query='untested'), useful)]
    assert pipeline.select_query(fresh, history, {})['query'] == 'useful'
    fresh[2]['done'] = True
    assert pipeline.select_query(fresh, history, {})['query'] == 'untested'
    fresh[1]['done'] = True
    assert pipeline.select_query(fresh, history, {})['query'] == 'dry'



def test_inventory_uses_real_sources_and_limits_empty_search_reservations():
    from topic_planner import production_inventory
    now = datetime.datetime(2026, 11, 19, tzinfo=datetime.timezone.utc)
    plan = {'series_id': 'rare_jdm', 'pillar': 'rare_models'}
    queue = [{'source_id': 'held', 'status': 'queued', 'publish_hold': True, 'item': {}},
             {'source_id': 'ready', 'status': 'planned', 'item': {}}]

    def job(status, items=None, minutes=0):
        return {'plan': plan, 'target': 3, 'status': status, 'checkpoint': {
            'created_at': (now - datetime.timedelta(minutes=minutes)).isoformat(),
            'items': {sid: {'status': stage} for sid, stage in (items or {}).items()}}}

    jobs = [job('budget_exhausted', {'approved': 'approved', 'held': 'approved', 'pending': 'awaiting_review'}),
            job('blocked'), job('exhausted'), job('cancelled', {'old': 'approved'}),
            job('awaiting_review', {'ready': 'approved', 'approved': 'approved', 'new': 'awaiting_review'}),
            job('searching', minutes=9), job('searching', minutes=10)]
    inventory, counts = production_inventory(queue, jobs, now)
    assert counts == {'available': 1, 'held': 1, 'reserved': 3, 'in_production': 2}
    assert len(inventory) == 6


def test_rally_prefilter_prioritizes_version_and_preserves_ambiguous_video(tmp_path):
    args = args_for(tmp_path, series='rally_road_cars', max_pages=1)
    captions = ['Final stage drama #WRC car', 'Rare car in a garage', 'Lancia 037 Stradale road car engine']
    rows = [dict(tweet(i), text=caption, favorite_count=1000 // i) for i, caption in enumerate(captions, 1)]
    backend = FakeBackend([(rows, None)])
    receipt = pipeline.run_pipeline(args, backend)
    assert backend.downloads == ['3', '2']
    assert receipt['series_mismatch_skipped'] == 1
    request = json.loads(Path(receipt['review_request']).read_text())
    assert '公路版本' in request['items']['3']['series_question']
    assert 'road_version_detail' in request['items']['3']['verification']['editorial']
    assert '1' not in json.loads((tmp_path / 'candidate_jobs.json').read_text())['rejected']


def test_series_mismatch_can_be_reviewed_under_another_series(tmp_path):
    args = args_for(tmp_path, series='rally_road_cars,experimental_vehicles', max_pages=2)
    row = dict(tweet(1), text='AI-generated video of an experimental concept car')
    backend = FakeBackend([([row], None), ([row], None)])
    result = pipeline.run_pipeline(args, backend)
    assert backend.downloads == ['1'] and result['series_mismatch_skipped'] == 1
    request = json.loads(Path(result['review_request']).read_text())
    assert request['items']['1']['series_id'] == 'experimental_vehicles'


def test_only_final_qualified_results_earn_search_credit(tmp_path):
    from query_schedule import prior_query_yield
    series = 'rally_road_cars'
    plan = {'series_id': series}
    ids = ['usable', 'withdrawn', 'rejected', 'failed', 'pending', 'duplicate']
    statuses = ['approved', 'approved', 'rejected', 'download_failed', 'awaiting_review', 'approved']
    items = {sid: {'status': status} for sid, status in zip(ids, statuses)}
    query = {'query': 'cars', 'pages': 2, 'series_id': series,
             'page_sources': {'1': ids[:4], '2': ['duplicate']}}
    job = {'status': 'complete', 'updated_at': '2026-11-18T10:00:00Z', 'out_dir': str(tmp_path),
           'checkpoint': {'job_id': 'past', 'plan': plan, 'queries': [query],
                          'items': items, 'enqueue': {'source_ids': ['usable', 'withdrawn']}}}
    state = {'created_at': '2026-11-18T11:00:00Z', 'plan': plan, 'job_id': 'new'}
    result = prior_query_yield([job], state, ['withdrawn'])
    assert result[series + '\ncars'] == {'qualified': 1, 'samples': 2, 'dry_streak': 1}


def test_stalled_ids_release_while_fresh_downloads_remain_exclusive():
    from topic_planner import reserved_source_ids
    now = datetime.datetime.now(datetime.timezone.utc)
    job = {'status': 'searching', 'updated_at': now.isoformat(), 'checkpoint': {'items': {
        '1': {'status': 'downloading'}, '2': {'status': 'awaiting_review'}, '3': {'status': 'approved'}}}}
    assert reserved_source_ids(job, now) == {'1', '2', '3'}
    assert reserved_source_ids(job, now + datetime.timedelta(minutes=10)) == {'2', '3'}
    assert reserved_source_ids(dict(job, status='exhausted'), now) == {'3'}


def test_rally_approval_needs_version_detail_before_enqueue(tmp_path):
    args = args_for(tmp_path, series='rally_road_cars', top=1, min_selected=1)
    backend = FakeBackend([([dict(tweet(1), text='Lancia 037 Stradale road car')], None)])
    pipeline.run_pipeline(args, backend)
    args.review_file = review_all(args.out)
    review_path = Path(args.review_file)
    review = json.loads(review_path.read_text())
    del review['items']['1']['verification']['editorial']['road_version_detail']
    review_path.write_text(json.dumps(review))
    receipt = pipeline.run_pipeline(args, backend)
    assert receipt['status'] == 'blocked' and 'road_version_detail' in receipt['error']
    assert not Path(receipt['queue_file']).exists()


def test_mixed_road_and_race_text_needs_visual_judgment():
    from series_match import SERIES, assess_series, reviewed_series_conflict
    assert set(SERIES) == {s['id'] for pillar in pipeline.PILLARS.values() for s in pillar['series']}
    for text in ['Lancia 037 Stradale alongside a Rally1', 'RS200 road version from the WRC era',
                 'A rare car #WRC', 'Not gameplay footage, filmed today', 'Subaru Impreza 22B on a special stage']:
        assert not assess_series('rally_road_cars', text)[1]
    assert reviewed_series_conflict({'topic_series_id': 'rally_road_cars', 'ai_disclosure': 'AI 生成画面'})


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


def test_finalization_recovers_two_reposts_sharing_one_download(tmp_path):
    source = tmp_path / 'shared.mp4'
    first = tmp_path / '.finalize-0.tmp'
    first.write_bytes(b'shared video')
    digest = candidate_review.file_digest(first)
    targets = [tmp_path / 'rejected' / name for name in ('first.mp4', 'second.mp4')]
    transaction = tmp_path / 'finalize_transaction.json'
    transaction.write_text(json.dumps({
        'status': 'pending', 'manifest': {'downloads': [], 'rejected': []},
        'operations': [
            {'source': str(source), 'sha256': digest, 'target': str(target),
             'temporary': str(tmp_path / ('.finalize-%d.tmp' % index))}
            for index, target in enumerate(targets)
        ],
    }))

    finalize.recover_finalization(tmp_path)

    assert [p.read_bytes() for p in targets] == [b'shared video', b'shared video']
    assert json.loads(transaction.read_text())['status'] == 'complete'
    assert not first.exists()


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
    args = args_for(tmp_path, max_pages=1)
    backend = FakeBackend()
    failed_searches = []

    def timeout(query, cursor, page, out):
        failed_searches.append((query, cursor, page))
        raise subprocess.TimeoutExpired('xfetch', 1)

    backend.search = timeout
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'blocked'
    assert result['published'] == 0
    state = json.loads((Path(args.out) / 'candidate_state.json').read_text())
    assert result['pages_used'] == state['pages'] == 0
    assert state['queries'][0]['cursor'] is None
    assert state['queries'][0]['done'] is False
    recovered = FakeBackend()
    result = pipeline.run_pipeline(args, recovered)
    assert result['status'] == 'awaiting_review'
    assert result['pages_used'] == 1
    assert recovered.searches == failed_searches


def test_blocked_second_page_retries_same_cursor_with_original_budget(tmp_path):
    args = args_for(tmp_path, max_pages=2)
    backend = FakeBackend([([tweet(1)], 'cursor1')])
    assert pipeline.run_pipeline(args, backend)['status'] == 'awaiting_review'
    args.review_file = review_all(args.out)
    first_query = backend.searches[0][0]

    def fail_search(query, cursor, page, out):
        assert (query, cursor, page) == (first_query, 'cursor1', 2)
        raise RuntimeError('X 搜索失败：SearchTimeline: Internal server error')

    backend.search = fail_search
    blocked = pipeline.run_pipeline(args, backend)
    assert blocked['status'] == 'blocked'
    assert 'Internal server error' in blocked['error']
    assert blocked['pages_used'] == 1
    args.review_file = None
    recovered = FakeBackend([([tweet(2), tweet(3)], None)])
    result = pipeline.run_pipeline(args, recovered)
    assert result['status'] == 'awaiting_review'
    assert result['pages_used'] == 2
    assert recovered.searches == [(first_query, 'cursor1', 2)]


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


def test_recent_mechanism_skips_before_download_with_auditable_match(tmp_path):
    args = args_for(tmp_path, series='car_mechanisms', max_pages=1)
    recent = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record = dict(source_id='old', brand='Tesla', model='Model X', title='鹰翼门窄位开启',
                  status='published', published_at=recent)
    Path(args.publish_history).write_text(json.dumps(record) + '\n')
    captions = ['Model X Falcon Wing doors opening', 'Tesla Model X opens its trunk',
                'These Falcon Wing doors on a car are amazing', 'BMW Z10 sliding doors']
    posts = [dict(tweet(i), text='Car: ' + caption) for i, caption in enumerate(captions, 1)]
    backend = FakeBackend([(posts, None)])
    result = pipeline.run_pipeline(args, backend)
    assert result['recent_mechanism_skipped'] == 1
    assert result['model_duplicates_skipped'] == 1
    assert result['downloads_attempted'] == 2
    assert backend.downloads == backend.frame_calls == ['3', '4']
    state = json.loads((Path(args.out) / 'candidate_state.json').read_text())
    assert state['items']['1']['recent_duplicate']['source_id'] == 'old'
    assert state['items']['1']['recent_duplicate']['mechanism'] == '鹰翼门'
    assert not (Path(args.out) / 'videos/1.mp4').exists()


def test_recent_mechanism_time_status_and_text_boundaries():
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=30)
    item = dict(brand='BMW', model='Z1', title='Z1下滑车门')
    row = dict(source_id='old', item=item, status='queued', enqueued_at=cutoff.isoformat())
    index = pipeline.recent_mechanisms([], [row], now)
    match = pipeline.match_recent_mechanism
    assert match('宝马Z1的车门下沉', index)['source_id'] == 'old'
    assert match('The sliding doors of the BMW Z1', index)
    for caption in ('BMW Z10 sliding doors', 'BMW Z1 headlights', 'Z1 sliding doors',
                    'BMW Z1. This van has sliding doors', 'BMW Z1 without sliding doors',
                    'Car with sliding doors https://example.com/BMW-Z1', '#BMWZ1 sliding doors',
                    'BMW Z1 vs a van with sliding doors', 'BMW Z1 and a van with sliding doors',
                    "BMW Z1 doesn't have sliding doors", 'BMW Z1 beside a van with sliding doors'):
        assert not match(caption, index)
    for update in (dict(enqueued_at=(cutoff - datetime.timedelta(seconds=1)).isoformat()),
                   dict(enqueued_at='unknown'), dict(status='withdrawn')):
        assert not pipeline.recent_mechanisms([], [{**row, **update}], now)
    history = dict(item, source_id='old', published_at=now.isoformat(), status='unconfirmed')
    assert pipeline.recent_mechanisms([history], [], now)
    assert not pipeline.recent_mechanisms([history, {**history, 'status': 'failed'}], [], now)


@pytest.mark.parametrize('brand,model,title,caption', [
    ('Citroën', 'DS', '可拆后翼子板', 'Removing the rear fender on the Citroen DS'),
    ('Mercedes-Benz', '300 SL Coupé', '300 SL 鸥翼门', 'Mercedes 300 SL Gullwing doors opening'),
    ('Rolls-Royce', 'Ghost', '古思特车门里怎么弹出雨伞', 'Rolls Royce Ghost door umbrella'),
])
def test_recent_queue_recognizes_bilingual_model_and_mechanism(brand, model, title, caption):
    now = datetime.datetime.now(datetime.timezone.utc)
    row = dict(source_id='queued', status='queued', enqueued_at=now.isoformat(),
               item=dict(brand=brand, model=model, title=title))
    index = pipeline.recent_mechanisms([], [row], now)
    assert pipeline.match_recent_mechanism(caption, index)['source_id'] == 'queued'
    assert not pipeline.match_recent_mechanism(brand + ' ' + model + ' driving', index)


def test_verified_same_batch_mechanism_used_on_resume():
    now = datetime.datetime.now(datetime.timezone.utc)
    item = {'candidate': {'id': 'reviewed'}, 'brand': 'Mercedes-Benz', 'model': 'G-Class electric prototype',
            'title': '电动G级原地掉头', 'verification': {'reviewed_at': now.isoformat()}}
    index = pipeline.recent_mechanisms([], [], now, [item])
    assert pipeline.match_recent_mechanism('Watch the Mercedes EQG do a tank turn', index)['status'] == 'approved'
    assert not pipeline.match_recent_mechanism('Mercedes G Wagon on a trail', index)


@pytest.mark.parametrize(('caption', 'expected'), [
    ('Meet the BMW Z1. Its sliding doors disappear into the sills.', True),
    ('This car is a BMW Z1. Watch its sliding doors drop.', True),
    ('今天拍到一辆宝马Z1。它的下滑车门开启。', True),
    ('这是宝马Z1。它的下滑车门正在打开。', True),
    ('Check out those sliding doors. These doors belong to the BMW Z1.', True),
    ('BMW Z1. Another coupe arrives. Its sliding doors open.', False),
    ('BMW Z1. Toyota Sera. Its sliding doors open.', False),
    ('BMW Z1: the car that inspired the Model Y. Its sliding doors open.', False),
    ('BMW Z1. The sliding doors on my new van open.', False),
    ('BMW Z1. The sliding doors sense you approaching and open automatically.', False),
    ('BMW Z1. Its sliding doors detect a nearby key.', False),
    ('BMW Z1 launch. Thousands in the room. Normal doors do not open in that gap. Sliding doors do.', True),
    ('BMW Z1. Sliding doors do.', False),
    ('BMW Z1 sliding doors. Another van has the same feature. Which car is this?', False),
    ('BMW Z1. Would its sliding doors work on a van?', False),
    ('BMW Z1. Its sliding doors do not work.', False),
    ('BMW Z1. Its gullwing doors open.', False),
    ('Look at the gullwing doors and sliding doors. These doors made the BMW Z1 famous.', False),
    ('BMW Z1 versus a Toyota Sera. Its sliding doors open.', False),
    ('Is that a BMW Z1? Its sliding doors open.', False),
    ('宝马Z1和另一台车。它的下滑车门开启。', False),
])
def test_cross_sentence_mechanism_requires_clear_ownership(caption, expected):
    now = datetime.datetime.now(datetime.timezone.utc)
    history = [dict(source_id='old', brand='BMW', model='Z1', title='Z1下滑车门', published_at=now.isoformat())]
    result = pipeline.match_recent_mechanism(caption, pipeline.recent_mechanisms(history, [], now))
    assert bool(result) == expected
    if result:
        assert result['match_scope'] == 'cross_sentence'
        assert 2 <= len(result['evidence_sentences']) <= 4
        assert result['source_id'] == 'old'


def test_color_only_gate_uses_series_and_precedes_downloader(tmp_path):
    args = args_for(tmp_path, series='car_mechanisms', max_pages=1)
    captions = ['Color changing BMW iX Flow', 'BMW just announced: Dee. A fully color-changing car.',
                'BMW iX Flow changes color and opens its doors',
                'Pagani Huayra active flaps and color-shifting finish', 'BMW iX Flow concept car']
    posts = [dict(tweet(i), text=caption) for i, caption in enumerate(captions, 1)]
    backend = FakeBackend([(posts, None)])
    result = pipeline.run_pipeline(args, backend)
    assert result['color_only_skipped'] == 2
    assert result['recent_mechanism_skipped'] == 0
    assert result['downloads_attempted'] == 3
    assert backend.downloads == backend.frame_calls == ['3', '4', '5']
    state = json.loads((Path(args.out) / 'candidate_state.json').read_text())
    assert state['items']['1']['color_only']['technology_evidence'] == 'ix flow'
    assert state['items']['2']['color_only'] == {
        'technology_evidence': 'bmw just announced: dee', 'color_evidence': 'color-changing'}


@pytest.mark.parametrize(('text', 'expected'), [
    ('i Vision Dee 电子墨水车身变色', True),
    ('New E Ink exterior changes color electronically.', True),
    ('BMW #iX Flow cambia de color.', True),
    ('BMW iX Flow changes color. Watch the wing deploy.', False),
    ('BMW iX Flow 车身变色，另一侧后视镜同时转动', False),
    ('Does this iX Flow change color?', False),
    ('The iX Flow cannot change color.', False),
    ('Color-changing car vs iX Flow', False),
    ('Color change demonstration https://example.com/ix-flow', False),
    ('Color-changing fabric', False),
    ('iX Flow 车身变化很大', False),
    ('BMW iX Flow changes color and shape.', False),
    ('BMW iX Flow and a color-changing jacket', False),
])
def test_color_filter_preserves_unclear_and_other_series(text, expected):
    assert bool(pipeline.color_only_match(text, 'car_mechanisms')) is expected
    assert pipeline.color_only_match(text, 'classic_restoration') is None


@pytest.mark.parametrize('caption', [
    "BMW’s Dee changes colour.",
    "BMW i Vision Dee w/ E-Ink!",
    "The new BMW i Vision Dee with 32 colour E-ink technology.",
    "BMW i Vision Dee. An individually configurable exterior with up to 32 different colours.",
    "车身颜色可配置，宝马 Dee 的电子墨水外皮能玩出什么花样！",
    "BMW iX Flow uses E Ink technology to transform its exterior appearance in real time.",
    "BMW apresenta carro que muda de cor. O BMW iX Flow muda a cor da carroceria e das rodas.",
    "تقنية iX Flow حيث يتغير لون المركبة من أبيض لأسود.",
    "السيارة المتغيرة اللون BMW iX Flow",
])
def test_electronic_body_color_evidence(caption):
    match = pipeline.color_only_match(caption, 'car_mechanisms')
    assert match and match['technology_evidence'] and match['color_evidence']
    assert not pipeline.color_only_match(caption, 'classic_restoration')


@pytest.mark.parametrize('caption', [
    "BMW iX Flow", "BMW just announced: Dee.", "Meet Dee, a fan of color-changing cars.",
    "Dee changes color. https://example.com/BMW",
    "Does BMW Dee have a configurable exterior with up to 32 different colours?",
    "BMW i Vision Dee with E Ink? No specs yet.",
    "BMW Dee could change color one day.",
    "BMW Dee has a 32 colour E-Ink dashboard.",
    "BMW i Vision Dee w/ E-Ink display.",
    "BMW Dee changes colours. Its door handles extend.",
    "BMW i Vision Dee: configurable exterior colors. The grille opens at speed.",
    "宝马 Dee 的车身颜色可配置，前灯也会翻转。",
    "BMW iX Flow: carro que muda de cor. As rodas também giram.",
    "BMW iX Flow: carro que muda de cor. O teto se abre.",
    "O BMW iX Flow não é um carro que muda de cor.",
    "BMW iX Flow cambia de color y las ruedas se mueven.",
    "تقنية iX Flow حيث يتغير لون المركبة ثم تنفتح الأبواب.",
    "iX Flow لا يتغير لون المركبة.",
    "هل يتغير لون المركبة في iX Flow؟",
    "BMW iX Flow changes color. #retractable spoiler",
    "BMW Dee changes colour. #ActiveSpoiler",
    "Changing a car’s exterior color typically requires paint. What if a button could do it?",
])
def test_color_only_requires_a_clear_single_feature(caption):
    assert not pipeline.color_only_match(caption, 'car_mechanisms')


@pytest.mark.parametrize(('caption', 'expected'), [
    ('BMW has a color-changing car called Dee', True),
    ('BMW unveiled its new colour-changing concept vehicle named “Dee”', True),
    ('宝马有一辆叫 Dee 的变色车', True),
    ('宝马推出一款变色概念车名为 Dee', True),
    ('Meet a colour-changing car created by a designer called Dee', False),
    ('BMW apresenta carro elétrico que muda de cor. O modelo iX Flow.', True),
    ('O iX Flow é um veículo elétrico que pode mudar de cor.', True),
    ('The BMW i Vision Dee can now transform into 32 colors, not just black and white. '
     'It is a color-changing car.', True),
    ('BMW’s color-changing E Ink i Vision Dee. “Vaporware or not, I want it.”', True),
    ('BMW Dee changes color without a paint shop.', True),
    ('BMW Dee is not the first color-changing car, but it changes colors.', True),
    ('BMW Dee has no color change in the clip.', False),
    ('BMW Dee can never actually change its exterior color.', False),
    ('BMW Dee was advertised as color-changing. It isn’t able to change colours.', False),
    ('BMW Dee does not have a color-changing exterior.', False),
    ('The BMW Dee color change is not possible in this demo.', False),
    ('BMW iX Flow não é um carro elétrico que muda de cor.', False),
    ('宝马 Dee 不能改变车身颜色。', False),
    ('iX Flow لا يمكن أن يتغير لون المركبة.', False),
    ('BMW has a color-changing car called Dee. Its doors open in the first shot.', False),
    ('BMW mostra o iX Flow: carro elétrico que muda de cor e abre o teto.', False),
    ('BMW has a color-changing car called Dee. E Ink changes color and the chassis raises.', False),
    ('The future of the color changing car for BMW has a name: “BMWi Vision DEE” '
     'and the possibilities are endless 🔥 https://t.co/Dnz2DCYlFD', False),
    ('The future of the color changing car for BMW has a name: “BMW i Vision DEE” '
     'and the possibilities are endless 🔥', False),
    ('BMW Dee changes color but it is no substitute for an active spoiler.', False),
])
def test_color_filter_scopes_named_dee_and_negation(caption, expected):
    assert bool(pipeline.color_only_match(caption, 'car_mechanisms')) is expected


def test_gina_reposts_require_prior_approval_of_the_fabric_hood():
    now = datetime.datetime.now(datetime.timezone.utc)
    source = {'candidate': {'id': 'original'}, 'brand': 'BMW', 'model': 'GINA Light Visionary Model',
              'verifiable_fact': '织物机盖沿中线向两侧分开', 'verification': {'reviewed_at': now.isoformat()}}
    repost = 'The BMW GINA has a moving skin 😱'
    assert not pipeline.match_recent_mechanism(repost, pipeline.recent_mechanisms([], [], now))
    index = pipeline.recent_mechanisms([], [], now, [source])
    assert pipeline.match_recent_mechanism(repost, index)['source_id'] == 'original'
    assert pipeline.match_recent_mechanism('BMW Gina has flexible skin that actually moves. 📹 NewCamera', index)
    for caption in ('BMW GINA has flexible skin', 'BMW GINA has moving skin around the headlights',
                    'BMW GINA has moving skin. Its doors open vertically.', 'BMW GINA and another car with moving skin'):
        assert not pipeline.match_recent_mechanism(caption, index)
    different = dict(source, verifiable_fact='织物覆盖的头灯打开')
    assert not pipeline.match_recent_mechanism(repost, pipeline.recent_mechanisms([], [], now, [different]))


def test_early_rotation_uses_remaining_series_in_same_job(tmp_path):
    args = args_for(tmp_path, max_pages=6)
    backend = FakeBackend([([], None)] * 6 + [([tweet(1), tweet(2), tweet(3)], None)])
    stopped = pipeline.run_pipeline(args, backend)
    state = json.loads((Path(args.out) / 'candidate_state.json').read_text())
    first_series = state['plan']['series_id'].split('+')
    assert stopped['status'] == 'budget_exhausted'
    assert stopped['empty_series_pages'] == dict.fromkeys(first_series, 2)
    assert stopped['downloads_attempted'] == 0
    next_series = state['fallback_series'][0]
    args.max_pages = 7
    resumed = pipeline.run_pipeline(args, backend)
    assert resumed['job_id'] == stopped['job_id'] and resumed['pages_used'] == 7
    req = json.loads(Path(resumed['review_request']).read_text())
    assert {entry['series_id'] for entry in req['items'].values()} == {next_series}
    assert resumed['empty_series_pages'][next_series] == 0
    assert req['topic_plan']['content_mix'] == {'rare_models': .6, 'mechanical_contrast': .25, 'visual_experiment': .15}


def test_empty_streak_resets_on_new_candidate_and_ignores_search_errors(tmp_path):
    args = args_for(tmp_path, series='limited_supercars', max_pages=2)
    backend = FakeBackend([([], None), ([tweet(1)], None)])
    res = pipeline.run_pipeline(args, backend)
    assert res['empty_series_pages'] == {'limited_supercars': 0}
    args.review_file = review_all(args.out, reject=['1'])
    args.max_pages = 4
    def fail(*args):
        raise RuntimeError('search interrupted')
    backend.search = fail
    res = pipeline.run_pipeline(args, backend)
    assert res['status'] == 'blocked' and res['pages_used'] == 2
    assert res['empty_series_pages'] == {'limited_supercars': 0}


def test_explicit_model_match_respects_scope_status_age_and_variant():
    from caption_models import caption_duplicate, model_index
    now = datetime.datetime.now(datetime.timezone.utc)
    old = (now - datetime.timedelta(days=31)).isoformat()
    record = dict(brand='Jaguar', model='XJ220', status='published', source_id='known', published_at=old)
    assert not model_index([record], [], now)
    assert not model_index([], [dict(record, status='withdrawn')], now)
    for records, pool, origin in (([], [dict(record, status='queued', enqueued_at=old, publish_hold=True)], '候选池'),
                                  ([dict(record, status='unconfirmed', published_at=now.isoformat())], [], '发布历史')):
        index = model_index(records, pool, now)
        for text in ('#Jaguar XJ220 https://t.co/short', '#JaguarXJ220 🥹', '捷豹XJ220的灯罩', 'XJ220', '#Jaguar XJ220 1992'):
            match = caption_duplicate(text, index)
            assert match['origin'] == origin and match['source_id'] == 'known'
        for text in ('Jaguar', 'Jaguar XJ220S', 'Jaguar XJ220 S', 'Jaguar XJ220 vs Porsche 959',
                     'Jaguar XJ220, Ferrari F40', '@JaguarXJ220 https://x.com/JaguarXJ220',
                     'Ford Transit powered by Jaguar XJ220 engine'):
            assert caption_duplicate(text, index) is None


def test_race_humor_requires_real_competition_and_incident_evidence(tmp_path):
    series = 'race_humor'
    args = args_for(tmp_path, series=series, top=1, min_selected=1, max_pages=1)
    captions = ['F1 wrong pit box parody', 'NASCAR pit stop practice', 'F1 wrong pit box in the race']
    backend = FakeBackend([([dict(tweet(i), text=text) for i, text in enumerate(captions, 1)], None)])
    result = pipeline.run_pipeline(args, backend)
    assert result['series_mismatch_skipped'] == 2 and backend.downloads == ['3']
    req = json.loads(Path(result['review_request']).read_text())
    assert req['topic_plan']['pillar'] == 'visual_experiment'
    assert 'race_source_url' in req['items']['3']['verification']['editorial']
    args.review_file = review_all(args.out)
    initial = json.loads(Path(args.review_file).read_text())
    editorial = initial['items']['3']['verification']['editorial']
    assert 'race_event' in pipeline.run_pipeline(args, backend)['error']
    editorial.update(race_incident={'key': 'indycar-2024-barber-mannequin', 'anchors': [['Barber'], ['mannequin fell']]},
                     race_event='测试分站正赛', race_phase='qualifying', race_source_url='https://example.com/race/report',
                     incident_evidence='测试样例：转播第 8 圈，赛车停错维修位后驶回，车号与样例来源一致')
    Path(args.review_file).write_text(json.dumps(initial))
    assert '只接受正赛' in pipeline.run_pipeline(args, backend)['error']
    editorial['race_phase'] = 'race'
    Path(args.review_file).write_text(json.dumps(initial))
    assert pipeline.run_pipeline(args, backend)['status'] == 'complete'
    row = publication_queue.PublicationQueue(history_path=Path(args.publish_history)).records()[0]
    assert row['item']['verification']['editorial']['incident_evidence'] == editorial['incident_evidence']


@pytest.mark.parametrize('text', ['Sora: race broadcast', 'iRacing funny moment', '赛后采访', '车队练习赛', '车手恶搞配音'])
def test_race_humor_rejects_parody_virtual_and_off_session(text):
    assert pipeline.assess_series('race_humor', text)[0] < 0
    assert pipeline.assess_series('race_humor', 'That happened on lap seven')[0] >= 0


def test_model_in_complete_sentence_and_distinct_named_edition():
    from caption_models import caption_duplicate, model_index
    row = dict(source_id='db', status='queued', item={'brand': 'Aston Martin', 'model': 'DB5 Goldfinger Continuation'})
    index = model_index([], [row], datetime.datetime.now(datetime.timezone.utc))
    assert caption_duplicate('Five DB5s! The first five DB5 Goldfinger Continuation cars have rolled off the line.', index)
    assert not caption_duplicate('A DB5. Inspired by the DB5 Goldfinger Continuation.', index)
    assert not caption_duplicate('The first five DB5 cars have rolled off the line.', index)
    assert caption_duplicate('In association with EON, our 25 DB5 Goldfinger Continuation cars recently started production.', index)


def test_race_event_download_gate_and_query_rotation(tmp_path):
    args = args_for(tmp_path, series='race_humor')
    captions = ['IndyCar mannequin fell at Barber', 'Another view: IndyCar mannequin fell at Barber',
                'IndyCar: a snake at Barber', 'Indy500 practice on Peacock', '#Q3 F1 traffic']
    backend = FakeBackend([([dict(tweet(i), text=t) for i, t in enumerate(captions, 1)], 'cursor'),
                           ([dict(tweet(6), text='New F1 race day incident')], None)])
    assert pipeline.run_pipeline(args, backend)['awaiting_review'] == 1
    args.review_file = review_all(args.out)
    review = json.loads(Path(args.review_file).read_text())
    review['items']['1']['verification']['editorial'].update(
        race_event='2024 IndyCar Barber', race_phase='race', race_source_url='https://example.com/barber',
        incident_evidence='同一分站，天桥人偶掉落，转播秒数与来源对应。',
        race_incident={'key': 'indycar-2024-barber-falling-mannequin',
                       'anchors': [['Barber'], ['mannequin'], ['fell', 'falling']]})
    Path(args.review_file).write_text(json.dumps(review))
    result = pipeline.run_pipeline(args, backend)
    assert result['race_event_skipped'] == 1
    assert result['series_mismatch_skipped'] == 2
    assert result['covered_race_queries'] == 2
    assert backend.downloads == ['1', '6'] and backend.searches[0] != backend.searches[1]
    state = json.loads((Path(args.out) / 'candidate_state.json').read_text())
    assert state['items']['3']['status'] == 'discovered'
    assert state['items']['2']['race_duplicate']['source_id'] == '1'


def test_race_event_scope_and_legacy_evidence():
    from race_humor import caption_incident, race_index
    item = dict(topic_series_id='race_humor', title='巴伯赛道人偶意外掉落',
                verifiable_fact='2024 年印地赛车巴伯站的人偶掉落。')
    index = race_index([], [{'source_id': 'old', 'status': 'queued', 'item': item}], [])
    assert caption_incident('The mannequin fell during the Barber IndyCar race.', index)['source_id'] == 'old'
    for text in ['IndyCar at Barber', 'The mannequin fell at another IndyCar track', 'New mannequin at Barber', '2025 IndyCar: the mannequin fell',
                 'First Georgina, now a snake. IndyCar race.']:
        assert caption_incident(text, index) is None
    assert not race_index([], [{'source_id': 'old', 'status': 'withdrawn', 'item': item}], [])
    for text in ['Indy500 practice on Peacock', 'F1 #FP1', 'F1 Q2 replay', '赛车排位', '车手摆拍', '恶搞短片']:
        assert pipeline.assess_series('race_humor', text)[1]
    assert not pipeline.assess_series('race_humor', 'F1: this is not a prank')[1]


def test_race_event_keeps_references_out_of_duplicate_counts():
    from race_humor import race_index, caption_incident
    text = ("I JUST DROVE AN F1 CAR! Today I drove the same Lotus Kimi drove in Abu Dhabi "
            "with his leave me alone moment. #RacingSchool")
    assert '个人试驾' in pipeline.assess_series('race_humor', text)[1]
    profile = {'key': 'f1-2012-abudhabi-radio', 'anchors': [['Abu Dhabi'], ['leave me alone']]}
    item = {'topic_series_id': 'race_humor', 'race_incident': profile, 'candidate': {'id': 'old'}}
    index = race_index([], [], [], [item])
    assert caption_incident('Abu Dhabi: leave me alone', index)['origin'] == '其他在制任务已核验'
    assert not caption_incident('This reminds me of Abu Dhabi: leave me alone', index)


@pytest.mark.parametrize('league,caption,author,skip', [
    ('nascar', 'The pace car just CRASHED into the sand barrels #NASCAR', '', False),
    ('indycar', 'Herta on the last lap in the 1996 IndyCar World Series #F1', '', False),
    ('wrc', 'Zebra crossing! #SafariRallyKenya #WRC', '', False),
    ('indycar', 'Fast Friday at Indy #Indy500', '', True),
    ('wrc', 'Power Stage starts at 13:18 #WRC', '', True),
    ('nascar', 'A massive wreck at Daytona #NASCAR', '', True),
    ('indycar', 'Grand Prix at Spa #F1', '', True),
    ('indycar', 'Football fans in Detroit', '', True),
    ('indycar', 'An unexpected visitor!', 'IndyCar', False),
])
def test_scoped_race_caption_before_download(league, caption, author, skip):
    from race_humor import predownload_reason
    assert bool(predownload_reason(caption, [league], author)) == skip

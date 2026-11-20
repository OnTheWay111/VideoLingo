"""品牌名录与逐车型入池使用临时队列和离线 X 数据。"""
import json
from pathlib import Path

import pytest

from tests.test_candidate_pipeline import FakeBackend, args_for, pipeline, review_all, tweet
from brand_catalog import coverage, read_catalog
from content_policy import BRAND_SERIES, engagement_rejection_reason
from publication_queue import PublicationQueue


def make_catalog(root):
    source = 'https://example.com/history'
    data = dict(brand='Test Motors', brand_aliases=['测试汽车'], founded_year=1950, as_of='2020-01-01',
                source=source, coverage_gaps=[], models=[
                    dict(model='Echo', first_year=1970, source=source),
                    dict(model='Aria', first_year=1960, aliases=['咏叹调', 'Aria Classic'], source=source)])
    path = root / 'brand_catalog.json'
    path.write_text(json.dumps(data))
    return path


def video(sid, caption, saves):
    return {**tweet(sid), 'text': caption, 'favorite_count': 0, 'reply_count': 1,
            'retweet_count': 9, 'bookmark_count': saves}


def review(receipt, brand, model):
    out = Path(receipt['review_request']).parent
    path = Path(review_all(out))
    data = json.loads(path.read_text())
    for item in data['items'].values():
        item['publish'].update(brand=brand, model=model)
    path.write_text(json.dumps(data))
    return str(path)


def test_brand_models_are_reviewed_individually_and_resumable(tmp_path):
    path = make_catalog(tmp_path)
    args = args_for(tmp_path, brand_catalog=path, model='Aria')
    backend = FakeBackend([([video(1, 'Aria', 90), video(2, 'Aria', 92),
                             video(3, '咏叹调', 91), video(4, 'Ariane', 999)], None)])
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'awaiting_review' and backend.downloads == ['2']
    # 搜到同名车，审核识别为其他品牌。该车型内递补，保留下一车型的审核机会。
    args.review_file = review(result, 'Other Motors', 'Aria')
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'awaiting_review' and backend.downloads == ['2', '3']
    args.review_file = review(result, '测试汽车', '咏叹调')
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'complete', result
    assert result['enqueue']['added'] == 1
    material, = result['materials']
    assert material['first_year'] == 1960 and material['model'] == 'Aria'
    assert (material['likes'], material['bookmarks'], material['reposts'], material['replies']) == (0, 91, 9, 1)
    assert pipeline.run_pipeline(args, backend)['status'] == 'complete'
    assert backend.downloads == ['2', '3']

    catalog = read_catalog(path)
    queue = PublicationQueue(history_path=args.publish_history)
    progress = coverage(catalog, args.out, queue.records())
    assert [r['model'] for r in progress['models']] == ['Aria', 'Echo']
    assert progress['queued'] == 1 and progress['status'] == 'incomplete'
    assert progress['models'][0]['total_engagement'] == 101
    assert progress['models'][0]['origin'] == 'this_job'
    assert progress['models'][0]['title'] == material['title']
    assert progress['models'][0]['body'] == material['body']
    assert progress['models'][0]['topics'] == material['topics']
    assert coverage(catalog, tmp_path / 'other', queue.records())['models'][0]['origin'] == 'existing_pool'
    assert queue.records()[0]['item']['model'] == 'Aria'
    args.model, args.review_file = 'Echo', None
    backend.pages = [([video(5, 'Echo', 92)], None)]
    result = pipeline.run_pipeline(args, backend)
    args.review_file = review(result, 'Test Motors', 'Echo')
    assert pipeline.run_pipeline(args, backend)['status'] == 'complete'
    assert coverage(catalog, args.out, queue.records())['status'] == 'complete'
    assert len(queue.records()) == 2 and not Path(args.publish_history).exists()

    catalog['coverage_gaps'] = ['有待查证的市场']
    assert coverage(catalog, args.out, queue.records())['status'] == 'incomplete'
    catalog['coverage_gaps'] = []
    Path(queue.records()[0]['item']['video']['file']).unlink()
    assert coverage(catalog, args.out, queue.records())['queued'] == 1


def test_brand_gate_at_enqueue_and_legacy_three_metric_gate(tmp_path):
    counts = dict(favorite_count=10, retweet_count=20, reply_count=30, bookmark_count=40)
    item = dict(topic_series_id=BRAND_SERIES, candidate=counts)
    with pytest.raises(ValueError, match='100 ≤ 100'):
        PublicationQueue(tmp_path / 'queue.json').enqueue([item], tmp_path)
    counts['bookmark_count'] = 41
    assert not engagement_rejection_reason(item)
    assert '60 ≤ 100' in engagement_rejection_reason(counts)
    del counts['bookmark_count']
    assert '60 ≤ 100' in engagement_rejection_reason(item)


def test_catalog_rejects_shared_aliases_and_model_can_exhaust_early(tmp_path):
    path = make_catalog(tmp_path)
    args = args_for(tmp_path, brand_catalog=path, model='Echo')
    backend = FakeBackend([([], None)] * 2)
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'exhausted' and backend.downloads == []
    assert (result['pages_used'], result['page_limit']) == (2, 3)
    assert all('Echo' in q for q, _, _ in backend.searches)
    assert not Path(args.out, 'publish_queue.json').exists()
    data = json.loads(path.read_text())
    data['models'][0]['aliases'] = ['ARIA']
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='重叠'):
        read_catalog(path)


def test_brand_can_page_past_a_failed_first_page(tmp_path):
    path = make_catalog(tmp_path)
    args = args_for(tmp_path, brand_catalog=path, model='Echo')
    backend = FakeBackend([([video(1, 'Echo', 90)], 'next'), ([], None),
                           ([video(2, 'Echo', 91)], None)])
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'awaiting_review' and result['pages_used'] == 3
    assert backend.searches[-1][1:] == ('next', 2) and backend.downloads == ['2']
    args.review_file = review(result, 'Test Motors', 'Echo')
    assert pipeline.run_pipeline(args, backend)['status'] == 'complete'
    assert len(backend.searches) == 3


def test_brand_page_budget_carries_over_review_and_only_explicit_backfill_extends_it(tmp_path):
    path = make_catalog(tmp_path)
    args = args_for(tmp_path, brand_catalog=path, model='Aria')
    backend = FakeBackend([([video(1, 'Aria', 90)], 'next-official'), ([], None),
                           ([video(2, 'Aria', 92)], 'next-media')])

    pending = pipeline.run_pipeline(args, backend)
    assert pending['status'] == 'awaiting_review' and pending['pages_used'] == 3
    assert backend.downloads == ['2']
    queries = [query for query, _, _ in backend.searches]
    assert queries[0].endswith('"Aria" filter:videos')
    assert queries[1].endswith('"咏叹调" filter:videos')
    assert queries[2].endswith('filter:media')
    args.review_file = review_all(Path(pending['review_request']).parent, reject=['2'])
    stopped = pipeline.run_pipeline(args, backend)
    assert stopped['status'] == 'budget_exhausted' and stopped['budget'] == 'search_pages'
    assert (stopped['pages_used'], stopped['page_limit'], stopped['awaiting_review']) == (3, 3, 0)

    catalog = read_catalog(path)
    queue = PublicationQueue(history_path=args.publish_history)
    aria, echo = coverage(catalog, args.out, queue.records())['models']
    assert (aria['status'], aria['budget'], aria['pages_used']) == ('budget_exhausted', 'search_pages', 3)
    assert aria['reason'] and aria['out'] == str(Path(pending['review_request']).parent)
    assert echo['status'] == 'pending'
    echo_args = args_for(tmp_path, brand_catalog=path, model='Echo')
    echo_backend = FakeBackend([([video(3, 'Echo', 91)], None)])
    pending = pipeline.run_pipeline(echo_args, echo_backend)
    assert (pending['status'], pending['pages_used']) == ('awaiting_review', 1)
    echo_args.review_file = review(pending, 'Test Motors', 'Echo')
    assert pipeline.run_pipeline(echo_args, echo_backend)['status'] == 'complete'

    backfill = '"Test Motors" "Aria" road test filter:videos'
    args.review_file, args.query = None, [backfill]
    assert pipeline.run_pipeline(args, backend)['budget'] == 'search_pages'
    assert len(backend.searches) == 3
    args.max_pages = 4
    backend.pages = [([video(4, 'Aria', 91)], None)]
    pending = pipeline.run_pipeline(args, backend)
    assert (pending['status'], pending['pages_used'], pending['page_limit']) == ('awaiting_review', 4, 4)
    assert backend.searches[-1] == (backfill, None, 1)
    args.review_file = review(pending, 'Test Motors', 'Aria')
    assert pipeline.run_pipeline(args, backend)['status'] == 'complete'
    assert coverage(catalog, args.out, queue.records())['status'] == 'complete'


def test_brand_catalog_can_extend_one_lineage_across_all_requested_categories(tmp_path):
    path = make_catalog(tmp_path)
    data = json.loads(path.read_text())
    categories = ('one_off', 'prototype', 'concept_car', 'hybrid_concept', 'race_car', 'homologation',
                  'limited_edition', 'special_edition', 'sports_car',
                  'unusual_structure', 'regional_exclusive', 'factory_special_purpose')
    for index, category in enumerate(categories):
        data['models'].append(dict(model=f'Echo Series {index}', first_year=1980 + index,
                                  source=data['source'], expansion=dict(
                                      category=category, reason='档案确认车型稀有依据',
                                      visual_cue='实车专属车身细节')))
    path.write_text(json.dumps(data))

    result = coverage(read_catalog(path), tmp_path, [])
    assert result['total'] == len(data['models'])
    assert 'expansion' not in result['models'][1]
    assert tuple(row['expansion']['category'] for row in result['models'][2:]) == categories
    data['models'][-1]['expansion']['category'] = 'cosmetic_package'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='扩展车型类别无效'):
        read_catalog(path)


def test_expanded_variant_can_resume_and_be_verified_separately_from_base_model(tmp_path):
    path = make_catalog(tmp_path)
    data = json.loads(path.read_text())
    variant = dict(model='Echo Sprint', first_year=1971, source=data['source'])
    data['models'].append(variant)
    path.write_text(json.dumps(data))
    args = args_for(tmp_path, brand_catalog=path, model='Echo Sprint')
    backend = FakeBackend([([video(1, 'Echo Sprint at the museum', 92),
                             video(2, 'Test Motors Echo arrives. Echo Sprint closeup at the museum.', 91)], None)])
    pending = pipeline.run_pipeline(args, backend)
    assert pending['status'] == 'awaiting_review' and backend.downloads == ['1']

    base_args = args_for(tmp_path, brand_catalog=path, model='Echo')
    base_backend = FakeBackend([([video(3, 'Test Motors Echo', 91)], None)])
    base_pending = pipeline.run_pipeline(base_args, base_backend)
    base_args.review_file = review(base_pending, 'Test Motors', 'Echo')
    assert pipeline.run_pipeline(base_args, base_backend)['status'] == 'complete'

    variant['expansion'] = dict(category='homologation', reason='品牌档案确认认证用途',
                                visual_cue='加宽轮拱和独立的后窗出风口')
    path.write_text(json.dumps(data))
    args.review_file = review(pending, 'Test Motors', 'Echo')
    resumed = pipeline.run_pipeline(args, backend)
    assert resumed['status'] == 'awaiting_review' and resumed['job_id'] == pending['job_id']
    assert backend.downloads == ['1', '2']
    assert resumed['pages_used'] == 1
    request = json.loads(Path(resumed['review_request']).read_text())
    assert request['topic_plan']['brand_expansion'] == variant['expansion']
    assert variant['expansion']['visual_cue'] in request['items']['2']['series_question']
    args.review_file = review(resumed, 'Test Motors', 'Echo Sprint')
    assert pipeline.run_pipeline(args, backend)['status'] == 'complete'
    records = PublicationQueue(history_path=args.publish_history).records()
    assert {row['item']['model'] for row in records} == {'Echo', 'Echo Sprint'}


def test_model_collection_accepts_all_matching_videos_above_100(tmp_path):
    args = args_for(tmp_path, brand='Test Motors', brand_catalog=None, model='Echo', max_pages=1)
    posts = [video(1, 'Test Motors Echo driving POV', 91), video(2, 'Test Motors Echo car show', 92),
             video(3, 'Test Motors Echo', 90), video(4, 'Other Motors Echo', 500)]
    result = pipeline.collect_model(args, FakeBackend([(posts, None)]))
    assert [item['source_id'] for item in result['materials']] == ['1', '2']
    replay = FakeBackend([(posts, None)])
    assert pipeline.collect_model(args, replay)['materials'] == []
    assert replay.downloads == []
    queue = PublicationQueue(history_path=args.publish_history)
    assert [entry['status'] for entry in queue.records()] == ['queued', 'queued']
    assert queue.select() is None

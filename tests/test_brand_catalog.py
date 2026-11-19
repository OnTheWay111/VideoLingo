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
                    dict(model='Aria', first_year=1960, aliases=['咏叹调'], source=source)])
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
    assert pipeline.run_pipeline(args, backend)['status'] == 'complete'
    assert backend.downloads == ['2', '3']

    catalog = read_catalog(path)
    queue = PublicationQueue(history_path=args.publish_history)
    progress = coverage(catalog, args.out, queue.records())
    assert [r['model'] for r in progress['models']] == ['Aria', 'Echo']
    assert progress['queued'] == 1 and progress['status'] == 'incomplete'
    assert progress['models'][0]['total_engagement'] == 101
    assert progress['models'][0]['origin'] == 'this_job'
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


def test_catalog_rejects_shared_aliases_and_model_job_does_not_switch(tmp_path):
    path = make_catalog(tmp_path)
    args = args_for(tmp_path, brand_catalog=path, model='Aria')
    backend = FakeBackend([([], None)] * 3)
    result = pipeline.run_pipeline(args, backend)
    assert result['status'] == 'exhausted' and backend.downloads == []
    assert all('Aria' in q or '咏叹调' in q for q, _, _ in backend.searches)
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
    assert result['status'] == 'awaiting_review'
    assert backend.searches[-1][1:] == ('next', 2) and backend.downloads == ['2']

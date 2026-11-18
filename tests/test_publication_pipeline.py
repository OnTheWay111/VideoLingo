"""发布可靠性回归：全部隔离历史和浏览器，不操作真实账号。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / '.agents/skills/xcar-xhs/scripts'))
import chrome_xiaohongshu as chrome
import publish_pipeline as pipeline
from publication_runtime import SubmissionJournal, execution_lock
from publication_queue import PublicationQueue
from publish_history import PublishedHistory
from xiaohongshu_publisher import save_plan
from tests.test_xiaohongshu_publisher import finalized_output


@pytest.fixture
def setup_plan(tmp_path):
    finalized_output(tmp_path)
    history = PublishedHistory(tmp_path / 'history.jsonl')
    plan = pipeline.build_plan(tmp_path, None, history)
    save_plan(tmp_path, plan)
    return history, plan


def test_execution_lock_covers_profile_and_releases(tmp_path):
    with execution_lock(tmp_path / 'a.jsonl'):
        with pytest.raises(RuntimeError, match='另一个'):
            with execution_lock(tmp_path / 'b.jsonl'):
                pytest.fail('同一 Profile 不应并行')
    with execution_lock(tmp_path / 'b.jsonl'):
        pass


def test_execution_lock_covers_shared_history(tmp_path):
    with execution_lock(tmp_path / 'a.jsonl', 'A'):
        with pytest.raises(RuntimeError):
            with execution_lock(tmp_path / 'a.jsonl', 'B'):
                pass


@pytest.mark.parametrize('body', ['正文' * 40, '前段' * 20 + '尾段'])
def test_full_text_rejects_missing_tail_question_disclosure(body):
    session = chrome.ChromeSession()
    plan = {'title': '标题', 'body': body, 'topics': ['#汽车'],
            'full_text': body + '\n讨论：问题？\nAI 生成画面\n#汽车'}
    session.evaluate = lambda expression: '标题' if expression.endswith('.value') else body + '\n#汽车'
    with pytest.raises(RuntimeError, match='全文复核'):
        session.verify_content(plan)
    session.evaluate = lambda expression: '标题' if expression.endswith('.value') else plan['full_text'].replace('\n', '\n\n')
    session.verify_content(plan)


def test_click_journal_precedes_mouse_and_response_loss(tmp_path, setup_plan):
    history, plan = setup_plan
    session = chrome.ChromeSession()
    journal = SubmissionJournal(history.path)
    session.before_submit = lambda: journal.write('submitting', plan, tmp_path)
    session.publish_backend_node = lambda name: 42
    def call(method, params=None):
        if method == 'DOM.getBoxModel':
            return {'model': {'content': [0, 0, 1, 0, 1, 1, 0, 1]}}
        if method == 'Input.dispatchMouseEvent':
            assert journal.read()['status'] == 'submitting'
            raise RuntimeError('CDP response lost')
    session.call = call
    with pytest.raises(RuntimeError, match='response lost'):
        session.click_publish()
    assert session.publish_clicked
    chrome.recover_submission(history)
    assert SubmissionJournal(history.path).read()['resolution'] == 'unconfirmed'
    assert len(list(SubmissionJournal(history.path).unresolved_archives())) == 1
    assert history.check(plan['source_id'])['status'] == 'unconfirmed'


def test_confirmed_result_recovers_without_current_plan(tmp_path, setup_plan):
    history, plan = setup_plan
    journal = SubmissionJournal(history.path)
    journal.write('confirmed', plan, tmp_path, result={
        'status': 'published', 'note_url': 'https://www.xiaohongshu.com/explore/real-note'})
    (tmp_path / 'xiaohongshu_publish_plan.json').unlink()
    chrome.recover_submission(history)
    chrome.recover_submission(history)
    assert len(history.records()) == 1
    assert history.check(plan['source_id'])['status'] == 'published'
    assert PublicationQueue(history_path=history.path).records()[0]['status'] == 'published'
    assert journal.read()['status'] == 'recorded'


def test_explicit_failed_resolution_requeues_interrupted_submission(tmp_path, setup_plan):
    history, plan = setup_plan
    journal = SubmissionJournal(history.path)
    journal.write('submitting', plan, tmp_path)
    chrome.recover_submission(history)
    history.add(plan['source_id'], plan['title'], status='failed', reason='后台确认没有发布')
    chrome.recover_submission(history)
    assert PublicationQueue(history_path=history.path).records()[0]['status'] == 'queued'
    assert journal.read()['status'] == 'recorded'


@pytest.mark.parametrize('candidates, expected', [
    ([{'title': '标题', 'urls': ['https://www.xiaohongshu.com/explore/one']}], 'https://www.xiaohongshu.com/explore/one'),
    ([{'urls': ['https://www.xiaohongshu.com/explore/one', 'https://www.xiaohongshu.com/explore/two']}], ''),
    ([{'urls': ['https://www.xiaohongshu.com/explore/one']}, {'urls': []}], ''),
])
def test_backend_link_requires_unique_match(candidates, expected):
    session = chrome.ChromeSession()
    session.call = lambda *args: None
    session.page_state = lambda: {'url': 'https://creator.xiaohongshu.com/creator/notes', 'text': '笔记管理'}
    session.evaluate = lambda expression: candidates
    assert session.lookup_public_note('标题') == expected


def test_pending_note_does_not_promote_unconfirmed(tmp_path):
    history = PublishedHistory(tmp_path / 'history.jsonl')
    history.add('1', '标题', status='unconfirmed')
    class NoLookup:
        def lookup_public_note(self, title):
            pytest.fail('未确认不能用标题匹配判成功')
    assert chrome.recover_note_urls(NoLookup(), tmp_path, history) == []
    assert history.check('1')['status'] == 'unconfirmed'


class FakeChrome:
    sessions = 0
    published = 0
    note_url = 'https://www.xiaohongshu.com/explore/published'
    def __init__(self, *args):
        type(self).sessions += 1
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def navigate_publish(self):
        pass
    def publish_plan(self, out_dir, plan):
        self.before_submit()
        self.publish_clicked = True
        type(self).published += 1
        result = {'status': 'published', 'note_url': '', 'result_url': 'https://creator.xiaohongshu.com/publish/success'}
        self.after_submit(result)
        return result
    def lookup_public_note(self, title):
        return self.note_url


@pytest.fixture
def fake_browser(monkeypatch):
    FakeChrome.sessions = FakeChrome.published = 0
    monkeypatch.setattr(pipeline, 'ChromeSession', FakeChrome)
    original = chrome.run_locked
    monkeypatch.setattr(pipeline, 'run_locked', lambda *a, **kw: original(*a, **kw, sleep_fn=lambda _: None))
    return FakeChrome


def test_one_call_publishes_and_attaches_note_one_session(tmp_path, setup_plan, fake_browser):
    history, plan = setup_plan
    result = pipeline.run_pipeline(tmp_path, history)
    assert result['status'] == 'complete'
    assert result['published'] == 1
    assert fake_browser.sessions == fake_browser.published == 1
    assert history.check(plan['source_id'])['note_url'] == fake_browser.note_url
    assert json.loads((tmp_path / 'publication_pipeline_receipt.json').read_text())['status'] == 'complete'
    assert 'metrics' in result


def test_missing_link_does_not_stop_other_videos_or_republish(tmp_path, fake_browser, monkeypatch):
    from tests.test_xiaohongshu_publisher import finalized_batch
    finalized_batch(tmp_path)
    history = PublishedHistory(tmp_path / 'history.jsonl')
    history.add('old', '历史笔记无链接', status='published')
    monkeypatch.setattr(FakeChrome, 'note_url', '')
    result = pipeline.run_pipeline(tmp_path, history, max_items=3)
    assert result['status'] == 'complete'
    assert result['published'] == 3
    assert fake_browser.published == 3
    assert all(r['note_url_pending'] for r in result['results'])
    assert len(result['pending_links']) == 4
    pipeline.run_pipeline(tmp_path, history)
    assert fake_browser.published == 3


def test_crashed_submission_blocks_only_same_video_before_browser(tmp_path, setup_plan, fake_browser):
    history, plan = setup_plan
    SubmissionJournal(history.path).write('submitting', plan, tmp_path)
    result = pipeline.run_pipeline(tmp_path, history, tweet_id=plan['source_id'])
    assert result['status'] == 'blocked'
    assert fake_browser.sessions == 0
    assert history.check(plan['source_id'])['status'] == 'unconfirmed'


def test_crashed_submission_allows_other_video_and_keeps_evidence(tmp_path, fake_browser):
    from tests.test_xiaohongshu_publisher import finalized_batch
    finalized_batch(tmp_path)
    history = PublishedHistory(tmp_path / 'history.jsonl')
    plan = pipeline.build_plan(tmp_path, None, history)
    SubmissionJournal(history.path).write('submitting', plan, tmp_path)
    result = pipeline.run_pipeline(tmp_path, history)
    assert result['status'] == 'complete'
    assert result['published'] == 1
    assert result['results'][0]['source_id'] != plan['source_id']
    assert history.check(plan['source_id'])['status'] == 'unconfirmed'
    archives = list(SubmissionJournal(history.path).unresolved_archives())
    assert len(archives) == 1
    assert archives[0][1]['plan'] == plan
    blocked = pipeline.run_pipeline(tmp_path, history, tweet_id=plan['source_id'])
    assert blocked['status'] == 'blocked'
    assert fake_browser.published == 1


def test_preview_does_not_open_browser(tmp_path, setup_plan, fake_browser):
    history, plan = setup_plan
    result = pipeline.run_pipeline(tmp_path, history, action='preview', tweet_id=plan['source_id'])
    assert result['status'] == 'complete'
    assert fake_browser.sessions == 0


def test_batch_reuses_session_and_selects_next_after_registration(tmp_path, fake_browser, monkeypatch):
    from tests.test_xiaohongshu_publisher import finalized_batch
    finalized_batch(tmp_path)
    history = PublishedHistory(tmp_path / 'history.jsonl')
    monkeypatch.setattr(FakeChrome, 'lookup_public_note', lambda self, title:
                        'https://www.xiaohongshu.com/explore/note' + str(self.published))
    result = pipeline.run_pipeline(tmp_path, history, max_items=3)
    assert result['status'] == 'complete'
    assert [r['source_id'] for r in result['results']] == ['200', '300', '100']
    assert result['published'] == 3
    assert fake_browser.sessions == 1
    assert all(r['status'] == 'published' for r in PublicationQueue(history_path=history.path).records())


def test_record_failure_preserves_confirmed_and_restart_does_not_republish(tmp_path, setup_plan, fake_browser, monkeypatch):
    history, plan = setup_plan
    real_record = chrome.record_result
    def fail(*args, **kwargs):
        raise OSError('disk unavailable')
    monkeypatch.setattr(chrome, 'record_result', fail)
    result = pipeline.run_pipeline(tmp_path, history)
    assert result['status'] == 'blocked'
    assert SubmissionJournal(history.path).read()['status'] == 'confirmed'
    assert fake_browser.published == 1
    monkeypatch.setattr(chrome, 'record_result', real_record)
    chrome.recover_submission(history)
    assert history.check(plan['source_id'])['status'] == 'published'
    assert fake_browser.published == 1


def test_pre_upload_failure_releases_planned_item(tmp_path, setup_plan, fake_browser, monkeypatch):
    history, plan = setup_plan
    def fail(self):
        raise RuntimeError('login unavailable')
    monkeypatch.setattr(FakeChrome, 'navigate_publish', fail)
    result = pipeline.run_pipeline(tmp_path, history)
    assert result['status'] == 'blocked'
    assert PublicationQueue(history_path=history.path).records()[0]['status'] == 'queued'
    assert fake_browser.published == 0


def test_screenshot_error_cannot_erase_confirmed_success(tmp_path, setup_plan):
    history, plan = setup_plan
    session = chrome.ChromeSession()
    journal = SubmissionJournal(history.path)
    session.prepare_plan = lambda *args: None
    session.click_publish = lambda: None
    session.page_state = lambda: {'text': '发布成功', 'url': 'https://creator.xiaohongshu.com/publish/success'}
    session.after_submit = lambda result: journal.write('confirmed', plan, tmp_path, result=result)
    def fail(*args):
        raise RuntimeError('screenshot failed')
    session.screenshot = fail
    assert session.publish_plan(tmp_path, plan)['status'] == 'published'
    assert journal.read()['status'] == 'confirmed'


def test_backend_rejects_url_already_assigned_to_another_note(tmp_path):
    history = PublishedHistory(tmp_path / 'history.jsonl')
    url = 'https://www.xiaohongshu.com/explore/one'
    history.add('1', '旧标题', note_url=url)
    history.add('2', '新标题')
    class Lookup:
        def lookup_public_note(self, title):
            return url
    pending = chrome.recover_note_urls(Lookup(), tmp_path, history)
    assert pending[0]['source_id'] == '2'
    assert not history.check('2').get('note_url')


@pytest.mark.parametrize('url', [
    'https://www.xiaohongshu.com/explore/',
    'https://www.xiaohongshu.com.evil.test/explore/one',
    'https://creator.xiaohongshu.com/publish/success',
])
def test_invalid_public_urls_rejected(url):
    assert chrome.public_note_url(url) == ''


def test_draft_interruption_can_be_resolved_without_deleting_journal(tmp_path, setup_plan, monkeypatch):
    import publish_history
    history, plan = setup_plan
    plan = {**plan, 'action': 'draft'}
    draft_path = tmp_path / 'drafts.jsonl'
    monkeypatch.setenv('X_CAR_DRAFT_HISTORY_FILE', str(draft_path))
    SubmissionJournal(history.path).write('submitting', plan, tmp_path)
    with pytest.raises(RuntimeError, match='草稿提交结果待确认'):
        chrome.recover_submission(history)
    assert publish_history.main(['--history-file', str(draft_path), 'resolve',
        '--source-id', plan['source_id'], '--title', plan['title'], '--reason', '后台确认草稿不存在']) == 0
    chrome.recover_submission(history)
    assert SubmissionJournal(history.path).read()['status'] == 'recorded'
    assert history.records() == []


def test_backend_cover_reads_actual_opened_url(monkeypatch):
    session = chrome.ChromeSession(timeout=1)
    calls = []
    targets = iter([
        {'targetInfos': [{'targetId': 'manager', 'type': 'page'}]},
        {'targetInfos': [{'targetId': 'manager', 'type': 'page'},
                         {'targetId': 'preview', 'type': 'page',
                          'url': 'https://www.xiaohongshu.com/explore/real?xsec_token=test'}]},
    ])
    def call(method, params=None):
        calls.append((method, params))
        return next(targets) if method == 'Target.getTargets' else {}
    session.call = call
    assert session.open_public_note({'x': 12, 'y': 34}) == 'https://www.xiaohongshu.com/explore/real?xsec_token=test'
    assert calls[-1] == ('Target.closeTarget', {'targetId': 'preview'})
    assert [p['type'] for m, p in calls if m == 'Input.dispatchMouseEvent'] == ['mousePressed', 'mouseReleased']


def test_backend_cover_rejects_multiple_opened_pages():
    session = chrome.ChromeSession(timeout=1)
    calls = []
    targets = iter([{'targetInfos': []}, {'targetInfos': [
        {'targetId': 'a', 'type': 'page', 'url': 'https://www.xiaohongshu.com/explore/a'},
        {'targetId': 'b', 'type': 'page', 'url': 'https://www.xiaohongshu.com/explore/b'},
    ]}])
    def call(method, params=None):
        calls.append((method, params))
        return next(targets) if method == 'Target.getTargets' else {}
    session.call = call
    assert session.open_public_note({'x': 12, 'y': 34}) == ''
    assert [p['targetId'] for m, p in calls if m == 'Target.closeTarget'] == ['a', 'b']


def test_archived_resolution_survives_later_submission(tmp_path, fake_browser):
    from tests.test_xiaohongshu_publisher import finalized_batch
    finalized_batch(tmp_path)
    history = PublishedHistory(tmp_path / 'history.jsonl')
    plan = pipeline.build_plan(tmp_path, None, history)
    journal = SubmissionJournal(history.path)
    journal.write('submitting', plan, tmp_path)
    assert pipeline.run_pipeline(tmp_path, history)['published'] == 1
    history.add(plan['source_id'], plan['title'], status='failed', reason='后台确认未提交')
    chrome.recover_submission(history)
    entry = next(r for r in PublicationQueue(history_path=history.path).records()
                 if r['source_id'] == plan['source_id'])
    assert entry['status'] == 'queued'
    assert not list(journal.unresolved_archives())


def test_unconfirmed_history_excludes_stale_planned_queue_entry(tmp_path, fake_browser):
    from tests.test_xiaohongshu_publisher import finalized_batch
    finalized_batch(tmp_path)
    history = PublishedHistory(tmp_path / 'history.jsonl')
    plan = pipeline.build_plan(tmp_path, None, history)
    history.add(plan['source_id'], plan['title'], status='unconfirmed')
    result = pipeline.run_pipeline(tmp_path, history)
    assert result['published'] == 1
    assert result['results'][0]['source_id'] != plan['source_id']
    current = history.check(result['results'][0]['source_id'])
    assert current['submission_status'] == 'confirmed'
    assert current['public_link_status'] == 'available'


def test_existing_legacy_candidate_publishes_without_reenqueue(tmp_path, setup_plan, fake_browser):
    history, plan = setup_plan
    queue = PublicationQueue(history_path=history.path)
    data = json.loads(queue.path.read_text())
    entry = data['entries'][0]
    entry['status'] = 'needs_review'
    entry['maintenance_reason'] = '入池必须有 approved / car_only 视觉核验结论'
    entry['item']['verification'] = {'status': 'verified', 'presentation': 'car_only'}
    queue.path.write_text(json.dumps(data))
    manifest_path = tmp_path / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['downloads'][0]['verification'] = entry['item']['verification']
    manifest_path.write_text(json.dumps(manifest))
    result = pipeline.run_pipeline(tmp_path, history)
    assert result['published'] == 1
    assert result['status'] == 'complete'


def test_ingestion_and_publication_omit_rights_but_validate_media(tmp_path, setup_plan):
    from candidate_review import validate_review, validate_publication_review
    history, plan = setup_plan
    item = PublicationQueue(history_path=history.path).records()[0]['item']
    item['verification'].pop('rights_evidence')
    item['verification'].pop('rights_source')
    validate_publication_review(item)
    validate_review(item)
    item['verification']['video_sha256'] = 'changed'
    with pytest.raises(ValueError, match='视频已变化'):
        validate_publication_review(item)


def test_publish_hold_is_atomic_and_blocks_queue_and_saved_plan(tmp_path, setup_plan, monkeypatch):
    history, plan = setup_plan
    queue = PublicationQueue(history_path=history.path)
    queue.release(plan['source_id'])
    second = tmp_path / 'second'
    second.mkdir()
    other = finalized_output(second, tweet_id='456', title='另一条候选')
    other.update(brand='BMW', model='Z1')
    from tests.candidate_fixtures import approve_item
    approve_item(other)
    queue.enqueue([other], second)
    with pytest.raises(ValueError):
        queue.set_publish_hold(['123', 'absent'], True)
    assert not any(e.get('publish_hold') for e in queue.records())
    assert queue.set_publish_hold(['123'], True)['changed'] == ['123']
    assert queue.set_publish_hold(['123'], True)['changed'] == []
    assert queue.select()['source_id'] == '456'
    queue.release('456')
    queue.set_publish_hold(['456'], True)
    assert queue.select() is None
    monkeypatch.setattr(chrome, 'ChromeSession', lambda *a, **kw: pytest.fail('保留项不能进入浏览器'))
    with pytest.raises(ValueError, match='仅保留'):
        chrome.run_locked(tmp_path, history, 'Default', True)
    assert all(e['status'] == 'queued' for e in queue.records())
    queue.set_publish_hold(['123'], False)
    assert queue.select()['source_id'] == '123'

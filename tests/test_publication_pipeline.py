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
    with pytest.raises(RuntimeError, match='禁止自动重发'):
        chrome.recover_submission(history)
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
    with pytest.raises(RuntimeError):
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


def test_missing_link_stops_batch_and_resume_never_republishes(tmp_path, setup_plan, fake_browser, monkeypatch):
    history, plan = setup_plan
    monkeypatch.setattr(FakeChrome, 'note_url', '')
    result = pipeline.run_pipeline(tmp_path, history, max_items=3)
    assert result['status'] == 'note_url_pending'
    assert fake_browser.published == 1
    pipeline.run_pipeline(tmp_path, history, max_items=3)
    assert fake_browser.published == 1


def test_crashed_submission_blocks_before_browser(tmp_path, setup_plan, fake_browser):
    history, plan = setup_plan
    SubmissionJournal(history.path).write('submitting', plan, tmp_path)
    result = pipeline.run_pipeline(tmp_path, history)
    assert result['status'] == 'blocked'
    assert fake_browser.sessions == 0


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

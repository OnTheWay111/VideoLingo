"""逐车型下载、审核及保留入池，同车型可收录多条实车视频。"""
import json
import subprocess
import time
import uuid
from pathlib import Path

import brand_catalog
import finder
from candidate_review import file_digest, validate_collection_review
from content_policy import (BRAND_SERIES, compact_video_title, engagement_total,
                            normalized, strip_repeated_discussion)
from finalize import (TABLE_HEADER, TABLE_SEPARATOR, apply_publish_content, candidate_result,
                      normalize_publish, render_row)
from publication_queue import PublicationQueue
from publication_runtime import atomic_json
from publish_history import PublishedHistory, locked_history, match_record
from query_schedule import search_text


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def collection_target(args):
    if not args.model or bool(args.brand) == bool(args.brand_catalog):
        raise ValueError('指定 --model，并选择 --brand 或 --brand-catalog')
    if args.brand:
        return dict(brand=args.brand, model=args.model, brand_aliases=[], aliases=[])
    catalog = brand_catalog.read_catalog(args.brand_catalog)
    for target in catalog['models']:
        if normalized(target['model']) == normalized(args.model):
            return target
    raise ValueError('名录没有该车型：' + args.model)


def prepare_review(entry, out, backend):
    item = entry['item']
    path = Path(item['video']['file'])
    if not path.is_file() or not path.stat().st_size:
        raise ValueError('采集视频文件缺失或为空')
    entry['video_sha256'] = file_digest(path)
    item['frames'] = item.get('frames') or backend.frames(item, out)
    entry.update(status='awaiting_review')
    entry.pop('error', None)


def review_items(state, review):
    if not isinstance(review, dict) or review.get('job_id') != state['job_id']:
        raise ValueError('审核文件缺失或 job_id 不匹配')
    decisions = review.get('items')
    if not isinstance(decisions, dict):
        raise ValueError('审核 items 必须以原帖 ID 为键')
    unknown = decisions.keys() - state['items'].keys()
    if unknown:
        raise ValueError('审核包含未知候选：' + '、'.join(sorted(unknown)))
    errors = {}
    target = state['target']
    for source_id, result in decisions.items():
        entry = state['items'][source_id]
        if entry['status'] != 'awaiting_review':
            continue
        try:
            if not isinstance(result, dict):
                raise ValueError('逐条审核须填写为对象')
            if result.get('decision') == 'pending':
                continue
            if (result.get('video_sha256') != entry['video_sha256']
                    or file_digest(entry['item']['video']['file']) != entry['video_sha256']):
                raise ValueError('审核素材哈希不匹配')
            if result.get('decision') == 'rejected':
                reason = result.get('reason')
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError('淘汰必须填写原因')
                entry.update(status='rejected', reason=reason.strip())
                continue
            if result.get('decision') != 'approved':
                raise ValueError('审核 decision 必须为 approved、rejected 或 pending')
            raw_copy = result.get('publish')
            if not isinstance(raw_copy, dict) or not raw_copy.get('topics'):
                raise ValueError('必须显式填写话题')
            if not all(isinstance(raw_copy.get(key), str) for key in ('title', 'body')):
                raise ValueError('标题和正文必须是字符串')
            content = normalize_publish({source_id: {**raw_copy, 'score_breakdown': None}})
            copy = content[source_id]
            if not brand_catalog.matches_identity(target, copy):
                raise ValueError('审核车型与任务不符。请核实具体版本，或淘汰并说明原因')
            copy.update(brand=target['brand'], model=target['model'],
                        title=compact_video_title(copy['title']),
                        body=strip_repeated_discussion(copy['body'], copy['discussion_question']))
            if target.get('first_year') is not None:
                copy['first_year'] = target['first_year']
            item = dict(entry['item'])
            item['topic_series_id'] = BRAND_SERIES
            apply_publish_content([item], content)
            item['verification'] = dict(result.get('verification') or {}, status='approved',
                                        video_sha256=entry['video_sha256'])
            validate_collection_review(item, target)
            item['body'] = candidate_result(item)['body']
            entry.update(status='approved', item=item)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors[source_id] = str(exc)
    return errors


def review_request(state, out):
    items = {}
    target = state['target']
    for source_id, entry in state['items'].items():
        if entry['status'] != 'awaiting_review':
            continue
        item = entry['item']
        items[source_id] = dict(
            source=item['candidate'], video=item['video'], frames=item['frames'],
            video_sha256=entry['video_sha256'], decision='pending', reason='',
            publish=dict(brand=target['brand'], model=target['model'], first_year=target.get('first_year'),
                         title='', title_detail='', body='', topics=[], verifiable_fact='', fact_source='',
                         discussion_question='', file_topic=''),
            verification=dict(presentation='', visual_evidence='', fact_evidence=''),
        )
    if not items:
        return None
    path = out / 'review_request.json'
    atomic_json(path, dict(job_id=state['job_id'], brand_target=target, items=items))
    return str(path)


def collect_model(args, backend):
    target = collection_target(args)
    out = brand_catalog.model_dir(args.out, target) / 'collection'
    queue = PublicationQueue(history_path=args.publish_history)
    history = PublishedHistory(args.publish_history)
    with locked_history(out / 'collection.lock', create=True):
        return collect_locked(args, backend, out, target, queue, history)


def collect_locked(args, backend, out, target, queue, history):
    state_path = out / 'collection_state.json'
    receipt_path = out / 'candidate_receipt.json'
    state = read_json(state_path)
    pool = {entry['source_id']: entry for entry in queue.records()}
    records = history.records()
    if state is None:
        if args.review_file:
            raise ValueError('请先运行采集命令生成审核请求')
        legacy = read_json(receipt_path, {})
        queries = [(query, None, 1) for query in brand_catalog.model_plan(target)['queries']]
        search_done = legacy.get('status') == 'complete'
        state = dict(job_id=uuid.uuid4().hex, target=target, queue_file=str(queue.path),
                     history_file=str(history.path), since=args.since,
                     pages_used=legacy.get('pages_used', 0) if search_done else 0,
                     queries=[] if search_done else queries, items={})
        for source_id, entry in pool.items():
            if (entry.get('status') == 'queued' and entry.get('collection_only')
                    and entry.get('out_dir') == str(out)):
                state['items'][source_id] = dict(status='downloaded', item=entry['item'],
                                                revision=entry.get('revision', 1))
    elif (state['target'] != target or state['queue_file'] != str(queue.path)
          or state['history_file'] != str(history.path) or state['since'] != args.since):
        raise ValueError('恢复采集须沿用车型资料、队列、历史及检索起始日期')

    def checkpoint():
        atomic_json(state_path, state)

    deadline = time.monotonic() + args.max_seconds
    failures = []
    checkpoint()
    if args.review_file:
        errors = review_items(state, read_json(Path(args.review_file)))
        failures.extend(dict(source_id=source_id, error=error) for source_id, error in errors.items())
        checkpoint()
    else:
        page_limit = args.max_pages or brand_catalog.DEFAULT_MODEL_PAGE_LIMIT
        makes = dict(model=target['brand'], aliases=target['brand_aliases'])
        try:
            while state['queries'] and state['pages_used'] < page_limit and time.monotonic() < deadline:
                query, cursor, page = state['queries'][0]
                args.operation_timeout = max(1, int(deadline - time.monotonic()))
                tweets, following = backend.search(search_text(query, {'since': args.since}), cursor, page, out)
                for tweet in tweets:
                    source_id = str(tweet.get('id') or '')
                    if not source_id or source_id in pool or source_id in state['items']:
                        continue
                    caption = tweet.get('text') or ''
                    if not all(brand_catalog.matches_caption(identity, caption) for identity in (makes, target)):
                        continue
                    candidate = finder.normalize(tweet, None, [], False)
                    if not candidate or match_record(records, source_id):
                        continue
                    candidate['topic_series_id'] = BRAND_SERIES
                    if engagement_total(candidate) > 100:
                        state['items'][source_id] = dict(status='discovered', item=dict(candidate=candidate))
                state['queries'].pop(0)
                if following and following != cursor:
                    state['queries'].append((query, following, page + 1))
                state['pages_used'] += 1
                checkpoint()
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            failures.append(dict(error=str(exc)))
        for source_id, entry in state['items'].items():
            if entry['status'] not in ('discovered', 'downloaded'):
                continue
            if time.monotonic() >= deadline:
                break
            args.operation_timeout = max(1, int(deadline - time.monotonic()))
            try:
                if entry['status'] == 'discovered':
                    video, error = backend.download(entry['item']['candidate'], out)
                    if error or not video:
                        raise ValueError(error or '未取得视频')
                    entry['item']['video'] = video
                    entry['status'] = 'downloaded'
                    checkpoint()
                prepare_review(entry, out, backend)
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                entry['error'] = str(exc)
            checkpoint()

    unfinished = state['queries'] and state['pages_used'] < (args.max_pages or brand_catalog.DEFAULT_MODEL_PAGE_LIMIT)
    waiting = review_request(state, out)
    for source_id, entry in state['items'].items():
        if entry['status'] in ('discovered', 'downloaded'):
            failures.append(dict(source_id=source_id, error=entry.get('error') or '等待下载或抽帧'))
    if unfinished:
        failures.append(dict(error='检索页尚未处理完，请沿用目录继续'))
    status = 'awaiting_review' if waiting else 'incomplete' if failures else 'complete'
    approved = {source_id: entry for source_id, entry in state['items'].items() if entry['status'] == 'approved'}
    rejected = [dict(source_id=source_id, reason=entry['reason']) for source_id, entry in state['items'].items()
                if entry['status'] == 'rejected']
    materials = []
    if status == 'complete':
        revisions = {source_id: entry['revision'] for source_id, entry in approved.items() if 'revision' in entry}
        # 重读发布历史，避免把审核期间已发布的原帖计作新增。
        records = history.records()
        if any(match_record(records, source_id) for source_id in approved):
            raise ValueError('审核期间有素材进入发布历史，请核对原帖后恢复')
        queue.enqueue([entry['item'] for entry in approved.values()], out, collected=True,
                      collection_revisions=revisions)
        for result in rejected:
            source_id = result['source_id']
            entry = state['items'][source_id]
            if 'revision' in entry and pool.get(source_id, {}).get('status') != 'withdrawn':
                queue.withdraw(source_id, entry['revision'], result['reason'])
        saved = {entry['source_id']: entry for entry in queue.records()}
        for source_id, entry in approved.items():
            stored = saved.get(source_id, {})
            if (stored.get('out_dir') != str(out) or stored.get('status') != 'queued'
                    or not stored.get('collection_only') or stored.get('title') != entry['item']['title']
                    or stored.get('item') != entry['item']):
                raise ValueError('候选池回读与已审核素材不一致：' + source_id)
            materials.append(candidate_result(stored['item']))
        lines = ['# 采集素材列表', '', '已入候选池，未发布。', '',
                 TABLE_HEADER.replace('| 标题 |', '| 视频标题 |'), TABLE_SEPARATOR]
        lines.extend(render_row(saved[source_id]['item']) for source_id in approved)
        (out / 'REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    result = dict(status=status, job_id=state['job_id'], pages_used=state['pages_used'],
                  materials=materials, rejected=rejected, failures=failures, published=0)
    if waiting:
        result['review_request'] = waiting
    atomic_json(receipt_path, result)
    return result

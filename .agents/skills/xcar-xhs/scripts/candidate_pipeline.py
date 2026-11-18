#!/usr/bin/env python3
"""自动选题、增量检索、审核交接、定稿与纯入池；从不调用浏览器发布。"""
import argparse
import copy
import datetime
import json
import re
import sys
import subprocess
import time
import uuid
from itertools import zip_longest
from pathlib import Path

import finder
from candidate_pool import enqueue_finalized
from candidate_review import (ACTION_SERIES, EDITORIAL_REVIEW_VERSION, candidate_sort_key,
                              content_digest, file_digest, validate_editorial_review)
from content_policy import SCORE_THRESHOLD, content_duplicate, content_identity, normalize_brand
from finalize import apply_publish_content, finalize, load_publish, recover_finalization
from publication_queue import PublicationQueue, WAITING_STATUSES
from publication_runtime import atomic_json
from publish_history import PublishedHistory, default_history_path, locked_history, ACTIVE_STATUSES
from monitor_telemetry import record_event
from topic_planner import build_topic_plan, PILLARS, NEGATIVE_FILTERS, series_queries
from topic_policy import forbidden_topic_match
from xiaohongshu_publisher import latest_records, validate_content


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read_json(path, default):
    return json.loads(Path(path).read_text()) if Path(path).exists() else copy.deepcopy(default)


class Backend:
    """真实 X 适配器，测试可注入假后端而不访问网络。"""
    def __init__(self, args):
        self.args = args
        self.ready = False

    def prepare(self):
        if self.ready:
            return
        self.xfetch = finder.resolve_xfetch(self.args.xfetch)
        self.python = finder.resolve_python(self.args.python)
        self.env = finder.prepare_x_auth(self.args, finder.load_env(self.args), self.python, self.xfetch)
        ok, detail = finder.auth_ok(self.python, self.xfetch, self.env)
        if not ok:
            raise RuntimeError('X 登录态不可用：' + finder.redact_session_values(detail[:200], self.env))
        self.ready = True

    def search(self, query, cursor, page, out):
        self.prepare()
        return finder.search_page(self.python, self.xfetch, query, self.args, self.env, out / 'raw', cursor, page)

    def download(self, candidate, out):
        self.prepare()
        return finder.download_video(candidate, out / 'videos', self.args)

    def frames(self, item, out):
        return finder.extract_frames(item['candidate'], item['video']['file'], out / 'frames', 5,
                                     timeout=self.args.operation_timeout)


def series_lookup(series_id):
    for pillar_id, pillar in PILLARS.items():
        for series in pillar['series']:
            if series['id'] == series_id:
                return {'id': series['id'], 'label': series['label'],
                        'pillar': pillar_id, 'pillar_label': pillar['label'],
                        'duration': pillar['duration'],
                        'queries': series_queries(series)}
    return None


def series_pillar(series_id):
    spec = series_lookup(series_id)
    return spec['pillar'] if spec else None


def apply_series_override(plan, series_ids):
    """按指定系列重建选题计划；支持逗号分隔的多个系列合并为一个批次。"""
    ids = [part.strip() for part in str(series_ids).split(',') if part.strip()]
    specs = []
    for series_id in ids:
        spec = series_lookup(series_id)
        if spec is None:
            valid = sorted(s['id'] for p in PILLARS.values() for s in p['series'])
            raise ValueError('未知系列：%s；可选：%s' % (series_id, ', '.join(valid)))
        if spec['id'] not in [existing['id'] for existing in specs]:
            specs.append(spec)
    if not specs:
        raise ValueError('--series 不能为空')
    pillars = {spec['pillar'] for spec in specs}
    # 先轮流取各系列的第一组词，再取第二组，避免小预算被首个系列用尽。
    query_series = {}
    for row in zip_longest(*(spec['queries'] for spec in specs)):
        for spec, query in zip(specs, row):
            if query:
                query_series.setdefault(query, spec['id'])
    queries = sorted(query_series, key=lambda query: 'filter:videos' not in query)
    if len(specs) == 1:
        spec = specs[0]
        plan.update(pillar=spec['pillar'], pillar_label=spec['pillar_label'],
                    series_id=spec['id'], series_label=spec['label'],
                    target_duration_seconds=spec['duration'], queries=queries,
                    prompt='指定选题｜%s｜%s｜候选生产' % (spec['pillar_label'], spec['label']))
    else:
        mixed = len(pillars) > 1
        plan.update(
            pillar=specs[0]['pillar'] if not mixed else 'mixed',
            pillar_label='多系列组合' if mixed else PILLARS[specs[0]['pillar']]['label'],
            series_id='+'.join(spec['id'] for spec in specs),
            series_label=' / '.join(spec['label'] for spec in specs),
            target_duration_seconds=specs[0]['duration'], queries=queries,
            prompt='指定选题｜多系列（%s）｜候选生产' % '、'.join(spec['label'] for spec in specs),
        )
    plan['series_specs'] = specs
    plan['query_series'] = query_series
    return plan


def make_queries(plan):
    # 车型、动作与排除条件共同定义系列，拆出单个短语会丢失检索范围。
    return list(dict.fromkeys(plan['queries']))


def retire_unscoped_queries(state, explicit_queries):
    """恢复旧任务时归档自动拆出的短语查询，保留原查询和显式补充查询。"""
    plan_queries = state['plan']['queries']
    preserved = set(plan_queries) | set(explicit_queries)
    legacy = {
        '"%s" filter:media %s' % (phrase, NEGATIVE_FILTERS)
        for group in plan_queries for phrase in re.findall(r'"([^"]+)"', group)
    }
    active = []
    for query in state['queries']:
        if query['query'] in legacy and query['query'] not in preserved:
            state.setdefault('retired_queries', []).append({
                **query, 'reason': '旧版短语扩展丢失系列约束或包含正向排除词',
            })
        else:
            active.append(query)
    state['queries'] = active


def review_request(state, out):
    requests = {}
    for source_id, entry in state['items'].items():
        if entry['status'] != 'awaiting_review':
            continue
        item = entry['item']
        requests[source_id] = {
            'series_id': entry.get('series_id') or state['plan']['series_id'],
            'source': item['candidate'], 'video': item['video'], 'frames': item['frames'],
            'video_sha256': entry['video_sha256'],
            'decision': 'pending', 'reason': '',
            'publish': {'title': '', 'body': '', 'brand': '', 'model': '', 'file_topic': '',
                        'modification_project': '', 'title_detail': '', 'verifiable_fact': '',
                        'fact_source': '', 'discussion_question': '', 'topics': [], 'ai_disclosure': '',
                        'score_breakdown': {}},
            'verification': {'presentation': '', 'reviewer': '', 'reviewed_at': '',
                             'visual_evidence': '', 'first_three_seconds_evidence': '',
                             'fact_evidence': '',
                             'series_evidence': '', 'score_reasons': {}},
        }
        if state['plan'].get('editorial_review_version'):
            requests[source_id]['verification']['editorial'] = {
                'opening_action_at_sec': None, 'discussion_basis': '', 'follow_up_topic': '',
            }
    atomic_json(out / 'review_request.json', {'job_id': state['job_id'], 'topic_plan': state['plan'],
                                             'items': requests})
    return requests


def apply_reviews(state, path, out, registry):
    review = read_json(path, None)
    if review is None or review.get('job_id') != state['job_id']:
        raise ValueError('审核文件缺失或 job_id 不匹配')
    for source_id, result in review.get('items', {}).items():
        entry = state['items'].get(source_id)
        if entry is None:
            raise ValueError('审核包含未知候选：' + source_id)
        if entry['status'] != 'awaiting_review' or result.get('decision') == 'pending':
            continue
        item = entry['item']
        if result.get('video_sha256') != file_digest(item['video']['file']):
            raise ValueError('审核素材哈希不匹配：' + source_id)
        if result.get('decision') == 'rejected':
            if not str(result.get('reason') or '').strip():
                raise ValueError('淘汰必须填写原因')
            entry.update(status='rejected', reason=result['reason'])
            registry['rejected'][source_id] = {'reason': result['reason'], 'at': now()}
            record_event('candidate', 'visual_fact_review', state['job_id'], source_id,
                         status='rejected', event_id='%s:%s:review' % (state['job_id'], source_id),
                         details={'reason': result['reason']},
                         path=Path(state['history_file']).with_name('monitor_events.jsonl'))
            continue
        if result.get('decision') != 'approved':
            raise ValueError('审核 decision 必须为 approved、rejected 或 pending')
        content_file = out / '.review-content.json'
        atomic_json(content_file, {source_id: result.get('publish', {})})
        content = load_publish(content_file)
        approved = copy.deepcopy(item)
        apply_publish_content([approved], content)
        approved['verification'] = {**result.get('verification', {}), 'status': 'approved',
                                    'video_sha256': result['video_sha256']}
        item_series = entry.get('series_id') or state['plan'].get('series_id')
        approved.update(topic_pillar=series_pillar(item_series) or state['plan']['pillar'],
                        topic_series_id=item_series,
                        topic_strategy_version=state['plan']['strategy_version'], candidate_job_id=state['job_id'])
        approved['verification']['content_sha256'] = content_digest(approved)
        forbidden = forbidden_topic_match(approved)
        reason = ''
        if forbidden:
            reason = '禁选内容：' + forbidden
        elif approved.get('score', 0) < SCORE_THRESHOLD:
            reason = '核验评分低于 %d' % SCORE_THRESHOLD
        elif state['plan'].get('editorial_review_version'):
            editorial = validate_editorial_review(approved)
            if item_series == ACTION_SERIES and editorial['opening_action_at_sec'] is None:
                reason = '汽车机关图鉴前 3 秒未见关键动作'
        if reason:
            entry.update(status='rejected', reason=reason, item=approved, publish=content[source_id])
            registry['rejected'][source_id] = {'reason': reason, 'at': now()}
            record_event('candidate', 'visual_fact_review', state['job_id'], source_id,
                         status='rejected', event_id='%s:%s:review' % (state['job_id'], source_id),
                         details={'reason': reason},
                         path=Path(state['history_file']).with_name('monitor_events.jsonl'))
            continue
        validate_content(approved)
        entry.update(status='approved', item=approved, publish=content[source_id])
        record_event('candidate', 'visual_fact_review', state['job_id'], source_id,
                     status='approved', event_id='%s:%s:review' % (state['job_id'], source_id),
                     details={'score': approved.get('score')},
                     path=Path(state['history_file']).with_name('monitor_events.jsonl'))


def reconcile_approved(state, history, queue):
    """每次恢复及定稿前重新判重，淘汰结果不会丢失。"""
    accepted, brands = [], set()
    records, queued = latest_records(history.records()), queue.records()
    approved = sorted((e for e in state['items'].values() if e['status'] == 'approved'),
                      key=lambda e: candidate_sort_key(e['item']))
    for entry in approved:
        item = entry['item']
        source_id = str(item['candidate']['id'])
        existing = next((e for e in queued if e['source_id'] == source_id), None)
        if existing and existing.get('item', {}).get('candidate_job_id') == state['job_id']:
            accepted.append(item)  # 入池后回执写入中断，幂等恢复。
            brands.add(normalize_brand(item['brand']))
            continue
        duplicate = content_duplicate(content_identity(item), records, statuses=ACTIVE_STATUSES)
        duplicate = duplicate or content_duplicate(content_identity(item), queued, days=None, statuses=WAITING_STATUSES)
        duplicate = duplicate or content_duplicate(content_identity(item), accepted, days=None)
        brand = normalize_brand(item['brand'])
        reason = ('原帖已发布或已入池' if history.check(source_id=source_id) or existing else
                  duplicate['reason'] if duplicate else '同品牌每批最多一条' if brand in brands else '')
        if reason:
            entry.update(status='rejected', reason=reason)
        else:
            accepted.append(item)
            brands.add(brand)
    return accepted


def write_manifest(state, out, history):
    selected, rejected, content = [], [], {}
    for source_id, entry in state['items'].items():
        if entry['status'] == 'approved':
            selected.append(entry['item'])
            content[source_id] = entry['publish']
        elif entry['status'] == 'rejected' and entry.get('item', {}).get('video'):
            item = copy.deepcopy(entry['item'])
            item['reason'] = entry['reason']
            rejected.append(item)
            content[source_id] = entry.get('publish') or {
                'title': '淘汰素材 ' + source_id, 'body': entry['reason'], 'brand': '汽车',
                'file_topic': source_id, 'topics': ['#汽车', '#素材核验']}
    manifest = {'schema_version': 2, 'minimum_selected': state.get('min_selected', 3), 'selection_target': state['target'],
                'selection_complete': False, 'topic_plan': state['plan'],
                'publish_history_file': str(history.path), 'downloads': selected, 'rejected': rejected}
    atomic_json(out / 'manifest.json', manifest)
    atomic_json(out / 'publish.json', content)


def run_pipeline(args, backend=None):
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    history = PublishedHistory(args.publish_history)
    queue = PublicationQueue(history_path=history.path)
    registry_path = history.path.with_name('candidate_jobs.json')
    state_path = out / 'candidate_state.json'
    deadline = time.monotonic() + args.max_seconds
    backend = backend or Backend(args)
    # 全局生产锁保护任务预约与源 ID，占用至本次调用结束，不跨审核等待持锁。
    with locked_history(registry_path.with_suffix('.lock'), create=True):
        registry = read_json(registry_path, {'jobs': {}, 'rejected': {}})
        state = read_json(state_path, None)
        # 全局登记是原子检查点的主副本，本地文件是方便审核的镜像。
        registered = next((job for job in registry['jobs'].values() if job.get('out_dir') == str(out)), None)
        if registered and registered.get('checkpoint'):
            state = copy.deepcopy(registered['checkpoint'])
        # 定稿门槛：显式 --min-selected 优先，其次沿用任务已存的值，最后回落默认 3。
        min_selected = getattr(args, 'min_selected', None)
        if min_selected is None:
            min_selected = (state or {}).get('min_selected', 3)
        if min_selected < 1:
            raise ValueError('--min-selected 必须为正整数')
        if args.top < min_selected:
            raise ValueError('--top(%d) 不能小于 --min-selected(%d)' % (args.top, min_selected))
        if state is not None:
            state['min_selected'] = min_selected
        if state is None:
            if (out / 'manifest.json').exists():
                raise ValueError('目录已有其他任务 manifest，请用新的候选任务目录')
            inventory_audit = queue.audit()
            plan = build_topic_plan(history.records(), queue.records(), list(registry['jobs'].values()))
            plan['series_episode'] = None  # 发布期数由发布计划分配。
            plan['prompt'] = '自动选题｜%s｜%s｜候选生产' % (plan['pillar_label'], plan['series_label'])
            selected = getattr(args, 'series', None)
            if not selected and not args.query and args.top > 1:
                selected = ','.join(plan['refill_series'][:args.top])
            if selected:
                plan = apply_series_override(plan, selected)
            plan['editorial_review_version'] = EDITORIAL_REVIEW_VERSION
            state = {'inventory_held': len(inventory_audit), 'settings': {key: getattr(args, key) for key in ('sort', 'type', 'since', 'min_likes', 'min_reposts', 'max_duration')}, 'job_id': uuid.uuid4().hex, 'status': 'searching', 'phase': 'collecting', 'target': args.top, 'min_selected': min_selected,
                     'history_file': str(history.path), 'plan': plan, 'items': {}, 'pages': 0,
                     'downloads': 0, 'queries': [{'query': q, 'cursor': None, 'pages': 0, 'done': False,
                                                  'series_id': (plan.get('query_series') or {}).get(q)
                                                  or plan['series_id']}
                                                 for q in make_queries(plan)], 'created_at': now()}
            record_event('candidate', 'topic_strategy', state['job_id'],
                         event_id=state['job_id'] + ':topic_strategy',
                         details={'series_id': plan.get('series_id'), 'pillar': plan.get('pillar')},
                         path=history.path.with_name('monitor_events.jsonl'))
        elif state['history_file'] != str(history.path):
            raise ValueError('恢复任务不能切换发布历史')
        for key, value in state.get('settings', {}).items():
            setattr(args, key, value)
        retire_unscoped_queries(state, args.query)
        for query in args.query:
            if query not in [q['query'] for q in state['queries']]:
                state['queries'].append({'query': query, 'cursor': None, 'pages': 0, 'done': False,
                                         'series_id': state['plan']['series_id'].split('+')[0]})

        def checkpoint():
            state['updated_at'] = now()
            registry['jobs'][state['job_id']] = {
                'plan': state['plan'], 'target': state['target'], 'status': state['status'],
                'out_dir': str(out), 'source_ids': [key for key, value in state['items'].items()
                    if value['status'] in ('downloading', 'downloaded', 'awaiting_review', 'approved', 'frame_failed')],
                'updated_at': state['updated_at'], 'checkpoint': copy.deepcopy(state)}
            atomic_json(registry_path, registry)
            atomic_json(state_path, state)
            atomic_json(out / 'topic_plan.json', state['plan'])

        def finish(status, error=None):
            state['status'] = status
            checkpoint()
            requests = review_request(state, out)
            result = {'status': status, 'job_id': state['job_id'], 'published': 0,
                      'inventory_held': state.get('inventory_held', 0),
                      'approved': sum(e['status'] == 'approved' for e in state['items'].values()),
                      'awaiting_review': len(requests), 'target': state['target'],
                      'rejected': sum(e['status'] == 'rejected' for e in state['items'].values()),
                      'retryable_failed': sum(e['status'] in ('download_failed', 'frame_failed') for e in state['items'].values()),
                      'pages_used': state['pages'], 'downloads_attempted': state['downloads'],
                      'review_request': str(out / 'review_request.json'), 'queue_file': str(queue.path),
                      'error': error, 'enqueue': state.get('enqueue')}
            atomic_json(out / 'candidate_receipt.json', result)
            return result

        checkpoint()
        if args.cancel:
            return finish('cancelled')
        if state['status'] in ('complete', 'cancelled'):
            return finish(state['status'])
        try:
            # 先恢复定稿，绝不能用旧路径状态覆盖进行到一半的重命名事务。
            with locked_history(out / '.finalize.lock', create=True):
                recover_finalization(out)
            if state.get('phase') == 'finalizing':
                manifest = read_json(out / 'manifest.json', {})
                if not manifest.get('finalized_at'):
                    if not manifest:
                        write_manifest(state, out, history)
                    finalize(out, out / 'publish.json')
                    manifest = read_json(out / 'manifest.json', {})
                if manifest.get('finalized_at'):
                    for item in manifest['downloads']:
                        state['items'][str(item['candidate']['id'])]['item'] = item
                    state['enqueue'] = enqueue_finalized(out, history, queue)
                    for source_id in (state['enqueue'].get('source_ids') or []):
                        record_event('candidate', 'copy_finalize_enqueue', state['job_id'], source_id,
                                     event_id='%s:%s:enqueue' % (state['job_id'], source_id),
                                     path=history.path.with_name('monitor_events.jsonl'))
                    return finish('complete')
            for entry in state['items'].values():
                if entry['status'] == 'downloading':
                    entry['status'] = 'discovered'  # 上次进程在下载中断，可恢复该次任务
            # 媒体改变会使旧帧和审核失效；重新抽帧后生成新的审核请求。
            for entry in state['items'].values():
                if entry['status'] not in ('approved', 'awaiting_review'):
                    continue
                item = entry['item']
                if not Path(item['video']['file']).is_file():
                    entry.update(status='download_failed', reason='媒体文件丢失，需重下并重审', retryable=True)
                    entry.pop('publish', None)
                    item.pop('video', None)
                    item.pop('verification', None)
                    item['frames'] = []
                    continue
                expected = entry.get('video_sha256') or (item.get('verification') or {}).get('video_sha256')
                if file_digest(item['video']['file']) != expected:
                    entry.update(status='downloaded', reason='媒体变化，旧审核失效，需重新抽帧审核')
                    entry.pop('publish', None)
                    item.pop('verification', None)
                    item['frames'] = []
            if args.retry_failed:
                for entry in state['items'].values():
                    if entry['status'] in ('download_failed', 'frame_failed'):
                        entry['status'] = 'downloaded' if entry.get('item', {}).get('video') else 'discovered'
                checkpoint()
            if args.review_file:
                apply_reviews(state, args.review_file, out, registry)
                checkpoint()
            approved = reconcile_approved(state, history, queue)
            checkpoint()
            while len(approved) < state['target']:
                if any(e['status'] == 'awaiting_review' for e in state['items'].values()):
                    return finish('awaiting_review')
                if time.monotonic() >= deadline:
                    return finish('budget_exhausted', '本次执行时间预算已耗尽，保留进度')
                args.operation_timeout = max(1, int(deadline - time.monotonic()))
                # 下载前重新核对历史、队列和其他任务预约。
                occupied = {e['source_id'] for e in queue.records()}
                for job_id, job in registry['jobs'].items():
                    if job_id != state['job_id'] and job['status'] not in ('complete', 'cancelled'):
                        occupied.update(job.get('source_ids', []))
                candidates = [e for e in state['items'].values() if e['status'] in ('discovered', 'downloaded')]
                candidates.sort(key=lambda e: -finder.to_int(e['item']['candidate'].get(args.sort)))
                if candidates:
                    # 每次准备补足目标所需的审核任务，然后交给 Agent。
                    pending = 0
                    for entry in candidates:
                        if len(approved) + pending >= state['target']:
                            break
                        if time.monotonic() >= deadline:
                            break
                        args.operation_timeout = max(1, int(deadline - time.monotonic()))
                        item = entry['item']
                        source_id = str(item['candidate']['id'])
                        engagement_reason = finder.engagement_filter_reason(
                            item['candidate'], args.min_likes, args.min_reposts)
                        if engagement_reason:
                            entry.update(status='skipped',
                                         reason='互动不足，下载前跳过（%s）' % engagement_reason)
                            checkpoint()
                            continue
                        if source_id in occupied or history.check(source_id=source_id):
                            entry.update(status='skipped', reason='下载前发现重复或其他任务占用')
                            checkpoint()
                            continue
                        if entry['status'] == 'discovered':
                            if state['downloads'] >= args.max_downloads:
                                return finish('budget_exhausted', '累计下载尝试预算耗尽；可提高 --max-downloads 后恢复')
                            state['downloads'] += 1
                            entry['status'] = 'downloading'
                            checkpoint()
                            try:
                                video, error = backend.download(item['candidate'], out)
                            except Exception as exc:
                                video, error = None, str(exc)
                            if not video:
                                entry.update(status='download_failed', reason=str(error), retryable=True)
                                checkpoint()
                                continue
                            item['video'] = video
                            entry['status'] = 'downloaded'
                            checkpoint()
                        reason = finder.reject_overlong_video(item['video'], out / 'rejected', args.max_duration)
                        if reason:
                            entry.update(status='rejected', reason=reason)
                            registry['rejected'][source_id] = {'reason': reason, 'at': now()}
                            checkpoint()
                            continue
                        if time.monotonic() >= deadline:
                            return finish('budget_exhausted', '时间预算耗尽，已下载素材保留，下次继续抽帧')
                        args.operation_timeout = max(1, int(deadline - time.monotonic()))
                        try:
                            item['frames'] = backend.frames(item, out)
                            if not item['frames']:
                                raise ValueError('抽帧未产出有效图片')
                            entry['status'] = 'awaiting_review'
                            entry['video_sha256'] = file_digest(item['video']['file'])
                            pending += 1
                            record_event('candidate', 'download_frames', state['job_id'], source_id,
                                         event_id='%s:%s:download_frames' % (state['job_id'], source_id),
                                         details={'frame_count': len(item['frames'])},
                                         path=history.path.with_name('monitor_events.jsonl'))
                        except Exception as exc:
                            entry.update(status='frame_failed', reason=str(exc), retryable=True)
                        checkpoint()
                    if pending:
                        return finish('awaiting_review')
                    continue
                available = [q for q in state['queries'] if not q['done']]
                if not available:
                    return finish('exhausted', '同系列检索已耗尽；可添加 --query 继续，不能降低核验标准')
                if state['pages'] >= args.max_pages:
                    return finish('budget_exhausted', '累计搜索页预算耗尽；可提高 --max-pages 后恢复')
                query = min(available, key=lambda q: q['pages'])
                state['pages'] += 1
                checkpoint()
                search_text = query['query']
                if args.min_likes and 'min_faves:' not in search_text:
                    search_text += ' min_faves:%d' % args.min_likes
                if args.since and 'since:' not in search_text:
                    search_text += ' since:' + args.since
                tweets, cursor = backend.search(search_text, query['cursor'], query['pages'] + 1, out)
                record_event('candidate', 'search_filter', state['job_id'],
                             event_id='%s:search:%d' % (state['job_id'], state['pages']),
                             details={'result_count': len(tweets), 'query_page': query['pages'] + 1},
                             path=history.path.with_name('monitor_events.jsonl'))
                previous = query['cursor']
                query.update(cursor=cursor, pages=query['pages'] + 1,
                             done=not tweets or not cursor or cursor == previous)
                for tweet in tweets:
                    if not tweet.get("id"):
                        continue
                    candidate = finder.normalize(tweet, re.compile(finder.CAR_LEXICON), [], True)
                    if not candidate:
                        continue
                    source_id = str(candidate['id'])
                    if source_id in state['items']:
                        continue
                    engagement_reason = finder.engagement_filter_reason(
                        candidate, args.min_likes, args.min_reposts)
                    reason = (engagement_reason or
                              registry['rejected'].get(source_id, {}).get('reason') or
                              ('原帖已入池或被预约' if source_id in occupied else '') or
                              ('已发布或待确认' if history.check(source_id=source_id) else '') or
                              forbidden_topic_match(candidate))
                    state['items'][source_id] = {'status': 'skipped' if reason else 'discovered',
                                                  'reason': reason,
                                                  'series_id': query.get('series_id') or state['plan'].get('series_id'),
                                                  'item': {'candidate': candidate}}
                checkpoint()
            state['status'] = 'finalizing'
            state['phase'] = 'finalizing'
            checkpoint()
            write_manifest(state, out, history)
            finalize(out, out / 'publish.json')
            state['enqueue'] = enqueue_finalized(out, history, queue)
            for source_id in (state['enqueue'].get('source_ids') or []):
                record_event('candidate', 'copy_finalize_enqueue', state['job_id'], source_id,
                             event_id='%s:%s:enqueue' % (state['job_id'], source_id),
                             path=history.path.with_name('monitor_events.jsonl'))
            return finish('complete')
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            if state.get('phase') == 'finalizing' and isinstance(exc, ValueError) and '至少要有' in str(exc):
                manifest = read_json(out / 'manifest.json', {})
                for item in manifest.get('rejected', []):
                    source_id = str(item['candidate']['id'])
                    if source_id in state['items']:
                        state['items'][source_id].update(status='rejected', reason=item['reason'], item=item)
                state['phase'] = 'collecting'
                return finish('needs_candidates', str(exc))
            return finish('blocked', str(exc))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, help='新任务目录；同目录再次运行恢复')
    parser.add_argument('--top', type=int, default=3)
    parser.add_argument('--min-selected', type=int, default=None,
                        help='本批定稿入池所需的最少核验入选数；默认 3，小批补池可显式设为 1')
    parser.add_argument('--query', action='append', default=[], help='追加同系列检索式')
    parser.add_argument('--sort', choices=sorted(finder.SORT_FIELDS), default='likes')
    parser.add_argument('--limit', type=int, default=25)
    parser.add_argument('--type', choices=('top', 'latest', 'media'), default='top')
    parser.add_argument('--since')
    parser.add_argument('--min-likes', type=int, default=0)
    parser.add_argument('--min-reposts', type=int, default=0)
    parser.add_argument('--delay', type=float, default=1.5)
    parser.add_argument('--max-height', type=int, default=1080)
    parser.add_argument('--max-duration', type=float, default=300)
    parser.add_argument('--publish-history', '--history-file', default=str(default_history_path()))
    parser.add_argument('--xfetch')
    parser.add_argument('--python')
    parser.add_argument('--auth-file')
    finder.add_session_arguments(parser, finder.DEFAULT_WEB_SESSION, finder.DEFAULT_BROWSER_PROFILE)
    parser.add_argument('--review-file', help='Agent 完成的 review_request.json 副本')
    parser.add_argument('--max-pages', type=int, default=12)
    parser.add_argument('--max-downloads', type=int, default=15)
    parser.add_argument('--max-seconds', type=int, default=600)
    parser.add_argument('--cancel', action='store_true')
    parser.add_argument('--retry-failed', action='store_true', help='显式重试下载/抽帧失败')
    parser.add_argument('--series', help='指定选题系列（新任务生效），用于一轮内并行多个选题策略')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.top < (args.min_selected or 3) or args.max_pages < 1 or args.max_downloads < 3 or args.max_seconds < 1:
        parser.error('目标条数不能小于 --min-selected（默认 3）；页数、下载和时间预算必须为正且下载预算至少 3')
    if args.max_duration <= 0 or args.min_likes < 0 or args.min_reposts < 0 or args.limit < 1 or args.delay < 0:
        parser.error('时长/页大小必须为正；互动门槛和延迟不能为负')
    args.auto_topic = args.xiaohongshu = True
    try:
        result = run_pipeline(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] in ('complete', 'cancelled') else 3 if result['status'] == 'awaiting_review' else 2
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'status': 'blocked', 'published': 0, 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())

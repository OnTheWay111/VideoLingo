#!/usr/bin/env python3
"""自动选题、增量检索、审核交接、定稿与纯入池；从不调用浏览器发布。"""
import argparse
import copy
import datetime
import hashlib
import json
import re
import sys
import subprocess
import time
import uuid
from itertools import zip_longest
from pathlib import Path

import finder
import brand_catalog
from caption_models import caption_duplicate, model_index
from candidate_pool import enqueue_finalized
from candidate_review import (ACTION_SERIES, EDITORIAL_REVIEW_VERSION, candidate_sort_key,
                              content_digest, file_digest, validate_editorial_review)
from content_policy import (BRAND_SERIES, SCORE_THRESHOLD, content_duplicate, content_identity,
                            engagement_total, normalize_brand, normalized, strip_repeated_discussion)
from finalize import apply_publish_content, candidate_result, finalize, load_publish, recover_finalization
from publication_queue import PublicationQueue, WAITING_STATUSES
from publication_runtime import atomic_json
from query_schedule import YIELD_VERSION, narrow_mechanism_query, prior_query_yield, search_text, select_query
from publish_history import PublishedHistory, default_history_path, locked_history, ACTIVE_STATUSES
from mechanism_duplicates import match_recent_mechanism, recent_mechanisms
from monitor_telemetry import record_event
from topic_planner import build_topic_plan, PILLARS, NEGATIVE_FILTERS, series_queries, reserved_source_ids
from topic_policy import color_only_match, forbidden_topic_match
from race_humor import (brief_candidate_matches, brief_queries, rotate_brief_family, LEAGUE_QUERIES, interview_queries, wrc_archive_reason, wrc_clip_rank, predownload_reason, RACE_FIELDS, RACE_TERMS, SERIES_ID as RACE_HUMOR, caption_incident,
                        covered_queries, incident_profile, race_index)
from series_match import SERIES, assess_series, reviewed_series_conflict
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
        linked = re.match(r'^wrc-post:(\d+)\b', query)
        if linked:
            proc = finder.run([self.python, self.xfetch, 'tweet', linked.group(1), '--format', 'jsonl'],
                              env=self.env, timeout=min(120, self.args.operation_timeout))
            if proc.returncode:
                raise RuntimeError('无法读取简报引用的 X 原帖：' +
                                   finder.redact_session_values((proc.stderr or '')[:200], self.env))
            raw = out / 'raw'
            raw.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256(query.encode()).hexdigest()[:16]
            (raw / (key + '_page1.jsonl')).write_text(proc.stdout or '')
            tweets = [json.loads(line) for line in (proc.stdout or '').split('\n') if line.strip()]
            return [tw for tw in tweets if isinstance(tw, dict) and str(tw.get('id')) == linked.group(1)], None
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


def narrow_mechanism_queries(state):
    """收紧宽泛查询，已有的原始结果和游标留在旧查询名下。"""
    known = {q['query'] for q in state['queries']}
    active = []
    for query in state['queries']:
        original = query['query']
        series_id = query.get('series_id') or state['plan']['series_id']
        narrowed = narrow_mechanism_query(original, series_id)
        if original == narrowed:
            active.append(query)
            continue
        if query['pages']:
            state.setdefault('retired_queries', []).append({
                **query, 'reason': '宽泛机构词已补车型，旧游标不适用于新查询',
            })
        if narrowed not in known:
            active.append({**({'explicit': True} if query.get('explicit') else {}),
                           'query': narrowed, 'cursor': None, 'pages': 0,
                           'done': False, 'series_id': series_id})
            known.add(narrowed)
    state['queries'] = active


def screen_series(entry, state):
    series = entry.get('series_id') or state['plan']['series_id']
    rank, conflict = assess_series(series, entry['item']['candidate'].get('text', ''))
    entry['series_priority'] = rank
    if conflict:
        source_id = str(entry['item']['candidate']['id'])
        state.setdefault('series_skips', {}).setdefault(source_id, {})[series] = conflict
        entry.update(status='skipped', series_mismatch=series, reason=conflict)


EMPTY_PAGE_LIMIT = 2


def next_search(state):
    if state.get('plan', {}).get('brand_target'):
        # 单车型只有一个名额，不能以首页已有批准作为翻页前提。
        active = [q for q in state['queries'] if not q['done'] and q.get('empty_pages', 0) < EMPTY_PAGE_LIMIT]
        return min(active, key=lambda q: (q['pages'], not q.get('explicit')), default=None)
    streaks = state.get('empty_series_pages', {})
    available = [q for q in state['queries'] if streaks.get(q.get('series_id'), 0) < EMPTY_PAGE_LIMIT
                 or (q.get('explicit') and q['pages'] == 0)]
    covered = state.get('covered_race_queries', {})
    available = [q for q in available if q['query'] not in covered]
    if state.get('wrc_brief'):
        linked = next((q for q in available if q.get('family') == 'linked' and not q['done']), None)
        if linked:
            return linked
    return select_query(available, state['query_yield'], state['items'])


def extend_refill(state):
    """自动任务在同栏目依库存顺序换系列，原游标、审核和页预算继续有效。"""
    remaining = state.get('fallback_series', [])
    if not remaining:
        return False
    series = remaining.pop(0)
    plan = state['plan']
    apply_series_override(plan, plan['series_id'].replace('+', ',') + ',' + series)
    old_queries = {q['query'] for q in state['queries']}
    state['queries'].extend({'query': q, 'series_id': series, 'pages': 0, 'cursor': None, 'done': False}
                            for q in plan['queries'] if q not in old_queries and plan['query_series'][q] == series)
    return True


def screen_caption(entry, state, models, mechanisms, race_events=None):
    if entry['status'] != 'discovered':
        return
    caption = entry['item']['candidate'].get('text', '')
    series = entry.get('series_id') or state['plan']['series_id']
    if state.get('wrc_brief') and not brief_candidate_matches(
            entry['item']['candidate'], entry.get('race_query', ''), state['wrc_brief']):
        entry.update(status='skipped', reason='原帖缺少该简报的车组或分站依据')
        return
    if series == RACE_HUMOR:
        linked = bool(state.get('wrc_brief') and entry.get('race_query') ==
                      'wrc-post:' + str(entry['item']['candidate']['id']))
        reason = predownload_reason(caption, state.get('race_leagues', ()),
                                    entry['item']['candidate'].get('author') or '', verified_link=linked)
        if state.get('race_leagues') == ['wrc']:
            reason = reason or wrc_archive_reason(caption)
            if entry.get('race_query') in state.get('retired_interview_queries', ()):
                reason = reason or '该事件检索已因停车采访换词'
            # A crew listing may contain "and" while explicitly naming the one competition car.
            subject = re.search(r'\b(?:in their (?:works )?|at the wheel of (?:the )?)([^,.!#]+?)'
                                r'(?= during| on (?:the|their)|[,.!#]|$)', caption, re.I)
            if subject:
                match = caption_duplicate(subject.group(1), models)
                if match:
                    entry.update(status='skipped', model_duplicate=match, reason='原帖明确参赛车型已重复')
                    return
        if reason:
            entry.update(status='skipped', reason=reason)
            return
    if series == RACE_HUMOR and race_events:
        match = caption_incident(caption, race_events)
        if match:
            entry.update(status='skipped', race_duplicate=match, reason='下载前同一正赛插曲：' + match['key'])
            return
    color = color_only_match(caption, series)
    if color:
        entry.update(status='skipped', color_only=color, reason='汽车机关图鉴：原帖仅展示车身变色')
        return
    plan = state['plan']
    # 扩展车型原帖可能同时提到基础款，比较对象在池内不能代替版本身份核验。
    expansion_target = plan.get('brand_target') if plan.get('brand_expansion') else None
    mechanism = match_recent_mechanism(caption, mechanisms)
    if mechanism and (not expansion_target or brand_catalog.matches_identity(expansion_target, mechanism)):
        entry.update(status='skipped', recent_duplicate=mechanism,
                     reason='下载前命中近 30 天同车型同机构：%s %s · %s' %
                            (mechanism['brand'], mechanism['model'], mechanism['mechanism']))
        return
    match = caption_duplicate(caption, models)
    if match and (not expansion_target or brand_catalog.matches_identity(expansion_target, match)):
        entry.update(status='skipped', model_duplicate=match,
                     reason='下载前同车型（%s）：%s %s · %s' %
                            (match['origin'], match['brand'], match['model'], match['source_id']))


def review_request(state, out):
    requests = {}
    for source_id, entry in state['items'].items():
        if entry['status'] != 'awaiting_review':
            continue
        item = entry['item']
        series = entry.get('series_id') or state['plan']['series_id']
        requests[source_id] = {
            'series_id': series, 'series_question': SERIES.get(series, ('', ''))[0],
            'source': item['candidate'], 'video': item['video'], 'frames': item['frames'],
            'video_sha256': entry['video_sha256'],
            'decision': 'pending', 'reason': '',
            'publish': {'title': '', 'body': '', 'brand': '', 'model': '', 'first_year': None, 'file_topic': '',
                        'modification_project': '', 'title_detail': '', 'verifiable_fact': '',
                        'fact_source': '', 'discussion_question': '', 'topics': [], 'ai_disclosure': '',
                        'score_breakdown': {}},
            'verification': {'presentation': '', 'reviewer': '', 'reviewed_at': '',
                             'visual_evidence': '', 'first_three_seconds_evidence': '',
                             'fact_evidence': '',
                             'series_evidence': '', 'score_reasons': {}},
        }
        if state['plan'].get('brand_target'):
            requests[source_id]['publish']['first_year'] = state['plan']['brand_target']['first_year']
            requests[source_id]['series_question'] = '画面中哪些特征与名录来源能确认指定车型？'
            expansion = state['plan'].get('brand_expansion')
            if expansion:
                requests[source_id]['series_question'] += (
                    f"请核对车型入选依据（{expansion['reason']}）及辨认点（{expansion['visual_cue']}）。"
                    '写明如何从实车及出处确认具体版本。')
        if state['plan'].get('editorial_review_version'):
            requests[source_id]['verification']['editorial'] = {
                'opening_action_at_sec': None, 'discussion_basis': '', 'follow_up_topic': '',
            }
            if series == RACE_HUMOR:
                editorial = requests[source_id]['verification']['editorial']
                editorial.update(dict.fromkeys(RACE_FIELDS, ''))
                if state.get('race_incident_version'):
                    editorial['race_incident'] = {'key': '', 'anchors': []}
            if series == 'rally_road_cars' and state['plan']['strategy_version'] >= 10:
                requests[source_id]['verification']['editorial']['road_version_detail'] = ''
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
            if not state['plan'].get('brand_target'):
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
        target = state['plan'].get('brand_target')
        if target:
            if not brand_catalog.matches_identity(target, content[source_id]):
                entry.update(status='rejected', reason='审核车型与本任务指定的品牌或车型不符')
                continue
            content[source_id].update(brand=target['brand'], model=target['model'], first_year=target['first_year'])
        copy_fields = content[source_id]
        copy_fields['body'] = strip_repeated_discussion(copy_fields['body'], copy_fields['discussion_question'])
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
        series_conflict = reviewed_series_conflict(approved)
        reason = ''
        if series_conflict:
            reason = series_conflict
        elif forbidden:
            reason = '禁选内容：' + forbidden
        elif approved.get('score', 0) < SCORE_THRESHOLD:
            reason = '核验评分低于 %d' % SCORE_THRESHOLD
        elif state['plan'].get('editorial_review_version'):
            editorial = validate_editorial_review(approved)
            if (item_series == RACE_HUMOR and state.get('race_incident_version')
                    and not (approved['verification'].get('editorial') or {}).get('race_incident')):
                raise ValueError('须在审核中填写 race_incident 供后续事件去重')
            if item_series == ACTION_SERIES and editorial['opening_action_at_sec'] is None:
                reason = '汽车机关图鉴前 3 秒未见关键动作'
        if reason:
            entry.update(status='rejected', reason=reason, item=approved, publish=content[source_id])
            if not series_conflict:
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
    events = race_index(records, queued, []) if any(e['item'].get('topic_series_id') == RACE_HUMOR for e in approved) else {}
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
        target = state['plan'].get('brand_target')
        if target and not reason:
            for record in queued:
                if record['status'] not in WAITING_STATUSES:
                    continue
                other = record['item']
                if brand_catalog.matches_identity(target, other):
                    reason = '同车型已在候选池：' + record['source_id']
                    break
                if (other.get('verification') or {}).get('video_sha256') == entry['video_sha256']:
                    reason = '不同原帖使用相同视频：' + record['source_id']
                    break
        profile = incident_profile(item)
        if profile and profile['key'] in events:
            reason = reason or '已有同一正赛插曲：' + profile['key']
        reason = reason or reviewed_series_conflict(item)
        if reason:
            entry.update(status='rejected', reason=reason)
        else:
            accepted.append(item)
            brands.add(brand)
            if profile:
                events[profile['key']] = profile
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
                'publish_history_file': str(history.path), 'downloads': selected, 'rejected': rejected,
                'series_mismatch_skipped': [{'source_id': sid, 'series_id': series, 'reason': reason}
                                            for sid, skipped in state.get('series_skips', {}).items()
                                            for series, reason in skipped.items()],
                'race_event_skipped': [{'source_id': key, 'match': entry['race_duplicate']}
                                       for key, entry in state['items'].items() if 'race_duplicate' in entry],
                'covered_race_queries': state.get('covered_race_queries', {}),
                'color_only_skipped': [{'source_id': key, 'match': entry['color_only']}
                                       for key, entry in state['items'].items() if 'color_only' in entry],
                'recent_mechanism_skipped': [{'source_id': key, 'match': entry['recent_duplicate']}
                                             for key, entry in state['items'].items() if 'recent_duplicate' in entry]}
    atomic_json(out / 'manifest.json', manifest)
    atomic_json(out / 'publish.json', content)


def run_pipeline(args, backend=None):
    out = Path(args.out).resolve()
    catalog_path = getattr(args, 'brand_catalog', None)
    model = getattr(args, 'model', None)
    if bool(catalog_path) != bool(model):
        raise ValueError('--brand-catalog 与 --model 须一起使用')
    target = None
    if catalog_path:
        catalog = brand_catalog.read_catalog(catalog_path)
        target = next((row for row in catalog['models'] if normalized(row['model']) == normalized(model)), None)
        if not target:
            raise ValueError('名录没有该车型：' + model)
        if any(getattr(args, key, None) for key in ('series', 'race_league', 'wrc_post', 'wrc_brief')):
            raise ValueError('品牌历代车型须使用独立任务')
        out = brand_catalog.model_dir(out, target)
        args.top = args.min_selected = 1
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
        if target and state and state['plan'].get('brand_target') != target:
            raise ValueError('恢复任务不能更换目标车型资料，请核对名录或使用新的任务目录')
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
            if target:
                plan = brand_catalog.model_plan(target)
            else:
                plan = build_topic_plan(history.records(), queue.records(), list(registry['jobs'].values()))
                plan['series_episode'] = None  # 发布期数由发布计划分配。
                plan['prompt'] = '自动选题｜%s｜%s｜候选生产' % (plan['pillar_label'], plan['series_label'])
                selected = getattr(args, 'series', None)
                if not selected and not args.query and args.top > 1:
                    selected = ','.join(plan['refill_series'][:args.top])
                if selected:
                    plan = apply_series_override(plan, selected)
            plan['editorial_review_version'] = EDITORIAL_REVIEW_VERSION
            state = {'inventory_held': len(inventory_audit), 'settings': {key: getattr(args, key) for key in ('sort', 'type', 'limit', 'since', 'min_likes', 'min_reposts', 'max_duration')}, 'job_id': uuid.uuid4().hex, 'status': 'searching', 'phase': 'collecting', 'target': args.top, 'min_selected': min_selected,
                     'history_file': str(history.path), 'plan': plan, 'items': {}, 'pages': 0,
                     'downloads': 0, 'race_incident_version': 1, 'queries': [{'query': q, 'cursor': None, 'pages': 0, 'done': False,
                                                  'series_id': (plan.get('query_series') or {}).get(q)
                                                  or plan['series_id']}
                                                 for q in make_queries(plan)], 'created_at': now()}
            if not target and not getattr(args, 'series', None) and not args.query:
                state['fallback_series'] = [sid for sid in plan['ranked_series']
                                            if sid not in plan['series_id'].split('+')]
            record_event('candidate', 'topic_strategy', state['job_id'],
                         event_id=state['job_id'] + ':topic_strategy',
                         details={'series_id': plan.get('series_id'), 'pillar': plan.get('pillar')},
                         path=history.path.with_name('monitor_events.jsonl'))
        elif state['history_file'] != str(history.path):
            raise ValueError('恢复任务不能切换发布历史')
        if target:
            expansion = catalog['model_expansions'].get(target['model'])
            if expansion:
                state['plan']['brand_expansion'] = expansion
            else:
                state['plan'].pop('brand_expansion', None)
        brand_job = bool(state['plan'].get('brand_target'))
        max_pages = args.max_pages
        if max_pages is None:
            max_pages = brand_catalog.DEFAULT_MODEL_PAGE_LIMIT if brand_job else 12
        for key, value in state.get('settings', {}).items():
            setattr(args, key, value)
        league = getattr(args, 'race_league', None)
        if league:
            if state['plan']['series_id'] != RACE_HUMOR:
                raise ValueError('--race-league requires --series race_humor')
            if state.get('race_leagues', [league]) != [league]:
                raise ValueError('恢复任务不能更换赛事')
            if 'race_leagues' not in state:
                if state['pages']:
                    raise ValueError('已检索的任务不能改变赛事范围')
                state['race_leagues'] = [league]
                state['queries'] = [dict(query=q + ' filter:videos', explicit=True, cursor=None,
                                         pages=0, done=False, series_id=RACE_HUMOR)
                                    for q in LEAGUE_QUERIES[league]]
        brief_file = getattr(args, 'wrc_brief', None)
        if brief_file:
            brief = json.loads(Path(brief_file).read_text())
            if state.get('race_leagues') != ['wrc'] or not isinstance(brief, dict):
                raise ValueError('--wrc-brief 只用于 WRC 单分站简报')
            if 'wrc_brief' in state and state['wrc_brief'] != brief:
                raise ValueError('恢复任务不能替换已核实的简报')
            if 'wrc_brief' not in state:
                if state['pages']:
                    raise ValueError('使用新目录开始简报任务')
                state['queries'] = brief_queries(brief)
                state['wrc_brief'] = brief
        for link in getattr(args, 'wrc_post', []):
            match = re.fullmatch(r'https://(?:www\.)?(?:x|twitter)\.com/\w+/status/(\d+)(?:/video/\d+)?', link)
            if not state.get('wrc_brief') or not match:
                raise ValueError('--wrc-post 需要简报及其对应的 X 原帖地址')
            lookup = 'wrc-post:' + match.group(1)
            if not any(q['query'] == lookup for q in state['queries']):
                state['queries'].insert(0, dict(query=lookup, family='linked', explicit=True, pages=0,
                                                cursor=None, done=False, series_id=RACE_HUMOR))
        retire_unscoped_queries(state, args.query)
        for query in args.query:
            if query not in [q['query'] for q in state['queries']]:
                state['queries'].append({'query': query, 'explicit': True, 'cursor': None, 'pages': 0, 'done': False,
                                         'series_id': state['plan']['series_id'].split('+')[0]})
        if not args.cancel and state['status'] not in ('complete', 'cancelled'):
            narrow_mechanism_queries(state)

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

        def finish(status, error=None, *, budget=None):
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
                      'empty_series_pages': state.get('empty_series_pages', {}),
                      'model_duplicates_skipped': sum('model_duplicate' in e for e in state['items'].values()),
                      'series_mismatch_skipped': len(state.get('series_skips', {})),
                      'race_event_skipped': sum('race_duplicate' in e for e in state['items'].values()),
                      'covered_race_queries': len(state.get('covered_race_queries', {})),
                      'color_only_skipped': sum('color_only' in e for e in state['items'].values()),
                      'recent_mechanism_skipped': sum('recent_duplicate' in e for e in state['items'].values()),
                      'review_request': str(out / 'review_request.json'), 'queue_file': str(queue.path),
                      'error': error, 'enqueue': state.get('enqueue')}
            if status == 'complete':
                added_ids = set((state.get('enqueue') or {}).get('source_ids', []))
                added_ids.update(entry['source_id'] for entry in queue.records()
                                 if entry['status'] == 'queued' and entry.get('out_dir') == str(out))
                manifest = read_json(out / 'manifest.json', {})
                result['materials'] = [candidate_result(item) for item in manifest.get('downloads', [])
                                       if str(item['candidate']['id']) in added_ids]
            if brand_job:
                result['page_limit'] = max_pages
            if budget:
                result['budget'] = budget
            atomic_json(out / 'candidate_receipt.json', result)
            return result

        if not args.cancel and state['status'] not in ('complete', 'cancelled') and state.get('query_yield_version') != YIELD_VERSION:
            excluded = {e['source_id'] for e in queue.records() if e['status'] in ('withdrawn', 'invalid', 'needs_review')}
            state['query_yield'] = prior_query_yield(registry['jobs'].values(), state, excluded)
            state['query_yield_version'] = YIELD_VERSION
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
                queued = queue.records()
                occupied = {e['source_id'] for e in queued}
                checked_at = datetime.datetime.now(datetime.timezone.utc)
                other_approved = []
                for job_id, job in registry['jobs'].items():
                    if job_id == state['job_id']:
                        continue
                    reserved = reserved_source_ids(job, checked_at)
                    occupied.update(reserved)
                    items = (job.get('checkpoint') or {}).get('items', {})
                    other_approved.extend(items[sid]['item'] for sid in reserved
                                          if items.get(sid, {}).get('status') == 'approved'
                                          and items[sid].get('series_id') == RACE_HUMOR)
                records = latest_records(history.records())
                target = state['plan'].get('brand_target')
                if target:
                    existing = next((e for e in queued if e['status'] in WAITING_STATUSES
                                     and brand_catalog.matches_identity(target, e['item'])), None)
                    if existing:
                        return finish('pool_conflict', '同车型已在候选池：' + existing['source_id'])
                    if any(brand_catalog.matches_identity(target, r) and
                           content_duplicate(content_identity(r), [r], statuses=ACTIVE_STATUSES) for r in records):
                        return finish('recently_published', '同车型在有效发布历史的 30 天内')
                models = model_index(records, queued, checked_at)
                recent = recent_mechanisms(records, queued, checked_at, approved)
                events = {}
                if any(q.get('series_id') == RACE_HUMOR for q in state['queries']):
                    events = race_index(records, queued, approved, other_approved)
                    state['covered_race_queries'] = covered_queries(state['queries'], state['items'], events)
                if state.get('race_leagues') == ['wrc']:
                    retired = interview_queries(state['items'])
                    for prior in registry['jobs'].values():
                        previous = prior.get('checkpoint') or {}
                        if previous.get('race_leagues') == ['wrc'] and previous.get('job_id') != state['job_id']:
                            retired.update(interview_queries(previous.get('items', {})))
                    state['retired_interview_queries'] = sorted(retired)
                    state.setdefault('covered_race_queries', {}).update(dict.fromkeys(retired, '停车采访'))
                candidates = [e for e in state['items'].values() if e['status'] in ('discovered', 'downloaded')]
                for entry in candidates:
                    screen_series(entry, state)
                    screen_caption(entry, state, models, recent, events)
                candidates = [entry for entry in candidates if entry['status'] != 'skipped']
                if state.get('covered_race_queries'):
                    fresh = [e for e in candidates if e.get('race_query') not in state['covered_race_queries']]
                    if fresh or next_search(state):
                        candidates = fresh
                candidates.sort(key=lambda e: (-e['series_priority'], -finder.to_int(e['item']['candidate'].get(args.sort))))
                if target:
                    candidates.sort(key=lambda e: -engagement_total(e['item']))
                if state.get('race_leagues') == ['wrc']:
                    candidates.sort(key=lambda e: wrc_clip_rank(e['item']['candidate']))
                if state.get('wrc_brief'):
                    # Resolve supplied evidence before downloading unrelated results from a race-day page.
                    if any(q.get('family') == 'linked' and not q['done'] for q in state['queries']):
                        candidates = []
                    phrases = [p.casefold() for p in state['wrc_brief']['phrases']]
                    phrases.extend(p.casefold() for q in args.query for p in re.findall(r'"([^"]+)"', q))
                    candidates.sort(key=lambda e: not any(p in e['item']['candidate']['text'].casefold()
                                                          for p in phrases))
                    candidates.sort(key=lambda e: not e.get('race_query', '').startswith('wrc-post:'))
                if candidates:
                    # 每次准备补足目标所需的审核任务，然后交给 Agent。
                    pending = 0
                    for entry in candidates:
                        if (pending and state.get('race_leagues') == ['wrc']) or len(approved) + pending >= state['target']:
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
                        if pending and entry.get('series_id') == RACE_HUMOR:
                            break  # 先审这一幕，再决定同页转载是否还需要下载。
                    if pending:
                        return finish('awaiting_review')
                    continue
                # 先审完页内候选，再应用车型页上限。页数取已持久化的累计值。
                if brand_job and state['pages'] >= max_pages:
                    return finish('budget_exhausted', '已达到该车型搜索页上限，未选到合格素材',
                                  budget='search_pages')
                query = next_search(state)
                if query is None and state['pages'] < max_pages and extend_refill(state):
                    excluded = {e['source_id'] for e in queued if e['status'] in ('withdrawn', 'invalid', 'needs_review')}
                    state['query_yield'] = prior_query_yield(registry['jobs'].values(), state, excluded)
                    checkpoint()
                    continue
                if query is None and state.get('fallback_series') and state['pages'] >= max_pages:
                    return finish('budget_exhausted', '页预算耗尽，恢复后可继续切换系列')
                if query is None:
                    if brand_job:
                        return finish('exhausted', '该车型可用检索式及游标已用尽，未选到合格素材')
                    return finish('exhausted', '同系列检索已耗尽；可添加 --query 继续，不能降低核验标准')
                if state['pages'] >= max_pages:
                    return finish('budget_exhausted', '累计搜索页预算耗尽；可提高 --max-pages 后恢复')
                tweets, cursor = backend.search(search_text(query['query'], vars(args)),
                                                query['cursor'], query['pages'] + 1, out)
                state['pages'] += 1
                record_event('candidate', 'search_filter', state['job_id'],
                             event_id='%s:search:%d' % (state['job_id'], state['pages']),
                             details={'result_count': len(tweets), 'query_page': query['pages'] + 1},
                             path=history.path.with_name('monitor_events.jsonl'))
                previous = query['cursor']
                query.update(cursor=cursor, pages=query['pages'] + 1,
                             done=not tweets or not cursor or cursor == previous)
                sources = query.setdefault('page_sources', {})[str(query['pages'])] = []
                series = query.get('series_id') or state['plan']['series_id']
                lexicon = finder.CAR_LEXICON + ('|' + RACE_TERMS if series == RACE_HUMOR else '')
                car_pattern = re.compile(lexicon)
                for tweet in tweets:
                    if not tweet.get("id"):
                        continue
                    verified_link = query.get('family') == 'linked' and bool(state.get('wrc_brief'))
                    if target and not brand_catalog.matches_caption(target, tweet.get('text') or ''):
                        continue
                    candidate = finder.normalize(tweet, car_pattern, [], not (target or verified_link))
                    if not candidate:
                        continue
                    if target:
                        candidate['topic_series_id'] = BRAND_SERIES
                    source_id = str(candidate['id'])
                    existing = state['items'].get(source_id)
                    if series in state.get('series_skips', {}).get(source_id, {}):
                        continue
                    if existing and not existing.get('series_mismatch'):
                        if (series == RACE_HUMOR and existing['status'] == 'discovered'
                                and existing.get('race_query') in state.get('covered_race_queries', {})):
                            existing['race_query'] = query['query']
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
                                                  'series_id': series, 'item': {'candidate': candidate}}
                    entry = state['items'][source_id]
                    if series == RACE_HUMOR:
                        entry['race_query'] = query['query']
                    if not reason:
                        screen_series(entry, state)
                        screen_caption(entry, state, models, recent, events)
                        if entry['status'] == 'discovered':
                            sources.append(source_id)
                if target:
                    query['empty_pages'] = 0 if sources else query.get('empty_pages', 0) + 1
                rotate_brief_family(state, query, sources)
                streaks = state.setdefault('empty_series_pages', {})
                streaks[series] = 0 if sources else streaks.get(series, 0) + 1
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
    parser.add_argument('--brand-catalog', help='已核实的品牌车型名录 JSON；--out 为品牌任务根目录')
    parser.add_argument('--model', help='本次补池的名录正式车型名，每个车型独立入池一条')
    parser.add_argument('--wrc-post', action='append', default=[], help='与简报事件交叉核对的原帖链接')
    parser.add_argument('--wrc-brief', help='已查证的单分站赛段简报 JSON')
    parser.add_argument('--race-league', choices=sorted(LEAGUE_QUERIES))
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
    parser.add_argument('--max-pages', type=int,
                        help='累计成功搜索页数上限，常规任务默认 12，品牌名录每车型默认 3')
    parser.add_argument('--max-downloads', type=int, default=15)
    parser.add_argument('--max-seconds', type=int, default=600)
    parser.add_argument('--cancel', action='store_true')
    parser.add_argument('--retry-failed', action='store_true', help='显式重试下载/抽帧失败')
    parser.add_argument('--series', help='指定选题系列（新任务生效），用于一轮内并行多个选题策略')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.brand_catalog:
        args.top = args.min_selected = 1
    if (args.top < (args.min_selected or 3) or (args.max_pages is not None and args.max_pages < 1)
            or args.max_downloads < 3 or args.max_seconds < 1):
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

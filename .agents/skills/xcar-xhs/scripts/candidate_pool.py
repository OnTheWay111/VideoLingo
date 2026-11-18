#!/usr/bin/env python3
"""纯候选池操作：入池、复核后更新、撤回和审计；不生成发布计划。"""
import argparse
import json
from pathlib import Path

from content_policy import content_duplicate, content_identity
from publication_queue import PublicationQueue
from publish_history import PublishedHistory, default_history_path, ACTIVE_STATUSES
from xiaohongshu_publisher import validate_content, latest_records


def enqueue_finalized(out_dir, history, queue=None):
    out_dir = Path(out_dir).resolve()
    manifest = json.loads((out_dir / 'manifest.json').read_text())
    items = manifest.get('downloads') or []
    if not manifest.get('finalized_at') or not manifest.get('selection_complete'):
        raise ValueError('必须先完成定稿再入池')
    required = max(manifest.get('minimum_selected') or 3, manifest.get('selection_target') or 0)
    if len(items) < required:
        raise ValueError('入池批次至少需要 %d 条核验通过的候选' % required)
    queue = queue or PublicationQueue(history_path=history.path)
    records = latest_records(history.records())
    candidates, skipped = [], []
    for item in items:
        validate_content(item)
        source_id = str(item['candidate']['id'])
        if history.check(source_id=source_id):
            skipped.append({'source_id': source_id, 'reason': '已发布或待确认'})
            continue
        duplicate = content_duplicate(content_identity(item), records, statuses=ACTIVE_STATUSES)
        if duplicate:
            raise ValueError('近期内容重复：' + duplicate['reason'])
        candidates.append(item)
    added = queue.enqueue(candidates, out_dir)
    added_ids = {entry['source_id'] for entry in added}
    skipped.extend({'source_id': str(item['candidate']['id']), 'reason': '同 ID 已在池中，更新需显式版本号'}
                   for item in candidates if str(item['candidate']['id']) not in added_ids)
    return {'status': 'complete', 'added': len(added), 'source_ids': sorted(added_ids),
            'skipped': skipped, 'queue_file': str(queue.path), 'published': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history-file', default=str(default_history_path()))
    commands = parser.add_subparsers(dest='command', required=True)
    enqueue = commands.add_parser('enqueue')
    enqueue.add_argument('--out', required=True)
    audit = commands.add_parser('audit')
    audit.add_argument('--max-age-days', type=int, default=30)
    withdraw = commands.add_parser('withdraw')
    withdraw.add_argument('--source-id', required=True)
    withdraw.add_argument('--revision', required=True, type=int)
    withdraw.add_argument('--reason', required=True)
    update = commands.add_parser('update')
    update.add_argument('--source-id', required=True)
    update.add_argument('--revision', required=True, type=int)
    update.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    history = PublishedHistory(args.history_file)
    queue = PublicationQueue(history_path=history.path)
    try:
        if args.command == 'enqueue':
            result = enqueue_finalized(args.out, history, queue)
        elif args.command == 'audit':
            result = {'changed': queue.audit(args.max_age_days)}
        elif args.command == 'withdraw':
            result = queue.withdraw(args.source_id, args.revision, args.reason)
        else:
            manifest = json.loads((Path(args.out) / 'manifest.json').read_text())
            if not manifest.get('finalized_at'):
                raise ValueError('更新必须使用已定稿批次')
            item = next((i for i in manifest['downloads'] if str(i['candidate']['id']) == args.source_id), None)
            if item is None:
                raise ValueError('批次中没有该候选')
            validate_content(item)
            if history.check(source_id=args.source_id):
                raise ValueError('已发布或待确认内容不能更新')
            duplicate = content_duplicate(content_identity(item), latest_records(history.records()), statuses=ACTIVE_STATUSES)
            if duplicate:
                raise ValueError('更新后与近期发布内容重复')
            result = queue.update_candidate(args.source_id, item, args.revision, args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'blocked', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

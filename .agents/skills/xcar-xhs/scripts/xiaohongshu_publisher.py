#!/usr/bin/env python3
"""把核验候选加入跨批次队列，逐条生成最高分发布计划。"""

import argparse
import datetime
import json
import sys
from urllib.parse import urlsplit
from pathlib import Path
from publication_runtime import atomic_json, execution_lock
from candidate_review import validate_review, validate_publication_review

from content_policy import (
    SCORE_THRESHOLD,
    content_duplicate,
    content_identity,
    strip_material_source_disclosure,
    strip_preset_ai_disclosure,
    validate_no_material_source_copy,
    validate_score,
    validate_verifiable_content,
)
from publish_history import (
    ACTIVE_STATUSES,
    PublishedHistory,
    default_draft_history_path,
    default_history_path,
)
from publication_queue import PublicationQueue
from topic_policy import forbidden_topic_match

PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target=video"
ACTIONS = ("preview", "draft", "publish")
PUBLIC_NOTE_PREFIXES = (
    "https://www.xiaohongshu.com/explore/",
    "https://www.xiaohongshu.com/discovery/item/",
)


def load_manifest(out_dir):
    return json.loads((Path(out_dir) / "manifest.json").read_text(encoding="utf-8"))


def load_download(out_dir, tweet_id):
    manifest = load_manifest(out_dir)
    for item in manifest.get("downloads", []):
        candidate = item.get("candidate") or {}
        if str(candidate.get("id")) == str(tweet_id):
            if not item.get("title") or not item.get("recommendation"):
                raise ValueError("该视频尚未运行 finalize.py 生成标题和正文")
            return item
    raise ValueError("manifest.json 的入选记录中找不到推文 ID：%s" % tweet_id)


def validate_content(item, publication=False):
    forbidden = forbidden_topic_match(item)
    if forbidden:
        raise ValueError("小红书禁选主题，禁止生成发布计划：%s" % forbidden)
    item["ai_disclosure"] = strip_preset_ai_disclosure(
        strip_material_source_disclosure(item.get("ai_disclosure")))
    title = (item.get("title") or "").strip()
    body = (item.get("recommendation") or "").strip()
    validate_no_material_source_copy(body)
    topics = item.get("topics") or []
    video = Path((item.get("video") or {}).get("file") or "")
    if not 1 <= len(title) <= 20:
        raise ValueError("小红书标题需为 1–20 个字符，当前为 %d 个：%s" % (len(title), title))
    if not body:
        raise ValueError("正文不能为空")
    if len(body) > 1000:
        raise ValueError("小红书正文不能超过 1000 个字符")
    if not isinstance(topics, list) or not 2 <= len(topics) <= 6:
        raise ValueError("topics 必须包含 2–6 个话题")
    if not all(isinstance(topic, str) and topic.strip() for topic in topics):
        raise ValueError("topics 不能包含空话题")
    if not video.is_file() or video.stat().st_size <= 0:
        raise ValueError("视频文件不存在或为空：%s" % video)
    score, breakdown = validate_score(item)
    details = validate_verifiable_content(item)
    item["score"] = score
    item["score_breakdown"] = breakdown
    item.setdefault("verification", {})["score"] = score
    item["verification"]["score_breakdown"] = breakdown
    item["verification"]["score_threshold"] = SCORE_THRESHOLD
    if publication:
        validate_publication_review(item)
    else:
        validate_review(item)
    return title, body, [topic.strip() for topic in topics], video.resolve(), details


def latest_records(records):
    latest = {}
    for record in reversed(records):
        source_id = str(record.get("source_id") or "")
        if source_id not in latest:
            latest[source_id] = record
    return list(latest.values())


def latest_published_record(history):
    published = [record for record in latest_records(history.records())
                 if record.get("status") == "published"]
    if not published:
        return None
    return max(published, key=lambda record: str(record.get("published_at") or record.get("recorded_at") or ""))


def ensure_publish_available(history, source_id=None, title=""):
    """链接缺失不是发布失败；待确认状态只阻止同一素材重发。"""
    if source_id is not None:
        match = history.check(source_id, title)
        if match and match.get("status") == "unconfirmed":
            raise ValueError("该视频提交结果待确认，禁止重发：%s" % match.get("title"))
    return latest_published_record(history)


def topic_metadata(item):
    fields = (
        "brand", "model", "modification_project", "file_topic", "topic_pillar",
        "topic_series_id", "topic_series_episode", "topic_strategy_version", "ai_disclosure",
        "title_detail", "verifiable_fact", "fact_source", "discussion_question",
        "score", "score_breakdown",
    )
    return {key: item[key] for key in fields if key in item}


def prepare_item(item, topic_plan):
    values = {
        "topic_pillar": topic_plan.get("pillar") or "",
        "topic_series_id": topic_plan.get("series_id") or "",
        "topic_series_episode": topic_plan.get("series_episode") or "",
        "topic_strategy_version": topic_plan.get("strategy_version") or "",
    }
    for key, value in values.items():
        if value not in ("", None):
            item[key] = value
    return item


def build_plan(out_dir, tweet_id, history, action="publish", queue=None, now=None):
    if action not in ACTIONS:
        raise ValueError("action 必须是 preview、draft 或 publish")
    if action == "publish" and tweet_id:
        ensure_publish_available(history, tweet_id)
    out_dir = Path(out_dir).resolve()
    manifest = load_manifest(out_dir)
    minimum_selected = manifest.get("minimum_selected")
    required_selected = (max(minimum_selected, manifest.get("selection_target") or 0)
                         if minimum_selected is not None else 0)
    if action in ("publish", "draft") and len(manifest.get("downloads", [])) < required_selected:
        raise ValueError(
            "核验入选名单至少要有 %d 条，当前只有 %d 条；禁止生成发布计划，请继续搜索递补"
            % (required_selected, len(manifest.get("downloads", []))))
    topic_plan = manifest.get("topic_plan") or {}
    queue = queue or PublicationQueue(history_path=history.path)

    if action in ("preview", "draft"):
        if not tweet_id:
            raise ValueError("preview 或 draft 必须指定 --tweet-id")
        item = prepare_item(load_download(out_dir, tweet_id), topic_plan)
    else:
        candidates = []
        existing_ids = {str(entry['source_id']) for entry in queue.records()}
        for original in manifest.get("downloads", []):
            if str(original.get('candidate', {}).get('id')) in existing_ids:
                continue  # 已入池候选直接选取；不从旧 manifest 覆盖或重新入池。
            item = prepare_item(original, topic_plan)
            validate_content(item)
            candidate = item["candidate"]
            if history.check(candidate.get("id"), item.get("title")):
                continue
            duplicate = content_duplicate(
                content_identity(item), latest_records(history.records()), now=now, statuses=ACTIVE_STATUSES)
            if duplicate:
                raise ValueError("近期内容重复：%s（%s）" % (duplicate["reason"], duplicate["title"]))
            candidates.append(item)
        queue.enqueue(candidates, out_dir)
        latest = ensure_publish_available(history)
        entry = queue.select(latest, excluded_source_ids={
            str(r.get("source_id")) for r in latest_records(history.records())
            if r.get("status") in ACTIVE_STATUSES})
        if not entry:
            raise ValueError("队列中没有与上一条不相似的素材；相似内容继续保留")
        if tweet_id and str(tweet_id) != entry["source_id"]:
            raise ValueError("publish 仅可生成当前队列最高分且不相似的一条；本次应选择原帖 ID：%s" % entry["source_id"])
        item = entry["item"]
        duplicate = content_duplicate(content_identity(item), latest_records(history.records()),
                                      now=now, statuses=ACTIVE_STATUSES)
        if duplicate:
            queue.release(entry['source_id'])
            raise ValueError("近期内容重复：%s（%s）" % (duplicate['reason'], duplicate['title']))

    if action == 'publish' and item.get('topic_series_id') and not item.get('topic_series_episode'):
        series_records = [record for record in latest_records(history.records())
                          if record.get('status') == 'published'
                          and record.get('topic_series_id') == item['topic_series_id']]
        item['topic_series_episode'] = len(series_records) + 1
    candidate = item["candidate"]
    title, body, topics, video, details = validate_content(item, publication=True)
    match = history.check(candidate.get("id"), title)
    base = {
        "source_id": str(candidate.get("id")),
        "source_url": candidate.get("url") or "",
        "title": title,
        "history_file": str(history.path),
        **topic_metadata(item),
    }
    if match:
        return {"status": "skipped", "reason": "already_published", "match": match, **base}
    disclosure = str(item.get("ai_disclosure") or "").strip()
    sections = [body, "讨论：%s" % details["discussion_question"]]
    if disclosure:
        sections.append(disclosure)
    sections.append(" ".join(topics))
    full_text = "\n\n".join(sections)
    validate_no_material_source_copy(full_text)
    browser_actions = ["upload_video", "fill_title", "fill_body", "add_topics", "verify_upload"]
    if action in ("publish", "draft"):
        browser_actions.append("publish" if action == "publish" else "save_draft")
    return {
        "status": "ready",
        "platform": "xiaohongshu",
        "action": action,
        "publish_url": PUBLISH_URL,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "video_file": str(video),
        "body": body,
        "topics": topics,
        "ai_disclosure": disclosure,
        "full_text": full_text,
        "browser_actions": browser_actions,
        **base,
    }


def save_plan(out_dir, plan):
    out_dir = Path(out_dir)
    path = out_dir / "xiaohongshu_publish_plan.json"
    atomic_json(path, plan)
    execution_path = out_dir / "plans" / ("%s.json" % plan["source_id"])
    if plan.get("status") == "ready" and plan.get("action") in ("publish", "draft"):
        execution_path.parent.mkdir(exist_ok=True)
        atomic_json(execution_path, plan)
    elif execution_path.is_file():
        execution_path.unlink()
    return path


def load_plan(out_dir):
    plan = json.loads((Path(out_dir) / "xiaohongshu_publish_plan.json").read_text(encoding="utf-8"))
    if plan.get("status") != "ready":
        raise ValueError("发布计划不是 ready 状态，不能记录结果")
    return plan


def valid_note_url(note_url):
    if not isinstance(note_url, str) or any(char.isspace() for char in note_url):
        return False
    try:
        parsed = urlsplit(note_url)
    except ValueError:
        return False
    parts = parsed.path.strip('/').split('/')
    return (parsed.scheme == 'https' and parsed.netloc == 'www.xiaohongshu.com'
            and ((len(parts) == 2 and parts[0] == 'explore' and bool(parts[1]))
                 or (len(parts) == 3 and parts[:2] == ['discovery', 'item'] and bool(parts[2]))))


def record_result(out_dir, history, status="published", note_url="", result_url="", queue=None, plan=None):
    out_dir = Path(out_dir)
    plan = plan if plan is not None else load_plan(out_dir)
    if plan.get("action") != "publish":
        raise ValueError("只有 publish 计划可以写入已发布历史")
    if note_url and not valid_note_url(note_url):
        raise ValueError("note_url 必须是真实公开笔记地址，不能保存发布结果页")
    record = history.add(
        plan["source_id"], plan["title"], plan.get("source_url", ""), status,
        note_url=note_url, result_url=result_url, video_file=plan.get("video_file", ""),
        note_url_pending=status == "published" and not note_url,
        submission_status="confirmed" if status == "published" else "unconfirmed",
        public_link_status="available" if note_url else "pending",
        **topic_metadata(plan),
    )
    if note_url and record.get("note_url") != note_url:
        record = history.update_note_url(plan["source_id"], note_url)
    queue = queue or PublicationQueue(history_path=history.path)
    queue.complete(plan["source_id"], status, note_url)
    receipt = {
        "status": status,
        "source_id": plan["source_id"],
        "title": plan["title"],
        "note_url": note_url,
        "note_url_pending": status == "published" and not note_url,
        "result_url": result_url,
        "history_file": str(history.path),
        "recorded_at": record["recorded_at"],
    }
    receipt_path = out_dir / "xiaohongshu_publish_receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def attach_note(out_dir, history, source_id, note_url, queue=None):
    if not valid_note_url(note_url):
        raise ValueError("note_url 必须是真实公开笔记地址，不能保存发布结果页")
    record = history.update_note_url(source_id, note_url)
    queue = queue or PublicationQueue(history_path=history.path)
    queue.attach_note(source_id, note_url)
    receipt = {
        "status": record["status"], "source_id": str(source_id), "title": record.get("title") or "",
        "note_url": note_url, "note_url_pending": False,
        "result_url": record.get("result_url") or "", "history_file": str(history.path),
        "recorded_at": record["recorded_at"],
    }
    (Path(out_dir) / "xiaohongshu_publish_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def release_plan(out_dir, history):
    plan = load_plan(out_dir)
    if plan.get("action") == "publish":
        return PublicationQueue(history_path=history.path).release(plan["source_id"])
    return None


def record_draft(out_dir, status="draft_saved", result_url="", draft_history=None, plan=None):
    out_dir = Path(out_dir)
    if status not in ("draft_saved", "unconfirmed_draft"):
        raise ValueError("草稿状态必须是 draft_saved 或 unconfirmed_draft")
    plan = plan if plan is not None else load_plan(out_dir)
    if plan.get("action") != "draft":
        raise ValueError("只有 draft 计划可以写入草稿记录")
    history = draft_history or PublishedHistory(default_draft_history_path())
    record = history.add(
        plan["source_id"], plan["title"], plan.get("source_url", ""), status,
        result_url=result_url, video_file=plan.get("video_file", ""), **topic_metadata(plan),
    )
    receipt = {
        "status": status,
        "source_id": plan["source_id"],
        "title": plan["title"],
        "result_url": result_url,
        "history_file": str(history.path),
        "recorded_at": record["recorded_at"],
    }
    receipt_path = out_dir / "xiaohongshu_draft_receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="finder/finalize 输出目录")
    parser.add_argument("--history-file", default=str(default_history_path()))
    commands = parser.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("plan", help="全部入选入队，生成下一条最高分且不相似的发布计划")
    plan_parser.add_argument("--tweet-id", help="preview/draft 必填；publish 仅可校验当前自动选择结果")
    plan_parser.add_argument("--action", choices=ACTIONS, default="publish")
    record_parser = commands.add_parser("record", help="仅在网页发布成功或结果待确认后调用")
    record_parser.add_argument("--status", choices=("published", "unconfirmed"), default="published")
    record_parser.add_argument("--note-url", default="")
    record_parser.add_argument("--result-url", default="")
    note_parser = commands.add_parser("attach-note", help="发布结果页不是公开地址时，补登记真实笔记 URL")
    note_parser.add_argument("--source-id", required=True)
    note_parser.add_argument("--note-url", required=True)
    draft_parser = commands.add_parser("record-draft", help="仅在草稿保存成功或结果待确认后调用")
    draft_parser.add_argument("--status", choices=("draft_saved", "unconfirmed_draft"), default="draft_saved")
    draft_parser.add_argument("--result-url", default="")
    args = parser.parse_args(argv)
    out_dir = Path(args.out).resolve()
    history = PublishedHistory(args.history_file)
    try:
        with execution_lock(history.path):
            if args.command == "plan":
                plan = build_plan(out_dir, args.tweet_id, history, args.action)
                path = save_plan(out_dir, plan)
                print(json.dumps({"plan_file": str(path), **plan}, ensure_ascii=False, indent=2))
                return 0
            if args.command == "record-draft":
                receipt = record_draft(out_dir, args.status, args.result_url)
            elif args.command == "attach-note":
                receipt = attach_note(out_dir, history, args.source_id, args.note_url)
            else:
                receipt = record_result(out_dir, history, args.status, args.note_url, args.result_url)
            print(json.dumps(receipt, ensure_ascii=False, indent=2))
            return 0
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, "错误: %s\n" % exc)


if __name__ == "__main__":
    sys.exit(main())

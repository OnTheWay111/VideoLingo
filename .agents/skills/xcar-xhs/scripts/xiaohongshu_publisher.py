#!/usr/bin/env python3
"""生成小红书发布/草稿计划，并分别登记发布和草稿结果。"""

import argparse
import datetime
import json
import sys
from pathlib import Path

from publish_history import PublishedHistory, default_draft_history_path, default_history_path
from topic_policy import forbidden_topic_match

PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target=video"
ACTIONS = ("preview", "draft", "publish")


def load_download(out_dir, tweet_id):
    manifest_path = Path(out_dir) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest.get("downloads", []):
        candidate = item.get("candidate") or {}
        if str(candidate.get("id")) == str(tweet_id):
            if not item.get("title") or not item.get("recommendation"):
                raise ValueError("该视频尚未运行 finalize.py 生成标题和正文")
            return item
    raise ValueError("manifest.json 的入选记录中找不到推文 ID：%s" % tweet_id)


def validate_content(item):
    forbidden = forbidden_topic_match(item)
    if forbidden:
        raise ValueError("小红书禁选主题，禁止生成发布计划：%s" % forbidden)
    title = (item.get("title") or "").strip()
    body = (item.get("recommendation") or "").strip()
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
    return title, body, [topic.strip() for topic in topics], video.resolve()


def build_plan(out_dir, tweet_id, history, action="publish"):
    if action not in ACTIONS:
        raise ValueError("action 必须是 preview、draft 或 publish")
    item = load_download(out_dir, tweet_id)
    manifest = json.loads((Path(out_dir) / "manifest.json").read_text(encoding="utf-8"))
    minimum_selected = manifest.get("minimum_selected")
    required_selected = (max(minimum_selected, manifest.get("selection_target") or 0)
                         if minimum_selected is not None else 0)
    if action in ("publish", "draft") and len(manifest.get("downloads", [])) < required_selected:
        raise ValueError(
            "发布名单至少要有 %d 条，当前只有 %d 条；禁止生成发布计划，请继续搜索递补"
            % (required_selected, len(manifest.get("downloads", []))))
    topic_plan = manifest.get("topic_plan") or {}
    candidate = item["candidate"]
    title, body, topics, video = validate_content(item)
    match = history.check(candidate.get("id"), title)
    topic_details = {
        "brand": item.get("brand") or "",
        "file_topic": item.get("file_topic") or "",
        "topic_pillar": topic_plan.get("pillar") or "",
        "topic_series_id": topic_plan.get("series_id") or "",
        "topic_series_episode": topic_plan.get("series_episode") or "",
        "topic_strategy_version": topic_plan.get("strategy_version") or "",
    }
    base = {
        "source_id": str(candidate.get("id")),
        "source_url": candidate.get("url") or "",
        "title": title,
        "history_file": str(history.path),
        **{key: value for key, value in topic_details.items() if value != ""},
    }
    if match:
        return {"status": "skipped", "reason": "already_published", "match": match, **base}
    disclosure = str(item.get("ai_disclosure") or "").strip()
    if action in ("publish", "draft") and not disclosure:
        raise ValueError("发布或保存草稿前必须填写 ai_disclosure")
    sections = [body]
    if disclosure:
        sections.append(disclosure)
    sections.append(" ".join(topics))
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
        "full_text": "\n\n".join(sections),
        "browser_actions": browser_actions,
        **base,
    }


def save_plan(out_dir, plan):
    out_dir = Path(out_dir)
    path = out_dir / "xiaohongshu_publish_plan.json"
    serialized = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    path.write_text(serialized, encoding="utf-8")
    execution_path = out_dir / "plans" / ("%s.json" % plan["source_id"])
    if plan.get("status") == "ready" and plan.get("action") in ("publish", "draft"):
        execution_path.parent.mkdir(exist_ok=True)
        execution_path.write_text(serialized, encoding="utf-8")
    elif execution_path.is_file():
        execution_path.unlink()
    return path


def load_plan(out_dir):
    plan = json.loads((Path(out_dir) / "xiaohongshu_publish_plan.json").read_text(encoding="utf-8"))
    if plan.get("status") != "ready":
        raise ValueError("发布计划不是 ready 状态，不能记录结果")
    return plan


def topic_metadata(plan):
    fields = (
        "brand", "file_topic", "topic_pillar", "topic_series_id",
        "topic_series_episode", "topic_strategy_version", "ai_disclosure",
    )
    return {key: plan[key] for key in fields if key in plan}


def record_result(out_dir, history, status="published", note_url="", result_url=""):
    out_dir = Path(out_dir)
    plan = load_plan(out_dir)
    if plan.get("action") != "publish":
        raise ValueError("只有 publish 计划可以写入已发布历史")
    record = history.add(
        plan["source_id"], plan["title"], plan.get("source_url", ""), status,
        note_url=note_url, result_url=result_url, video_file=plan.get("video_file", ""),
        **topic_metadata(plan),
    )
    receipt = {
        "status": status,
        "source_id": plan["source_id"],
        "title": plan["title"],
        "note_url": note_url,
        "result_url": result_url,
        "history_file": str(history.path),
        "recorded_at": record["recorded_at"],
    }
    receipt_path = out_dir / "xiaohongshu_publish_receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt


def record_draft(out_dir, status="draft_saved", result_url="", draft_history=None):
    out_dir = Path(out_dir)
    if status not in ("draft_saved", "unconfirmed_draft"):
        raise ValueError("草稿状态必须是 draft_saved 或 unconfirmed_draft")
    plan = load_plan(out_dir)
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
    plan_parser = commands.add_parser("plan", help="生成发布计划，不直接操作网页；默认直接发布")
    plan_parser.add_argument("--tweet-id", required=True)
    plan_parser.add_argument("--action", choices=ACTIONS, default="publish")
    record_parser = commands.add_parser("record", help="仅在网页发布成功或结果待确认后调用")
    record_parser.add_argument("--status", choices=("published", "unconfirmed"), default="published")
    record_parser.add_argument("--note-url", default="")
    record_parser.add_argument("--result-url", default="")
    draft_parser = commands.add_parser("record-draft", help="仅在草稿保存成功或结果待确认后调用")
    draft_parser.add_argument("--status", choices=("draft_saved", "unconfirmed_draft"), default="draft_saved")
    draft_parser.add_argument("--result-url", default="")
    args = parser.parse_args(argv)
    out_dir = Path(args.out).resolve()
    history = PublishedHistory(args.history_file)
    try:
        if args.command == "plan":
            plan = build_plan(out_dir, args.tweet_id, history, args.action)
            path = save_plan(out_dir, plan)
            print(json.dumps({"plan_file": str(path), **plan}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "record-draft":
            receipt = record_draft(out_dir, args.status, args.result_url)
        else:
            receipt = record_result(out_dir, history, args.status, args.note_url, args.result_url)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, "错误: %s\n" % exc)


if __name__ == "__main__":
    sys.exit(main())

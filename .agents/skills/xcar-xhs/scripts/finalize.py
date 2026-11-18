#!/usr/bin/env python3
"""用最终标题、推荐正文和话题重命名视频、抽帧，并生成发布表格。"""

import argparse
import hashlib
import datetime
import json
import re
from pathlib import Path

from content_policy import (
    content_duplicate,
    content_identity,
    normalize_brand,
    strip_material_source_disclosure,
    strip_preset_ai_disclosure,
    validate_no_material_source_copy,
    validate_score,
    validate_verifiable_content,
)
from publication_queue import PublicationQueue, WAITING_STATUSES
from publish_history import ACTIVE_STATUSES, PublishedHistory, locked_history
from publication_runtime import atomic_json
from candidate_review import candidate_sort_key, validate_review, file_digest
from topic_policy import explainer_video_match

START_MARKER = "<!-- PUBLISH_TABLES_START -->"
END_MARKER = "<!-- PUBLISH_TABLES_END -->"
TABLE_HEADER = "| # | 标题名 | 推荐正文 | 推荐话题 | 文件名 | 本地路径 | 时长 | 点赞 | 原帖 |"
TABLE_SEPARATOR = "|---|---|---|---|---|---|---|---|---|"


def safe_title(title, max_length=48):
    title = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "-", title)
    title = re.sub(r"\s+", " ", title).strip(" .")
    if not title:
        raise ValueError("标题不能为空或只包含文件系统保留字符")
    return title[:max_length].rstrip(" .")


def load_topics(item, tweet_id):
    raw_topics = item.get("topics")
    if raw_topics is None:
        return ["#汽车", "#改装车"]
    if not isinstance(raw_topics, list):
        raise ValueError("推文 %s 的 topics 必须是字符串数组" % tweet_id)
    topics = []
    for raw_topic in raw_topics:
        if not isinstance(raw_topic, str):
            raise ValueError("推文 %s 的 topics 必须是字符串数组" % tweet_id)
        topic = re.sub(r"\s+", "", raw_topic.strip())
        if not topic:
            raise ValueError("推文 %s 的 topics 不能包含空话题" % tweet_id)
        topic = topic if topic.startswith("#") else "#" + topic
        if topic == "#":
            raise ValueError("推文 %s 的 topics 不能包含空话题" % tweet_id)
        if topic not in topics:
            topics.append(topic)
    if not topics:
        raise ValueError("推文 %s 的 topics 不能为空数组" % tweet_id)
    return topics


def load_publish(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("publish.json 必须是以推文 ID 为键的 JSON 对象")
    result = {}
    for tweet_id, item in data.items():
        if not isinstance(item, dict):
            raise ValueError("推文 %s 的发布内容必须是对象" % tweet_id)
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        brand = str(item.get("brand") or "汽车").strip()
        file_topic = str(item.get("file_topic") or title).strip()
        if not title or not body:
            raise ValueError("推文 %s 缺少 title 或 body" % tweet_id)
        validate_no_material_source_copy(body)
        if not brand or not file_topic:
            raise ValueError("推文 %s 的 brand 或 file_topic 不能为空" % tweet_id)
        result[str(tweet_id)] = {
            "title": title,
            "body": body,
            "brand": brand,
            "file_topic": file_topic,
            "topics": load_topics(item, tweet_id),
            "ai_disclosure": strip_preset_ai_disclosure(
                strip_material_source_disclosure(item.get("ai_disclosure"))),
            "model": str(item.get("model") or "").strip(),
            "modification_project": str(item.get("modification_project") or "").strip(),
            "title_detail": str(item.get("title_detail") or "").strip(),
            "verifiable_fact": str(item.get("verifiable_fact") or "").strip(),
            "fact_source": str(item.get("fact_source") or "").strip(),
            "discussion_question": str(item.get("discussion_question") or "").strip(),
            "score_breakdown": item.get("score_breakdown"),
        }
    return result


def markdown_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_row(item, index, rejected=False):
    candidate = item["candidate"]
    video = item["video"]
    path = Path(video["file"])
    title = "[%s](<%s>)" % (item["title"], path.resolve().as_posix())
    cells = [
        index,
        title,
        item["recommendation"],
        " ".join(item["topics"]),
        "`%s`" % path.name,
        str(path.parent.resolve()),
        "%.1fs" % video["duration"],
        candidate["likes"],
        "[链接](%s)" % candidate["url"],
    ]
    if rejected:
        cells.append(item["reason"])
    return "| " + " | ".join(markdown_cell(cell) for cell in cells) + " |"


def render_publish_tables(downloads, rejected):
    lines = [START_MARKER, "## 入选", "", TABLE_HEADER, TABLE_SEPARATOR]
    lines.extend(render_row(item, index) for index, item in enumerate(downloads, 1))
    lines += ["", "## 被淘汰", ""]
    if rejected:
        lines += [TABLE_HEADER + " 淘汰原因 |", TABLE_SEPARATOR + "---|"]
        lines.extend(render_row(item, index, True) for index, item in enumerate(rejected, 1))
    else:
        lines.append("无淘汰")
    lines.append(END_MARKER)
    return "\n".join(lines)


def update_report(path, tables):
    report = path.read_text() if path.is_file() else "# X 汽车视频搜索报告\n"
    if START_MARKER in report and END_MARKER in report:
        start = report.index(START_MARKER)
        end = report.index(END_MARKER) + len(END_MARKER)
        report = report[:start] + tables + report[end:]
    else:
        report = report.rstrip() + "\n\n" + tables
    path.write_text(report.rstrip() + "\n")


def prepare_rename(source, target):
    if source == target:
        return None
    if target.exists():
        raise ValueError("目标文件已存在：%s" % target)
    if not source.is_file():
        raise ValueError("视频文件不存在：%s" % source)
    return source, target


def resolve_source(source, tweet_id, suffix, frame_index=None):
    if source.is_file():
        return source
    id_suffix = "%s%s" % (tweet_id, suffix) if frame_index is None else "%s_%d%s" % (
        tweet_id, frame_index, suffix)
    matches = list(source.parent.glob("*_" + id_suffix))
    if len(matches) == 1:
        return matches[0]
    raise ValueError("媒体文件不存在或无法唯一匹配：%s" % source)


def reject_explainer_downloads(manifest):
    allowed = []
    rejected = manifest.setdefault("rejected", [])
    for item in manifest.get("downloads", []):
        match = explainer_video_match(item)
        if not match:
            allowed.append(item)
            continue
        item["reason"] = "讲解视频禁选：真人出镜、口播或屏幕讲解（匹配“%s”）" % match
        verification = item.setdefault("verification", {})
        if isinstance(verification, dict):
            verification["status"] = "rejected"
            verification["rejection_policy"] = "explainer_video"
        rejected.append(item)
    manifest["downloads"] = allowed
    stats = manifest.setdefault("stats", {})
    stats["explainer_rejected"] = sum(
        1 for item in rejected
        if (item.get("verification") or {}).get("rejection_policy") == "explainer_video"
    )


def apply_publish_content(items, publish):
    for item in items:
        content = publish[str(item["candidate"]["id"])]
        item["title"] = content["title"]
        item["recommendation"] = content["body"]
        for key in (
            "brand", "model", "modification_project", "file_topic", "title_detail",
            "verifiable_fact", "fact_source", "discussion_question", "topics", "ai_disclosure",
        ):
            item[key] = content[key]
        if content.get("score_breakdown") is not None:
            item["score_breakdown"] = content["score_breakdown"]
            item["score"] = sum(content["score_breakdown"].values())


def reject_duplicate_downloads(manifest, history_records, queue_records):
    downloads = sorted(
        manifest.get("downloads", []),
        key=candidate_sort_key,
    )
    current_ids = {str(item["candidate"]["id"]) for item in downloads}
    history_records = [record for record in history_records
                       if str(record.get("source_id") or "") not in current_ids]
    queue_records = [record for record in queue_records
                     if str(record.get("source_id") or "") not in current_ids]
    structured = manifest.get("minimum_selected") is not None
    accepted, rejected = [], []
    seen_brands = set()
    for item in downloads:
        identity = content_identity(item)
        duplicate = content_duplicate(identity, accepted, days=None)
        duplicate = duplicate or content_duplicate(
            identity, history_records, days=30, statuses=ACTIVE_STATUSES)
        duplicate = duplicate or content_duplicate(
            identity, queue_records, days=None, statuses=WAITING_STATUSES)
        brand = normalize_brand(item.get("brand"))
        if duplicate:
            item["reason"] = "车型级去重：%s（%s）" % (
                duplicate["reason"], duplicate["title"] or duplicate["source_id"])
        elif structured and brand and brand in seen_brands:
            item["reason"] = "车型级去重：同品牌每批最多一条（%s）" % item.get("brand")
        else:
            accepted.append(item)
            if brand:
                seen_brands.add(brand)
            continue
        item.setdefault("verification", {})["status"] = "rejected"
        item["verification"]["rejection_policy"] = "content_duplicate"
        rejected.append(item)
    manifest["downloads"] = accepted
    manifest.setdefault("rejected", []).extend(rejected)
    manifest.setdefault("stats", {})["content_duplicate_rejected"] = len(rejected)


def _finalize(out_dir, publish_file):
    out_dir = Path(out_dir).resolve()
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    publish = load_publish(publish_file)
    items = manifest.get("downloads", []) + manifest.get("rejected", [])
    ids = [str(item["candidate"]["id"]) for item in items]
    missing = sorted(set(ids) - set(publish))
    unknown = sorted(set(publish) - set(ids))
    if missing or unknown:
        raise ValueError("发布内容与 manifest 不一致；缺少=%s，多余=%s" % (missing, unknown))

    apply_publish_content(manifest.get("downloads", []) + manifest.get("rejected", []), publish)
    reject_explainer_downloads(manifest)
    minimum_selected = manifest.get("minimum_selected")
    if minimum_selected is not None:
        required_selected = max(minimum_selected, manifest.get("selection_target") or 0)
        if len(manifest.get("downloads", [])) < required_selected:
            manifest['selection_complete'] = False
            manifest.pop('finalized_at', None)
            atomic_json(manifest_path, manifest)
            raise ValueError(
                "核验入选名单至少要有 %d 条，当前只有 %d 条；请继续扩大检索并递补合格视频后再定稿"
                % (required_selected, len(manifest.get("downloads", []))))
        for item in manifest.get("downloads", []):
            score, breakdown = validate_score(item)
            validate_verifiable_content(item)
            validate_review(item)
            item["score"] = score
            item["score_breakdown"] = breakdown
            item.setdefault("verification", {})["score"] = score
            item["verification"]["score_breakdown"] = breakdown
    history_path = manifest.get("publish_history_file")
    history_records = PublishedHistory(history_path).records() if history_path else []
    queue = PublicationQueue(history_path=history_path) if history_path else PublicationQueue(
        path=out_dir / "publish_queue.json")
    reject_duplicate_downloads(manifest, history_records, queue.records())
    if minimum_selected is not None:
        required_selected = max(minimum_selected, manifest.get("selection_target") or 0)
        if len(manifest.get("downloads", [])) < required_selected:
            manifest['selection_complete'] = False
            manifest.pop('finalized_at', None)
            atomic_json(manifest_path, manifest)
            raise ValueError(
                "核验入选名单至少要有 %d 条，当前只有 %d 条；请继续扩大检索并递补合格视频后再定稿"
                % (required_selected, len(manifest.get("downloads", []))))
        manifest["selection_complete"] = True
        manifest.setdefault("stats", {})["selection_complete"] = True
    operations = []
    for group_name in ("downloads", "rejected"):
        group = manifest.get(group_name, [])
        target_dir = out_dir / ("videos" if group_name == "downloads" else "rejected")
        for number, item in enumerate(group, 1):
            candidate = item["candidate"]
            tweet_id = str(candidate["id"])
            content = publish[tweet_id]
            brand = safe_title(content["brand"]).replace("_", "-")
            file_topic = safe_title(content["file_topic"]).replace("_", "-")
            video = item["video"]
            source = Path(video["file"]).resolve()
            source = resolve_source(source, tweet_id, source.suffix)
            target = target_dir / ("%d.%s_%s%s" % (number, brand, file_topic, source.suffix))
            operation = prepare_rename(source, target)
            if operation:
                operations.append(operation)
            video["file"] = str(target)
            for index, frame in enumerate(item.get("frames", []), 1):
                frame_source = Path(frame["file"]).resolve()
                frame_source = resolve_source(frame_source, tweet_id, frame_source.suffix, index)
                frame_target = frame_source.with_name("%d.%s_%s_%d%s" % (
                    number, brand, file_topic, index, frame_source.suffix))
                operation = prepare_rename(frame_source, frame_target)
                if operation:
                    operations.append(operation)
                frame["file"] = str(frame_target)
            item["title"] = content["title"]
            item["recommendation"] = content["body"]
            item["brand"] = content["brand"]
            item["file_topic"] = content["file_topic"]
            item["topics"] = content["topics"]
            item["ai_disclosure"] = content["ai_disclosure"]

    for group_name in ("downloads", "rejected"):
        if manifest.get(group_name):
            (out_dir / ("videos" if group_name == "downloads" else "rejected")).mkdir(exist_ok=True)
    manifest["finalized_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    transaction = {'status': 'pending', 'manifest': manifest, 'operations': []}
    for index, (source, target) in enumerate(operations):
        transaction['operations'].append({'source': str(source), 'target': str(target),
            'temporary': str(out_dir / ('.finalize-%d.tmp' % index)), 'sha256': file_digest(source)})
    atomic_json(out_dir / 'finalize_transaction.json', transaction)
    return recover_finalization(out_dir)


def recover_finalization(out_dir):
    out_dir = Path(out_dir)
    path = out_dir / 'finalize_transaction.json'
    if not path.exists():
        return None
    transaction = json.loads(path.read_text())
    if transaction['status'] == 'complete':
        return None
    # 先暂存全部文件，再完成目标命名；每步可依据哈希恢复。
    for operation in transaction['operations']:
        source, temporary, target = (Path(operation[key]) for key in ('source', 'temporary', 'target'))
        if temporary.exists():
            candidate = temporary
        elif source.exists():
            candidate = source
        elif target.exists():
            candidate = target
        else:
            raise ValueError('定稿恢复缺少媒体：' + str(source))
        if file_digest(candidate) != operation['sha256']:
            raise ValueError('定稿恢复媒体内容已变化：' + str(candidate))
        if candidate == source:
            source.rename(temporary)
    for operation in transaction['operations']:
        temporary, target = Path(operation['temporary']), Path(operation['target'])
        if temporary.exists():
            if target.exists():
                raise ValueError('定稿恢复目标已存在：' + str(target))
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.rename(target)
    manifest = transaction['manifest']
    atomic_json(out_dir / 'manifest.json', manifest)
    tables = render_publish_tables(manifest.get('downloads', []), manifest.get('rejected', []))
    update_report(out_dir / 'REPORT.md', tables)
    transaction['status'] = 'complete'
    atomic_json(path, transaction)
    return tables


def finalize(out_dir, publish_file):
    out_dir = Path(out_dir).resolve()
    with locked_history(out_dir / '.finalize.lock', create=True):
        recovered = recover_finalization(out_dir)
        if recovered is not None:
            return recovered
        return _finalize(out_dir, publish_file)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="finder 输出目录")
    parser.add_argument("--publish-file", required=True, help="标题、正文和推荐话题 JSON")
    args = parser.parse_args()
    try:
        tables = finalize(args.out, args.publish_file)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, "错误: %s\n" % exc)
    print(tables)


if __name__ == "__main__":
    main()

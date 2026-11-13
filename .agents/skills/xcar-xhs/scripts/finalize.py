#!/usr/bin/env python3
"""用最终标题、推荐正文和话题重命名视频、抽帧，并生成发布表格。"""

import argparse
import datetime
import json
import re
from pathlib import Path

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
        if not brand or not file_topic:
            raise ValueError("推文 %s 的 brand 或 file_topic 不能为空" % tweet_id)
        result[str(tweet_id)] = {
            "title": title,
            "body": body,
            "brand": brand,
            "file_topic": file_topic,
            "topics": load_topics(item, tweet_id),
            "ai_disclosure": str(item.get("ai_disclosure") or "").strip(),
            "rights_confirmed": item.get("rights_confirmed") is True,
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


def finalize(out_dir, publish_file):
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

    reject_explainer_downloads(manifest)
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
            item["rights_confirmed"] = content["rights_confirmed"]

    for group_name in ("downloads", "rejected"):
        if manifest.get(group_name):
            (out_dir / ("videos" if group_name == "downloads" else "rejected")).mkdir(exist_ok=True)
    for source, target in operations:
        source.rename(target)
    manifest["published_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    tables = render_publish_tables(manifest.get("downloads", []), manifest.get("rejected", []))
    update_report(out_dir / "REPORT.md", tables)
    return tables


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

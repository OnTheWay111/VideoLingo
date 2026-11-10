#!/usr/bin/env python3
"""使用 yt-dlp 和显式 Web 登录会话检索、拉取各平台内容。"""

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from web_session import (
    SessionError,
    add_session_arguments,
    redact_session_values,
    session_summary,
    ytdlp_session_args,
)


PLATFORM_DOMAINS = {
    "x.com": "x", "twitter.com": "x",
    "youtube.com": "youtube", "youtu.be": "youtube",
    "tiktok.com": "tiktok", "instagram.com": "instagram",
    "facebook.com": "facebook", "fb.watch": "facebook",
    "bilibili.com": "bilibili", "b23.tv": "bilibili",
    "reddit.com": "reddit", "twitch.tv": "twitch", "vimeo.com": "vimeo",
}


def log(message):
    print(message, file=sys.stderr)


def run(cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def detect_platform(url, extractor=""):
    if extractor:
        key = extractor.lower().split(":")[0]
        aliases = {"youtube": "youtube", "twitter": "x", "tiktok": "tiktok",
                   "instagram": "instagram", "facebook": "facebook",
                   "bilibili": "bilibili", "reddit": "reddit", "twitch": "twitch",
                   "vimeo": "vimeo"}
        if key in aliases:
            return aliases[key]
    host = urlparse(url or "").netloc.lower().split("@")[-1].split(":")[0]
    for domain, platform in PLATFORM_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return platform
    return "web"


def normalize_date(entry):
    raw = entry.get("upload_date") or ""
    if re.fullmatch(r"\d{8}", raw):
        return "%s-%s-%s" % (raw[:4], raw[4:6], raw[6:8])
    timestamp = entry.get("timestamp") or entry.get("release_timestamp")
    if timestamp:
        return datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).date().isoformat()
    return ""


def normalize_entry(entry, source_url=""):
    url = entry.get("webpage_url") or entry.get("original_url") or source_url
    platform = detect_platform(url, entry.get("extractor_key") or entry.get("extractor") or "")
    raw_id = str(entry.get("id") or entry.get("display_id") or url)
    title = entry.get("title") or entry.get("description") or raw_id
    description = entry.get("description") or ""
    return {
        "id": "%s-%s" % (platform, raw_id),
        "source_id": raw_id,
        "platform": platform,
        "url": url,
        "author": entry.get("uploader_id") or entry.get("channel_id") or entry.get("uploader") or "",
        "author_name": entry.get("channel") or entry.get("uploader") or "",
        "date": normalize_date(entry),
        "likes": entry.get("like_count") or 0,
        "replies": entry.get("comment_count") or 0,
        "reposts": entry.get("repost_count") or 0,
        "views": entry.get("view_count") or 0,
        "title": title,
        "text": (title + ("\n" + description if description else "")).strip(),
        "duration": entry.get("duration") or 0,
        "thumbnail": entry.get("thumbnail") or "",
    }


def metadata_command(target, args, direct_url):
    cmd = ["yt-dlp", "--ignore-config"] + ytdlp_session_args(args)
    cmd += ["--dump-json", "--no-download", "--no-warnings"]
    if direct_url:
        cmd.append("--no-playlist")
    return cmd + ["--", target]


def parse_json_lines(output):
    entries = []
    for line in (output or "").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            entries.append(value)
    return entries


def error_detail(proc, env=None):
    detail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
    first = next((line.strip() for line in detail.splitlines() if line.strip()), "未知错误")
    return redact_session_values(first[:500], env)


def fetch_metadata(target, args, direct_url):
    cmd = metadata_command(target, args, direct_url)
    try:
        proc = run(cmd, timeout=300)
    except FileNotFoundError:
        return [], "找不到 yt-dlp，请先安装并确保它在 PATH 中"
    except subprocess.TimeoutExpired:
        return [], "yt-dlp 拉取超时（300s）"
    entries = parse_json_lines(proc.stdout)
    if proc.returncode != 0 and not entries:
        return [], error_detail(proc)
    items = [normalize_entry(entry, target if direct_url else "") for entry in entries]
    error = error_detail(proc) if proc.returncode != 0 else None
    return items, error


def download_command(item, args, videos_dir):
    height = args.max_height
    template = str(videos_dir / "%(uploader).40s_%(id)s.%(ext)s")
    return [
        "yt-dlp", "--ignore-config",
    ] + ytdlp_session_args(args) + [
        "--no-playlist", "--no-warnings", "--no-overwrites",
        "-f", "bv*[height<=%d]+ba/b[height<=%d]/b" % (height, height),
        "--merge-output-format", "mp4", "-o", template,
        "--write-info-json", "--print", "after_move:filepath",
        "--", item["url"],
    ]


def download_item(item, args, videos_dir):
    videos_dir.mkdir(parents=True, exist_ok=True)
    cmd = download_command(item, args, videos_dir)
    try:
        proc = run(cmd, timeout=1800)
    except subprocess.TimeoutExpired:
        return None, "下载超时（1800s）"
    except FileNotFoundError:
        return None, "找不到 yt-dlp"
    paths = [line.strip() for line in (proc.stdout or "").splitlines()
             if line.strip().lower().endswith((".mp4", ".mkv", ".webm", ".mov"))]
    path = paths[-1] if paths else None
    if proc.returncode != 0 or not path or not Path(path).is_file():
        return None, error_detail(proc)
    return {"file": str(Path(path).resolve()), "size_bytes": Path(path).stat().st_size}, None


def collect(args):
    if args.source == "youtube":
        targets = [("ytsearch%d:%s" % (args.limit, query), False) for query in args.query]
    else:
        targets = [(url, True) for url in args.url]
    items, errors, seen = [], [], set()
    for target, direct_url in targets:
        log("拉取 %s" % target)
        fetched, error = fetch_metadata(target, args, direct_url)
        if error:
            errors.append({"target": target, "error": error})
        for item in fetched:
            key = (item["platform"], item["source_id"], item["url"])
            if key not in seen:
                seen.add(key)
                items.append(item)
    return items, errors


def write_outputs(args, items, errors, downloads, out_dir):
    manifest = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": args.source,
        "queries": args.query,
        "urls": args.url,
        "session": session_summary(args),
        "item_count": len(items),
        "errors": errors,
        "items": items,
        "downloads": downloads,
    }
    with open(out_dir / "items.jsonl", "w") as fp:
        for item in items:
            fp.write(json.dumps(item, ensure_ascii=False) + "\n")
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    return manifest


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("url", "youtube"), default=None,
                        help="url=直接拉取链接；youtube=关键词搜索，默认按输入自动判断")
    parser.add_argument("--url", action="append", default=[], help="内容 URL，可重复")
    parser.add_argument("--query", action="append", default=[], help="YouTube 检索词，可重复")
    parser.add_argument("--limit", type=int, default=10, help="每组 YouTube 检索返回上限")
    parser.add_argument("--download", action="store_true", help="同时下载媒体；默认只拉取元数据")
    parser.add_argument("--max-height", type=int, default=1080,
                        help="优先限制视频高度；无匹配流时回退最佳可用画质")
    parser.add_argument("--out", default="work/web-content")
    add_session_arguments(parser)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.source is None:
        args.source = "youtube" if args.query and not args.url else "url"
    if args.limit < 1 or args.limit > 100 or args.max_height < 1:
        parser.error("--limit 必须在 1–100 之间，--max-height 必须大于 0")
    if args.source == "youtube" and (not args.query or args.url):
        parser.error("YouTube 检索需要 --query，且不能同时传 --url")
    if args.source == "url" and (not args.url or args.query):
        parser.error("URL 拉取需要 --url，且不能同时传 --query")
    try:
        session_summary(args)
    except SessionError as exc:
        parser.error(str(exc))

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        items, errors = collect(args)
        downloads = []
        if args.download:
            for item in items:
                result, error = download_item(item, args, out_dir / "videos")
                downloads.append({"item_id": item["id"], "video": result, "error": error})
                if error:
                    errors.append({"target": item["url"], "error": error})
        write_outputs(args, items, errors, downloads, out_dir)
    except SessionError as exc:
        parser.error(str(exc))

    for item in items:
        print("| %s | %s | %s | %s |" % (
            item["platform"], item["date"] or "-",
            item["title"].replace("|", "/")[:80], item["url"],
        ))
    log("内容 %d 条，错误 %d 条；报告: %s" % (
        len(items), len(errors), out_dir / "manifest.json"
    ))
    if not items or any(download.get("error") for download in downloads):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

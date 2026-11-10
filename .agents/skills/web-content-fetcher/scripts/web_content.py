#!/usr/bin/env python3
"""筛选适合微信公众号的 X 主题内容，并支持显式 URL 素材核验。"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from editorial import select_wechat_items
from web_session import (
    SessionError,
    add_session_arguments,
    load_key_value_file,
    prepare_x_auth,
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


def run(cmd, timeout=300, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


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


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_x_tweet(tweet):
    author = tweet.get("author") or {}
    text = (tweet.get("text") or "").strip()
    raw_date = tweet.get("created_at") or ""
    date = ""
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            date = datetime.datetime.strptime(raw_date, fmt).date().isoformat()
            break
        except ValueError:
            continue
    if not date:
        date = raw_date[:10]
    media = tweet.get("media") or []
    return {
        "id": "x-%s" % tweet.get("id"),
        "source_id": str(tweet.get("id") or ""),
        "platform": "x",
        "url": tweet.get("url") or "",
        "author": author.get("screen_name") or "",
        "author_name": author.get("name") or "",
        "date": date,
        "likes": to_int(tweet.get("favorite_count")),
        "replies": to_int(tweet.get("reply_count")),
        "reposts": to_int(tweet.get("retweet_count")),
        "views": to_int(tweet.get("view_count")),
        "title": text.replace("\n", " ")[:120],
        "text": text,
        "duration": 0,
        "thumbnail": (media[0].get("url") or "") if media else "",
    }


def resolve_xfetch(explicit=None):
    skill_root = Path(__file__).resolve().parent.parent.parent
    candidates = [explicit, os.environ.get("WEB_CONTENT_XFETCH")]
    candidates += [
        skill_root / "xfetch/scripts/xfetch.py",
        Path.cwd() / ".agents/skills/xfetch/scripts/xfetch.py",
        Path.cwd() / ".dsh/skills/xfetch/scripts/xfetch.py",
        Path.home() / ".agents/skills/xfetch/scripts/xfetch.py",
        Path.home() / ".dsh/skills/xfetch/scripts/xfetch.py",
        Path.home() / ".claude/skills/xfetch/scripts/xfetch.py",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise SessionError("X 检索需要 xfetch skill；请安装或用 --xfetch 指定 xfetch.py")


def resolve_x_python(explicit=None):
    if explicit:
        return explicit
    for candidate in (Path.cwd() / ".venv-xfetch/bin/python", Path.home() / ".venv-xfetch/bin/python"):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def x_search_command(query, args, py, xfetch):
    return [
        py, xfetch, "search", query, "--type", args.type,
        "-n", str(args.limit), "--format", "jsonl",
    ]


def x_auth_environment(args, py, xfetch):
    env = os.environ.copy()
    if args.auth_file:
        path = Path(os.path.expanduser(args.auth_file))
        if not path.is_file():
            raise SessionError("auth-file 不存在: %s" % path)
        env.update(load_key_value_file(path))
    env = prepare_x_auth(args, env, py, xfetch)
    try:
        proc = run([py, xfetch, "auth", "check"], timeout=120, env=env)
    except subprocess.TimeoutExpired:
        return env, "X 会话校验超时"
    except OSError as exc:
        return env, "X 会话校验失败: %s" % type(exc).__name__
    if proc.returncode != 0:
        return env, error_detail(proc, env)
    return env, None


def fetch_x_query(query, args, env, py, xfetch):
    try:
        proc = run(x_search_command(query, args, py, xfetch), timeout=300, env=env)
    except subprocess.TimeoutExpired:
        return [], "xfetch 检索超时（300s）"
    except OSError as exc:
        return [], "xfetch 启动失败: %s" % type(exc).__name__
    tweets = parse_json_lines(proc.stdout)
    if proc.returncode != 0 and not tweets:
        return [], error_detail(proc, env)
    items = [normalize_x_tweet(tweet) for tweet in tweets]
    error = error_detail(proc, env) if proc.returncode != 0 else None
    return items, error


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


def sort_items(items, sort):
    if sort == "none":
        return items
    field = {"likes": "likes", "replies": "replies", "reposts": "reposts", "views": "views"}[sort]
    return sorted(items, key=lambda item: (-(item.get(field) or 0), -(item.get("likes") or 0)))


def collect_x(args):
    py = resolve_x_python(args.python)
    xfetch = resolve_xfetch(args.xfetch)
    env, auth_error = x_auth_environment(args, py, xfetch)
    if auth_error:
        return [], [{"target": "X auth", "error": auth_error}]
    items, errors = [], []
    seen_queries = set()
    for query in args.query:
        if args.since and "since:" not in query:
            query += " since:%s" % args.since
        if query in seen_queries:
            continue
        seen_queries.add(query)
        log("检索 X: %s" % query)
        fetched, error = fetch_x_query(query, args, env, py, xfetch)
        if error:
            errors.append({"target": query, "error": error})
        items.extend(fetched)
    return items, errors


def collect(args):
    if args.source == "x":
        items, errors = collect_x(args)
    else:
        targets = [(url, True) for url in args.url]
        items, errors = [], []
        for target, direct_url in targets:
            log("拉取 %s" % target)
            fetched, error = fetch_metadata(target, args, direct_url)
            if error:
                errors.append({"target": target, "error": error})
            items.extend(fetched)
    deduped = []
    seen = set()
    for item in items:
        key = (item["platform"], item["source_id"], item["url"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return sort_items(deduped, args.sort), errors


def write_outputs(args, items, errors, downloads, out_dir, rejected=None, aliases=None):
    rejected = rejected or []
    manifest = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": args.source,
        "queries": args.query,
        "urls": args.url,
        "sort": args.sort,
        "x_search_type": args.type if args.source == "x" else None,
        "since": args.since,
        "topic_aliases": aliases or [],
        "min_wechat_score": getattr(args, "min_wechat_score", 70),
        "session": session_summary(args),
        "candidate_count": len(items) + len(rejected),
        "item_count": len(items),
        "rejected_count": len(rejected),
        "errors": errors,
        "items": items,
        "rejected_items": rejected,
        "downloads": downloads,
    }
    with open(out_dir / "items.jsonl", "w") as fp:
        for item in items:
            fp.write(json.dumps(item, ensure_ascii=False) + "\n")
    with open(out_dir / "rejected.jsonl", "w") as fp:
        for item in rejected:
            fp.write(json.dumps(item, ensure_ascii=False) + "\n")
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    return manifest


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("url", "x"), default=None,
                        help="url=显式素材链接；x=X 主题检索")
    parser.add_argument("--url", action="append", default=[], help="素材 URL，可重复")
    parser.add_argument("--query", action="append", default=[], help="X 检索式，可重复")
    parser.add_argument("--topic", default="", help="公众号选题的规范主题，例如 OpenAI Codex")
    parser.add_argument("--topic-alias", action="append", default=[],
                        help="额外主题别名，可重复；默认也会从 query 提取")
    parser.add_argument("--min-wechat-score", type=int, default=70,
                        help="公众号适配最低分，0–100，默认 70")
    parser.add_argument("--limit", type=int, default=10, help="每组关键词检索返回上限（1–100）")
    parser.add_argument("--sort", choices=("none", "likes", "replies", "reposts", "views"),
                        default="none", help="入选内容本地排序，默认保留平台顺序")
    parser.add_argument("--type", choices=("top", "latest", "media"), default="top",
                        help="X 搜索视图，默认 top")
    parser.add_argument("--since", default=None, help="X 检索起始日期 YYYY-MM-DD")
    parser.add_argument("--auth-file", default=None, help="X 专用 KEY=VALUE 登录态文件")
    parser.add_argument("--xfetch", default=None, help="X 检索使用的 xfetch.py 路径")
    parser.add_argument("--python", default=None, help="运行 xfetch 的 Python 解释器")
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
        args.source = "x" if args.query and not args.url else "url"
    if args.limit < 1 or args.limit > 100 or args.max_height < 1:
        parser.error("--limit 必须在 1–100 之间，--max-height 必须大于 0")
    if not 0 <= args.min_wechat_score <= 100:
        parser.error("--min-wechat-score 必须在 0–100 之间")
    if args.source == "x" and (not args.query or args.url):
        parser.error("X 检索需要 --query，且不能同时传 --url")
    if args.source == "url" and (not args.url or args.query):
        parser.error("URL 拉取需要 --url，且不能同时传 --query")
    if args.source != "x" and args.since:
        parser.error("--since 仅用于 X 关键词检索")
    try:
        session_summary(args)
    except SessionError as exc:
        parser.error(str(exc))

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        items, errors = collect(args)
        rejected, aliases = [], []
        if args.source == "x":
            items, rejected, aliases = select_wechat_items(
                items, topic=args.topic, aliases=args.topic_alias,
                queries=args.query, min_score=args.min_wechat_score,
            )
        downloads = []
        if args.download:
            for item in items:
                result, error = download_item(item, args, out_dir / "videos")
                downloads.append({"item_id": item["id"], "video": result, "error": error})
                if error:
                    errors.append({"target": item["url"], "error": error})
        write_outputs(args, items, errors, downloads, out_dir, rejected, aliases)
    except SessionError as exc:
        parser.error(str(exc))

    for item in items:
        print("| %s | %s | %s | %s | %s |" % (
            item["platform"], item.get("wechat_score", "-"), item["date"] or "-",
            item["title"].replace("|", "/")[:80], item["url"],
        ))
    log("入选 %d 条，淘汰 %d 条，错误 %d 条；报告: %s" % (
        len(items), len(rejected), len(errors), out_dir / "manifest.json"
    ))
    if (not items and not rejected) or any(download.get("error") for download in downloads):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

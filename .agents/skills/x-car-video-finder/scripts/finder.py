#!/usr/bin/env python3
"""x-car-video-finder: 提示词 -> X 检索式 -> 汽车视频过滤 -> 排序 -> 下载 -> 抽帧。

依赖: xfetch skill（twikit 搜索）、yt-dlp、ffmpeg。
登录态: 通过 --auth-file（KEY=VALUE）或 XFETCH_AUTH_TOKEN/XFETCH_CT0 环境变量提供。
"""

import argparse
import csv
import datetime
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# 汽车内容关键词：用于剔除“标题党/非汽车”的媒体推文
CAR_LEXICON = (
    r"(?i)(?:\b(?:cars?|auto|vehicle|suv|crossover|truck|pickup|supercar|hypercar|"
    r"sportscar|musclecars?|cybertruck|tesla|porsche|ferrari|lamborghini|mclaren|"
    r"bugatti|bmw|mercedes|amg|audi|rivian|ford|mustang|corvette|gtr|supra|jdm|"
    r"rally|offroad|off-road|drift|drag\s?race|car\s?meet|car\s?show|car\s?spotting|"
    r"test\s?drive|first\s?drive|car\s?review|exhaust|horsepower|engine|motorsport|f1)\b"
    r"|汽车|越野|超跑|跑车|改装车|车展|车评|试驾|赛车|赛道|皮卡|漂移|引擎|马力)"
)

# 提示词里出现这些词时，自动把它拼进检索式（中英映射）
BRAND_MAP = {
    "特斯拉": "tesla",
    "皮卡": "pickup",
    "越野": "offroad",
    "超跑": "supercar",
    "跑车": "sportscar",
    "车展": "car show",
    "车评": "car review",
    "试驾": "test drive",
    "改装": "tuned",
    "漂移": "drift",
    "赛车": "race car",
    "赛道": "track",
    "保时捷": "porsche",
    "法拉利": "ferrari",
    "兰博基尼": "lamborghini",
    "迈凯伦": "mclaren",
    "宝马": "bmw",
    "奔驰": "mercedes",
    "奥迪": "audi",
    "福特": "ford",
    "野马": "mustang",
    "科尔维特": "corvette",
    "汽车": "car",
    "引擎": "engine",
    "马力": "horsepower",
}

KNOWN_TERMS = [
    "cybertruck", "cyber truck", "tesla", "porsche", "911", "gt3", "ferrari",
    "lamborghini", "mclaren", "bugatti", "bmw", "m3", "m4", "mercedes", "amg",
    "audi", "rs6", "rivian", "ford", "mustang", "corvette", "gtr", "supra",
    "jdm", "rally", "offroad", "off-road", "drift", "supercar", "hypercar",
    "car meet", "car show", "car spotting", "car review", "test drive",
    "first drive", "drag race", "pickup", "suv",
]

SORT_FIELDS = {
    "likes": "favorite_count",
    "replies": "reply_count",
    "reposts": "retweet_count",
    "views": "view_count",
}

SORT_LABELS = {
    "likes": "点赞量",
    "replies": "评论数",
    "reposts": "转发数",
    "views": "播放量",
}

VIDEO_TYPES = {"video", "animated_gif", "gif"}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def die(msg, code=1):
    log("错误: " + msg)
    sys.exit(code)


def run(cmd, env=None, timeout=600, cwd=None):
    return subprocess.run(cmd, capture_output=True, text=True, env=env,
                          timeout=timeout, cwd=cwd)


def resolve_xfetch(explicit):
    candidates = [explicit, os.environ.get("XFINDER_XFETCH")]
    cwd = Path.cwd()
    candidates += [
        cwd / ".dsh/skills/xfetch/scripts/xfetch.py",
        cwd / ".agents/skills/xfetch/scripts/xfetch.py",
        cwd / ".claude/skills/xfetch/scripts/xfetch.py",
        Path.home() / ".dsh/skills/xfetch/scripts/xfetch.py",
        Path.home() / ".agents/skills/xfetch/scripts/xfetch.py",
        Path.home() / ".claude/skills/xfetch/scripts/xfetch.py",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return str(Path(c).resolve())
    die("找不到 xfetch.py，请用 --xfetch 指定，或先安装 xfetch skill。")


def resolve_python(explicit):
    if explicit:
        return explicit
    cwd = Path.cwd()
    for p in [
        cwd / ".venv-xfetch/bin/python",
        Path.home() / ".venv-xfetch/bin/python",
    ]:
        if p.is_file():
            return str(p)
    return sys.executable


def load_env(args):
    env = os.environ.copy()
    if args.auth_file:
        path = Path(os.path.expanduser(args.auth_file))
        if not path.is_file():
            die("auth-file 不存在: %s" % path)
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def auth_ok(py, xfetch, env):
    try:
        p = run([py, xfetch, "auth", "check"], env=env, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "auth check 超时"
    detail = ((p.stdout or "") + (p.stderr or "")).strip()
    return p.returncode == 0, detail


def q(token):
    return '"%s"' % token.replace('"', "")


def build_auto_queries(prompt, min_likes, since):
    lower = prompt.lower()
    models, scenes = [], []
    for cn, en in BRAND_MAP.items():
        if cn in prompt:
            models.append(en)
    for term in KNOWN_TERMS:
        if term in lower:
            if term in ("offroad", "off-road", "rally", "drift", "car meet",
                        "car show", "car spotting", "car review", "test drive",
                        "first drive", "drag race"):
                scenes.append(term)
            else:
                models.append(term)

    def dedupe(items):
        out = []
        for item in items:
            if item not in out:
                out.append(item)
        return out

    models, scenes = dedupe(models), dedupe(scenes)

    queries = []
    if models:
        base = "(%s)" % " OR ".join(q(m) for m in models[:3])
        if scenes:
            queries.append("%s (%s) filter:media" % (base, " OR ".join(q(s) for s in scenes[:2])))
        else:
            queries.append("%s (offroad OR POV OR exhaust OR review) filter:media" % base)
    else:
        queries.append('("car" OR "supercar" OR 汽车 OR 越野) filter:media')

    queries.append('("car meet" OR "car show" OR "car spotting" OR 车展) filter:media')
    queries.append('("car review" OR "test drive" OR "first drive" OR 试驾 OR 车评) filter:media')
    queries.append('("cybertruck" OR "offroad" OR "rally" OR 超跑 OR 越野 OR 改装车) filter:media')

    result = []
    for item in queries:
        if min_likes and "min_faves:" not in item:
            item += " min_faves:%d" % min_likes
        if since and "since:" not in item:
            item += " since:%s" % since
        result.append(item)
    return dedupe(result)


def search_query(py, xfetch, query, args, env, raw_dir):
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", query)[:60].strip("_") or "query"
    tweets, cursor, pages = [], None, max(1, args.pages)
    for page in range(pages):
        cmd = [py, xfetch, "search", query, "--type", args.type,
               "-n", str(args.limit), "--format", "jsonl", "--delay", str(args.delay)]
        if cursor:
            cmd += ["--cursor", cursor]
        log("搜索[%d/%d] %s" % (page + 1, pages, query))
        try:
            proc = run(cmd, env=env, timeout=300)
        except subprocess.TimeoutExpired:
            log("  超时，跳过")
            break
        if proc.returncode != 0:
            log("  失败(exit %d): %s" % (proc.returncode, (proc.stderr or "").strip()[:300]))
            break
        page_tweets = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                page_tweets.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        tweets.extend(page_tweets)
        log("  本页 %d 条" % len(page_tweets))
        (raw_dir / ("%s_page%d.jsonl" % (slug, page + 1))).write_text(proc.stdout or "")
        match = re.search(r"next_cursor=(\S+)", proc.stderr or "")
        cursor = match.group(1) if match else None
        if not cursor or not page_tweets:
            break
    return tweets


def has_video(tweet):
    return any((m.get("type") or "").lower() in VIDEO_TYPES for m in (tweet.get("media") or []))


def car_related(tweet, car_re):
    author = tweet.get("author") or {}
    haystack = " ".join([
        tweet.get("text") or "",
        author.get("screen_name") or "",
        author.get("name") or "",
    ])
    return bool(car_re.search(haystack))


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_date(raw):
    raw = raw or ""
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw[:10]


def normalize(tweet, car_re, dropped, require_car):
    if not has_video(tweet):
        return None
    if require_car and not car_related(tweet, car_re):
        dropped.append(tweet)
        return None
    author = tweet.get("author") or {}
    video_media = [m for m in (tweet.get("media") or [])
                   if (m.get("type") or "").lower() in VIDEO_TYPES]
    return {
        "id": str(tweet.get("id")),
        "url": tweet.get("url") or ("https://x.com/%s/status/%s" % (author.get("screen_name"), tweet.get("id"))),
        "author": author.get("screen_name") or "",
        "author_name": author.get("name") or "",
        "date": normalize_date(tweet.get("created_at")),
        "likes": to_int(tweet.get("favorite_count")),
        "replies": to_int(tweet.get("reply_count")),
        "reposts": to_int(tweet.get("retweet_count")),
        "views": to_int(tweet.get("view_count")),
        "text": (tweet.get("text") or "").replace("\n", " ").strip(),
        "media": video_media,
        "video_url": (video_media[0].get("url") if video_media else None),
    }


def sort_candidates(candidates, sort_key):
    field = SORT_FIELDS[sort_key]
    return sorted(candidates, key=lambda c: (-(c.get(field) or 0), -(c.get("likes") or 0)))


def print_table(candidates, sort_key, limit):
    print("| # | 点赞 | 评论 | 转发 | 播放 | 作者 | 日期 | 内容 |")
    print("|---|---|---|---|---|---|---|---|")
    for i, c in enumerate(candidates[:limit], 1):
        text = (c["text"] or "").replace("|", "/")[:46]
        print("| %d | %d | %d | %d | %d | @%s | %s | [%s](%s) |" % (
            i, c["likes"], c["replies"], c["reposts"], c["views"],
            c["author"], c["date"], text, c["url"]))


def probe_video(path):
    try:
        p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-show_entries", "stream=width,height,codec_type", "-of", "json", path],
                timeout=60)
        data = json.loads(p.stdout or "{}")
        duration = float(data.get("format", {}).get("duration") or 0)
        width = height = 0
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                width, height = stream.get("width", 0), stream.get("height", 0)
                break
        return {"duration": round(duration, 2), "width": width, "height": height}
    except Exception:  # noqa: BLE001
        return {"duration": 0, "width": 0, "height": 0}


def download_video(candidate, videos_dir, args):
    videos_dir.mkdir(parents=True, exist_ok=True)
    template = str(videos_dir / "%(uploader).40s_%(id)s.%(ext)s")
    height = args.max_height
    cmd = [
        "yt-dlp", "--no-playlist", "--no-warnings", "--no-overwrites",
        "-f", "bv*[height<=%d]+ba/b[height<=%d]/b" % (height, height),
        "--merge-output-format", "mp4",
        "-o", template, "--write-info-json",
        "--print", "after_move:filepath",
        candidate["url"],
    ]
    log("下载 %s" % candidate["url"])
    try:
        proc = run(cmd, timeout=1800)
    except subprocess.TimeoutExpired:
        log("  下载超时")
        return None, "timeout"
    paths = [line.strip() for line in (proc.stdout or "").splitlines()
             if line.strip().lower().endswith((".mp4", ".mkv", ".webm"))]
    path = paths[-1] if paths else None
    if path is None:
        hits = [p for p in glob.glob(str(videos_dir / ("*_%s.*" % candidate["id"])))
                if not p.endswith(".info.json")]
        path = hits[0] if hits else None
    if not path or not os.path.isfile(path):
        log("  失败(exit %d): %s" % (proc.returncode, (proc.stderr or "").strip()[:400]))
        return None, (proc.stderr or "")[-500:]
    info = probe_video(path)
    size = os.path.getsize(path)
    log("  完成 %s (%.1f MB)" % (os.path.basename(path), size / 1024 / 1024))
    return {"file": path, "size_bytes": size, **info}, None


def extract_frames(candidate, video_path, frames_dir, count):
    if count <= 0:
        return []
    frames_dir.mkdir(parents=True, exist_ok=True)
    info = probe_video(video_path)
    duration = info["duration"] or 3.0
    frames = []
    for i in range(count):
        frac = 0.15 if count == 1 else 0.15 + 0.6 * i / (count - 1)
        t = min(max(0.5, duration * frac), max(0.5, duration - 0.4))
        out = frames_dir / ("%s_%d.jpg" % (candidate["id"], i + 1))
        try:
            run(["ffmpeg", "-v", "error", "-ss", "%.2f" % t, "-i", video_path,
                 "-frames:v", "1", "-vf", "scale=640:-2", str(out), "-y"], timeout=120)
        except subprocess.TimeoutExpired:
            continue
        if out.is_file() and out.stat().st_size > 0:
            frames.append({"file": str(out), "at_sec": round(t, 2)})
    return frames


def write_outputs(args, prompt, queries, candidates, downloads, dropped, stats, out_dir):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    manifest = {
        "prompt": prompt,
        "generated_at": now,
        "sort": args.sort,
        "queries": queries,
        "stats": stats,
        "candidates": candidates,
        "downloads": downloads,
        "dropped_non_car_sample": [
            {"id": str(t.get("id")), "url": t.get("url"),
             "text": (t.get("text") or "")[:120]} for t in dropped[:10]
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    with open(out_dir / "candidates.csv", "w", newline="") as fp:
        fields = ["id", "url", "author", "author_name", "date", "likes", "replies",
                  "reposts", "views", "text"]
        writer = csv.DictWriter(fp, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(candidates)

    by_likes = sort_candidates(candidates, "likes")
    by_replies = sort_candidates(candidates, "replies")
    lines = ["# X 汽车视频搜索报告", "",
             "- 提示词：%s" % prompt,
             "- 生成时间：%s" % now,
             "- 排序：%s（%s）" % (args.sort, SORT_FIELDS[args.sort]),
             "- 检索式：%d 组，候选视频推文 %d 条，非汽车剔除 %d 条，下载 %d 条" % (
                 len(queries), len(candidates), len(dropped), len(downloads)),
             "", "## 检索式", ""]
    for query in queries:
        lines.append("- `%s`" % query)
    lines += ["", "## 按点赞量 Top 10", "",
              "| 点赞 | 评论 | 转发 | 播放 | 作者 | 日期 | 链接 |",
              "|---|---|---|---|---|---|---|"]
    for c in by_likes[:10]:
        lines.append("| %d | %d | %d | %d | @%s | %s | [链接](%s) |" % (
            c["likes"], c["replies"], c["reposts"], c["views"], c["author"], c["date"], c["url"]))
    lines += ["", "## 按评论数 Top 10", "",
              "| 评论 | 点赞 | 作者 | 日期 | 链接 |",
              "|---|---|---|---|---|"]
    for c in by_replies[:10]:
        lines.append("| %d | %d | @%s | %s | [链接](%s) |" % (
            c["replies"], c["likes"], c["author"], c["date"], c["url"]))
    lines += ["", "## 下载清单", ""]
    if downloads:
        lines += ["| 文件 | 作者 | 分辨率 | 时长 | 点赞 | 评论 | 链接 |",
                  "|---|---|---|---|---|---|---|"]
        for item in downloads:
            video = item["video"]
            lines.append("| `%s` | @%s | %dx%d | %.1fs | %d | %d | %s |" % (
                os.path.basename(video["file"]), item["candidate"]["author"],
                video["width"], video["height"], video["duration"],
                item["candidate"]["likes"], item["candidate"]["replies"],
                item["candidate"]["url"]))
    else:
        lines.append("（本次未下载；加 `--top N` 或去掉 `--dry-run` 即可下载。）")
    if downloads:
        lines += ["", "## 抽帧核验", ""]
        for item in downloads:
            lines.append("- `%s`：" % item["candidate"]["url"])
            for frame in item["frames"]:
                lines.append("  - `%s`（第 %.1fs）" % (frame["file"], frame["at_sec"]))
    lines += ["", "> 文件：`videos/` 视频、`frames/` 抽帧、`manifest.json` 全量元数据、"
                  "`candidates.csv` 可自行排序。"]
    (out_dir / "REPORT.md").write_text("\n".join(lines) + "\n")

    log("报告: %s" % (out_dir / "REPORT.md"))
    log("清单: %s" % (out_dir / "manifest.json"))


def main():
    ap = argparse.ArgumentParser(description="按提示词搜 X 汽车视频，排序后下载")
    ap.add_argument("--prompt", required=True, help="原始提示词（中文/英文均可）")
    ap.add_argument("--query", action="append", default=[], help="X 检索式，可重复")
    ap.add_argument("--sort", choices=sorted(SORT_FIELDS), default="likes")
    ap.add_argument("--top", type=int, default=3, help="下载前 N 条")
    ap.add_argument("--candidates", type=int, default=15, help="stdout 展示条数")
    ap.add_argument("--limit", type=int, default=25, help="每组每页条数")
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--type", choices=("top", "latest", "media"), default="top")
    ap.add_argument("--since", default=None)
    ap.add_argument("--min-likes", type=int, default=None)
    ap.add_argument("--delay", type=float, default=1.5)
    ap.add_argument("--out", default="work/xfetch-run")
    ap.add_argument("--auth-file", default=None)
    ap.add_argument("--xfetch", default=None)
    ap.add_argument("--python", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-car-filter", action="store_true")
    ap.add_argument("--max-height", type=int, default=1080)
    ap.add_argument("--frames", type=int, default=2)
    args = ap.parse_args()

    out_dir = Path(args.out).resolve()
    raw_dir = out_dir / "raw"
    videos_dir = out_dir / "videos"
    frames_dir = out_dir / "frames"
    for directory in (out_dir, raw_dir):
        directory.mkdir(parents=True, exist_ok=True)

    xfetch = resolve_xfetch(args.xfetch)
    py = resolve_python(args.python)
    env = load_env(args)

    if not (env.get("XFETCH_AUTH_TOKEN") and env.get("XFETCH_CT0")):
        log("提示: 未提供 auth-file/环境变量，尝试使用 xfetch 已保存的会话。")
    ok, detail = auth_ok(py, xfetch, env)
    if not ok:
        die("X 登录态不可用。先运行:\n"
            "  python3 .agents/skills/x-car-video-finder/scripts/auth_from_chrome.py --out /tmp/xfetch-auth.env\n"
            "再传 --auth-file /tmp/xfetch-auth.env。\n详情: %s" % detail[:300], code=2)

    queries = args.query or build_auto_queries(args.prompt, args.min_likes, args.since)
    if args.query and args.min_likes:
        queries = [q_ if "min_faves:" in q_ else "%s min_faves:%d" % (q_, args.min_likes) for q_ in queries]
    if args.query and args.since:
        queries = [q_ if "since:" in q_ else "%s since:%s" % (q_, args.since) for q_ in queries]

    car_re = re.compile(CAR_LEXICON)
    seen, dropped, candidates = set(), [], []
    for query in queries:
        for tweet in search_query(py, xfetch, query, args, env, raw_dir):
            tid = str(tweet.get("id") or "")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            if args.min_likes and to_int(tweet.get("favorite_count")) < args.min_likes:
                continue
            item = normalize(tweet, car_re, dropped, not args.no_car_filter)
            if item:
                candidates.append(item)

    if not candidates:
        die("没有找到符合条件的视频推文。试试放宽 --min-likes/--since，或调整检索式。")

    ranked = sort_candidates(candidates, args.sort)
    for index, item in enumerate(ranked, 1):
        item["rank"] = index

    print()
    print("## 候选（按 %s 排序）" % SORT_LABELS[args.sort])
    print_table(ranked, args.sort, args.candidates)
    print()

    stats = {"queries": len(queries), "candidates": len(candidates),
             "dropped_non_car": len(dropped), "requested_top": args.top}

    downloads = []
    if not args.dry_run and args.top > 0:
        for candidate in ranked[:args.top]:
            video, error = download_video(candidate, videos_dir, args)
            if not video:
                log("  跳过 %s（%s）" % (candidate["url"], str(error)[:120]))
                continue
            frames = extract_frames(candidate, video["file"], frames_dir, args.frames)
            downloads.append({"candidate": candidate, "video": video, "frames": frames})

    write_outputs(args, args.prompt, queries, ranked, downloads, dropped, stats, out_dir)

    print("完成：%d 条候选，%d 条已下载。" % (len(candidates), len(downloads)))
    if downloads:
        print("视频目录: %s" % videos_dir)
        print("抽帧目录: %s（用 read_image 核验）" % frames_dir)
    print("报告: %s" % (out_dir / "REPORT.md"))


if __name__ == "__main__":
    main()

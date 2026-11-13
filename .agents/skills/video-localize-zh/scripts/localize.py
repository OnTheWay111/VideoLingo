#!/usr/bin/env python3
"""Request-driven Chinese video localization: analyze, render, and approve."""

import argparse
import datetime
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

MODES = {"auto", "voiceover", "caption_light"}
RIGHTS = {"official_source", "original_creator", "licensed"}
INTERJECTIONS = {"oh", "ah", "wow", "yeah", "hmm", "hm", "uh", "eh", "hey"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def dump(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run(command, cwd=None):
    proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if proc.returncode:
        detail = (proc.stderr or proc.stdout or "command failed").strip()[-1500:]
        raise RuntimeError(detail)
    return proc


def probe(path):
    proc = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=index,codec_type,width,height", "-of", "json", str(path),
    ])
    data = json.loads(proc.stdout)
    data["duration"] = float(data.get("format", {}).get("duration") or 0)
    data["has_video"] = any(s.get("codec_type") == "video" for s in data.get("streams", []))
    data["has_audio"] = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return data


def request_data(path):
    request_path = Path(path).resolve()
    data = load_json(request_path)
    required = ("source_id", "video_file", "rights_status", "presentation")
    missing = [key for key in required if not data.get(key)]
    if missing:
        raise ValueError("请求缺少字段：" + ", ".join(missing))
    if data["presentation"] != "car_only":
        raise ValueError("只接受 presentation=car_only；不能用本地化绕过原片讲解形式禁选")
    if data["rights_status"] not in RIGHTS:
        raise ValueError("rights_status 必须是 official_source、original_creator 或 licensed")
    data["source_id"] = str(data["source_id"])
    video = Path(data["video_file"]).expanduser()
    if not video.is_absolute():
        video = request_path.parent / video
    data["video_file"] = str(video.resolve())
    if not video.is_file():
        raise ValueError("视频不存在：" + str(video))
    out = Path(data.get("out_dir") or request_path.parent / data["source_id"]).expanduser()
    if not out.is_absolute():
        out = request_path.parent / out
    data["out_dir"] = str(out.resolve())
    out.mkdir(parents=True, exist_ok=True)
    facts = data.get("facts")
    if not isinstance(facts, list) or not facts:
        raise ValueError("facts 至少包含一条带来源的事实")
    fact_ids = set()
    for fact in facts:
        if not all(str(fact.get(key) or "").strip() for key in ("id", "text", "source")):
            raise ValueError("每条 fact 都必须包含 id、text、source")
        if fact["id"] in fact_ids:
            raise ValueError("fact id 重复：" + fact["id"])
        fact_ids.add(fact["id"])
    mode = data.get("mode", "auto")
    if mode not in MODES:
        raise ValueError("mode 必须是 auto、voiceover 或 caption_light")
    data["mode"] = mode
    data["_request_path"] = str(request_path)
    return data


def extract_audio(data, info):
    out = Path(data["out_dir"])
    source = out / "source_audio.wav"
    asr = out / "asr_audio.wav"
    if info["has_audio"]:
        run(["ffmpeg", "-v", "error", "-y", "-i", data["video_file"], "-vn",
             "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(source)])
        run(["ffmpeg", "-v", "error", "-y", "-i", data["video_file"], "-vn",
             "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(asr)])
    else:
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-t", "%.3f" % info["duration"], "-c:a", "pcm_s16le", str(source)])
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
             "-t", "%.3f" % info["duration"], "-c:a", "pcm_s16le", str(asr)])
    return source, asr


def qwen_transcribe(audio, duration, language):
    project_root = Path(__file__).resolve().parents[4]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from core.asr_backend import qwen_asr_local

    original = qwen_asr_local.load_key

    def request_language(key):
        if key == "whisper.language":
            return language
        return original(key)

    qwen_asr_local.load_key = request_language
    try:
        result = qwen_asr_local.transcribe_audio(str(audio), str(audio), 0, duration)
    finally:
        qwen_asr_local.load_key = original
    return result


def normalized_transcript(data, audio, duration):
    supplied = data.get("transcript")
    if supplied is not None:
        result = {"language": data.get("language", "en"), "segments": supplied}
    elif data.get("asr", "qwen") == "qwen":
        try:
            result = qwen_transcribe(audio, duration, data.get("language", "en"))
        except (ValueError, RuntimeError) as exc:
            if data.get("asr_failure", "strict") != "allow":
                raise
            result = {"language": data.get("language", "en"), "segments": [], "asr_error": str(exc)}
    elif data.get("asr") == "none":
        result = {"language": data.get("language", "en"), "segments": []}
    else:
        raise ValueError("asr 只支持 qwen 或 none；也可直接提供 transcript")
    segments = []
    for item in result.get("segments", []):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start, end = float(item.get("start", 0)), float(item.get("end", 0))
        if end < start:
            raise ValueError("transcript 时间段无效")
        if end == start:
            end = start + 0.01
        segments.append({"start": start, "end": end, "text": text})
    normalized = {"language": result.get("language") or data.get("language", "en"), "segments": segments}
    if result.get("asr_error"):
        normalized["asr_error"] = result["asr_error"]
    return normalized


def meaningful_words(text):
    words = re.findall(r"[A-Za-zÀ-ÿ]+", text.lower())
    return [word for word in words if word not in INTERJECTIONS and len(word) > 1]


def speech_assessment(transcript, override="auto"):
    text = " ".join(item["text"] for item in transcript["segments"])
    detected = bool(text.strip())
    meaningful = meaningful_words(text)
    informational = len(meaningful) >= 2 and len("".join(meaningful)) >= 6
    if override == "present":
        informational = True
    elif override == "absent":
        informational = False
    elif override != "auto":
        raise ValueError("speech_information 必须是 auto、present 或 absent")
    return {
        "speech_detected": detected,
        "informational_speech": informational,
        "meaningful_words": meaningful,
        "override": override,
    }


def analyze_request(path):
    data = request_data(path)
    out = Path(data["out_dir"])
    info = probe(data["video_file"])
    if not info["has_video"] or info["duration"] <= 0:
        raise ValueError("输入没有有效视频流或时长")
    source_audio, asr_audio = extract_audio(data, info)
    transcript = normalized_transcript(data, asr_audio, info["duration"])
    assessment = speech_assessment(transcript, data.get("speech_information", "auto"))
    mode = data["mode"]
    if mode == "auto":
        mode = "voiceover" if assessment["informational_speech"] else "caption_light"
    analysis = {
        "source_id": data["source_id"],
        "duration": info["duration"],
        "has_source_audio": info["has_audio"],
        "sound_focus": bool(data.get("sound_focus", False)),
        "asr_error": transcript.get("asr_error"),
        **assessment,
        "created_at": now(),
    }
    decision = {
        "source_id": data["source_id"],
        "mode": mode,
        "requested_mode": data["mode"],
        "reason": ("检测到承担信息表达的口播" if assessment["informational_speech"]
                   else "未检测到承担信息表达的口播，保留原声并使用中文字幕"),
        "status": "ready_for_script",
    }
    dump(out / "audio_analysis.json", analysis)
    dump(out / "transcript.json", transcript)
    dump(out / "decision.json", decision)
    return {"request": data, "analysis": analysis, "decision": decision}


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(char) in "WFA" else 1 for char in text)


def wrap_subtitle(text, width=32):
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return ""
    lines, current = [], ""
    for char in text:
        candidate = current + char
        if current and display_width(candidate) > width:
            lines.append(current.strip())
            current = char.lstrip()
        else:
            current = candidate
    if current:
        lines.append(current.strip())
    if len(lines) > 2:
        raise ValueError("字幕超过两行，请缩短：" + text)
    return "\n".join(lines)


def has_chinese(text):
    return bool(re.search(r"[\u3400-\u9fff]", text))


def srt_time(seconds):
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)
    return "%02d:%02d:%02d,%03d" % (hours, minutes, secs, millis)


def ass_time(seconds):
    centis = max(0, round(seconds * 100))
    hours, centis = divmod(centis, 360000)
    minutes, centis = divmod(centis, 6000)
    secs, centis = divmod(centis, 100)
    return "%d:%02d:%02d.%02d" % (hours, minutes, secs, centis)


def validate_script(data, decision, script, duration):
    mode = script.get("mode", decision["mode"])
    if mode != decision["mode"]:
        raise ValueError("script 模式与 decision 不一致，请重新分析或修改请求模式")
    if mode not in MODES - {"auto"}:
        raise ValueError("script 模式无效")
    if not str(script.get("ai_disclosure") or "").strip():
        raise ValueError("script 必须包含 ai_disclosure")
    segments = script.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("script.segments 不能为空")
    fact_ids = {fact["id"] for fact in data["facts"]}
    previous_end = 0.0
    narration_count = 0
    for index, segment in enumerate(segments):
        start, end = float(segment["start"]), float(segment["end"])
        if start < previous_end or end <= start or end > duration + 0.05:
            raise ValueError("字幕时间段重叠、倒序或超出视频：第 %d 段" % (index + 1))
        previous_end = end
        segment["start"], segment["end"] = start, end
        subtitle = str(segment.get("subtitle") or "").strip()
        narration = str(segment.get("narration") or "").strip()
        if not subtitle and not narration:
            raise ValueError("第 %d 段 subtitle 与 narration 不能同时为空" % (index + 1))
        if subtitle:
            segment["subtitle"] = wrap_subtitle(subtitle)
        if mode == "caption_light" and narration:
            raise ValueError("caption_light 不允许配音，请改用 voiceover")
        if narration:
            narration_count += 1
        if segment.get("kind") != "question":
            refs = segment.get("fact_refs")
            if not isinstance(refs, list) or not refs:
                raise ValueError("第 %d 段必须引用 fact_refs；提问段可标 kind=question" % (index + 1))
            unknown = set(refs) - fact_ids
            if unknown:
                raise ValueError("未知 fact_refs：" + ", ".join(sorted(unknown)))
    if mode == "voiceover" and not narration_count:
        raise ValueError("voiceover 至少需要一段 narration")
    opening = [s for s in segments if s["start"] < 3 and has_chinese(s.get("subtitle", ""))]
    if not opening:
        raise ValueError("前三秒必须出现中文字幕")
    return mode


def write_subtitles(out, segments, width, height):
    srt = []
    ass = [
        "[Script Info]", "ScriptType: v4.00+", "ScaledBorderAndShadow: yes",
        "PlayResX: %d" % width, "PlayResY: %d" % height, "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,PingFang SC,%d,&H00FFFFFF,&H000000FF,&H00101010,&H64000000,0,0,0,0,100,100,0,0,1,3,0,2,36,36,%d,1" % (
            max(24, round(height * 0.045)), max(32, round(height * 0.08))),
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    index = 1
    for segment in segments:
        subtitle = segment.get("subtitle")
        if not subtitle:
            continue
        srt += [str(index), "%s --> %s" % (srt_time(segment["start"]), srt_time(segment["end"])),
                subtitle, ""]
        text = subtitle.replace("{", "（").replace("}", "）").replace("\n", r"\N")
        ass.append("Dialogue: 0,%s,%s,Default,,0,0,0,,%s" % (
            ass_time(segment["start"]), ass_time(segment["end"]), text))
        index += 1
    (out / "subtitles_zh.srt").write_text("\n".join(srt), encoding="utf-8")
    (out / "subtitles_zh.ass").write_text("\n".join(ass) + "\n", encoding="utf-8")


def synthesize_speech(text, path, voice, rate):
    run([sys.executable, "-m", "edge_tts", "--voice", voice, "--rate", rate,
         "--text", text, "--write-media", str(path)])


def narration_filter(data, narrations):
    original = float(data.get("original_volume", 0.85))
    ducked = float(data.get("ducked_volume", 0.12))
    if not 0 <= ducked <= original <= 1.5:
        raise ValueError("音量需满足 0 <= ducked_volume <= original_volume <= 1.5")
    filters = []
    if narrations:
        enables = "+".join("between(t,%.3f,%.3f)" % (item["start"], item["end"]) for item in narrations)
        ratio = 1 if original == 0 else ducked / original
        filters.append("[1:a]volume=%.5f,volume=%.5f:enable='%s'[bed]" % (original, ratio, enables))
    else:
        filters.append("[1:a]anull[bed]")
    labels = []
    for index, item in enumerate(narrations):
        delay = round(item["start"] * 1000)
        filters.append("[%d:a]aresample=48000,adelay=%d:all=1[n%d]" % (index + 2, delay, index))
        labels.append("[n%d]" % index)
    filters.append("[bed]%samix=inputs=%d:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]" % (
        "".join(labels), len(labels) + 1))
    return filters


def extract_qc_frames(out, video, duration):
    frame_dir = out / "qc_frames"
    frame_dir.mkdir(exist_ok=True)
    times = [min(1.0, duration / 3), duration / 2, max(0.0, duration - 0.5)]
    paths = []
    for index, at in enumerate(times, 1):
        target = frame_dir / ("%d.jpg" % index)
        run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % at, "-i", str(video),
             "-frames:v", "1", str(target)])
        paths.append({"file": str(target), "at_sec": round(at, 3)})
    return paths


def render_request(path, tts=synthesize_speech):
    data = request_data(path)
    out = Path(data["out_dir"])
    decision = load_json(out / "decision.json")
    script = load_json(out / "script_zh.json")
    source_info = probe(data["video_file"])
    mode = validate_script(data, decision, script, source_info["duration"])
    video_stream = next(s for s in source_info["streams"] if s.get("codec_type") == "video")
    write_subtitles(out, script["segments"], video_stream["width"], video_stream["height"])
    narrations = []
    for index, segment in enumerate(script["segments"]):
        text = str(segment.get("narration") or "").strip()
        if not text:
            continue
        target = out / ("narration_%02d.mp3" % (index + 1))
        tts(text, target, data.get("voice", "zh-CN-YunxiNeural"), data.get("rate", "+0%"))
        narration_duration = probe(target)["duration"]
        available = segment["end"] - segment["start"]
        if narration_duration > available - 0.03:
            raise ValueError("第 %d 段配音 %.2fs 超过分配时长 %.2fs，请缩短文案或调整时间轴" % (
                index + 1, narration_duration, available))
        narrations.append({**segment, "file": str(target), "narration_duration": narration_duration})
    filters = ["[0:v]ass=subtitles_zh.ass[vout]"]
    filters += narration_filter(data, narrations)
    (out / "filters.txt").write_text(";\n".join(filters) + "\n", encoding="utf-8")
    command = ["ffmpeg", "-v", "error", "-y", "-i", data["video_file"], "-i", "source_audio.wav"]
    for item in narrations:
        command += ["-i", item["file"]]
    command += ["-filter_complex_script", "filters.txt", "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
                "-t", "%.3f" % source_info["duration"], "-movflags", "+faststart", "localized.mp4"]
    run(command, cwd=out)
    localized = out / "localized.mp4"
    result = probe(localized)
    if not result["has_video"] or not result["has_audio"]:
        raise RuntimeError("渲染结果缺少视频流或音频流")
    if abs(result["duration"] - source_info["duration"]) > 0.25:
        raise RuntimeError("渲染前后时长不一致")
    run(["ffmpeg", "-v", "error", "-i", str(localized), "-f", "null", "-"])
    frames = extract_qc_frames(out, localized, result["duration"])
    qc = {
        "source_id": data["source_id"], "status": "needs_visual_review",
        "objective": {"decode": True, "duration_match": True, "audio_present": True,
                      "facts_linked": True, "opening_chinese": True},
        "source_duration": source_info["duration"], "localized_duration": result["duration"],
        "frames": frames, "visual_review": None, "updated_at": now(),
    }
    dump(out / "qc.json", qc)
    handoff = {
        "source_id": data["source_id"], "source_url": data.get("source_url", ""),
        "status": "needs_visual_review", "mode": mode, "video_file": str(localized),
        "title": script.get("title", ""), "body": script.get("body", ""),
        "ai_disclosure": script["ai_disclosure"], "qc_file": str(out / "qc.json"),
    }
    dump(out / "handoff.json", handoff)
    return {"request": data, "handoff": handoff, "qc": qc, "narrations": narrations}


def approve_request(path, review_path):
    data = request_data(path)
    out = Path(data["out_dir"])
    qc = load_json(out / "qc.json")
    review = load_json(review_path)
    required = ("subtitle_clear", "key_visual_uncovered", "audio_clear", "source_sound_appropriate")
    failed = [key for key in required if review.get(key) is not True]
    if failed:
        raise ValueError("视觉或听感复核未通过：" + ", ".join(failed))
    if review.get("presentation") != "car_only":
        raise ValueError("复核后的 presentation 必须为 car_only")
    if not all(qc.get("objective", {}).values()):
        raise ValueError("客观质量检查尚未全部通过")
    qc["status"] = "passed"
    qc["visual_review"] = {**review, "reviewed_at": now()}
    qc["updated_at"] = now()
    dump(out / "qc.json", qc)
    handoff = load_json(out / "handoff.json")
    handoff["status"] = "ready"
    handoff["video_file"] = str(out / "localized.mp4")
    dump(out / "handoff.json", handoff)
    return handoff


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("analyze", "render", "approve"))
    parser.add_argument("--request", required=True)
    parser.add_argument("--visual-review", help="approve 所需的视觉与听感复核 JSON")
    args = parser.parse_args()
    try:
        if args.command == "analyze":
            result = analyze_request(args.request)
        elif args.command == "render":
            result = render_request(args.request)
        else:
            if not args.visual_review:
                raise ValueError("approve 必须提供 --visual-review")
            result = approve_request(args.request, args.visual_review)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, "错误：%s\n" % exc)


if __name__ == "__main__":
    main()

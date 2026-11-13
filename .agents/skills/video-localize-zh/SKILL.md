---
name: video-localize-zh
description: 将来源清晰、已获授权的视频做成中文本地化版本，支持英文信息口播转中文配音，以及机械原声视频轻字幕。适用于用户要求中文化、中文配音、中文字幕、翻译重写 X/其他平台视频；不执行搜索或发布。
---

# 中文视频本地化（video-localize-zh）

把合格素材转换为中文用户能直接理解的视频。只负责本地化，不搜索、不绕过原片禁选规则、不自动发布。汽车场景可接在 `xcar-xhs` 的抽帧核验之后、发布定稿之前；其他主题也可独立使用。

## 前置条件

- 在项目根目录使用 `.venv/bin/python`。
- FFmpeg / ffprobe 可用。
- `voiceover` 使用项目已有的 `edge-tts`，需要网络。
- 默认 ASR 复用项目 Qwen3-ASR；也可在请求中提供 `transcript`，或设置 `asr: none` 跳过转写。纯机械声可能触发 ASR 退化保护，默认会阻断；确认无需依赖口播结果时可显式设置 `asr_failure: allow`，错误会保存在 `transcript.json` 和 `audio_analysis.json`。
- 输入必须是 `presentation=car_only`，且 `rights_status` 为 `official_source`、`original_creator` 或 `licensed`。真人讲解、屏幕讲解、授权不清素材直接拒绝。

## 工作流

1. Agent 为每条素材编写请求 JSON，填写来源、事实卡、声音重点和模式。
2. 运行 `analyze`：提取音轨、转写、区分实际信息口播与短促感叹声，写入 `audio_analysis.json`、`transcript.json`、`decision.json`。
3. Agent 根据原片、转写和事实卡写 `script_zh.json`。脚本只能使用请求中的事实；除 `kind=question` 外，每段必须填写 `fact_refs`。
4. 运行 `render`：检查前三秒中文、字幕长度、事实引用、时间轴和配音时长，生成字幕、配音、`localized.mp4`、`qc.json`、`handoff.json` 及三张复核帧。
5. Agent 查看复核帧并检查音轨，写视觉与听感复核 JSON，再运行 `approve`。只有 `handoff.json.status=ready` 才能交给其他发布流程。
6. 预演阶段不得自动发布。接入发布器前仍需完成对应平台的文案、版权和发布检查。

## 模式

- `auto`：检测到承担信息表达的口播时使用 `voiceover`，否则使用 `caption_light`。短促的“Oh / Wow”等不视为信息口播；可用 `speech_information: present/absent` 显式纠正。
- `voiceover`：中文配音和字幕，原片声音在配音期间自动降低，非配音段保留。适合英文讲解或需要背景解释的无对白纸面。
- `caption_light`：只加中文字幕，不生成配音，保留机械原声。

## 请求示例

保存为 `work/localization-run/<原帖ID>/request.json`；相对路径以请求文件所在目录解析。

```json
{
  "source_id": "1570389257948127233",
  "source_url": "https://x.com/Bugatti/status/1570389257948127233",
  "video_file": "/absolute/path/source.mp4",
  "out_dir": "/absolute/path/work/localization-run/1570389257948127233",
  "presentation": "car_only",
  "rights_status": "official_source",
  "mode": "auto",
  "language": "en",
  "asr": "qwen",
  "speech_information": "auto",
  "sound_focus": true,
  "voice": "zh-CN-YunxiNeural",
  "rate": "+0%",
  "original_volume": 0.85,
  "ducked_volume": 0.12,
  "facts": [
    {
      "id": "design",
      "text": "CENTODIECI 从 EB110 Supersport 汲取设计灵感。",
      "source": "https://x.com/Bugatti/status/1570389257948127233"
    }
  ]
}
```

执行分析：

```bash
.venv/bin/python .agents/skills/video-localize-zh/scripts/localize.py analyze \
  --request work/localization-run/1570389257948127233/request.json
```

## 中文脚本

在同一输出目录写 `script_zh.json`：

```json
{
  "mode": "voiceover",
  "title": "两代布加迪尾部差在哪？",
  "body": "CENTODIECI 与 EB110 Supersport 同框，直观看到两代布加迪的尾部设计差异。",
  "ai_disclosure": "中文解说、字幕及文字由 AI 辅助整理；素材来源见原帖。",
  "segments": [
    {
      "start": 0,
      "end": 5,
      "subtitle": "两代布加迪，尾部差在哪？",
      "narration": "两代布加迪同框，尾部设计差在哪？",
      "fact_refs": ["design"]
    },
    {
      "start": 5,
      "end": 12,
      "subtitle": "CENTODIECI 从 EB110 汲取设计灵感",
      "narration": "CENTODIECI 从 EB110 Supersport 汲取设计灵感。",
      "fact_refs": ["design"]
    },
    {
      "start": 12,
      "end": 17,
      "subtitle": "你更喜欢哪一代？",
      "kind": "question"
    }
  ]
}
```

- `caption_light` 的片段只写 `subtitle`，不能写 `narration`。
- 字幕自动按最多两行排版；过长、重叠、超出视频或缺少事实引用会阻断。
- 配音实际时长必须短于对应片段，失败时缩短文案或调整时间轴，不自动加速到难以听懂。
- 字幕除与事实卡一致，还必须匹配当前画面场景；不能在山路等动态画面上仍写静态特写说明。
- 英文车型名的 TTS 发音不自然时，配音改用“这台车／这台新车”等中文指代，字幕保留准确车型名。
- `title` 和 `body` 会写入交接文件，但发布平台仍需另行校验。

渲染：

```bash
.venv/bin/python .agents/skills/video-localize-zh/scripts/localize.py render \
  --request work/localization-run/1570389257948127233/request.json
```

## 视觉与听感复核

查看 `qc_frames/` 并检查 `localized.mp4`，写 `visual_review.json`：

```json
{
  "subtitle_clear": true,
  "key_visual_uncovered": true,
  "audio_clear": true,
  "source_sound_appropriate": true,
  "presentation": "car_only",
  "notes": "字幕清晰，未遮挡车辆；中文配音清楚，非配音段保留原声。"
}
```

通过交接检查：

```bash
.venv/bin/python .agents/skills/video-localize-zh/scripts/localize.py approve \
  --request work/localization-run/1570389257948127233/request.json \
  --visual-review work/localization-run/1570389257948127233/visual_review.json
```

`approve` 只接受四项复核均为 `true` 且 `presentation=car_only` 的结果。通过后 `qc.json.status=passed`、`handoff.json.status=ready`。

## 输出

```text
<out_dir>/
├── request.json          # 含事实卡
├── audio_analysis.json
├── transcript.json
├── decision.json
├── script_zh.json
├── subtitles_zh.srt
├── subtitles_zh.ass
├── narration_XX.mp3
├── localized.mp4
├── qc_frames/
├── qc.json
├── visual_review.json
└── handoff.json
```

中间产物均保留，渲染失败不必重新转写。任何翻译或二次创作都不能替代素材授权；AI 辅助解说和文字必须在交接文案中披露。

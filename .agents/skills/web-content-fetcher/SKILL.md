---
name: web-content-fetcher
description: 使用显式浏览器 Web 登录会话或 cookies.txt，检索 X/Twitter、YouTube 最新内容，并拉取 yt-dlp 支持平台的 URL 元数据和媒体。适用于某主题最新新闻/帖子、X 热榜、YouTube 搜索、跨平台内容归档与视频下载，不需要 X 开发者 API 或第三方数据 API。
license: MIT
---

# Web 内容检索与拉取（web-content-fetcher）

用统一 CLI 完成「关键词检索 / URL 元数据拉取 → 去重与可选互动排序 → 可选媒体下载 → JSONL + manifest 报告」。默认不读取浏览器，不使用 `X_BEARER_TOKEN`、`XQUIK_API_KEY`、`XAI_API_KEY` 或 ScrapeCreators。

## 能力边界

| 数据源 | 关键词检索 | 登录态 URL 元数据与媒体拉取 |
|---|---|---|
| X / Twitter | 支持 Top / Latest / Media，可本地按点赞、评论、转发、播放排序 | 支持，经 yt-dlp |
| YouTube | 支持 ytsearch | 支持 |
| TikTok / Instagram / Facebook / Bilibili / Reddit / Twitch / Vimeo | 未内置 | 取决于 yt-dlp 提取器和当前账号可见权限 |
| 其他 yt-dlp 支持的网站 | 未内置 | 使用 `--url`，错误写入 manifest |

不要把“支持 URL 拉取”说成“支持全站搜索”，不要伪造内容 URL 或互动数据。X 文本帖不一定含媒体；只有用户要求下载时才加 `--download`，下载失败会明确记录。

## 前置条件

- `yt-dlp` 与 `ffmpeg` 在 PATH 中，用于检索、元数据和媒体合并。
- X 关键词检索还需要 `xfetch` skill 及 Python 环境：
  - 项目位置：`../xfetch/scripts/xfetch.py`。
  - 全局安装时把 `web-content-fetcher` 与 `xfetch` 放在同一个 skills 根下。
  - 也可用 `--xfetch /path/to/xfetch.py` 和 `--python /path/to/python` 显式指定。
- CDP 回退是可选依赖：`websocket-client`；仅 macOS Chromium 原生 Cookie 提取失败时使用。
- YouTube 和通用 URL 拉取不依赖 xfetch。

## Web 登录会话

`web_session.py` 由本 Skill 和 `x-car-video-finder` 共用。会话选择必须显式：

- 浏览器：`--web-session chrome --browser-profile Default`。
- Cookie 文件：`--cookies-file ~/Downloads/cookies.txt`，要求 Netscape cookies.txt，与 `--web-session` 二选一。
- 默认：`--web-session off`，同时给 yt-dlp 传 `--ignore-config --no-cookies-from-browser`，避免隐式使用其他会话配置。
- X 兼容既有 `--auth-file` 和 `XFETCH_AUTH_TOKEN` / `XFETCH_CT0`，优先级为 `auth-file > cookies-file > web-session > 环境变量或 xfetch 已保存会话`。
- macOS Chrome / Edge / Brave / Chromium 先原生提取，失败后自动回退 CDP；其他浏览器经 xfetch 提取。非 Chromium 的 X 提取不支持指定 Profile，需改用 cookies.txt。
- 只输出会话方式、浏览器和 Profile 摘要，不打印或提交 Cookie 值。临时 X 凭据随进程清理。

用户未明确选择浏览器/Profile 或 Cookie 文件时，不要自行扩大读取范围。公开内容可匿名尝试；遇到登录、反爬或权限错误，报告需要的会话，不绕过访问控制。

## 用法

### X 某主题最新帖子

```bash
python3 .agents/skills/web-content-fetcher/scripts/web_content.py \
  --source x --query '("OpenAI Codex" OR "Codex CLI")' \
  --type latest --limit 20 \
  --web-session chrome --browser-profile Default \
  --out work/codex-x-latest
```

只要最近一天可加 `--since YYYY-MM-DD`。按互动量热榜可将 `--type latest` 改为 `--type top --sort likes`；`views` / `replies` / `reposts` 也可排序。服务端视图决定候选集，本地排序不会把未抓取的帖子补进来。

### YouTube 某主题视频

```bash
python3 .agents/skills/web-content-fetcher/scripts/web_content.py \
  --source youtube --query "OpenAI Codex" --limit 10 \
  --web-session chrome --browser-profile Default \
  --out work/codex-youtube
```

### 多平台 URL 元数据与媒体

```bash
python3 .agents/skills/web-content-fetcher/scripts/web_content.py \
  --url "https://www.tiktok.com/@example/video/123" \
  --url "https://www.bilibili.com/video/BV..." \
  --web-session chrome --download --out work/web-content-mixed
```

省略 `--download` 时只拉元数据。多个 `--query` 或 `--url` 可重复；`--query` 默认推断为 YouTube，X 必须显式写 `--source x`。

## 主要选项

| 选项 | 默认 | 说明 |
|---|---|---|
| `--source` | 按输入推断 | `x` / `youtube` / `url` |
| `--query` | 无 | X 或 YouTube 检索词，可重复 |
| `--url` | 无 | 内容 URL，可重复，不能与 query 混用 |
| `--limit` | `10` | 每组关键词检索数量，1–100 |
| `--sort` | `none` | `likes` / `replies` / `reposts` / `views`；`none` 保留平台顺序 |
| `--type` | `top` | X 的 `top` / `latest` / `media` 视图 |
| `--since` | 无 | X 起始日期，自动追加 `since:` |
| `--web-session` | `off` | 显式浏览器会话 |
| `--browser-profile` | 浏览器默认 | 例如 `Default` / `Profile 1` |
| `--cookies-file` | 无 | Netscape cookies.txt |
| `--auth-file` | 无 | X 专用 KEY=VALUE 文件，不用于其他平台下载 |
| `--download` | 关 | 下载媒体；默认只拉元数据 |
| `--max-height` | `1080` | 优先限制高度，无匹配流时回退最佳可用画质，不转码 |
| `--out` | `work/web-content` | 输出目录 |

## 输出与结果判断

```text
<out>/
├── items.jsonl       # 归一化内容，一行一条
├── manifest.json     # 查询、会话摘要、内容、错误和下载结果
└── videos/           # 仅 --download 时生成
```

统一字段包括 `platform`、`source_id`、`url`、`author`、`date`、`title`、`text`、`likes`、`replies`、`reposts`、`views`、`duration`、`thumbnail`。不保存平台完整原始响应。

Agent 必须检查 `manifest.json` 的 `item_count`、`errors` 和 `downloads[].error`，不能只看进程输出就说成功。全部检索失败或任一下载失败时 CLI 返回 1；部分平台失败但已有内容时保留结果并记录错误。向用户汇报实际成功的平台、数量和失败原因。

## Agent 工作流

1. 判断用户要 X、YouTube 还是 URL；主题新闻优先 X `latest`，热帖用 `top` 加本地互动排序。
2. 用户指定会话才使用对应浏览器/Profile 或 cookies.txt；否则默认匿名，X 使用已有 xfetch 会话。
3. 先用较小 `--limit` 验证数据源和会话，再扩大检索；不要并发轰击同一账号。
4. 检查 JSONL 与 manifest，去重后按实际字段排序，不编造缺失互动数。
5. 需要媒体时再加 `--download`，核对文件存在和下载错误。
6. 提醒版权、频率限制和平台条款，只处理当前账号有权访问的内容。

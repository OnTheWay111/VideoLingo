---
name: x-car-video-finder
description: 按一句提示词在 X/Twitter 上搜索汽车视频，支持按点赞量、评论数、转发数、播放量排序后取 Top N，用 yt-dlp 下载并抽帧核验。Use when the user wants to search X for car/auto videos by description, rank results by likes or comments, or batch-download X/Twitter videos.
license: MIT
---

# X 汽车视频搜索与下载（x-car-video-finder）

把一句自然语言提示词变成「X 检索式 → 去重 → 汽车相关性过滤 → 排序 → 下载 → 抽帧核验 → 报告」的完整流水线。

底层复用两个现成工具，本 skill 只做编排：

| 能力 | 工具 | 说明 |
|---|---|---|
| 搜索 X（带登录态） | `xfetch` skill（`../xfetch/scripts/xfetch.py`，twikit） | 输出 JSONL，含 `favorite_count` / `reply_count` / `retweet_count` / `view_count` / `media` |
| 下载视频 | `yt-dlp` | X 视频是 HLS，yt-dlp 自动合并成 mp4 |
| 抽帧核验 | `ffmpeg` | 每条约 2 帧，供 Agent 用 `read_image` 判断是否真是汽车 |

## 前置条件

- **xfetch skill**：本仓库内 `.agents/skills/xfetch/`（不存在则 `npx skills add agents365-ai/365-skills@xfetch`）。
- **Python venv**：`.venv-xfetch/`（含 twikit / httpx / browser_cookie3）。
  ```bash
  python3 -m venv .venv-xfetch
  .venv-xfetch/bin/pip install -r .agents/skills/xfetch/scripts/requirements.txt
  ```
- **yt-dlp + ffmpeg**：`which yt-dlp ffmpeg`，macOS 可用 `brew install yt-dlp ffmpeg`。
- **X 登录态**：X 搜索必须登录。三选一（见下）。

## 在 DeepSeek Harness 中被发现与 / 调用

DSH 的本地 skill 提供方只扫描下列根（rank 越高优先级越低），**不会扫描 `.claude/skills`**：

| rank | 来源 | 路径 |
|---|---|---|
| 100 | project-dsh | `<项目根>/.dsh/skills` |
| 200 | project-agents | `<项目根>/.agents/skills` |
| 300 | custom | 配置项 `customSkillDirs` |
| 400 | user-dsh | `~/.dsh/skills` |
| 500 | user-agents | `~/.agents/skills` |

项目根是最近包含 `.git` 的祖先目录；skill 必须是 `<root>/<name>/SKILL.md` 或 `<root>/<name>.md`，只发现一层。

- 本 skill 当前在 `<项目根>/.agents/skills/x-car-video-finder`，DSH 会话目录会自动收录（本项目内已验证热更新）。
- Web 编辑器输入 `/` 会列出用户可调用的 skill，直接键入 `/x-car-video-finder 找几条 Cybertruck 越野视频` 即可调用；宿主会把完整指令注入本轮，无需模型再调 `skill` 工具。TUI / ACP 行为一致。
- 只想允许 `/` 调用、不让模型自动选：frontmatter 加 `disable-model-invocation: true`；想隐藏 `/` 入口：`user-invocable: false`。
- 全局安装（所有项目）：把本目录和 `xfetch` 复制到 `~/.agents/skills/` 或 `~/.dsh/skills/`。
- 手改文件后模型目录会自动刷新；若 `/` 菜单没更新，刷新页面、新建会话或重连即可。

## 登录态（按优先级）

1. **复用 Chrome 已登录会话（macOS，走钥匙串，无需密码）**：
   ```bash
   python3 .agents/skills/x-car-video-finder/scripts/auth_from_chrome.py --out /tmp/xfetch-auth.env
   ```
   脚本从钥匙串取 `Chrome Safe Storage` 密钥、解出 `.x.com` 的 `auth_token`/`ct0`，写成 `KEY=VALUE` 文件（chmod 600），**不打印 cookie 值**。出现钥匙串弹窗点“允许”。然后给 finder 传 `--auth-file /tmp/xfetch-auth.env`。
2. **钥匙串取不到时的备用方案（CDP，不需要钥匙串）**：
   ```bash
   .venv-xfetch/bin/pip install websocket-client        # 一次性
   .venv-xfetch/bin/python .agents/skills/x-car-video-finder/scripts/auth_from_chrome_cdp.py \
     --out /tmp/xfetch-auth.env
   ```
   脚本复制一份 Cookie 库到临时目录，启动独立的 headless Chrome（随机端口、`--no-sandbox`），通过 DevTools 的 `Storage.getCookies` 让 Chrome 自己解密，用完自动杀掉进程并清理临时目录。同样只输出文件路径、不打印 cookie。
3. **已有 `cookies.txt` 导出**：
   `python3 .venv-xfetch/bin/python .agents/skills/xfetch/scripts/xfetch.py auth import --file ~/Downloads/x.com_cookies.txt`
4. **手动设环境变量**：`export XFETCH_AUTH_TOKEN=... XFETCH_CT0=...`（DevTools → Application → Cookies → x.com）。

验证：`python3 .venv-xfetch/bin/python .agents/skills/xfetch/scripts/xfetch.py auth check`（exit 0 = 已登录）。

## 用法

**默认时长规则：视频时长不得超过 5 分钟（300 秒）。** 正好 300 秒可以入选；超过 300 秒的视频会自动移入 `rejected/`，并写入 `REPORT.md` 和 `manifest.json` 的淘汰清单。可用 `--max-duration 秒数` 调整上限，显式传 `--max-duration 0` 可关闭限制。

最少一行（自动把提示词展开成多组检索式）：

```bash
python3 .agents/skills/x-car-video-finder/scripts/finder.py \
  --prompt "找几条 Cybertruck 越野视频，点赞高的优先" \
  --auth-file /tmp/xfetch-auth.env \
  --sort likes --top 3 --out work/xfetch-run
```

手动指定检索式（更精确，`--query` 可重复；查询里自己带 `filter:media`、`min_faves:`）：

```bash
python3 .../finder.py \
  --prompt "超跑山路 POV" \
  --query '(supercar OR "car spotting") POV filter:media min_faves:1000' \
  --query '("car meet" OR "car show") filter:media min_faves:500' \
  --sort replies --top 3 --since 2026-09-01 --limit 25
```

**排序（本次需求的核心）**：X 服务端只有 Top/Latest/Media 三种视图，没有 sort 参数；本脚本先拉一批，再本地精确排序：

| `--sort` | 字段 | 说明 |
|---|---|---|
| `likes`（默认） | `favorite_count` | 按点赞量降序 |
| `replies` | `reply_count` | 按评论数降序 |
| `reposts` | `retweet_count` | 按转发数降序 |
| `views` | `view_count` | 按播放量降序 |

服务端预过滤：`--min-likes N` 会追加 `min_faves:N`；`--since YYYY-MM-DD` 追加 `since:`。`--type top` 用 X 的综合互动榜做初排，最终名次以上表字段本地排序为准。

## 选项

| 选项 | 默认 | 说明 |
|---|---|---|
| `--prompt` | 必填 | 原始提示词，写进报告和 manifest |
| `--query` | 自动生成 | X 检索式，可重复；不给则按提示词自动生成 4 组汽车检索式 |
| `--sort` | `likes` | `likes` / `replies` / `reposts` / `views` |
| `--top` | `3` | 下载前 N 条（按 `--sort` 排名） |
| `--candidates` | `15` | stdout 表格展示前 N 条候选 |
| `--limit` | `25` | 每组检索式每页条数 |
| `--pages` | `1` | 每组翻页数（>1 时自动带 cursor） |
| `--type` | `top` | `top` / `latest` / `media` |
| `--since` | 无 | 只搜此日期之后 |
| `--min-likes` | 无 | 点赞下限（服务端 `min_faves` + 本地二次过滤） |
| `--delay` | `1.5` | 翻页间隔秒数 |
| `--out` | `work/xfetch-run` | 输出根目录 |
| `--auth-file` | 无 | `KEY=VALUE` 登录态文件 |
| `--xfetch` | 自动探测 | xfetch.py 路径 |
| `--python` | 自动探测 | 跑 xfetch 的解释器（优先 `.venv-xfetch/bin/python`） |
| `--dry-run` | 关 | 只搜索+排序+报告，不下载 |
| `--no-car-filter` | 关 | 关闭汽车关键词过滤（会混入非汽车内容） |
| `--max-height` | `1080` | 下载分辨率上限 |
| `--max-duration` | `300` | 视频时长上限（秒），超过则自动淘汰；`0` 表示不限制 |
| `--frames` | `2` | 每条视频抽帧数（0 = 不抽） |

## 输出

```
work/xfetch-run/
├── raw/                 # 每组检索式的原始 JSONL
├── videos/              # 最终入选的 mp4（<uploader>_<tweet_id>.mp4）
├── rejected/            # 被淘汰的 mp4（超过时长上限/画面不符），备查
├── frames/              # 抽帧 jpg（Agent 用 read_image 核验）
├── manifest.json        # 候选、排序、下载、抽帧和淘汰原因的完整元数据
├── candidates.csv       # 全部候选（含点赞/评论/播放），可直接排序
└── REPORT.md            # 中文报告：检索式、榜单、入选清单和淘汰清单
```

stdout 会打印候选 Markdown 表（含链接、点赞、评论、日期），可直接粘给用户。

## Agent 工作流（重要）

1. 从提示词提取主题/车型/场景，必要时补充 2–4 组同义检索式（中英混用，X 上英文车内容远多于中文）。
2. 确认 `xfetch auth check` 通过；未登录先走上面三种方式之一。
3. 运行 finder（先 `--dry-run` 看榜单，确认内容对路再真下）。
4. 对下载的每条视频做两项核验，并记录每条的结果与淘汰原因：
   - **时长**：默认上限 300 秒。finder 自动用 ffprobe 检查，超时视频直接进入 `<out>/rejected/`，不再抽帧；除非用户明确指定其他上限，不得放宽。
   - **画面**：对 `frames/` 里的图用 `read_image` 抽查，剔除“标题像汽车但画面不是”的结果；不合格就换候选重下。
   - 因画面等原因被额外淘汰的视频不要删除，也移入 `<out>/rejected/` 备查。
5. 按下面「最终汇报格式」输出**入选表 + 淘汰表两张表格**，再附 `REPORT.md`、`videos/`、`rejected/`、`frames/` 的路径；提醒版权与平台条款。

## 最终汇报格式（必选）

回复用户时必须包含两张 Markdown 表格。列固定为 `# | 标题名 | 推荐正文 | 文件名 | 本地路径 | 时长 | 点赞 | 原帖`。“标题名”用简洁、准确的中文发布标题；“推荐正文”用可直接用于国内自媒体发布的中文正文，概括车型、事件和看点，不能只写视频内容简介或简单翻译原帖；“文件名”只写视频文件名；“本地路径”写视频所在目录的完整绝对路径，不要只写 `videos/` 等相对路径；“原帖”用 `[链接](https://x.com/...)`：

**入选**：

| # | 标题名 | 推荐正文 | 文件名 | 本地路径 | 时长 | 点赞 | 原帖 |
|---|---|---|---|---|---|---|---|
| 1 | Apollo Evo 超跑亮相 Goodwood 2026 | Apollo Evo 在 Goodwood 2026 正式亮相，夸张的空气动力学造型和赛道化设计成为全场焦点。 | `fosgoodwood_2075618551918985363.mp4` | `/Users/example/work/xfetch-run/videos` | 31s | 708 | [链接](https://x.com/fosgoodwood/status/2075618551918985363) |

**被淘汰**（同样的列，末尾追加“淘汰原因”，如 `超时 122s` / `画面非汽车` / `画质差疑似摆拍`）：

| # | 标题名 | 推荐正文 | 文件名 | 本地路径 | 时长 | 点赞 | 原帖 | 淘汰原因 |
|---|---|---|---|---|---|---|---|---|
| 1 | Peralta S 定制超跑亮相 | Peralta S 以定制化车身和复古超跑造型登场，展示了独特的设计细节。 | `Rainmaker1973_2037424381962670242.mp4` | `/Users/example/work/xfetch-run/rejected` | 122s | 2350 | [链接](https://x.com/Rainmaker1973/status/2037424381962670242) | 超时 122s > 60s |

淘汰表为空时写明“无淘汰”。两张表也追加到 `<out>/REPORT.md` 末尾，保持报告可独立阅读。

## 注意

- 登录态只在本机使用，cookie 文件用完即删（`rm /tmp/xfetch-auth.env`），不要提交进仓库。
- X 有频率限制：默认 `--delay 1.5` 不要调太小；大批量建议分多次、`--limit` 别贪大（单次 20–40/组足够）。
- `min_replies` 等操作符能否被 X 接受未实测；评论数排序请依赖本地 `--sort replies`。
- 下载内容版权归原作者，二次创作/商用需自行确认授权，遵守 X 平台条款。

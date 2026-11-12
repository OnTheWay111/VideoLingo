---
name: x-car-video-finder
description: 按一句提示词或账号选题策略自动搜索、排序、下载并抽帧核验 X/Twitter 汽车视频，支持小红书自动选题、发布历史下载前去重，以及通过内置浏览器上传并发布视频。Use when the user wants to find X car videos, automatically choose Xiaohongshu car topics, maintain a car account, skip already-published videos, or publish car videos to Xiaohongshu.
license: MIT
---

# X 汽车视频搜索与下载（x-car-video-finder）

把一句自然语言提示词或内置账号策略变成「自动选题计划 → X 检索式 → 发布历史去重 → 汽车相关性过滤 → 排序 → 下载 → 抽帧核验 → 标题正文定稿 → 小红书发布 → 已发布登记」的完整流水线。用户没有给选题时直接使用 `--auto-topic`，不要先追问主题；通用 X 主题筛选和其他平台 URL 素材核验请使用 `web-content-fetcher`，本 Skill 只负责汽车场景。

| 能力 | 工具 | 说明 |
|---|---|---|
| 搜索 X（带登录态） | `xfetch` skill（`../xfetch/scripts/xfetch.py`，twikit） | 输出 JSONL，含 `favorite_count` / `reply_count` / `retweet_count` / `view_count` / `media` |
| 统一 Web 会话 | `web-content-fetcher/scripts/web_session.py` | 显式复用浏览器 Cookie 或 cookies.txt，临时 X 凭据自动清理 |
| 下载媒体 | `yt-dlp` | X 视频自动合并成 mp4 |
| 抽帧核验 | `ffmpeg` | 每条约 2 帧，供 Agent 用 `read_image` 判断是否真是汽车 |
| 发布前去重 | `scripts/publish_history.py` | 固定 JSONL 保存唯一 ID、标题和状态；已发布或结果待确认的视频下载前跳过 |
| 小红书发布 | `scripts/xiaohongshu_publisher.py` + 内置浏览器 | 生成计划，自动上传、填标题正文话题；显式确认后才发布并登记 |

## 前置条件

- **web-content-fetcher skill**：本仓库内 `.agents/skills/web-content-fetcher/`，提供共享会话与浏览器提取脚本。
- **xfetch skill**：本仓库内 `.agents/skills/xfetch/`（不存在则 `npx skills add agents365-ai/365-skills@xfetch`）。
- **Python venv**：`.venv-xfetch/`（含 twikit / httpx / browser_cookie3）。
  ```bash
  python3 -m venv .venv-xfetch
  .venv-xfetch/bin/pip install -r .agents/skills/xfetch/scripts/requirements.txt
  ```
- **yt-dlp + ffmpeg**：`which yt-dlp ffmpeg`，macOS 可用 `brew install yt-dlp ffmpeg`。
- **X 登录态**：X 搜索必须登录，可显式复用浏览器会话、cookies.txt 或既有 xfetch 会话（见下）。
- **小红书登录态**：使用 Kimi Code 内置浏览器打开创作者平台；未登录时由用户完成登录。无需 Playwright/Selenium，不读取个人浏览器 Cookie。

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
- 全局安装（所有项目）：把本目录、`web-content-fetcher` 和 `xfetch` 一起复制到 `~/.agents/skills/` 或 `~/.dsh/skills/`。
- 手改文件后模型目录会自动刷新；若 `/` 菜单没更新，刷新页面、新建会话或重连即可。

## Web 登录会话（推荐）

`finder.py` 复用 `web-content-fetcher/scripts/web_session.py`。**默认 `--web-session off`，不会暗中读取浏览器 Cookie**；只有用户明确选择浏览器或 Cookie 文件时才访问登录态。

- **复用浏览器**：`--web-session chrome --browser-profile Default`。浏览器可为 `chrome` / `chromium` / `edge` / `brave` / `firefox` / `safari` / `opera` / `vivaldi` / `whale`，具体可用性取决于本机浏览器和 yt-dlp。
- **使用导出的 Cookie**：`--cookies-file ~/Downloads/cookies.txt`，要求 Netscape cookies.txt 格式，与 `--web-session` 二选一。
- **X 的既有方式仍可用**：`--auth-file /tmp/xfetch-auth.env` 或 `XFETCH_AUTH_TOKEN` / `XFETCH_CT0`。`--auth-file` 只用于 X，不会传给其他平台。
- **X 凭据优先级**：显式 `--auth-file` > `--cookies-file` > `--web-session` > 环境变量或 xfetch 已保存会话。macOS 上 Chrome / Edge / Brave / Chromium 先原生提取，失败后自动回退 CDP；CDP 需要可选依赖 `websocket-client`（`.venv-xfetch/bin/pip install websocket-client`）。其他浏览器通过 xfetch 提取。非 Chromium 的 X 提取暂不支持指定 Profile，需改用 cookies.txt。
- **其他平台**：由 yt-dlp 的 `--cookies-from-browser` / `--cookies` 读取对应站点会话，不使用 `X_BEARER_TOKEN`、`XQUIK_API_KEY`、`XAI_API_KEY` 或 ScrapeCreators。

`finder.py` 会先通过 `xfetch auth check` 实际请求 X 验证会话，再开始搜索；临时导入的 X 凭据在进程结束时清理。报告只记录会话方式、浏览器和 Profile，不记录 Cookie 值。macOS 可能出现钥匙串授权弹窗，这是浏览器 Cookie 解密所需；用户不应在未明确授权时替其选择浏览器。

手动提取仍可用：

```bash
python3 .agents/skills/web-content-fetcher/scripts/auth_from_chrome.py --out /tmp/xfetch-auth.env
python3 .venv-xfetch/bin/python .agents/skills/xfetch/scripts/xfetch.py auth check
```

钥匙串取不到时可继续使用 `web-content-fetcher/scripts/auth_from_chrome_cdp.py`，或手动设置 `XFETCH_AUTH_TOKEN` / `XFETCH_CT0`；这些凭据都不能打印、提交或写入报告。

## 用法

**默认时长规则：视频时长不得超过 5 分钟（300 秒）。** 正好 300 秒可以入选；超过 300 秒的视频会自动移入 `rejected/`，并写入 `REPORT.md` 和 `manifest.json` 的淘汰清单。可用 `--max-duration 秒数` 调整上限，显式传 `--max-duration 0` 可关闭限制。

自动选题（无需人工输入主题）：

```bash
python3 .agents/skills/x-car-video-finder/scripts/finder.py \
  --auto-topic --sort likes --top 3 --out work/xfetch-run
```

`--auto-topic` 按发布历史执行 60% 稀有车型、25% 改装机械、15% 视觉实验的 20 条轮动，同一系列连续规划 5 期，并自动生成带禁选排除词的检索式。它隐含 `--xiaohongshu`，不能与 `--prompt` 或 `--query` 同用。重复运行在实际发布登记前会保持同一计划，避免预演消耗期数。

只查看下一期计划、不访问 X：

```bash
python3 .agents/skills/x-car-video-finder/scripts/topic_planner.py
```

手动提示词仍然可用（自动展开多组检索式）：

```bash
python3 .agents/skills/x-car-video-finder/scripts/finder.py \
  --prompt "找几条 Cybertruck 越野视频，点赞高的优先" \
  --web-session chrome --browser-profile Default \
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

**排序**：X 服务端只有 Top/Latest/Media 三种视图，没有 sort 参数；本脚本先拉一批，再本地精确排序：

| `--sort` | 字段 | 说明 |
|---|---|---|
| `likes`（默认） | `favorite_count` | 按点赞量降序 |
| `replies` | `reply_count` | 按评论数降序 |
| `reposts` | `retweet_count` | 按转发数降序 |
| `views` | `view_count` | 按播放量降序 |

服务端预过滤：`--min-likes N` 会追加 `min_faves:N`；`--since YYYY-MM-DD` 追加 `since:`。`--type top` 用 X 的综合互动榜做初排。自动模式下互动排序只是候选预筛，Agent 仍须按 100 分模型评分，只让核验后达到 70 分的素材进入最终入选。

## 选项

| 选项 | 默认 | 说明 |
|---|---|---|
| `--prompt` | 手动模式必填 | 原始提示词，写进报告和 manifest；与 `--auto-topic` 二选一 |
| `--auto-topic` | 关 | 按策略和历史自动选题，隐含 `--xiaohongshu`；不与 `--prompt` / `--query` 同用 |
| `--query` | 自动生成 | X 检索式，可重复；手动模式不给则按提示词生成，自动模式使用选题计划 |
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
| `--publish-history` | Skill 内 `state/published_videos.jsonl` | 已发布历史；命中唯一 ID 时下载前跳过，不进入后续环节 |
| `--xiaohongshu` | 关 | 小红书禁选过滤：车聚车展、车主故事、特色驾驶场景下载前跳过 |
| `--auth-file` | 无 | X 专用 `KEY=VALUE` 登录态文件 |
| `--web-session` | `off` | 显式复用指定浏览器的 Web 登录会话 |
| `--cookies-file` | 无 | Netscape cookies.txt；可用于 X 和其他 yt-dlp 平台 |
| `--browser-profile` | 浏览器默认 | 浏览器 Profile，例如 `Default` / `Profile 1` |
| `--xfetch` | 自动探测 | xfetch.py 路径 |
| `--python` | 自动探测 | 跑 xfetch 的解释器（优先 `.venv-xfetch/bin/python`） |
| `--dry-run` | 关 | 只搜索+排序+报告，不下载 |
| `--no-car-filter` | 关 | 关闭汽车关键词过滤（会混入非汽车内容） |
| `--max-height` | `1080` | 优先限制下载高度；无匹配流时回退最佳可用画质，不做转码压缩 |
| `--max-duration` | `300` | 视频时长上限（秒），超过则自动淘汰；`0` 表示不限制 |
| `--frames` | `2` | 每条视频抽帧数（0 = 不抽） |

## 通用内容检索与拉取

非汽车主题的公众号候选筛选和其他平台 URL 素材核验统一使用 `../web-content-fetcher/SKILL.md`。该独立 Skill 支持 X 主题筛选、显式 URL 元数据与媒体下载，并输出 `items.jsonl`、`rejected.jsonl` 和 `manifest.json`；不要把本汽车 Skill 的 `--no-car-filter` 当成通用内容拉取器。

## X 汽车搜索输出

```
work/xfetch-run/
├── raw/                 # 每组检索式的原始 JSONL
├── videos/              # 定稿后的入选 mp4（<编号>.<品牌名>_<主题名>.mp4）
├── rejected/            # 定稿后的淘汰 mp4（独立编号，超过时长上限/画面不符），备查
├── frames/              # 定稿后的抽帧 jpg（<编号>.<品牌名>_<主题名>_帧序号.jpg）
├── publish.json         # Agent 写入的标题、正文、品牌、文件主题和推荐话题
├── topic_plan.json      # 自动模式：栏目、系列期数、检索式、评分维度和近期避开项
├── manifest.json        # 选题计划、候选、发布跳过、标题正文、下载、抽帧和淘汰原因
├── candidates.csv       # 未发布候选（含点赞/评论/播放），可直接排序
├── xiaohongshu_publish_plan.json     # 可选：小红书浏览器发布计划
├── xiaohongshu_publish_receipt.json  # 可选：实际发布登记回执
└── REPORT.md            # 中文报告：检索式、榜单、发布跳过、入选清单和淘汰清单
```

stdout 会打印候选 Markdown 表（含链接、点赞、评论、日期），可直接粘给用户。finder 下载时的 `<uploader>_<media_id>.mp4` 只是临时文件名；交付前必须运行下面的定稿器，统一改成 `编号.品牌名_主题名`。

## 标题命名、推荐话题与发布定稿（必做）

画面核验和候选递补完成后，为 `manifest.json` 里**每条入选和淘汰记录**写标题、推荐正文、品牌、文件主题和 2–6 个推荐话题。话题应结合品牌、车型、改装点和场景，避免只写宽泛标签。在 `<out>/publish.json` 中以原帖推文 ID 为键：

```json
{
  "2075618551918985363": {
    "title": "Apollo Evo 空气动力学细节",
    "body": "Apollo Evo 以夸张的空气动力学造型和赛道化设计展示车身细节，适合拆解其独特的设计语言。",
    "brand": "Apollo",
    "file_topic": "Evo 空气动力学细节",
    "topics": ["#ApolloEvo", "#超跑", "#汽车设计"]
  },
  "2037424381962670242": {
    "title": "Peralta S 定制超跑亮相",
    "body": "Peralta S 以定制化车身和复古超跑造型登场，展示了独特的设计细节。",
    "brand": "Peralta",
    "file_topic": "S 定制超跑亮相",
    "topics": ["#PeraltaS", "#定制超跑", "#汽车设计"]
  }
}
```

`brand` 是车辆原品牌，不是改装厂商，例如 Venuum 改装的 Bugatti Chiron 应写 `Bugatti`；`file_topic` 是用于文件名的简短主题，表格中的完整发布标题仍由 `title` 决定。为兼容旧结果，缺少 `brand` 时使用 `汽车`，缺少 `file_topic` 时使用完整 `title`；新任务必须逐条填写，不能依赖默认值。

`topics` 必须是字符串数组，可省略 `#`，定稿器会统一补齐并去除话题内空白。为兼容旧结果，缺少 `topics` 时使用 `#汽车 #改装车`；新任务应逐条撰写具体话题，不能依赖默认值。

运行定稿器：

```bash
python3 .agents/skills/x-car-video-finder/scripts/finalize.py \
  --out work/xfetch-run \
  --publish-file work/xfetch-run/publish.json
```

定稿器会自动完成并同步以下结果，不能只手工改文件名：

- 入选视频改为 `<入选编号>.<品牌名>_<主题名>.mp4`，淘汰视频改为 `<淘汰编号>.<品牌名>_<主题名>.mp4`，两组均从 `1.` 开始，与各自表格行号一致；抽帧使用相同编号，改为 `<编号>.<品牌名>_<主题名>_帧序号.jpg`。文件名不再强制包含推文 ID，原帖 ID 继续保留在 manifest 和原帖链接中。
- 品牌和主题分别清理文件系统不允许的字符及内部下划线，单个字段最长取 48 个字符；`publish.json` 和表格仍保留完整标题。
- 把 `title`、`recommendation`、`brand`、`file_topic`、`topics` 和更新后的路径写回 `manifest.json`。
- 把最终入选表、淘汰表写入 `<out>/REPORT.md`，并在 stdout 输出相同表格；重复运行会更新原表格，不会重复追加，也不会重复添加编号。
- 旧 manifest 中的媒体路径失效时，可按唯一推文 ID 找回采用旧命名的现有文件；缺少、多余推文 ID、匹配不唯一或目标文件已存在时直接报错，避免静默覆盖和图文错配。

## 小红书选题、去重与自动发布

完整规则见 [小红书汽车账号选题与发布手册](references/xiaohongshu-playbook.md)。账号承诺为“每天认识一台国内少见的车”，以关注转化而非单条点赞为目标：60% 稀有车型连续图鉴、25% 改装与机械反差、15% 视觉实验选题。按系列化潜力、前 3 秒画面、稀缺性、收藏分享、讨论空间和原帖互动评分；标题 1–20 字，正文说明车型和核心看点，话题 2–6 个。

**自动选题不是自动放行素材**：`topic_planner.py` 只负责栏目、系列、期数和检索式。Agent 必须结合原帖文字、车型信息和抽帧按 100 分模型评分，核验后低于 70 分、不符合当前系列或与近期车型重复的候选不得进入最终入选；不足时递补候选，不能降低禁选标准。`topic_plan.json` 中的 `recent_titles` 和 `recent_brands` 用于辅助避免重复，不能代替车型核验。

**硬性禁选**：海外玩车现场不是栏目；地下车聚、车展、车主故事、山路/公路驾驶、驾驶 POV 等特色驾驶场景一律不允许，即使高赞也不得入选。不能通过改写标题、正文或话题规避。面向小红书运行 finder 必须加 `--xiaohongshu`（`--auto-topic` 已隐含），禁选候选在下载前写入 `topic_skipped`；发布器会再次检查原帖文字和发布文案。

**固定历史文件**：`.agents/skills/x-car-video-finder/state/published_videos.jsonl`。手工发布过的内容先用 `publish_history.py add --source-id ... --title ...` 登记。finder 默认在任何下载前按原帖唯一 ID 过滤，命中项写入 manifest 的 `publish_skipped`，不下载、不抽帧、不进入发布环节；发布计划还会按唯一 ID 和标题再次检查。自动模式的实际发布登记会保存 `topic_pillar`、`topic_series_id`、`topic_series_episode`、品牌和文件主题，供下一次轮动；预演和搜索不消耗期数。

定稿后生成默认预演计划：

```bash
python3 .agents/skills/x-car-video-finder/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run plan --tweet-id 2075618551918985363
```

Agent 按 `<out>/xiaohongshu_publish_plan.json` 使用内置浏览器打开小红书创作者平台，自动上传 `video_file`、填写标题正文、逐个添加话题并核验。预演到点击“发布”前停止，不写历史。只有用户明确要求实际发布时，生成计划才加 `--confirm-publish`，浏览器才允许点击“发布”。

页面明确成功后运行 `xiaohongshu_publisher.py --out work/xfetch-run record --status published`；点击后结果不明时运行 `record --status unconfirmed`，同样阻止重发，不得盲目重试。未登录、验证码和平台风控由用户处理。

## Agent 工作流（重要）

1. 用户没有给主题时直接使用 `--auto-topic`，不要追问选题；用户给了主题才从 `--prompt` 提取车型/场景并补充同义检索式。自动模式读取 `topic_plan.json`，手动模式才使用提示词展开规则。
2. 在用户明确授权的浏览器/Profile 或 cookies.txt 中选择 X 会话；X 由 finder 自动执行 `xfetch auth check` 验证，默认不要读取浏览器。
3. 运行 finder（先 `--dry-run` 看榜单，由 Agent 检查内容是否对路再真下，无需让人工补选题）。固定发布历史必须启用，已发布候选在下载前跳过；此前手动发布的内容先补登记。手动面向小红书必须加 `--xiaohongshu`，自动模式已隐含；禁选内容在下载前跳过，画面核验时再次剔除文字未命中的现场内容。
4. 对下载的每条视频做三项核验，并记录每条的结果与淘汰原因：
   - **选题**：按 100 分模型逐项评分，自动模式只最终入选达到 70 分、符合当前系列且不与近期车型重复的视频；互动量不得替代其他评分项。
   - **时长**：默认素材上限 300 秒。finder 自动用 ffprobe 检查，超时视频直接进入 `<out>/rejected/`，不再抽帧；除非用户明确指定其他上限，不得放宽。自动计划的 15–45/20–60 秒是推荐成片时长，不是放宽素材上限的理由。
   - **画面**：对 `frames/` 里的图用 `read_image` 抽查，剔除“标题像汽车但画面不是”的结果；不合格就换候选重下。
   - 因画面等原因被额外淘汰的视频不要删除，也移入 `<out>/rejected/` 备查，并同步更新 `manifest.json`。
5. 为最终入选、淘汰记录撰写标题、推荐正文、原品牌、文件主题和具体推荐话题，写入 `publish.json`，运行 `finalize.py`。小红书标题必须为 1–20 字。
6. 用户要求发布小红书时，按手册生成计划并用内置浏览器执行。默认只预演；实际点击发布需要用户明确指令和 `confirm_publish=true`，成功或结果不明后才能登记历史。自动选题不等于授权实际发布。
7. 按下面「最终汇报格式」输出定稿器生成的**入选表 + 淘汰表两张表格**，再附 `REPORT.md`、`videos/`、`rejected/`、`frames/` 的路径；自动模式另附 `topic_plan.json`。提醒版权与平台条款。若执行了发布，另附发布状态、笔记 URL（如有）和历史文件路径。

## 最终汇报格式（X 汽车完整流水线必选）

完成上述 X 汽车视频流水线时，回复必须包含两张 Markdown 表格。列固定为 `# | 标题名 | 推荐正文 | 推荐话题 | 文件名 | 本地路径 | 时长 | 点赞 | 原帖`。“标题名”用简洁、准确的中文发布标题；“推荐正文”用可直接用于国内自媒体发布的中文正文，概括车型、事件和看点，不能只写视频内容简介或简单翻译原帖；“推荐话题”用空格分隔 2–6 个具体话题；“文件名”采用 `编号.品牌名_主题名`，编号必须与表格行号一致；“本地路径”写视频所在目录的完整绝对路径，不要只写 `videos/` 等相对路径；“原帖”用 `[链接](https://x.com/...)`：

**入选**：

| # | 标题名 | 推荐正文 | 推荐话题 | 文件名 | 本地路径 | 时长 | 点赞 | 原帖 |
|---|---|---|---|---|---|---|---|---|
| 1 | Apollo Evo 空气动力学细节 | Apollo Evo 以夸张的空气动力学造型和赛道化设计展示车身细节，适合拆解其独特的设计语言。 | #ApolloEvo #超跑 #汽车设计 | `1.Apollo_Evo 空气动力学细节.mp4` | `/Users/example/work/xfetch-run/videos` | 31s | 708 | [链接](https://x.com/fosgoodwood/status/2075618551918985363) |

**被淘汰**（同样的列，末尾追加“淘汰原因”，如 `超时 122s` / `画面非汽车` / `画质差疑似摆拍`）：

| # | 标题名 | 推荐正文 | 推荐话题 | 文件名 | 本地路径 | 时长 | 点赞 | 原帖 | 淘汰原因 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Peralta S 定制超跑亮相 | Peralta S 以定制化车身和复古超跑造型登场，展示了独特的设计细节。 | #PeraltaS #定制超跑 #汽车设计 | `1.Peralta_S 定制超跑亮相.mp4` | `/Users/example/work/xfetch-run/rejected` | 122s | 2350 | [链接](https://x.com/Rainmaker1973/status/2037424381962670242) | 超时 122s > 60s |

淘汰表为空时写明“无淘汰”。两张表由定稿器追加到 `<out>/REPORT.md` 末尾，保持报告可独立阅读。

## 注意

- 登录态只在本机使用；自动创建的临时 X 会话随进程清理，手动创建的 `/tmp/xfetch-auth.env` 用完即删，任何 Cookie 文件都不要提交进仓库。
- X 有频率限制：默认 `--delay 1.5` 不要调太小；大批量建议分多次、`--limit` 别贪大（单次 20–40/组足够）。其他平台也应控制并发和频率。
- `min_replies` 等操作符能否被 X 接受未实测；评论数排序请依赖本地 `--sort replies`。
- 下载内容版权归原作者，二次创作/商用需自行确认授权，遵守对应平台条款；只能拉取当前账号有权访问的内容。

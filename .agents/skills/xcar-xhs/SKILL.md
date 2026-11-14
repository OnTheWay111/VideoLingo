---
name: xcar-xhs
description: 按一句提示词或账号选题策略自动搜索、排序、下载并抽帧核验 X/Twitter 汽车视频，支持小红书自动选题、发布历史下载前去重，以及复用本机 Chrome Default 登录态上传并发布视频。Use when the user wants to find X car videos, automatically choose Xiaohongshu car topics, maintain a car account, skip already-published videos, or publish car videos to Xiaohongshu.
license: MIT
---

# X 汽车视频搜索与下载（xcar-xhs）

把一句自然语言提示词或内置账号策略变成「自动选题计划 → X 检索式 → 原帖与车型级去重 → 汽车相关性过滤 → 弱参考排序 → 下载 → 抽帧核验 → 标题正文定稿 → 跨批次排队 → 逐条发布 → 公开笔记与效果登记」的完整流水线。用户没有给选题时直接使用 `--auto-topic`，不要先追问主题；通用 X 主题筛选和其他平台 URL 素材核验请使用 `web-content-fetcher`，本 Skill 只负责汽车场景。

**每次正式任务仍须核验并入选至少 3 个视频；发布按评分与多样性逐条进行，不设每日发布数量上限，其余进入持久队列。** 下载失败、超时、选题不达标、画面不合格、重复或版权不清都只淘汰当前候选，不减少 3 条核验数量要求；必须继续翻页、补充同义检索式并扩大同栏目车型/场景搜索，递补到至少 3 个合格视频后才能定稿。不得用低分、禁选或授权不清素材凑数。

| 能力 | 工具 | 说明 |
|---|---|---|
| 搜索 X（带登录态） | `xfetch` skill（`../xfetch/scripts/xfetch.py`，twikit） | 输出 JSONL，含 `favorite_count` / `reply_count` / `retweet_count` / `view_count` / `media` |
| 统一 Web 会话 | `web-content-fetcher/scripts/web_session.py` | 固定复用本机 Chrome `Default`，临时 X 凭据自动清理 |
| 下载媒体 | `yt-dlp` | 使用同一 Chrome `Default` 会话，X 视频自动合并成 mp4 |
| 抽帧核验 | `ffmpeg` | 每条约 2 帧，供 Agent 用 `read_image` 判断是否真是汽车 |
| 发布前去重 | `scripts/publish_history.py` + `scripts/content_policy.py` | 原帖 ID 永久去重；同车型、同品牌同改装项目 30 天去重；同品牌每批最多一条 |
| 发布队列 | `scripts/publication_queue.py` | 固定保存跨批次候选，按评分和与上一条的相似度选择下一条 |
| 小红书发布 | `scripts/xiaohongshu_publisher.py` + `scripts/chrome_xiaohongshu.py` | 固定复用 Chrome `Default`，只执行当前发布计划并登记，无人工确认环节 |
| 效果复盘 | `scripts/note_metrics.py` | 24/72 小时记录六项真实数据，计算收藏率和每千次播放新增关注 |

## 前置条件

- **web-content-fetcher skill**：本仓库内 `.agents/skills/web-content-fetcher/`，提供共享会话与浏览器提取脚本。
- **xfetch skill**：本仓库内 `.agents/skills/xfetch/`（不存在则 `npx skills add agents365-ai/365-skills@xfetch`）。
- **Python venv**：`.venv-xfetch/`（含 twikit / httpx / browser_cookie3）。
  ```bash
  python3 -m venv .venv-xfetch
  .venv-xfetch/bin/pip install -r .agents/skills/xfetch/scripts/requirements.txt
  ```
- **yt-dlp + ffmpeg**：`which yt-dlp ffmpeg`，macOS 可用 `brew install yt-dlp ffmpeg`。
- **X 登录态**：固定使用本机 Chrome `Default` 的 X 登录态，无需每次选择浏览器或 Profile。
- **小红书登录态**：固定使用本机 Chrome `Default` 的小红书登录态，由 `scripts/chrome_xiaohongshu.py` 复制到临时隔离配置并通过 CDP 发布。无需 Playwright/Selenium，临时配置用完删除且不输出 Cookie 值。

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

- 本 skill 当前在 `<项目根>/.agents/skills/xcar-xhs`，DSH 会话目录会自动收录（本项目内已验证热更新）。
- Web 编辑器输入 `/` 会列出用户可调用的 skill，直接键入 `/xcar-xhs 找几条 Cybertruck 越野视频` 即可调用；宿主会把完整指令注入本轮，无需模型再调 `skill` 工具。TUI / ACP 行为一致。
- 只想允许 `/` 调用、不让模型自动选：frontmatter 加 `disable-model-invocation: true`；想隐藏 `/` 入口：`user-invocable: false`。
- 全局安装（所有项目）：把本目录、`web-content-fetcher` 和 `xfetch` 一起复制到 `~/.agents/skills/` 或 `~/.dsh/skills/`。
- 手改文件后模型目录会自动刷新；若 `/` 菜单没更新，刷新页面、新建会话或重连即可。

## Web 登录会话（固定 Chrome Default）

`finder.py` 复用 `web-content-fetcher/scripts/web_session.py`，默认固定为 `--web-session chrome --browser-profile Default`。X 搜索、登录校验和视频下载都复用本机 Chrome `Default` 的 X 登录态，无需每次选择或重复传参。

- **排障用 Cookie 文件**：`--cookies-file ~/Downloads/cookies.txt`，要求 Netscape cookies.txt 格式，与 `--web-session` 二选一；只有显式传参时才替代固定默认会话。
- **排障用 X 凭据**：`--auth-file /tmp/xfetch-auth.env` 或 `XFETCH_AUTH_TOKEN` / `XFETCH_CT0`。`--auth-file` 只用于 X，不会传给其他平台。
- **X 凭据优先级**：显式 `--auth-file` > `--cookies-file` > 固定 Chrome `Default` 会话 > 环境变量或 xfetch 已保存会话。macOS 上先原生提取 Chrome 会话，失败后自动回退 CDP；CDP 需要可选依赖 `websocket-client`（`.venv-xfetch/bin/pip install websocket-client`）。
- **其他平台**：由 yt-dlp 的 `--cookies-from-browser chrome:Default` 读取对应站点会话；显式使用 cookies.txt 时改传 `--cookies`。不使用 `X_BEARER_TOKEN`、`XQUIK_API_KEY`、`XAI_API_KEY` 或 ScrapeCreators。

`finder.py` 会先通过 `xfetch auth check` 实际请求 X 验证会话，再开始搜索；临时导入的 X 凭据在进程结束时清理。报告只记录会话方式、浏览器和 Profile，不记录 Cookie 值。macOS 可能出现钥匙串授权弹窗，这是解密 Chrome `Default` Cookie 所需。

手动提取仅用于排障：

```bash
python3 .agents/skills/web-content-fetcher/scripts/auth_from_chrome.py --profile Default --out /tmp/xfetch-auth.env
python3 .venv-xfetch/bin/python .agents/skills/xfetch/scripts/xfetch.py auth check
```

钥匙串取不到时可继续使用 `web-content-fetcher/scripts/auth_from_chrome_cdp.py --profile Default`，或手动设置 `XFETCH_AUTH_TOKEN` / `XFETCH_CT0`；这些凭据都不能打印、提交或写入报告。

## 用法

**默认时长规则：视频时长不得超过 5 分钟（300 秒）。** 正好 300 秒可以入选；超过 300 秒的视频会自动移入 `rejected/`，并写入 `REPORT.md` 和 `manifest.json` 的淘汰清单。可用 `--max-duration 秒数` 调整上限，显式传 `--max-duration 0` 可关闭限制。

**最少入选规则：`--top` 表示目标核验入选数，不是单次发布数，正式运行不得低于 3。** finder 会越过下载失败和超时候选自动递补；候选耗尽仍不足时以退出码 3 报告未完成。此时应增加 `--pages`、补充同义 `--query` 或扩大同栏目检索后继续，不得把不足 3 条的结果交给定稿或入队。`--dry-run` 只预演榜单，不视为完成核验名单；正式发布不受每日一条的数量限制。

自动选题（无需人工输入主题）：

```bash
python3 .agents/skills/xcar-xhs/scripts/finder.py \
  --auto-topic --sort likes --top 3 --out work/xfetch-run
```

`--auto-topic` 按发布历史执行 60% 稀有车型、25% 改装机械、15% 视觉实验（含山路驾驶场景）的 20 条轮动，同一系列连续规划 5 期，并自动生成带禁选排除词的检索式。它隐含 `--xiaohongshu`，不能与 `--prompt` 或 `--query` 同用。重复运行在实际发布登记前会保持同一计划，避免预演消耗期数。

只查看下一期计划、不访问 X：

```bash
python3 .agents/skills/xcar-xhs/scripts/topic_planner.py
```

手动提示词仍然可用（自动展开多组检索式）：

```bash
python3 .agents/skills/xcar-xhs/scripts/finder.py \
  --prompt "找几条 Cybertruck 越野视频，点赞高的优先" \
  --sort likes --top 3 --out work/xfetch-run
```

手动指定检索式（更精确，`--query` 可重复；查询里自己带 `filter:media`、`min_faves:`）：

```bash
python3 .../finder.py \
  --prompt "超跑山路 POV" \
  --query '(supercar OR sportscar) ("mountain road" OR "mountain pass" OR touge) filter:media min_faves:1000' \
  --query '("mountain road POV" OR "mountain drive POV" OR "touge POV") filter:media min_faves:500' \
  --sort replies --top 3 --since 2026-09-01 --limit 25
```

**排序**：X 服务端只有 Top/Latest/Media 三种视图，没有 sort 参数；本脚本先拉一批，再本地精确排序：

| `--sort` | 字段 | 说明 |
|---|---|---|
| `likes`（默认） | `favorite_count` | 按点赞量降序 |
| `replies` | `reply_count` | 按评论数降序 |
| `reposts` | `retweet_count` | 按转发数降序 |
| `views` | `view_count` | 按播放量降序 |

服务端预过滤：`--min-likes N` 会追加 `min_faves:N`；`--since YYYY-MM-DD` 追加 `since:`。`--type top` 用 X 的综合互动榜做初排。**默认 `--min-likes 0 --min-reposts 0`，点赞和转发不占评分、不是主要放行依据，只作候选排序的弱参考。** 只有用户显式设置正数门槛才在下载前过滤并写入 `engagement_skipped`；`--min-reposts` 使用本地精确过滤。Agent 仍须按 100 分模型评分，只让核验后达到 70 分的素材进入最终入选。

## 选项

| 选项 | 默认 | 说明 |
|---|---|---|
| `--prompt` | 手动模式必填 | 原始提示词，写进报告和 manifest；与 `--auto-topic` 二选一 |
| `--auto-topic` | 关 | 按策略和历史自动选题，隐含 `--xiaohongshu`；不与 `--prompt` / `--query` 同用 |
| `--query` | 自动生成 | X 检索式，可重复；手动模式不给则按提示词生成，自动模式使用选题计划 |
| `--sort` | `likes` | `likes` / `replies` / `reposts` / `views` |
| `--top` | `3` | 目标核验入选 N 条，不是发布数；正式运行最少 3 条，失败或超时自动递补 |
| `--candidates` | `15` | stdout 表格展示前 N 条候选 |
| `--limit` | `25` | 每组检索式每页条数 |
| `--pages` | `1` | 每组翻页数（>1 时自动带 cursor） |
| `--type` | `top` | `top` / `latest` / `media` |
| `--since` | 无 | 只搜此日期之后 |
| `--min-likes` | `0` | 点赞仅作弱参考；显式正数才启用服务端和本地下载过滤 |
| `--min-reposts` | `0` | 转发仅作弱参考；显式正数才启用本地下载过滤 |
| `--delay` | `1.5` | 翻页间隔秒数 |
| `--out` | `work/xfetch-run` | 输出根目录 |
| `--publish-history` | Skill 内 `state/published_videos.jsonl` | 已发布历史；命中唯一 ID 时下载前跳过，不进入后续环节 |
| `--xiaohongshu` | 关 | 小红书禁选过滤：车聚车展、车主故事、非山路特色驾驶场景下载前跳过；山路驾驶（含山路 POV）可入选 |
| `--auth-file` | 无 | X 专用 `KEY=VALUE` 登录态文件 |
| `--web-session` | `chrome` | 固定复用本机 Chrome 登录态；显式传参仅用于排障 |
| `--cookies-file` | 无 | Netscape cookies.txt；显式传参时替代固定浏览器会话 |
| `--browser-profile` | `Default` | 固定使用 Chrome `Default`；显式传参仅用于排障 |
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
├── publish.json         # 标题、正文、品牌/车型/项目、事实来源、评分和推荐话题
├── topic_plan.json      # 自动模式：栏目、系列期数、检索式、评分维度和近期避开项
├── manifest.json        # 选题计划、候选、最少核验入选数和淘汰原因
├── candidates.csv       # 未发布候选（含点赞/评论/播放），可直接排序
├── xiaohongshu_publish_plan.json     # 当前发布计划
├── plans/               # 当前发布计划的执行副本，其他候选只留在持久队列
├── xiaohongshu_publish_receipt.json  # 可选：实际发布登记回执
└── REPORT.md            # 中文报告：检索式、榜单、发布跳过、入选清单和淘汰清单
```

stdout 会打印候选 Markdown 表（含链接、点赞、评论、日期），可直接粘给用户。finder 下载时的 `<uploader>_<media_id>.mp4` 只是临时文件名；交付前必须运行下面的定稿器，统一改成 `编号.品牌名_主题名`。

## 标题命名、推荐话题与发布定稿（必做）

画面核验和候选递补完成后，为 `manifest.json` 里**每条入选和淘汰记录**写标题、推荐正文、品牌、文件主题和 2–6 个推荐话题。入选记录另须填写车型、改装项目、标题细节、可核验事实及来源、讨论问题和五项评分。话题应结合品牌、车型、改装点和场景，避免只写宽泛标签。在 `<out>/publish.json` 中以原帖推文 ID 为键：

```json
{
  "2075618551918985363": {
    "title": "BMW Z1 的车门去哪了？",
    "body": "BMW Z1 的车门可电动下滑收入车侧门槛，而不是向外或向上打开。视频展示了这个少见结构的实际运动过程。",
    "brand": "BMW",
    "model": "Z1",
    "modification_project": "",
    "file_topic": "Z1 下滑车门",
    "title_detail": "车门",
    "verifiable_fact": "BMW Z1 的车门可电动下滑收入车侧门槛",
    "fact_source": "https://x.com/fosgoodwood/status/2075618551918985363",
    "discussion_question": "你更想看下滑车门还是跳灯结构？",
    "score_breakdown": {
      "first_three_seconds": 23,
      "knowledge_point": 23,
      "save_value": 18,
      "discussion_question": 12,
      "recent_diversity": 14
    },
    "topics": ["#BMWZ1", "#汽车设计", "#罕见车型"],
    "ai_disclosure": "选题、素材初筛及文字由 AI 辅助整理。"
  }
}
```

`brand` 是车辆原品牌，不是改装厂商，例如 Venuum 改装的 Bugatti Chiron 应写 `Bugatti`；`model` 必须是具体车型；`modification_project` 填发动机移植、宽体等具体项目，原厂车留空。`file_topic` 是用于文件名的简短主题，表格中的完整发布标题仍由 `title` 决定。为兼容旧结果，缺少 `brand` 时使用 `汽车`，缺少 `file_topic` 时使用完整 `title`；新任务必须逐条填写，不能依赖默认值。

`title_detail` 必须原样出现在标题中，`verifiable_fact` 必须原样出现在正文中，`fact_source` 必须是实际核验过的 HTTP(S) 地址，`discussion_question` 必须是明确问题。前三秒运动机构/反差 25 分、知识点 25 分、收藏价值 20 分、讨论问题 15 分、近期多样性 15 分，总分达到 70 才能入选；点赞不参与加分。减少“有多……”空泛模板，每条都要能回答一个具体问题。

`topics` 必须是字符串数组，可省略 `#`，定稿器会统一补齐并去除话题内空白。为兼容旧结果，缺少 `topics` 时使用 `#汽车 #改装车`；新任务应逐条撰写具体话题，不能依赖默认值。

运行定稿器：

```bash
python3 .agents/skills/xcar-xhs/scripts/finalize.py \
  --out work/xfetch-run \
  --publish-file work/xfetch-run/publish.json
```

定稿器会自动完成并同步以下结果，不能只手工改文件名：

- 入选视频改为 `<入选编号>.<品牌名>_<主题名>.mp4`，淘汰视频改为 `<淘汰编号>.<品牌名>_<主题名>.mp4`，两组均从 `1.` 开始，与各自表格行号一致；抽帧使用相同编号，改为 `<编号>.<品牌名>_<主题名>_帧序号.jpg`。文件名不再强制包含推文 ID，原帖 ID 继续保留在 manifest 和原帖链接中。
- 品牌和主题分别清理文件系统不允许的字符及内部下划线，单个字段最长取 48 个字符；`publish.json` 和表格仍保留完整标题。
- 把标题正文、品牌、车型、改装项目、事实来源、评分、话题和更新后的路径写回 `manifest.json`，入选按总分降序排列。
- 根据明显讲解文字和 `verification.presentation` 自动拦截讲解视频，从入选清单移入 `rejected/`，写入 `reason`、`verification.rejection_policy=explainer_video` 和 `stats.explainer_rejected`，不再以 70 分评分作为放行依据。
- 同品牌每批只保留最高分一条；同车型 30 天内不重复，同品牌同改装项目 30 天内不重复，队列中的同车型/项目也不重复。`迈凯伦 + 转子` 与 `McLaren + rotary swap` 会归一为同一项目，即使原帖 ID 不同也会拦截。
- 新版 manifest 记录 `minimum_selected=3`；讲解或重复拦截后若核验入选不足 3 条，定稿器直接报错，发布器也会阻止入队，必须先继续搜索补足。
- 把最终入选表、淘汰表写入 `<out>/REPORT.md`，并在 stdout 输出相同表格；重复运行会更新原表格，不会重复追加，也不会重复添加编号。
- 旧 manifest 中的媒体路径失效时，可按唯一推文 ID 找回采用旧命名的现有文件；缺少、多余推文 ID、匹配不唯一或目标文件已存在时直接报错，避免静默覆盖和图文错配。

## 自动发布与结果预览

完成下载、抽帧和定稿后，**不得再询问是否发布，也不得要求二次授权确认**。运行一次 `plan` 会把本批全部合格视频加入固定队列，并为当前最高分且不与上一条同品牌、同项目或同系列的一条生成 `action=publish` 计划，随即按下方 Chrome 流程自动发布。登记结果后可再次运行 `plan` 发布下一条，不设每日发布数量上限。只有用户明确要求预演或保存草稿时，才使用 `preview` 或 `draft`；正常流水线不得提供选项后等待回复。

`publish.json` 的每条内容必须包含 `ai_disclosure`（例如“选题、素材初筛及文字由 AI 辅助整理”），缺项直接阻断；不再需要 `rights_confirmed` 字段。AI 生成或概念动画必须单独标明，不能用翻译或拟人化措辞规避披露。授权不清的内容在筛选阶段直接淘汰，不通过额外人工确认放行。

```bash
# 全部入选入队，默认发布当前选中的一条，无需 --action 或确认参数
python3 .agents/skills/xcar-xhs/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run plan

# 仅用户明确要求时保存草稿
python3 .agents/skills/xcar-xhs/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run plan --tweet-id 原帖ID --action draft
```

计划生成器同时写入 `<out>/xiaohongshu_publish_plan.json` 和 `<out>/plans/<原帖ID>.json`，Chrome 只读取前者，不扫描其他候选。全量队列固定在 `state/publish_queue.json`，发布成功后写入 `state/published_videos.jsonl`；用户明确要求草稿时，成功结果写入独立的 `state/draft_videos.jsonl`，草稿不得冒充已发布记录。

当前发布登记完成并补齐真实公开笔记地址后，可再次运行计划命令选择下一条，不受每日一条限制。最高分候选若与上一条同品牌、同改装项目或同系列会顺延，选择下一条不相似内容；不使用 1–10 分钟的批量连续发布间隔。上传前失败会把当前候选释放回队列，点击后结果不明则记为 `unconfirmed` 并阻止后续发布。

完成后的入选表“标题名”必须输出为 `[标题](<视频绝对路径>)`，让用户直接点击标题预览入选视频；同时标明哪一条已发布、哪些仍在队列。文件名和所在目录继续保留。预览是结果交付，不是发布前的等待环节。

## 小红书选题、去重与自动发布

完整规则见 [小红书汽车账号选题与发布手册](references/xiaohongshu-playbook.md)。账号承诺为“每天认识一台国内少见的车”，以关注转化而非单条点赞为目标：60% 稀有车型连续图鉴、25% 改装与机械反差、15% 视觉实验选题（含山路驾驶场景）。按前三秒、知识点、收藏价值、讨论问题和近期多样性评分；标题 1–20 字且含具体细节，正文必须有事实来源和讨论问题，话题 2–6 个。

**自动选题不是自动放行素材**：`topic_planner.py` 只负责栏目、系列、期数和检索式。Agent 必须结合原帖文字、车型信息和抽帧按 100 分模型评分，核验后低于 70 分、不符合当前系列或与近期车型重复的候选不得进入最终入选；最终合格入选不足 3 条时必须继续搜索和递补，不能降低禁选、评分、事实核验或版权标准。`topic_plan.json` 中的 `recent_titles` 和 `recent_brands` 用于辅助避免重复，不能代替车型核验。

**山路驾驶场景已放开**：山路驾驶、山腰/山口道路、Touge、峡谷道路及相应驾驶 POV 均可作为视频主题，按正常评分、画面和版权流程入选。**其他硬性禁选**：海外玩车现场不是栏目；地下车聚、车展、车主故事、非山路的公路驾驶或驾驶 POV 等特色驾驶场景一律不允许，即使高赞也不得入选。真人出镜讲解、人物分屏口播、销售页或网页截图配合讲解同样禁选，车辆清晰或评分达到 70 分也不能放行。不能通过改写标题、正文或话题规避。面向小红书运行 finder 必须加 `--xiaohongshu`（`--auto-topic` 已隐含），明显讲解文字在下载前写入 `topic_skipped`；抽帧后由定稿器移入 `rejected/`，发布器还会最终检查原帖文字、发布文案和画面标注。

**固定历史文件**：`.agents/skills/xcar-xhs/state/published_videos.jsonl`。手工发布过的内容先用 `publish_history.py add --source-id ... --title ... --brand ... --model ... --modification-project ...` 登记。finder 默认在任何下载前按原帖唯一 ID 过滤，命中项写入 manifest 的 `publish_skipped`；定稿和入队时再按品牌、具体车型、改装项目及规范化标题去重。自动模式的实际发布登记会保存栏目、系列、品牌、车型、项目、事实和评分，供后续轮动与复盘；预演和搜索不消耗期数。

定稿后运行一次不带 `--tweet-id` 的计划命令，三条全部入队并生成一条当前发布计划；登记结果后可再次运行，不受每日一条限制：

```bash
python3 .agents/skills/xcar-xhs/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run plan
```

计划命令本身不操作网页；它生成 `action=publish` 的 `<out>/xiaohongshu_publish_plan.json`，并自动保存到 `<out>/plans/<原帖ID>.json`。随后必须继续执行下方 Chrome 发布流程，不能停在计划阶段等待人工确认。

### 固定复用 Chrome Default 小红书登录态

生成当前发布计划后，直接验证会话并只执行这一条：

```bash
python3 .agents/skills/xcar-xhs/scripts/chrome_xiaohongshu.py \
  --out work/xfetch-run --headless --preflight-only

python3 .agents/skills/xcar-xhs/scripts/chrome_xiaohongshu.py \
  --out work/xfetch-run --headless --timeout 90
```

发布导航直接访问 `https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target=video`，不经过创作者首页或菜单跳转；发布计划中的 `publish_url` 使用同一地址。

`chrome_xiaohongshu.py` 固定复制本机 Chrome `Default` 的 `Cookies`、`Preferences` 和 `Local State` 到临时隔离配置，启动独立 Chrome，通过 CDP 上传和填写，使用正常 Chrome 标识，等待无障碍树中的发布按钮真正启用后发送真实鼠标事件。原 Chrome 配置和标签页不被修改，初始化或发布异常时也清理临时配置，Cookie 值不打印、不写入报告。`--browser-profile` 默认固定为 `Default`；可省略 `--headless` 显示隔离浏览器，`--only <原帖ID>` 只执行一条。

Chrome 不再按 manifest 批量执行，只读取当前发布计划。填写后复读标题、正文和话题，发布按钮必须处于启用状态。`result_url` 保存发布结果页，`note_url` 只保存真实公开笔记地址，不能把 `/publish/success` 当作分享链接；成功页没有公开地址时会尝试读取页面中的公开笔记链接。实测故障、根因和检查清单统一查阅 [小红书 Chrome 发布实测经验与故障手册](references/xiaohongshu-chrome-troubleshooting.md)，修改发布逻辑后按手册完成回归。

脚本逐条检查历史，成功页明确出现后才登记 `published`。点击后结果不明时登记 `unconfirmed` 并停止后续发布。只有在后台确认实际未发布时，才可运行 `publish_history.py resolve --source-id ... --title ... --reason ...` 将误记更正为 `failed` 后重试；`failed` 不再阻止重试，最新状态优先于历史状态。

### 公开笔记地址与 24/72 小时效果

若发布成功页没有给出公开笔记地址，回执会标记 `note_url_pending=true`，后续发布前必须先从公开分享页或创作者后台取得真实地址并补登记，不能用结果页代替：

```bash
python3 .agents/skills/xcar-xhs/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run attach-note \
  --source-id 原帖ID \
  --note-url "https://www.xiaohongshu.com/explore/真实笔记ID"
```

用 `note_metrics.py due` 查看已到期的 24/72 小时任务。到期后从公开笔记和创作者后台读取真实数据，六项指标都必须填写；未知值不能填 0 冒充。播放和新增关注通常需要创作者后台，公开页拿不到时必须明确补查，不得估算。

```bash
python3 .agents/skills/xcar-xhs/scripts/note_metrics.py due

python3 .agents/skills/xcar-xhs/scripts/note_metrics.py record \
  --source-id 原帖ID --checkpoint 24 \
  --views 10000 --likes 500 --favorites 120 \
  --comments 30 --shares 20 --new-followers 15
```

数据固定追加到 `state/note_metrics.jsonl`，72 小时使用 `--checkpoint 72` 再记一次，不覆盖 24 小时快照。脚本自动计算 **收藏率 = 收藏 ÷ 播放** 和 **每千次播放新增关注 = 新增关注 ÷ 播放 × 1000**；播放为 0 时比率保存为 `null`。复盘优先看这两个指标，而不是只追求点赞。

## Agent 工作流（重要）

1. 用户没有给主题时直接使用 `--auto-topic`，不要追问选题；用户给了主题才从 `--prompt` 提取车型/场景并补充同义检索式。自动模式读取 `topic_plan.json`，手动模式才使用提示词展开规则。
2. X 登录态固定使用本机 Chrome `Default`，不要询问浏览器或 Profile，也不要默认改用内置浏览器。finder 自动执行 `xfetch auth check` 验证；只有登录校验失败时，才提示用户在 Chrome `Default` 登录 X，或显式选择 cookies.txt / auth-file 排障。
3. 运行 finder（先 `--dry-run` 看榜单，由 Agent 检查内容是否对路再真下，无需让人工补选题）。固定发布历史必须启用，已发布候选在下载前跳过；此前手动发布的内容先补登记。默认互动门槛均为 0，点赞只作弱参考，不能为了凑数量降低选题、画面或版权标准；只有用户显式要求才设置点赞/转发出下载门槛。手动面向小红书必须加 `--xiaohongshu`，自动模式已隐含；禁选内容在下载前跳过，画面核验时再次剔除文字未命中的现场内容。
4. 对下载的每条视频做三项核验，并记录每条的结果与淘汰原因：
   - **选题**：按 100 分模型逐项评分，自动模式只最终入选达到 70 分、符合当前系列且不与近期车型重复的视频；互动量不得替代其他评分项。
   - **时长**：默认素材上限 300 秒。finder 自动用 ffprobe 检查，超时视频直接进入 `<out>/rejected/`，不再抽帧；除非用户明确指定其他上限，不得放宽。自动计划的 15–45/20–60 秒是推荐成片时长，不是放宽素材上限的理由。
   - **画面**：对 `frames/` 里的图用 `read_image` 抽查，剔除“标题像汽车但画面不是”的结果；不合格就换候选重下。每条必须写入 `verification.presentation`：纯车辆细节或山路驾驶画面为 `car_only`，真人出镜或人物分屏讲解为 `talking_head_explainer`，网页、销售页或屏幕配合讲解为 `screen_explainer`。后两类即使车辆清晰、原帖高赞或评分达到 70 分也必须淘汰。
   - 因画面等原因被额外淘汰的视频不要删除，也移入 `<out>/rejected/` 备查，并同步更新 `manifest.json`。运行 `finalize.py` 时会根据画面标注和明显讲解文字自动完成讲解视频的移动与淘汰记录，不能只把分数调低后仍留在入选清单。
   - **数量闭环**：每淘汰一条就检查合格入选数；不足 3 条时继续增加 `--pages`、补充同义 `--query`、扩大同栏目车型/场景检索并下载抽帧，直到至少 3 条全部通过核验。候选耗尽或接口受限只能报告阻塞，不能定稿、发布不足 3 条的名单或降低标准凑数。
5. 确认最终入选至少 3 条后，为入选、淘汰记录撰写标题、正文、品牌、具体车型、改装项目、事实来源、讨论问题、五项评分和具体话题，写入 `publish.json`，运行 `finalize.py`。小红书标题必须为 1–20 字并包含 `title_detail`，正文必须原样包含 `verifiable_fact`。定稿器再执行同品牌每批一条、同车型/项目 30 天和队列去重。
6. 定稿后运行一次不带 `--tweet-id` 的 `plan`，把合格视频全部加入 `state/publish_queue.json`，生成当前最高分且不相似的一条计划。固定复用 Chrome `Default` 的小红书登录态，先 preflight 再直接发布这一条；不得询问“自动发布/自动保存草稿”，不得要求 `rights_confirmed` 或其他二次授权确认。其余视频留在队列；登记当前结果并补齐公开笔记地址后，可再次运行计划继续发布，不设每日一条限制；结果不明时停止后续操作。
7. 发布成功必须保存真实公开笔记地址；成功页拿不到时用 `attach-note` 补登记，未补齐会阻止后续发布。运行 `note_metrics.py due` 检查 24/72 小时任务，到期后记录六项真实数据并复盘收藏率和每千次播放新增关注。
8. 按下面「最终汇报格式」输出定稿器生成的**入选表 + 淘汰表两张表格**，再附 `REPORT.md`、`videos/`、`rejected/`、`frames/` 的路径；自动模式另附 `topic_plan.json`。明确标注本次已发布内容及其余排队状态，附队列、笔记 URL、历史与效果文件路径。提醒版权与平台条款。

## 最终汇报格式（X 汽车完整流水线必选）

完成上述 X 汽车视频流水线时，回复必须包含两张 Markdown 表格。列固定为 `# | 标题名 | 推荐正文 | 推荐话题 | 文件名 | 本地路径 | 时长 | 点赞 | 原帖`。“标题名”用简洁、准确的中文发布标题；“推荐正文”用可直接用于国内自媒体发布的中文正文，概括车型、事件和看点，不能只写视频内容简介或简单翻译原帖；“推荐话题”用空格分隔 2–6 个具体话题；“文件名”采用 `编号.品牌名_主题名`，编号必须与表格行号一致；“本地路径”写视频所在目录的完整绝对路径，不要只写 `videos/` 等相对路径；“原帖”用 `[链接](https://x.com/...)`：

**入选**：

| # | 标题名 | 推荐正文 | 推荐话题 | 文件名 | 本地路径 | 时长 | 点赞 | 原帖 |
|---|---|---|---|---|---|---|---|---|
| 1 | [Apollo Evo 空气动力学细节](</Users/example/work/xfetch-run/videos/1.Apollo_Evo 空气动力学细节.mp4>) | Apollo Evo 以夸张的空气动力学造型和赛道化设计展示车身细节，适合拆解其独特的设计语言。 | #ApolloEvo #超跑 #汽车设计 | `1.Apollo_Evo 空气动力学细节.mp4` | `/Users/example/work/xfetch-run/videos` | 31s | 708 | [链接](https://x.com/fosgoodwood/status/2075618551918985363) |

**被淘汰**（同样的列，末尾追加“淘汰原因”，如 `超时 122s` / `画面非汽车` / `画质差疑似摆拍`）：

| # | 标题名 | 推荐正文 | 推荐话题 | 文件名 | 本地路径 | 时长 | 点赞 | 原帖 | 淘汰原因 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Peralta S 定制超跑亮相 | Peralta S 以定制化车身和复古超跑造型登场，展示了独特的设计细节。 | #PeraltaS #定制超跑 #汽车设计 | `1.Peralta_S 定制超跑亮相.mp4` | `/Users/example/work/xfetch-run/rejected` | 122s | 2350 | [链接](https://x.com/Rainmaker1973/status/2037424381962670242) | 超时 122s > 60s |

淘汰表为空时写明“无淘汰”。两张表由定稿器追加到 `<out>/REPORT.md` 末尾，保持报告可独立阅读。

## 注意

- 登录态只在本机使用；自动创建的临时 X 会话随进程清理，手动创建的 `/tmp/xfetch-auth.env` 用完即删，任何 Cookie 文件都不要提交进仓库。
- X 有频率限制：默认 `--delay 1.5` 不要调太小；大批量建议分多次、`--limit` 别贪大（单次 20–40/组足够）。其他平台也应控制并发和频率。
- `min_replies` 等操作符能否被 X 接受未实测；评论数排序请依赖本地 `--sort replies`。
- 下载内容版权归原作者，二次创作/商用必须符合素材授权与对应平台条款；授权不清的素材直接淘汰，只能拉取当前账号有权访问的内容。

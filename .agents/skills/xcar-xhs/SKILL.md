---
name: xcar-xhs
description: 按一句提示词或账号选题策略自动搜索、排序、下载并抽帧核验 X/Twitter 汽车视频，支持小红书自动选题、断点递补、核验后只保存候选池；用户明确要求发布时复用本机 Chrome Default 上传并发布。Use when the user wants to find X car videos, automatically choose Xiaohongshu car topics, maintain a car account, skip already-published videos, or publish car videos to Xiaohongshu.
license: MIT
---

# X 汽车视频搜索与下载（xcar-xhs）

把一句自然语言提示词或内置账号策略变成「库存感知自动选题 → 增量 X 检索 → 下载前去重 → 下载抽帧 → Agent 核验与文案 → 可恢复定稿 → 只保存跨批次候选池」的流水线。默认入池即完成，不生成发布计划、不启动小红书浏览器；仅用户明确要求发布时进入独立发布流程。用户没有给选题时直接使用 `candidate_pipeline.py`，不要先追问主题；通用 X 主题筛选和其他平台 URL 素材核验请使用 `web-content-fetcher`，本 Skill 只负责汽车场景。

**每次正式候选任务须核验并入选至少 3 个视频，全部保存到持久队列；默认不发布。** 下载失败、超时、选题不达标、画面不合格、重复或版权不清都只淘汰当前候选，不减少 3 条核验数量要求；必须继续翻页、补充同义检索式并扩大同栏目车型/场景搜索，递补到至少 3 个合格视频后才能定稿。不得用低分、禁选或授权不清素材凑数。

| 能力 | 工具 | 说明 |
|---|---|---|
| 搜索 X（带登录态） | `xfetch` skill（`../xfetch/scripts/xfetch.py`，twikit） | 输出 JSONL，含 `favorite_count` / `reply_count` / `retweet_count` / `view_count` / `media` |
| 统一 Web 会话 | `web-content-fetcher/scripts/web_session.py` | 固定复用本机 Chrome `Default`，临时 X 凭据自动清理 |
| 下载媒体 | `yt-dlp` | 使用同一 Chrome `Default` 会话，X 视频自动合并成 mp4 |
| 抽帧核验 | `ffmpeg` | 每条约 2 帧，供 Agent 用 `read_image` 判断是否真是汽车 |
| 发布前去重 | `scripts/publish_history.py` + `scripts/content_policy.py` | 原帖 ID 永久去重；同车型、同品牌同改装项目 30 天去重；同品牌每批最多一条 |
| 候选生产 | `scripts/candidate_pipeline.py` | 自动选题、增量检索、审核交接、递补、定稿并只入池 |
| 候选池管理 | `scripts/candidate_pool.py` + `scripts/publication_queue.py` | 独立入池、带版本更新、撤回、失效与过期复核；不选择发布计划 |
| 小红书发布 | `scripts/publish_pipeline.py`（复用现有计划与 Chrome 模块） | 固定复用 Chrome `Default`，只执行当前发布计划并登记，无人工确认环节 |
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

自动选题并生产候选（默认只入池，不发布）：

```bash
python3 .agents/skills/xcar-xhs/scripts/candidate_pipeline.py \
  --out work/xcar-candidates-本次任务名
```

新任务使用新目录；同一目录再次运行即恢复。脚本输出 `awaiting_review`（退出码 3）时，Agent 读取 `review_request.json`、查看真实视频/抽帧并核实事实和授权，将逐条审核与文案保存为 `review.json`，随后继续：

```bash
python3 .agents/skills/xcar-xhs/scripts/candidate_pipeline.py \
  --out work/xcar-candidates-本次任务名 \
  --review-file work/xcar-candidates-本次任务名/review.json
```

**审核是 Agent 工作步骤，不是让用户再确认。** 不可把模板中的 pending 直接批量改成 approved；必须完成画面、前三秒、事实、授权和评分核验。有淘汰时脚本保留结果并递补，Agent 继续处理下一批审核请求，直到 `candidate_receipt.json` 为 `complete`。脚本本身不调用模型，不替代视觉判断或事实核查。输出为 `blocked`、`budget_exhausted` 或 `exhausted` 时按原因处理；不可把不足三条的批次标为完成。

`needs_candidates` 表示定稿时去重出现新缺口，再运行同目录继续递补。默认累计搜索预算 12 页、下载尝试 15 次，每次执行时间预算 600 秒；用 `--max-pages`、`--max-downloads`、`--max-seconds` 调整。预算耗尽保留进度；`--query` 可补充当前系列检索式；`--retry-failed` 显式重试临时下载/抽帧错误；`--cancel` 取消任务并释放预约，保留文件。详见 [候选流水线与审核协议](references/candidate-pipeline.md)。

选题器按最新已发布内容计算 60%/25%/15% 轮动参考，补链接与 unconfirmed 不推进期数；生产选题同时参考排队库存和在制作任务，优先补充覆盖不足的栏目/系列。候选保存独立 job_id 和系列，不绑定发布期数；真正生成发布计划时才按该系列的实际发布记录分配期数。旧 `finder.py --auto-topic` 保留为底层搜索/排障入口，不等于完成核验或入池。

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

## finder 底层选项（正常候选生产使用上方统一入口）

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
    "topics": ["#BMWZ1", "#汽车设计", "#罕见车型"]
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

## 默认入池与显式发布

正常候选任务由 `candidate_pipeline.py` 自动完成定稿和纯入池，读 `candidate_receipt.json` 汇报即可，**不得自动接着调用发布脚本**。手动流程已经定稿时可独立运行 `candidate_pool.py enqueue --out <out>`，不产生 planned 状态。它不受前一条发布结果待确认或缺公开链接影响。

仅在用户明确要求发布时，调用 `publish_pipeline.py` 完成已有发布链路；沿用本次明确授权，无需重复确认。以下命令仅适用于明确的发布请求：

普通实车内容不需要 `ai_disclosure`，发布正文不得再附加项目预设的“选题、素材初筛及文字由 AI 辅助整理”等通用 AI 辅助披露；定稿、计划生成和旧计划加载都会删除这句话。只有 AI 生成画面、概念动画等需要明确披露的内容才在 `ai_disclosure` 中填写专项说明（例如“AI 生成画面”或“概念车画面”），不能用翻译或拟人化措辞规避披露。`ai_disclosure` 不得包含“素材来自……”或“素材来源……”等素材来源文案；定稿和计划生成会删除披露字段中的来源句，正文或已有发布计划含来源文案会直接阻断。不再需要 `rights_confirmed` 字段。授权不清的内容在筛选阶段直接淘汰，不通过额外人工确认放行。

```bash
# 正常发布：一次调用，默认发布一条，无需另跑 plan 和 preflight
python3 .agents/skills/xcar-xhs/scripts/publish_pipeline.py \
  --out work/xfetch-run

# 本次最多逐条发布三条；每条成功并补齐公开链接后才继续
python3 .agents/skills/xcar-xhs/scripts/publish_pipeline.py \
  --out work/xfetch-run --max-items 3

# 仅用户明确要求草稿或预演时使用；预演不会启动 Chrome
python3 .agents/skills/xcar-xhs/scripts/publish_pipeline.py \
  --out work/xfetch-run --action draft --tweet-id 原帖ID
```

发布入口默认无头 Chrome `Default`，`--headed` 显示隔离浏览器，`--timeout` 默认 180 秒。计划仍写入 `xiaohongshu_publish_plan.json` 和 `plans/<原帖ID>.json`，全量队列仍为 `state/publish_queue.json`。发布和草稿分别登记到 `published_videos.jsonl`、`draft_videos.jsonl`。

统一回执为 `<out>/publication_pipeline_receipt.json`：`complete` 表示本次正常完成，`note_url_pending` 表示发布成功但链接补查未完成，`incomplete` 表示执行失败或结果未确认，`blocked` 包含具体阻塞原因。非成功状态返回非零退出码，禁止盲目循环重试。回执同时列出 24/72 小时已到期效果任务，不编造或自动填零指标。

进程锁同时保护 Chrome Profile 和共享历史。提交日志位于历史文件旁的 `<历史文件主名>.submission.json`，在发送发布点击前原子写入 `submitting`，出现明确成功证据后写入 `confirmed`，历史和队列登记完成后写入 `recorded`。重启可补齐已确认的登记；`submitting` 不会自动重发，必须核验后台后按故障手册处理。不要删除日志来绕过阻断。

公开链接补查只读取后台中精确标题唯一匹配的笔记卡片实际公开链接；缺失、多条匹配或链接已属于其他历史记录时停止，保留 `note_url_pending`，不猜测笔记 ID。后台结构变化时也可能需要手动 `attach-note`。再次运行统一入口会先补查历史缺失链接，然后才选择新内容。

完成后的入选表“标题名”必须输出为 `[标题](<视频绝对路径>)`，让用户直接点击标题预览入选视频；默认标明“已入候选池，未发布”；明确执行过发布任务时再区分已发布与排队状态。文件名和所在目录继续保留。预览是结果交付，不是发布前的等待环节。

## 小红书选题、去重与自动发布

完整规则见 [小红书汽车账号选题与发布手册](references/xiaohongshu-playbook.md)。账号承诺为“每天认识一台国内少见的车”，以关注转化而非单条点赞为目标：60% 稀有车型连续图鉴、25% 改装与机械反差、15% 视觉实验选题（含山路驾驶场景）。按前三秒、知识点、收藏价值、讨论问题和近期多样性评分；标题 1–20 字且含具体细节，正文必须有事实来源和讨论问题，话题 2–6 个。

**自动选题不是自动放行素材**：`topic_planner.py` 只负责栏目、系列、期数和检索式。Agent 必须结合原帖文字、车型信息和抽帧按 100 分模型评分，核验后低于 70 分、不符合当前系列或与近期车型重复的候选不得进入最终入选；最终合格入选不足 3 条时必须继续搜索和递补，不能降低禁选、评分、事实核验或版权标准。`topic_plan.json` 中的 `recent_titles` 和 `recent_brands` 用于辅助避免重复，不能代替车型核验。

**山路驾驶场景已放开**：山路驾驶、山腰/山口道路、Touge、峡谷道路及相应驾驶 POV 均可作为视频主题，按正常评分、画面和版权流程入选。**其他硬性禁选**：海外玩车现场不是栏目；地下车聚、车展、车主故事、非山路的公路驾驶或驾驶 POV 等特色驾驶场景一律不允许，即使高赞也不得入选。真人出镜讲解、人物分屏口播、销售页或网页截图配合讲解同样禁选，车辆清晰或评分达到 70 分也不能放行。不能通过改写标题、正文或话题规避。面向小红书运行 finder 必须加 `--xiaohongshu`（`--auto-topic` 已隐含），明显讲解文字在下载前写入 `topic_skipped`；抽帧后由定稿器移入 `rejected/`，发布器还会最终检查原帖文字、发布文案和画面标注。

**固定历史文件**：`.agents/skills/xcar-xhs/state/published_videos.jsonl`。手工发布过的内容先用 `publish_history.py add --source-id ... --title ... --brand ... --model ... --modification-project ...` 登记。finder 默认在任何下载前按原帖唯一 ID 过滤，命中项写入 manifest 的 `publish_skipped`；定稿和入队时再按品牌、具体车型、改装项目及规范化标题去重。自动模式的实际发布登记会保存栏目、系列、品牌、车型、项目、事实和评分，供后续轮动与复盘；预演和搜索不消耗期数。

### 发布执行与排障入口

只有明确的发布请求才调用上面的 `publish_pipeline.py`。默认候选生产在入池后结束。旧 `xiaohongshu_publisher.py plan` 和 `chrome_xiaohongshu.py` 保留供排障使用，正常任务不要再由 Agent 分别串联。旧 Chrome 入口同样获取执行锁并检查提交日志，不能用 `--only` 绕过队列。

发布直接访问视频上传页 `https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target=video`。脚本复制 Chrome `Default` 的会话文件到临时隔离配置，在同一会话内预检并发布；原 Chrome 配置和标签页不修改，退出清理临时配置，不输出 Cookie 值。

填写后核对完整标题及 `full_text`（正文、讨论问题、专项披露和话题，仅归一化空白），等待上传完成和无障碍树按钮启用后发送真实鼠标事件。只有明确成功提示或 `/publish/success` 才算成功，进入笔记管理页本身不算。`result_url` 与真实公开 `note_url` 分别保存。

独立登录诊断仍可使用已有当前计划执行：

```bash
python3 .agents/skills/xcar-xhs/scripts/chrome_xiaohongshu.py \
  --out work/xfetch-run --headless --preflight-only
```

发生错误、结果不明或链接待补查时，阅读 [小红书 Chrome 发布实测经验与故障手册](references/xiaohongshu-chrome-troubleshooting.md)。只有后台确认实际未发布时才可用 `publish_history.py resolve` 更正为 `failed`；统一入口重启后将对应待确认候选释放回队列。

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

1. 自动选题并保存候选池时，使用 `candidate_pipeline.py --out <新任务目录>`；同目录再次调用即恢复，默认不发布。用户指定主题时仍可使用 finder 手动检索，最后必须补齐同等审核证据并通过纯入池门禁。
2. X 登录态固定为 Chrome Default。登录失败按错误提示处理，不另选浏览器或打印凭据。
3. 脚本按库存与任务预约选择栏目、系列，维护 cursor、已见 ID、下载和核验状态。已发布、排队、在制作或已明确淘汰的原帖在下载前排除；不足三条时按预算增量递补，不重新覆盖旧结果。
4. 收到 `awaiting_review` 后，Agent 查看请求中的视频和抽帧：核验纯车辆/山路画面，排除人物和屏幕讲解；检查时长、系列匹配、前三秒表现、事实来源和授权依据。为五项评分逐项写理由，为批准候选写标题、正文、车型、讨论问题与 2–6 个话题。真实不合格写 rejected 和具体原因，不能为凑数量放行。
5. 按 [审核协议](references/candidate-pipeline.md) 保存 `review.json`，用 `--review-file` 恢复。视频哈希不一致、审核证据缺失会阻断；评分不足、禁选、同品牌/车型/项目重复会淘汰递补。任务预算耗尽可提高预算或添加同系列检索式；接口受限时保存进度并报告，不伪称完成。
6. 至少三条合格后，脚本自动定稿、可恢复重命名并写入候选池；正常完成只产生 queued。报告新增/跳过数量、审核及淘汰情况、`candidate_receipt.json`、队列与视频路径。默认不调用 `publish_pipeline.py`，不把已入池写成已发布。
7. 只有明确发布请求才调用发布入口；公开链接、中断恢复及 24/72 小时效果流程沿用发布手册。既有候选缺新版审核证据时需复核，不得自动生成假的证明。
8. 完成候选任务时按下方入选和淘汰表交付，注明“已入候选池，未发布”；缺口或阻塞时如实说明。队列维护使用 `candidate_pool.py audit/update/withdraw`，更新和撤回必须带当前 revision，不修改 planned、published 或 unconfirmed 条目。

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

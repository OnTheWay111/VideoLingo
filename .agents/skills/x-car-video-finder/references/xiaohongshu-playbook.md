# 小红书汽车账号选题与发布手册

## 一、选题策略：以关注转化为目标

账号定位为 **“每天认识一台国内少见的车”**。目标不是单条播放最高，而是让用户因为“下一期还想看”而关注。任何策略都不能保证涨粉，必须用真实发布数据持续修正。

### 硬性禁选

**“海外玩车现场”不是账号栏目，一律不允许选题。** 地下车聚、车展、车主故事、山路/公路驾驶、驾驶 POV 等特色驾驶场景，即使原帖点赞很高也不得入选、下载或发布。不能通过改写标题、正文或话题规避。面向小红书运行 finder 时必须加 `--xiaohongshu`，发布器会再次检查。

其他直接淘汰项：Tesla 等用户明确排除的品牌；纯人物/屏幕；血腥事故；危险驾驶被包装成教程；无法确认来源的摆拍；未标明的 AI/概念画面；重复车型连续发布；明显水印或授权不清的素材。

### 内容组合：系列优先，实验补充

| 占比 | 栏目 | 适合内容 | 涨粉作用 | 推荐时长 |
|---:|---|---|---|---:|
| 60% | 稀有车型连续图鉴 | 经典 JDM、Kei Car、限量超跑、电影车、概念车 | 建立稳定预期和追更理由 | 15–45 秒 |
| 25% | 改装与机械反差 | 发动机移植、宽体、排气、马力机、定制工艺 | 提供收藏价值和技术辨识度 | 20–60 秒 |
| 15% | 视觉实验选题 | 极小汽车、飞行汽车概念、奇特结构、罕见原型车 | 测试新人群和分享潜力 | 15–45 秒 |

同一系列至少规划 5 期，例如“转子发动机系列”“90 年代 JDM 系列”“不可思议的概念车系列”。每条独立看懂，但统一命名为《罕见车图鉴 01》《罕见车图鉴 02》等；结尾预告下一期的车型类别，不泄露不准确信息。不要连续发布相同车型或只靠豪车价格刺激。

### 自动选题执行规则

无需人工输入主题时运行 `finder.py --auto-topic`。计划器按固定 20 条顺序轮动，每轮包含 12 条稀有车型、5 条改装机械和 3 条视觉实验，避免短期样本不足导致配比漂移；同一栏目内每 5 期切换一个系列。只查看计划时可单独运行 `topic_planner.py`，不会访问 X 或修改历史。

计划器以 `published_videos.jsonl` 中 `published` 和 `unconfirmed` 记录计算下一条位置，并读取自动发布登记保存的 `topic_pillar`、`topic_series_id` 和 `topic_series_episode`。搜索、预演和未登记结果不会消耗期数；旧记录没有这些字段时仍参与去重和 20 条位置计算，但不能参与系列进度计算。

每次计划包含栏目、系列、期数、推荐成片时长、三组英文检索式、禁选排除词、100 分评分维度、70 分阈值以及近期标题和品牌。互动量排序只负责候选预筛；Agent 必须结合原帖和抽帧完成评分、系列相关性及近期车型重复检查，最终入选不足时递补候选。自动选题不授权自动点击发布，版权不清、未标明的 AI/概念画面和其他硬性禁选项仍直接淘汰。

### 100 分选题模型

先执行禁选和版权检查，再评分；优先发布 70 分以上：

- 系列化潜力 25 分：能否扩展成至少 5 期，是否值得用户追更。
- 前 3 秒画面 20 分：立刻看清车型、结构或改装反差，没有长开场。
- 国内稀缺性 15 分：国内少见且辨识度高，不只是“贵”。
- 收藏/分享价值 15 分：有明确知识点、机械细节或视觉反差。
- 评论讨论空间 15 分：能自然提出“你选哪台/下一期看什么”，不制造谣言或引战。
- 原帖互动 10 分：只作同类候选的辅助证据，不能用高点赞替代关注价值。

### 标题、正文与追更设计

标题控制在 **1–20 个字符**，采用“具体车型 + 一个反差/问题”，例如《转子跑车为何停产？》《这台本田只有 660cc》。正文前两行说清车型和一个核心看点，中段补充一个准确细节，结尾用“下一期想看哪类稀有车？”收集选题；话题保持 2–6 个具体标签。禁止夸大、虚假悬念、互粉互赞或关注送礼。

建议每天 1 条，先运行 2–4 周。每 5 条复盘一次，重点看 **每千次播放新增关注 = 新增关注 ÷ 播放量 × 1000**，同时观察 3 秒留存、完播、收藏、分享和主页访问，不只看点赞：

- 高播放、低关注：内容有流量但账号承诺不清，统一系列包装并强化下一期。
- 低播放、高关注：缩小选题范围但保留栏目，优化首帧和标题，继续同系列测试。
- 高收藏、高分享：增加同类机械细节或图鉴内容。
- 连续两期同系列关注转化下降：暂停该系列，换车型年代或机械主题，不用禁选现场内容救场。

可直接使用的检索提示词：

- `找国内少见的经典 JDM 视频，15–45 秒，适合连续图鉴，排除 Tesla、车聚和驾驶场景`
- `找发动机移植、宽体或排气改装视频，不超过 60 秒，排除事故、车主故事和 Tesla`
- `找电影车、概念车或罕见量产车的静态/细节视频，画面要有车，排除车展`
- `找极小汽车、奇特结构或罕见原型车视频，优先有收藏和分享价值的素材`

## 二、固定发布历史与下载前去重

历史文件固定为：

```text
.agents/skills/x-car-video-finder/state/published_videos.jsonl
```

每行一条 JSON，至少保存 `source_id`（X 原帖唯一 ID）、`title`、`source_url`、`platform`、`status`、`recorded_at`。`published` 和 `unconfirmed` 都视为已占用，禁止再次发布。自动选题发布还会保存 `topic_pillar`、`topic_series_id`、`topic_series_episode`、`topic_strategy_version`、`brand` 和 `file_topic`，作为后续轮动及重复检查的输入。可用 `--history-file` 或环境变量 `X_CAR_PUBLISH_HISTORY_FILE` 在测试时指定隔离文件，正式使用不要改路径。

如果以前已经手动发布过，先登记历史；唯一 ID 和标题都提供更可靠。要保证在下载前跳过必须登记 `source_id`；只有标题的历史记录会在生成发布计划时兜底拦截：

```bash
python3 .agents/skills/x-car-video-finder/scripts/publish_history.py add \
  --source-id 2075618551918985363 \
  --title "Apollo Evo 空气动力学细节" \
  --source-url "https://x.com/fosgoodwood/status/2075618551918985363"
```

查询历史：

```bash
python3 .agents/skills/x-car-video-finder/scripts/publish_history.py check \
  --source-id 2075618551918985363
```

`finder.py` 默认读取上述固定文件，在搜索排序后、任何下载和抽帧前按 `source_id` 过滤。命中后写入 `publish_skipped`、`stats.already_published_skipped` 和报告的“已发布跳过（未下载）”，随后从剩余候选中取 Top N。面向小红书时必须再加 `--xiaohongshu`，禁选候选写入 `topic_skipped` 和报告的“小红书禁选跳过（未下载）”。发布器在生成计划时还会检查发布历史、规范化标题及禁选主题。

## 三、生成小红书发布计划

必须先完成 `finalize.py`，确保入选记录已有标题、正文、话题和最终视频路径。默认只生成预演计划，不发布：

```bash
python3 .agents/skills/x-car-video-finder/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run plan --tweet-id 2075618551918985363
```

输出 `work/xfetch-run/xiaohongshu_publish_plan.json`，包含视频绝对路径、标题、正文、话题和 `confirm_publish`。状态为 `skipped` 时不得操作浏览器。标题超过 20 字、正文为空、话题不在 2–6 个或视频缺失时直接报错，不会进入上传环节。

只有用户在当前任务明确要求实际发布时才加：

```bash
python3 .agents/skills/x-car-video-finder/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run plan --tweet-id 2075618551918985363 --confirm-publish
```

此参数只是授权浏览器最后一步点击“发布”，脚本本身不会操作网页或自动登记假成功。

## 四、Kimi Code 内置浏览器自动执行

Agent 读取发布计划后，调用 `mcp__desktop_browser__run` 完成以下动作；不需要 Playwright、Selenium 或读取个人浏览器 Cookie：

1. 打开 `https://creator.xiaohongshu.com/publish/publish?source=official`。若出现短信登录页，展示页面并让用户完成登录，不能代替用户接收或猜测验证码。
2. 在“上传视频”页点击“上传视频”，通过文件对话框上传计划里的 `video_file`。等待编辑页出现，并等到上传完成，不能在上传进行中点击发布。
3. 用元素快照定位标题和正文输入框，填入 `title` 与 `body`。正文末尾逐个输入 `topics`；出现话题候选时选择匹配项，并核对没有串行、重复或误选。
4. 检查视频、标题、正文和话题均与计划一致。若 `confirm_publish=false`，到这里停止，不点击“发布”，也不写历史。
5. 若 `confirm_publish=true` 且用户已明确要求发布，点击“发布”。等待页面出现明确的成功状态或新笔记 URL，不能用“按钮已点击”代替发布成功。

成功后登记：

```bash
python3 .agents/skills/x-car-video-finder/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run record --status published --note-url "实际笔记 URL（如有）"
```

如果点击后超时、页面异常或无法确认是否成功，不要盲目重试；登记 `unconfirmed` 阻止重复发布，并由用户到创作者后台核对：

```bash
python3 .agents/skills/x-car-video-finder/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run record --status unconfirmed
```

`record` 会拒绝预演计划，只有带 `confirm_publish=true` 的计划才能写入历史。回执保存在 `<out>/xiaohongshu_publish_receipt.json`，历史文件才是后续去重的权威依据。

## 五、边界与安全

- 这是 Agent 驱动的浏览器自动化，不是无人值守的定时发布服务；登录、平台验证码和风控仍需用户处理。
- 页面文案或结构变化时重新读取元素快照，不要盲点固定坐标。
- 每次只发布一个计划；批量任务必须逐条生成计划、核验和登记，不能并发点击发布。
- 素材授权、平台规则、原创声明和转载设置由发布者确认。版权不清时不发布。

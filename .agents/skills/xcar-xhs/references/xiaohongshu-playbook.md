# 小红书汽车账号选题与发布手册

## 一、选题策略：以关注转化为目标

账号定位为 **“每天认识一台国内少见的车”**。目标不是单条播放最高，而是让用户因为“下一期还想看”而关注。任何策略都不能保证涨粉，必须用真实发布数据持续修正。

### 山路驾驶场景与其他硬性禁选

**山路驾驶场景是可选题。** 山路驾驶、山腰/山口道路、Touge、峡谷道路及相应驾驶 POV 均可入选、下载和发布，仍须通过正常评分、画面与版权核验。

**“海外玩车现场”不是账号栏目，一律不允许选题。** 地下车聚、车展、车主故事、非山路的公路驾驶或驾驶 POV 等特色驾驶场景，即使原帖点赞很高也不得入选、下载或发布。不能通过改写标题、正文或话题规避。面向小红书运行 finder 时必须加 `--xiaohongshu`，发布器会再次检查。

其他直接淘汰项：纯人物/屏幕；真人出镜讲解、人物分屏口播、销售页或网页截图配合讲解，即使车辆清晰、评分达到 70 分也不例外；血腥事故；危险驾驶被包装成教程；无法确认来源的摆拍；未标明的 AI/概念画面；重复车型连续发布；明显水印或授权不清的素材。只有车辆本身（含山路驾驶画面）、结构或机械细节作为画面主体，且没有讲解员遮挡或讲解分屏，才可按正常评分流程入选。内置策略不因汽车品牌本身禁选；品牌仅用于识别车型、生成检索式和整理发布信息。

### 内容组合：系列优先，实验补充

| 占比 | 栏目 | 适合内容 | 涨粉作用 | 推荐时长 |
|---:|---|---|---|---:|
| 60% | 稀有车型连续图鉴 | 经典 JDM、Kei Car、限量超跑、电影车、概念车 | 建立稳定预期和追更理由 | 15–45 秒 |
| 25% | 改装与机械反差 | 发动机移植、宽体、排气、马力机、定制工艺 | 提供收藏价值和技术辨识度 | 20–60 秒 |
| 15% | 视觉实验选题 | 极小汽车、飞行汽车概念、奇特结构、罕见原型车、山路驾驶场景 | 测试新人群和分享潜力 | 15–45 秒 |

同一系列至少规划 5 期，例如“转子发动机系列”“JDM 稀有车型系列”“不可思议的概念车系列”。每条独立看懂，但统一命名为《罕见车图鉴 01》《罕见车图鉴 02》等；结尾预告下一期的车型类别，不泄露不准确信息。不要连续发布相同车型或只靠豪车价格刺激。

### 自动选题执行规则

无需人工输入主题时运行 `finder.py --auto-topic`。计划器按固定 20 条顺序轮动，每轮包含 12 条稀有车型、5 条改装机械和 3 条视觉实验，避免短期样本不足导致配比漂移；同一栏目内每 5 期切换一个系列。只查看计划时可单独运行 `topic_planner.py`，不会访问 X 或修改历史。

计划器以 `published_videos.jsonl` 中 `published` 和 `unconfirmed` 记录计算下一条位置，并读取自动发布登记保存的 `topic_pillar`、`topic_series_id` 和 `topic_series_episode`。搜索、预演和未登记结果不会消耗期数；旧记录没有这些字段时仍参与去重和 20 条位置计算，但不能参与系列进度计算。

默认互动下载门槛为转发 ≥100 且点赞 ≥1000，任一指标不足都不下载，并写入 `engagement_skipped`、统计和报告。可用 `--min-reposts` 与 `--min-likes` 调整，显式设为 0 可关闭对应限制；达到互动门槛只代表允许下载，仍须通过禁选、抽帧和评分核验。

每次计划包含栏目、系列、期数、推荐成片时长、三组英文检索式、禁选排除词、100 分评分维度、70 分阈值以及近期标题和品牌。互动量排序只负责候选预筛；Agent 必须结合原帖和抽帧完成评分、系列相关性及近期车型重复检查，最终入选不足时递补候选。抽帧核验必须为每条记录写入 `verification.presentation`：纯车辆细节或山路驾驶画面为 `car_only`，真人讲解为 `talking_head_explainer`，网页、销售页或屏幕配合讲解为 `screen_explainer`；后两类直接淘汰，不参与评分放行。定稿器据此自动移入淘汰清单，发布器再次阻断。自动选题不授权自动点击发布，版权不清、未标明的 AI/概念画面和其他硬性禁选项仍直接淘汰。

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

- `找国内少见的经典 JDM 视频，15–45 秒，适合连续图鉴，排除车聚和非山路驾驶场景`
- `找山路、山口或 Touge 驾驶视频，优先车辆主体清晰、过弯画面有冲击力的素材`
- `找发动机移植、宽体或排气改装视频，不超过 60 秒，排除事故和车主故事`
- `找电影车、概念车或罕见量产车的静态/细节视频，画面要有车，排除车展`
- `找极小汽车、奇特结构或罕见原型车视频，优先有收藏和分享价值的素材`

## 二、固定发布历史与下载前去重

历史文件固定为：

```text
.agents/skills/xcar-xhs/state/published_videos.jsonl
```

每行一条 JSON，至少保存 `source_id`（X 原帖唯一 ID）、`title`、`source_url`、`platform`、`status`、`recorded_at`。`published` 和 `unconfirmed` 都视为已占用，禁止再次发布。自动选题发布还会保存 `topic_pillar`、`topic_series_id`、`topic_series_episode`、`topic_strategy_version`、`brand` 和 `file_topic`，作为后续轮动及重复检查的输入。可用 `--history-file` 或环境变量 `X_CAR_PUBLISH_HISTORY_FILE` 在测试时指定隔离文件，正式使用不要改路径。

如果以前已经手动发布过，先登记历史；唯一 ID 和标题都提供更可靠。要保证在下载前跳过必须登记 `source_id`；只有标题的历史记录会在生成发布计划时兜底拦截：

```bash
python3 .agents/skills/xcar-xhs/scripts/publish_history.py add \
  --source-id 2075618551918985363 \
  --title "Apollo Evo 空气动力学细节" \
  --source-url "https://x.com/fosgoodwood/status/2075618551918985363"
```

查询历史：

```bash
python3 .agents/skills/xcar-xhs/scripts/publish_history.py check \
  --source-id 2075618551918985363
```

`finder.py` 默认读取上述固定文件，在搜索排序后、任何下载和抽帧前按 `source_id` 过滤。命中后写入 `publish_skipped`、`stats.already_published_skipped` 和报告的“已发布跳过（未下载）”，随后从剩余候选中取 Top N。面向小红书时必须再加 `--xiaohongshu`，禁选候选写入 `topic_skipped` 和报告的“小红书禁选跳过（未下载）”。发布器在生成计划时还会检查发布历史、规范化标题及禁选主题。

## 三、生成小红书发布计划

必须先完成 `finalize.py`，确保入选记录已有标题、正文、话题和最终视频路径。默认只生成预演计划，不发布：

```bash
python3 .agents/skills/xcar-xhs/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run plan --tweet-id 2075618551918985363
```

输出 `work/xfetch-run/xiaohongshu_publish_plan.json`，包含视频绝对路径、标题、正文、话题和 `action`。状态为 `skipped` 时不得操作浏览器。标题超过 20 字、正文为空、话题不在 2–6 个或视频缺失时直接报错，不会进入上传环节。

账号操作前先让用户预览入选表中的视频标题链接，再明确选择 **自动发布** 或 **自动保存草稿**，未选择时只生成本地计划。`publish.json` 每条必须包含 `ai_disclosure` 和 `rights_confirmed: true`，缺项不能进入发布或草稿动作。AI 生成或概念动画单独标明，授权不清不操作。

```bash
# 自动发布
python3 .agents/skills/xcar-xhs/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run plan --tweet-id 2075618551918985363 --action publish

# 自动保存草稿
python3 .agents/skills/xcar-xhs/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run plan --tweet-id 2075618551918985363 --action draft
```

发布成功写入 `state/published_videos.jsonl`，草稿成功写入独立的 `state/draft_videos.jsonl`。正式发布在两条成功记录之间使用 `SystemRandom` 随机等待 60–600 秒；最后一条、草稿、跳过和失败均不等待。该间隔仅用于降低突发频率，不是规避审核的手段。

## 四、Chrome Default 浏览器自动执行

小红书登录态固定复用本机 Chrome `Default`。Agent 不再使用内置浏览器，也不再询问浏览器或 Profile；发布或保存草稿仍需用户事先选择。

把各条确认计划保存为 `<out>/plans_confirmed/<原帖ID>.json`，然后先验证会话，再逐条执行：

```bash
python3 .agents/skills/xcar-xhs/scripts/chrome_xiaohongshu.py \
  --out work/xfetch-run --headless --preflight-only

python3 .agents/skills/xcar-xhs/scripts/chrome_xiaohongshu.py \
  --out work/xfetch-run --headless --timeout 90
```

该脚本在 macOS 上只复制 Chrome `Default` 的 `Cookies`、`Preferences` 和 `Local State` 到临时隔离配置，使用 `websocket-client` 通过 CDP 操作独立 Chrome；不会修改原浏览器配置或标签页，初始化或发布异常时也清理临时配置，不输出 Cookie 值。`--browser-profile` 默认固定为 `Default`，`--only` 可限制为一条，去掉 `--headless` 可显示浏览器。

脚本自动完成以下动作，不需要 Playwright 或 Selenium：

1. 直接打开 `https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target=video`，不经过创作者首页或菜单跳转。若出现短信登录页，让用户在本机 Chrome `Default` 完成登录，不能代替用户接收或猜测验证码。
2. 上传计划里的 `video_file`，等待编辑页出现并确认上传完成，不能在上传进行中点击发布。
3. 填入 `title` 与 `body`，逐个添加 `topics`，再复读标题、正文和话题，核对没有串行、重复或误选。
4. 若 `confirm_publish=false`，在点击“发布”前停止，不写历史。
5. 若 `confirm_publish=true` 且用户已明确要求发布，通过无障碍树等待发布按钮启用，再发送真实鼠标事件。只有页面出现明确成功状态才登记 `published`，不能用“按钮已点击”代替成功。

脚本使用正常 Chrome 标识避免降级页面，并保存发布前、成功或异常截图到 `<out>/browser/`。批量按 manifest 入选顺序逐条执行，任何待确认或错误都会停止后续项。点击后结果不明时登记 `unconfirmed` 阻止重复发布，由用户到创作者后台核对，不得盲目重试。`result_url` 是发布结果页，`note_url` 只能是公开笔记地址，二者不得混用。

所有实测问题、根因、异常处理和发布前检查清单见 [小红书 Chrome 发布实测经验与故障手册](xiaohongshu-chrome-troubleshooting.md)。修改 Chrome 发布逻辑后必须按该手册补回归，不要只验证“脚本能运行”。

如果点击后结果不确定，历史先记 `unconfirmed`。必须到创作者后台确认实际未发布后，才能用以下命令更正为 `failed` 并重试；不得仅因超时就强行解锁：

```bash
python3 .agents/skills/xcar-xhs/scripts/publish_history.py resolve \
  --source-id 2075618551918985363 \
  --title "实际未发布的标题" \
  --reason "后台确认未发布的核验依据"
```

## 五、边界与安全

- 这是 Agent 驱动的浏览器自动化，不是无人值守的定时发布服务；登录、平台验证码和风控仍需用户处理。
- 页面文案或结构变化时重新读取元素快照，不要盲点固定坐标。
- 每次只发布一个计划；批量任务必须逐条生成计划、核验和登记，不能并发点击发布。
- 素材授权、平台规则、原创声明和转载设置由发布者确认。版权不清时不发布。

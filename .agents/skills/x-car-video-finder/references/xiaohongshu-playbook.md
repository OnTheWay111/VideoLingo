# 小红书汽车账号选题与发布手册

## 一、选题策略

建议把账号定位为 **“国内少见的车与海外汽车文化”**，不要每天混发豪车、事故、搞笑和新闻。固定三个内容栏目，让用户形成稳定预期：

| 栏目 | 适合内容 | 推荐时长 | 标题切口 |
|---|---|---:|---|
| 稀有车型图鉴 | 经典 JDM、限量超跑、Kei Car、概念车、电影车 | 15–45 秒 | “国内很难见到的…” |
| 改装与机械细节 | 宽体、发动机移植、排气声浪、马力机、定制工艺 | 20–60 秒 | 具体车型 + 一个改装看点 |
| 海外玩车现场 | 地下车聚、车展、山路驾驶、车主故事 | 30–120 秒 | 地点 + 人物/事件 + 现场感 |

候选按 100 分评估，优先发布 70 分以上：

- 国内稀缺性 30 分：国内少见、有明确车型或文化差异，不只是“贵”。
- 前 3 秒画面 25 分：第一帧就能看懂车或冲突，无长时间人物讲话、电脑屏幕。
- 原帖互动 20 分：在本次同类候选中比较点赞、评论、转发和播放，不跨主题绝对比较。
- 中文表达空间 15 分：能准确说清车型、事件和看点，而不是只翻译原帖。
- 账号栏目契合 10 分：能归入固定栏目，并与最近 5 条形成车型或场景差异。

以下候选直接淘汰：Tesla 等用户明确排除的品牌；纯人物/屏幕；血腥事故；危险驾驶被包装成教程；无法确认来源的摆拍；只有 AI 画面却未标明 AI/概念；重复车型连续发布；带其他平台明显水印或授权不清的素材。

标题控制在 **1–20 个字符**，正文回答“什么车、发生什么、为什么值得看”，话题使用 2–6 个具体标签。不要堆砌“豪车、炸裂、震惊”等空泛词。建议每天 1 条，先连续更新 2–4 周，再按小红书完播率、收藏率和关注转化调整三个栏目的占比。

可直接使用的检索提示词：

- `找国内少见的经典 JDM 视频，15–60 秒，按点赞排序，排除 Tesla`
- `找海外地下车聚或汽车文化视频，不超过 90 秒，优先高评论`
- `找发动机移植、宽体或排气改装视频，不超过 60 秒，排除事故和 Tesla`
- `找电影车、概念车或罕见量产车视频，画面要有实车，按点赞排序`

## 二、固定发布历史与下载前去重

历史文件固定为：

```text
.agents/skills/x-car-video-finder/state/published_videos.jsonl
```

每行一条 JSON，至少保存 `source_id`（X 原帖唯一 ID）、`title`、`source_url`、`platform`、`status`、`recorded_at`。`published` 和 `unconfirmed` 都视为已占用，禁止再次发布。可用 `--history-file` 或环境变量 `X_CAR_PUBLISH_HISTORY_FILE` 在测试时指定隔离文件，正式使用不要改路径。

如果以前已经手动发布过，先登记历史；唯一 ID 和标题都提供更可靠。要保证在下载前跳过必须登记 `source_id`；只有标题的历史记录会在生成发布计划时兜底拦截：

```bash
python3 .agents/skills/x-car-video-finder/scripts/publish_history.py add \
  --source-id 2075618551918985363 \
  --title "Apollo Evo 超跑亮相 Goodwood 2026" \
  --source-url "https://x.com/fosgoodwood/status/2075618551918985363"
```

查询历史：

```bash
python3 .agents/skills/x-car-video-finder/scripts/publish_history.py check \
  --source-id 2075618551918985363
```

`finder.py` 默认读取上述固定文件，在搜索排序后、任何下载和抽帧前按 `source_id` 过滤。命中后写入 `publish_skipped`、`stats.already_published_skipped` 和报告的“已发布跳过（未下载）”，随后从剩余候选中取 Top N。发布器在生成计划时还会按唯一 ID 和规范化标题二次检查。

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

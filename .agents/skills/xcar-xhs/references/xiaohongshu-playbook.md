# 小红书汽车账号选题、逐条发布与效果手册

## 一、选题策略：以关注转化为目标

账号定位为 **“每天认识一台国内少见的车”**。目标不是单条播放或点赞最高，而是让用户获得可收藏的知识，并因为“下一期还想看”而关注。任何策略都不能保证涨粉，必须用 24/72 小时真实数据持续修正。

### 山路驾驶场景与其他硬性禁选

**山路驾驶场景是可选题。** 山路、山腰/山口道路、Touge、峡谷道路及相应驾驶 POV 均可入选、下载和发布，仍须通过正常评分、画面与版权核验。

**“海外玩车现场”不是账号栏目。** 地下车聚、车展、车主故事、非山路的公路驾驶或驾驶 POV 等特色驾驶场景，即使原帖点赞很高也不得入选、下载或发布。不能通过改写标题、正文或话题规避。面向小红书运行 finder 时必须加 `--xiaohongshu`，发布器会再次检查。

其他直接淘汰项：纯人物/屏幕；真人出镜讲解、人物分屏口播、销售页或网页截图配合讲解；血腥事故；危险驾驶教程；无法确认来源的摆拍；未标明的 AI/概念画面；重复内容；明显水印或授权不清的素材。只有车辆本身、结构、机械细节或山路驾驶画面作为主体，且没有讲解员遮挡或讲解分屏，才可进入评分。

### 内容组合

| 占比 | 栏目 | 适合内容 | 推荐时长 |
|---:|---|---|---|
| 60% | 稀有车型连续图鉴 | 经典 JDM、Kei Car、限量超跑、电影车、概念车 | 15–45 秒 |
| 25% | 改装与机械反差 | 发动机移植、宽体、排气、马力机、定制工艺 | 20–60 秒 |
| 15% | 视觉实验选题 | 极小汽车、奇特结构、罕见原型车、山路驾驶 | 15–45 秒 |

无需人工输入主题时运行 `finder.py --auto-topic`。计划器按固定 20 条顺序轮动，同一栏目内每 5 期切换一个系列。计划器只负责栏目、系列、期数和检索式，不自动放行素材；发布队列仍会避免相邻笔记出现同系列内容。

## 二、三条核验与逐条发布解耦

每次正式任务必须至少有 **3 条完整通过评分、时长、画面、版权和去重核验的素材**。不足时继续翻页、补充同义检索式、扩大同栏目搜索并递补，不能降低标准凑数。

3 条是候选池数量，不是每日发布数量上限：

- `finalize.py` 按总分降序整理本批入选。
- `xiaohongshu_publisher.py plan` 把本批全部入选加入 `state/publish_queue.json`，生成下一条发布计划。
- 当前最高分候选若与上一条同品牌、同改装项目或同系列，则保留在队列，改选下一条不相似内容。
- 当前发布登记完成并补齐公开笔记地址后，可再次运行计划命令，旧队列与新批次一起参与评分选择，不设每日一条限制。
- 每次只执行当前计划，不扫描 `plans/` 批量发布，也不使用两条之间 1–10 分钟的随机等待。
- 上传或填写失败且尚未点击发布时，候选释放回队列；点击后结果不明记为 `unconfirmed`，必须先核验后台。

固定队列文件：

```text
.agents/skills/xcar-xhs/state/publish_queue.json
```

## 三、100 分选题模型

先执行禁选和版权检查，再逐项评分；总分必须达到 70 分：

| 维度 | 分值 | 核验重点 |
|---|---:|---|
| `first_three_seconds` | 25 | 前三秒是否出现运动机构、机械动作或明显反差，没有长开场 |
| `knowledge_point` | 25 | 是否有一个能讲清楚且可核验的知识点 |
| `save_value` | 20 | 是否值得收藏，能否作为以后查车型、结构或改装的资料 |
| `discussion_question` | 15 | 是否有一个具体、不引战的讨论问题 |
| `recent_diversity` | 15 | 与近期车型、品牌、项目、画面和知识点是否有差异 |

X 点赞、评论、转发和播放只作弱参考，不占分，不能作为主要放行依据。finder 默认 `--min-likes 0 --min-reposts 0`；只有用户显式设置正数时才启用下载过滤。

每条入选必须在 `publish.json` 填写完整 `score_breakdown`，五项之和必须等于总分，不能只写一个总分。`verification.presentation` 仍须标记为 `car_only`、`talking_head_explainer` 或 `screen_explainer`，后两类直接淘汰。

## 四、车型级去重

原帖 `source_id` 和规范化标题继续永久去重，另增加内容身份：

- `brand`：车辆原品牌，不是改装厂；中英文品牌名归一，例如 `迈凯伦` 与 `McLaren`。
- `model`：具体车型，不能只填“跑车”“JDM”。
- `modification_project`：具体改装项目；原厂车留空。转子、宽体、发动机移植等同义项目归一。

执行规则：

1. 同车型 30 天内不重复，按已发布或待确认记录的发布时间计算。
2. 同品牌、同改装项目 30 天内不重复，即使原帖 ID、车型年款或标题不同也能识别。例如“迈凯伦转子”和“McLaren rotary swap”属于重复项目。
3. 同品牌每批最多一条，保留该品牌评分最高者，其余淘汰并继续递补到 3 条。
4. 队列中的同车型或同品牌同项目也不重复，避免不同原帖换皮后排队再发。
5. 每次选择额外避开与上一条同品牌、同项目或同系列的内容。

finder 在下载前按原帖 ID 过滤；`finalize.py` 在定稿前执行结构化去重；入队时再次检查，发布器最终复核。

手工登记旧发布内容时应补齐身份字段：

```bash
python3 .agents/skills/xcar-xhs/scripts/publish_history.py add \
  --source-id 2075618551918985363 \
  --title "BMW Z1 的车门去哪了？" \
  --source-url "https://x.com/example/status/2075618551918985363" \
  --brand BMW --model Z1
```

历史固定为 `.agents/skills/xcar-xhs/state/published_videos.jsonl`，测试可用 `--history-file` 或 `X_CAR_PUBLISH_HISTORY_FILE` 隔离。

## 五、标题、正文与发布计划

标题控制在 **1–20 个字符**，采用“具体车型 + 一个可核验细节/问题”，减少“有多……”空泛模板。例如：

- BMW Z1 的车门去哪了？
- 660cc 的 AZ-1 凭什么好玩？
- 这台丰田为什么塞着法拉利发动机？
- CENTODIECI 继承了 EB110 哪三处？

每条入选必须填写：

- `title_detail`：原样出现在标题中的具体细节。
- `verifiable_fact`：原样出现在正文中的可核验事实。
- `fact_source`：实际核验过的 HTTP(S) 来源，不能编造。
- `discussion_question`：一个明确问题，发布时加入正文末尾。
- `brand`、`model`、`modification_project`、`score_breakdown`、2–6 个具体 `topics` 和 `ai_disclosure`。

定稿：

```bash
python3 .agents/skills/xcar-xhs/scripts/finalize.py \
  --out work/xfetch-run --publish-file work/xfetch-run/publish.json
```

全部入选入队并生成下一条发布计划：

```bash
python3 .agents/skills/xcar-xhs/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run plan
```

输出 `work/xfetch-run/xiaohongshu_publish_plan.json` 和 `work/xfetch-run/plans/<原帖ID>.json`。Chrome 只读取当前发布计划，不扫描目录里的其他计划。标题超长、事实未写入正文、来源缺失、评分不完整、话题数量不符或视频缺失都会直接阻断。

只有用户明确要求时才使用 `--action preview` 或 `--action draft`，此时必须传 `--tweet-id`。正式发布默认 `action=publish`，不需要二次确认；AI 披露仍不可省略。`ai_disclosure` 不得包含“素材来自……”或“素材来源……”等素材来源文案；来源句会从披露字段中删除，正文或已有计划含来源文案时禁止发布。

## 六、Chrome Default 自动执行

小红书登录态固定复用本机 Chrome `Default`，不询问浏览器或 Profile：

```bash
python3 .agents/skills/xcar-xhs/scripts/chrome_xiaohongshu.py \
  --out work/xfetch-run --headless --preflight-only

python3 .agents/skills/xcar-xhs/scripts/chrome_xiaohongshu.py \
  --out work/xfetch-run --headless --timeout 90
```

脚本只复制 `Cookies`、`Preferences` 和 `Local State` 到临时隔离配置，通过 CDP 操作独立 Chrome，不修改原浏览器配置或标签页，不输出 Cookie 值。去掉 `--headless` 可显示隔离浏览器；`--only` 只能核对当前发布计划，不能绕过队列选其他素材。

执行流程：直接打开视频发布页，上传并等待转码完成，填入标题正文和话题，复读核验，等待发布按钮启用后发送真实鼠标事件。只有页面出现明确成功状态才登记 `published`。任何待确认或错误都停止操作，不能并发点击。

`result_url` 是发布结果页，`note_url` 只能是 `/explore/` 或 `/discovery/item/` 的真实公开笔记地址。成功页没有公开地址时，脚本会尝试读取页面内的公开笔记链接；仍拿不到则保存 `note_url_pending=true`，后续发布前必须补齐。

实测问题与异常处理见 [Chrome 发布故障手册](xiaohongshu-chrome-troubleshooting.md)。

## 七、公开笔记与 24/72 小时效果

补登记真实公开地址，不改变原发布时间：

```bash
python3 .agents/skills/xcar-xhs/scripts/xiaohongshu_publisher.py \
  --out work/xfetch-run attach-note \
  --source-id 2075618551918985363 \
  --note-url "https://www.xiaohongshu.com/explore/真实笔记ID"
```

查看到期任务：

```bash
python3 .agents/skills/xcar-xhs/scripts/note_metrics.py due
```

到期后从公开笔记和创作者后台读取真实数据，分别记录 24 和 72 小时快照：

```bash
python3 .agents/skills/xcar-xhs/scripts/note_metrics.py record \
  --source-id 2075618551918985363 --checkpoint 24 \
  --views 10000 --likes 500 --favorites 120 \
  --comments 30 --shares 20 --new-followers 15
```

72 小时改用 `--checkpoint 72`，不会覆盖 24 小时数据。播放、点赞、收藏、评论、分享、新增关注都必须是非负整数；未知值不能填 0 代替，未到期不能提前登记。数据固定追加到 `.agents/skills/xcar-xhs/state/note_metrics.jsonl`。

核心复盘指标：

- **收藏率 = 收藏 ÷ 播放量**，同时保存百分比。
- **每千次播放新增关注 = 新增关注 ÷ 播放量 × 1000**。
- 播放为 0 时比率为 `null`，不制造无穷值。
- 高播放、低关注：强化具体知识点和账号承诺。
- 低播放、高关注：优化首帧和标题，不急于换栏目。
- 高收藏、高分享：增加不同车型或项目的机械细节内容。
- 连续两期同系列关注转化下降：暂停该系列，换车型年代或机械主题。

## 八、边界与安全

- 这是 Agent 驱动的浏览器自动化，不是无人值守定时发布服务；登录、验证码和风控仍需用户处理。
- 页面文案或结构变化时重新读取元素快照，不要盲点固定坐标。
- 素材授权、平台规则、原创声明和转载设置必须符合条件；授权不清直接淘汰。
- 效果数据必须来自真实公开笔记或创作者后台，不能估算、编造或把结果页当公开地址。

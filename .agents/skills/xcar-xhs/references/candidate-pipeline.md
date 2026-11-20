# 候选流水线与审核协议

## 入口与职责

`candidate_pipeline.py --out <新目录>` 自动选题、增量搜索、下载抽帧；需要内容判断时输出 `review_request.json` 并返回 `awaiting_review`。Agent 完成逐条核验及文案，保存独立 `review.json`，用 `--review-file` 恢复。到至少三条合格后自动定稿并全部入候选池；从不调用发布浏览器，也不创建发布计划。

审核是 Agent 的工作，不是新增人工授权环节。不校验可追溯转载许可，不要求 rights_evidence 或 rights_source，不因缺少许可字段淘汰。仍须真实核查画面、事实与评分，不得用固定分数或虚构来源填写证据。脚本检查证据结构和一致性，不判断证据真伪。

正赛趣事的 editorial 需填 race_event（年份和分站）、race_phase（race / sprint_race / timed_rally_stage）、race_source_url（直接佐证此事的赛事、车队或转播报道）及 incident_evidence（时间点、连续动作、自然笑点及与来源相符的细节）。只有真实比赛中自发发生的片段可入池。

## 审核文件

复制本次 `review_request.json` 为 `review.json`，保留 `job_id`、候选 ID 与 `video_sha256`。每条填写：

- `decision`: `approved`、`rejected` 或暂未完成的 `pending`。淘汰必须有 `reason`。
- `publish`: 按 [Skill 文案规则](../SKILL.md#标题命名推荐话题与发布定稿必做)填写。`title` 为 直接拼接的 `品牌车型主题`（如 `奔驰CLK-LM爬坡发车`），`body` 对应最终 `recommendation`，讨论另存 `discussion_question`。`first_year` 是核实的车型首发年份，未知填 `null`；品牌任务自动沿用名录。`topics` 先写独立的英文、中文品牌标签，再写车型和素材主题。
- `verification.presentation`: 合格只能为 `car_only`；人物/屏幕讲解淘汰。
- `verification.reviewer`、`reviewed_at`: 实际审核者标识及带时区 ISO 8601 时间。
- `visual_evidence`、`first_three_seconds_evidence`: 对画面及前三秒的具体观察。默认五帧覆盖约 0.5、1.5、2.5 秒和中后段，必要时观看视频确认运动、声音和画面完整性。
- `fact_evidence` 写来源怎样支持正文事实，`series_evidence` 回答请求内该系列的 `series_question`。新任务的拉力公路版还需在 `verification.editorial.road_version_detail` 描述可见的量产版本辨认细节，并用事实来源核对。公路版也参加过赛事、赛车存在公路衍生型，都不能单独证明镜头里的版本。
- `score_reasons`: 必须包含 `first_three_seconds`、`knowledge_point`、`save_value`、`discussion_question`、`recent_diversity` 五项具体理由。
- 新建候选任务生成 `verification.editorial`。`opening_action_at_sec` 写标题关键动作实际可见的时间点（0–3 秒），`first_three_seconds_evidence` 描述部件如何变化；没有可见动作填 `null`。汽车机关图鉴发现此值为空时淘汰并递补。`discussion_basis` 写正文问题对应的场景、设计取舍或车友经验，遇到泛偏好题先改写并重评。`follow_up_topic` 写同系列下一期可检索的具体车型、部件和问题，例如「丰田 Sera 与 BMW Z1 的开门方式，窄车位各需要多少空间？」。下一期线索用于选题，留在审核记录里。

脚本验证媒体哈希，删除正文中重复的整句讨论问题，再绑定文案、已填写的首发年份和选材证据。正文、选材证据或视频变化必须重审；单纯文件改名不会使审核失效。没有有效前三秒/中后段抽帧或证据不完整时阻断，不能直接入池。低分和明显禁选会被淘汰，重复项在定稿前再次核查。同批合格素材先按前三秒可见动作排序，再按讨论分和总分。已开始的旧任务可继续使用原审核格式。

## 下载前的车型与机构去重

「汽车机关图鉴」按每条素材实际所属系列检查原帖。iX Flow 等电子车身明确展示变色时，证据写入 `color_only_skipped`。支持由宝马引出的 Dee 简称（含「一辆叫 Dee 的变色车」）、英文及中葡西阿语的车身变色描述（含葡语「电动车变色」）、电子墨水调色和车身颜色可配置。否定词直接修饰变色时保留画面审核，「不仅是黑白」或原帖其他话题的否定不影响已明确的变色描述。Dee 直接搭配 E Ink 指向该车型的电子墨水外皮；只给车型名、仅设问、变色归属不清或同时描述机械动作时继续抽帧。GINA 首条通过织物机盖开口审核后，本批后续明确以同车可动蒙皮为唯一主题的转帖计入 `recent_mechanism_skipped`；仅有车型名、介绍其他部件时继续核验。

按最近一次有效发布状态或当前 queued / planned 条目的入池时间回看 30 天，递补时也检查本批已审核通过条目的审核时间。原帖正文须同时命中具体车型（含可靠别名）和近期标题、文件主题或已核验事实中相同的机构。分句可以凭单一车型主题和明确归属匹配，也可以由后句把前句的同一部件明确归给车型。命中时另存 `match_scope=cross_sentence` 和从车型到机构（或反向）的连续证据句。主体切换、否定、假设或多车型归属不清时保留画面审核。中英机构词表在 `scripts/mechanism_duplicates.py`。短车型代号结合品牌识别；含糊、仅有标签、比较其他车型或超出词表的描述继续走原有审核。命中项在本任务记为 skipped，不占下载次数，不写永久淘汰缓存。回执给出命中数量，manifest 的 `recent_mechanism_skipped` 逐条记录新旧原帖 ID、车型、机构、匹配词和历史时间。

## 搜索请求顺序

新任务按相同搜索模式和门槛，读取最近 24 小时内最多 12 个已完成或已用完预算的同系列批次。按每个有观测结果的搜索页实际新增的唯一合格原帖数排序。已入池任务采用入池回执中的 ID；其他已结算任务采用去重后仍为 approved 的 ID，排除撤回、失效和待复核素材。仅待裁定的页不纳入低产统计。连续两次有效观测没有合格产出的检索式降到最后探测，未试过的组合仍有机会；24 小时之外的记录不参与排序。只有首页首次发现的素材经过画面、事实、评分和去重审核后仍为 `approved`，才请求第二页。首页只返回旧帖，或下载后都因转帖、无关 AI 视频等原因淘汰，均停止续页。首页仍待审核时，先完成审核交接。第三页起还要求上一页有首次发现且通过审核的素材，并让给新的首页。检查点缺少首页来源时继续尝试其他首页。网络失败不作为零产出。检查点保存排序口径版本、检索依据和每页首次发现的候选 ID。旧任务首次恢复时重算排序依据，保留原游标。旧页缺少归因时按留存原始页恢复。每个原帖按实际系列初筛，明确冲突记入 `series_mismatch_skipped`；另一系列再次命中时仍可独立评估。证据不足保留待审，同等系列匹配度再按 `--sort` 排序。

历史对照可运行 `python3 .agents/skills/xcar-xhs/scripts/replay_selection.py --date 2026-11-18 --first-pages 3`。按上海自然日选取创建的任务，在同一组已观测首页与相同预算下，比较旧排序和仅使用任务开始前历史的新排序。输出已知合格原帖数、可前置跳过的历史下载记录及其中已知合格数。未试过的页和未审核的原帖没有真实标签，回放不能估计线上完整入池率。

「汽车机关图鉴」的宽泛补充词应先带上具体车型或机构，例如 `("Mazda MX-5" OR "Nissan 300ZX") ("跳灯" OR "翻灯") filter:videos`、`"Honda Ridgeline" "双向尾门" filter:videos`。统一入口给已知的裸词（含「跳灯 翻灯」「尾门 侧开」）自动追加车型组，保留互动、媒体及排除条件。已经检索过的宽词保留原有检查点和素材；新查询从首页开始。

## 品牌车型扩展策略

先看品牌档案和博物馆的实车记录，再按下表补查不同类别。优先用厂商原始资料、赛事或认证档案核对完整名称、首次公布年份和实车依据。资料确认后再按完整车型名或已核实的专属别名搜索 X。原帖热度用于素材筛选，车型是否罕见看生产、用途、市场或设计证据。

| 类别 | 值得入选的条件 |
|---|---|
| 孤品 `one_off` | 有品牌制造或委托打造的实物记录、正式名称或项目代号，并有独特车身或机构 |
| 原型车 `prototype` | 有实体原型，能核对其验证的工程方案、实验用途或量产前开发阶段 |
| 概念车 `concept_car` | 有品牌正式名称与实体展示记录，能辨认它展示的车身、座舱或机构。静态实体展示车也可纳入名录 |
| 混动概念车 `hybrid_concept` | 有实体车，厂商资料明确混动是该车的设计重点。注明混动方案的实际验证状态 |
| 竞赛车 `race_car`、认证公路版 `homologation` | 能区分赛事专用原车、同源公路版和后续复刻，能核对赛车型号或认证关系 |
| 限量版 `limited_edition`、特别版 `special_edition` | 能核实产量、地区、用途或特殊车身等稀有依据，并找到足以识别版本的细节 |
| 跑车 `sports_car` | 优先低产量、罕见结构、市场受限或具有独立历史看点的车型 |
| 特殊结构量产车 `unusual_structure` | 正式量产，车身、开门机构、转向或动力布局有可辨认的独特设计。雷诺 Avantime 可找无框车门开启的近景。 [雷诺博物馆](https://theoriginals.renault.com/avantime) |
| 地区专供车型 `regional_exclusive` | 能核实销售市场，并能从实车辨认车型或版本的独立设计特征。同一款车的地区名称合并为别名。日本专供的马自达 Road Pacer AP 可搜车型全名加机舱或启动画面。 [马自达车型史](https://www.mazda.com/en/about/history/1920-1979/)、[马自达百年档案](https://www2.mazda.com/en/100th/virtual_museum/history/history004.html) |
| 原厂专用车 `factory_special_purpose` | 品牌为救援、勘探、机场作业等用途打造，有正式型号，画面可展示专用结构。丰田 Mega Cruiser 可找能看到四轮转向的画面。 [丰田发布资料](https://global.toyota/en/detail/7882359) |

优先检索稀有事实明确、实车特征好辨认、已有真实影像线索的车型。同品牌、同车系均可纳入所有符合本次范围的独立车型和版本。普通换色、年款、常规配置和地区译名归入对应车型。视频须能确认对应实车，民间复刻、普通版剪辑、游戏、渲染或概念海报均淘汰。厂商资料能证明独立版本时，在 `models` 使用完整正式名建一行，并把专属别名只放在该行。

扩展车型可在 `brand_catalog.json` 的 `models` 条目内增加 `expansion`。同一车型符合多类时选最能说明其稀有价值的一类。来源使用该车型条目的 `source`。下面字段值仅为示例。

```json
{
  "model": "车型正式名 Hybrid Prototype",
  "first_year": 1995,
  "source": "https://example.com/model-archive",
  "expansion": {
    "category": "hybrid_concept",
    "reason": "品牌档案记载该混动实验车只造了一台",
    "visual_cue": "右后翼子板的独立充电盖和实验车专用后窗进风口"
  }
}
```

用品牌名录和逐车型命令执行同一套搜索流程。可给已有车型补充 `expansion`，继续原任务目录。每款沿用 3 页、四项总互动超过 100 和原有画面、评分、去重门槛。原帖同时提及已入池的基础款和目标专名时，保留版本核验机会。审核在 `series_evidence` 记录画面辨认点及来源，介绍产量、动力或赛事关系时核对对应资料。交付列出具体版本、类别、稀有依据、已入池或缺口原因及本地视频。

## 状态、恢复与预算

- `candidate_state.json`: 本任务状态、检索 cursor、已见 ID、下载/抽帧/审核状态与累计预算消耗的本地镜像；全局任务登记中的原子 checkpoint 是恢复依据，不手工编辑镜像绕过状态。
- `topic_plan.json`: 固定的本任务栏目与系列，不含预占的发布期数。
- `review_request.json`: 当前待审核条目；恢复时重新生成，已完成的审核不会反复要求处理。不要把此文件当成已审核结果。
- `candidate_receipt.json`: `complete`、`awaiting_review`、`budget_exhausted`、`exhausted`、`needs_candidates`、`blocked`、`cancelled`，始终 `published=0`。
- 历史文件旁的 `candidate_jobs.json`: 跨批次任务预约、在制作原帖和明确淘汰缓存。`topic_plan.json` 的 `inventory_counts` 分列可发布、保留、实际在制、启动预占。尚无候选的 searching 任务从创建起预占最多 10 分钟。正常在制按已下载、待审及合格原帖的实际系列计数，已入池或已发布的 ID 不重复计数。阻塞或耗尽只保留已通过原帖。`--cancel` 释放预约并保留素材文件。
- `finalize_transaction.json`: 重命名事务和目标 manifest。中断后先恢复文件与清单，再入池；入池成功但回执写入失败时按原帖 ID 幂等恢复。

连续两页初筛后无新增可下载原帖，会暂停该系列。每系列独立累计，有新候选即清零；搜索异常保留原游标与空搜计数。自动任务可在原任务内切到同栏目后备系列；显式指定系列时可用新的同系列查询恢复。回执的 empty_series_pages 为当前连续空页数，model_duplicates_skipped 为下载前被明确车型身份拦截的原帖数，命中依据在对应条目的 model_duplicate 中。

常规任务默认累计最多 12 个成功返回的搜索页（含有效空页）、15 次下载尝试。搜索失败时回执为 `blocked`，保留错误原因、原查询和游标，不扣页预算；恢复后以相同参数运行同一任务目录即可重试。每次调用执行时间预算 600 秒，在操作边界检查并限制网络/下载子操作超时，登录初始化等底层操作也可能占用额外时间。审核等待不计入下一次调用预算。定稿时因并发去重出现新缺口会返回 `needs_candidates`，再运行同目录自动递补。预算不足时显式提高 `--max-pages` / `--max-downloads`，继续同目录；不要换目录重复生产。目标数和检索筛选条件在任务创建时固定，恢复时沿用；增加预算不会清空已经消耗的计数。`--query` 可增加同系列检索词；仍不足三条只报告缺口，不能降低标准。

品牌名录任务的 `--max-pages` 默认每车型 3 页，初次搜索、追加检索式和审核续跑均累计。优先不同检索式的首页，单个检索式连续两页无新增可下载原帖时停止沿其游标翻页。处理完已搜到的候选后，若达到页上限仍没有合格素材，回执返回 `status=budget_exhausted`、`budget=search_pages`，并记录 `pages_used` 和 `page_limit`。提前用尽检索式和游标时返回 `exhausted`。品牌覆盖表保留这些状态及恢复目录，Agent 继续下一款。单独回补时可在原目录追加 `--query` 并显式提高累计 `--max-pages`；每次回补续跑沿用该参数。

`--retry-failed` 只重试临时下载/抽帧错误；已下载素材抽帧失败不会再次下载。明确内容不合格的原帖会进入跨任务淘汰缓存，不能通过重启绕过。新目录首次拒绝覆盖已有 manifest；候选任务目录不与旧 finder 输出混用。

## 独立候选池维护

```bash
# 已定稿批次仅入池，不选择 planned，不检查前一条发布是否待确认
python3 .agents/skills/xcar-xhs/scripts/candidate_pool.py enqueue --out <out>

# 视频失效、审核缺失/变化或超过默认 30 天的条目标记 invalid / needs_review
python3 .agents/skills/xcar-xhs/scripts/candidate_pool.py audit --max-age-days 30

# 用重新审核、定稿后的同 ID 候选更新；revision 从当前队列读取
python3 .agents/skills/xcar-xhs/scripts/candidate_pool.py update \
  --out <重新审核定稿目录> --source-id <ID> --revision <当前版本>

python3 .agents/skills/xcar-xhs/scripts/candidate_pool.py withdraw \
  --source-id <ID> --revision <当前版本> --reason <原因>
```

`--history-file` 是全局选项，放在 enqueue/audit/update/withdraw 子命令之前。队列文件继续使用固定路径或既有环境覆盖。更新与撤回采用版本比较，拒绝修改 planned、published、unconfirmed；同 ID 再次 enqueue 不覆盖旧内容。正常入池、队列变更使用稳定锁文件和原子数据替换。新生产任务选题前及发布选择前会审计 queued 库存，待复核/失效条目不计入可用库存；不会改动正在执行的 planned 条目。

既有候选不会自动补写审核证据。缺新版证据、内容哈希或前三秒抽帧的旧条目需要实际复核，补齐 verification 后重新定稿，再显式 update；不要批量伪造 approved。原视频无需为了迁移重新搜索下载。已有历史、队列和素材不因安装代码而被修改。

# 候选流水线与审核协议

## 入口与职责

`candidate_pipeline.py --out <新目录>` 自动选题、增量搜索、下载抽帧；需要内容判断时输出 `review_request.json` 并返回 `awaiting_review`。Agent 完成逐条核验及文案，保存独立 `review.json`，用 `--review-file` 恢复。到至少三条合格后自动定稿并全部入候选池；从不调用发布浏览器，也不创建发布计划。

审核是 Agent 的工作，不是新增人工授权环节。不能用固定分数、虚构来源或“官方账号”等表述代替实际授权证据；缺少证据的素材淘汰，不能填满字段就宣称事实已核查。脚本检查证据结构和一致性，不判断证据真伪。

## 审核文件

复制本次 `review_request.json` 为 `review.json`，保留 `job_id`、候选 ID 与 `video_sha256`。每条填写：

- `decision`: `approved`、`rejected` 或暂未完成的 `pending`。淘汰必须有 `reason`。
- `publish`: 与 Skill 原 `publish.json` 相同的标题正文、品牌、车型、项目、文件主题、事实及来源、讨论问题、五项评分、2–6 个话题、必要时专项披露。body 对应最终 recommendation。
- `verification.presentation`: 合格只能为 `car_only`；人物/屏幕讲解淘汰。
- `verification.reviewer`、`reviewed_at`: 实际审核者标识及带时区 ISO 8601 时间。
- `visual_evidence`、`first_three_seconds_evidence`: 对画面及前三秒的具体观察。默认五帧覆盖约 0.5、1.5、2.5 秒和中后段，必要时观看视频确认运动、声音和画面完整性。
- `fact_evidence`: 来源如何支持正文事实；`rights_evidence` 与 `rights_source`: 实际授权依据和可追溯链接；`series_evidence`: 内容为何符合本次系列。
- `score_reasons`: 必须包含 `first_three_seconds`、`knowledge_point`、`save_value`、`discussion_question`、`recent_diversity` 五项具体理由。

脚本验证媒体哈希，规范化文案后绑定内容哈希。正文、视频变化必须重审；单纯文件改名不会使审核失效。没有有效前三秒/中后段抽帧或证据不完整时阻断，不能直接入池。低分和明显禁选会被淘汰，重复项在定稿前再次核查。

## 状态、恢复与预算

- `candidate_state.json`: 本任务状态、检索 cursor、已见 ID、下载/抽帧/审核状态与累计预算消耗的本地镜像；全局任务登记中的原子 checkpoint 是恢复依据，不手工编辑镜像绕过状态。
- `topic_plan.json`: 固定的本任务栏目与系列，不含预占的发布期数。
- `review_request.json`: 当前待审核条目；恢复时重新生成，已完成的审核不会反复要求处理。不要把此文件当成已审核结果。
- `candidate_receipt.json`: `complete`、`awaiting_review`、`budget_exhausted`、`exhausted`、`needs_candidates`、`blocked`、`cancelled`，始终 `published=0`。
- 历史文件旁的 `candidate_jobs.json`: 跨批次任务预约、在制作原帖和明确淘汰缓存。任务在审核等待中保留预约，`--cancel` 释放预约但不删除素材。
- `finalize_transaction.json`: 重命名事务和目标 manifest。中断后先恢复文件与清单，再入池；入池成功但回执写入失败时按原帖 ID 幂等恢复。

默认每任务累计最多 12 次搜索页请求、15 次下载尝试；每次调用执行时间预算 600 秒，在操作边界检查并限制网络/下载子操作超时，登录初始化等底层操作也可能占用额外时间。审核等待不计入下一次调用预算。定稿时因并发去重出现新缺口会返回 `needs_candidates`，再运行同目录自动递补。预算不足时显式提高 `--max-pages` / `--max-downloads`，继续同目录；不要换目录重复生产。目标数和检索筛选条件在任务创建时固定，恢复时沿用；增加预算不会清空已经消耗的计数。`--query` 可增加同系列检索词；仍不足三条只报告缺口，不能降低标准。

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

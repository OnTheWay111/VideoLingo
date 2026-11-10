---
name: web-content-fetcher
description: 为微信公众号筛选 X/Twitter 上的主题新闻与实用讨论。支持主题别名、前两句相关性、70 分发布适配、Top/Latest/Media、时效与赞评转览综合热度排序、入选/淘汰审计和显式 URL 素材核验；不再提供 YouTube 关键词检索，不使用 X 开发者 API 或第三方数据 API。
license: MIT
---

# 公众号主题内容筛选（web-content-fetcher）

把 X 检索结果加工成「主题相关性过滤 → 公众号发布适配评分 → 综合热度排序 → 入选/淘汰清单 → 可选素材下载」。目标不是通用热榜，而是**只保留适合进一步创作微信公众号内容的候选**，并优先推荐其中近期互动表现更强的内容。

不使用 `X_BEARER_TOKEN`、`XQUIK_API_KEY`、`XAI_API_KEY` 或 ScrapeCreators。默认不读取浏览器。

## 能力边界

| 场景 | 支持范围 |
|---|---|
| X 主题新闻、产品动态、技术实践、对比分析 | Top / Latest / Media 检索，主题过滤、发布适配筛选和综合热度排序 |
| X 热帖 | 默认按时效、赞、评论、转发和浏览综合排序，也可指定单项互动排序 |
| 显式 URL 素材核验 | 通过 yt-dlp 拉取元数据；用户要求时才下载媒体 |
| YouTube 关键词检索 | **已移除，不再支持** |
| 其他平台关键词检索 | 未内置；显式 URL 仅用于素材核验，不进入 X 自动选题榜单 |

不要把“支持 URL 拉取”说成“支持全站搜索”。不要伪造 URL、新闻点或互动数据。显式 URL 可以是 X 或其他 yt-dlp 支持的网站，但不会自动获得公众号入选资格。

## 双层筛选规则

实现位于 `scripts/editorial.py`，X 检索默认执行，不能只按热度直接交付原始结果。

### 第一层：主题必须主导内容

- 用 `--topic` 指定规范主题，用 `--topic-alias` 补充别名；未显式提供时从 `--query` 的引号短语或普通关键词提取。
- `Codex` 主题会自动归并 `Codex`、`OpenAI Codex`、`Codex CLI` 等别名，避免只认完整产品名。
- 正文没有明确提到主题：以 `主题缺失` 淘汰。
- 主题只在第三句及以后出现，通常属于背景、合集或附带提及：以 `非主题主导` 淘汰。
- 对 `OpenAI Codex`，推荐同时提供 `Codex CLI`、`Codex Desktop`、`Codex App` 等实际需要的别名，不要为了扩大数量加入无关品牌词。

例如，Grok Bot 合集只在清单里提到 Codex、泛 AI 提示词帖正文没有 Codex，都不能进入 Codex 专题。

### 第二层：公众号发布适配评分

默认最低 **70 分**，可用 `--min-wechat-score 0–100` 调整。分数用于可解释的预筛选，不代表事实真实性或最终文章质量。

| 规则 | 分值 |
|---|---:|
| 主题相关后的基础分 | 50 |
| 正文达到 120 / 300 / 800 字符 | +5 / +10 / +10 |
| 有发布、更新、故障、调查、价格等明确新闻点 | +15 |
| 有教程、CLI、MCP、对比、复盘等实用或技术信息 | +10 |
| 缺少新闻点和实用信息 | -10 |
| 含“兄弟们”“评论区”“你会入吗”等引流互动话术 | -20 |
| 含稳赚、月入、年入、被动收入等高收益宣传风险 | -35 |
| 含付费、 pricing、订阅制等推广信息 | -5 |

低于门槛的内容以 `公众号适配分低于阈值` 淘汰。内容即使互动很高，也不能绕过主题和发布适配过滤。

**Agent 必须再做语义复核**：阅读入选正文，确认 Codex 等主题确实是主要新闻对象，而不是关键词命中；必要时复核淘汰清单，纠正明显误判。不得把启发式分数或热度分数说成事实核查结论。

## 综合热度排序

X 主题检索默认使用 `--sort heat`。实现位于 `scripts/web_content.py`，同时考虑发布时间和四项互动，不再只按日期或单个点赞数推荐“最热”内容。

```text
heat_score = 25×likes_norm + 25×replies_norm + 20×reposts_norm
           + 15×views_norm + 15×recency

metric_norm = log1p(当前值) / log1p(本批候选该指标最大值)
recency = 0.5 ** (发布日期距参考日期的天数 / 3)
```

| 信号 | 权重 | 作用 |
|---|---:|---|
| 点赞 | 25% | 基础认可度 |
| 评论 | 25% | 讨论深度，避免只按点赞判断热度 |
| 转发 | 20% | 传播意愿 |
| 浏览 | 15% | 曝光规模；降低权重并进行对数归一化，避免淹没其他信号 |
| 发布时间 | 15% | 3 天半衰期，优先近期内容 |

计算与边界：

- 四项互动分别在本批抓取、去重后的候选集内做 `log1p` 归一化；最大值为 0 时，该指标所有候选的归一化值均为 0。
- 缺失互动按 0 处理，不估算或伪造。缺失日期的时效分为 0；发布日期晚于参考日期时，年龄按 0 天处理。
- `heat_score` 为 0–100 的**本批候选内相对分**，不是跨主题、跨日期可比较的绝对热度，也不代表 X 全量热榜。
- 热度在双层内容筛选前计算，因此淘汰帖也保留热度字段供审计；高热但低适配的内容仍必须淘汰。
- `heat_score` 不等于 `wechat_score`，不代表事实真实性，也不保证最终文章价值。Agent 仍需语义复核和一手事实核查。
- 用户显式指定 `--sort likes|replies|reposts|views` 时沿用单项排序；`none` 保留原顺序。单项排序不应被称为综合最热。

每条结果在 `heat` 模式下增加：

- `heat_score`：综合热度分。
- `heat_breakdown`：赞、评论、转发、浏览和时效各自的加权得分，便于解释推荐原因。
- `heat_age_days`：相对参考日期的年龄，缺失日期时为 `null`。

`manifest.json` 的 `heat` 字段记录权重、归一化方式、时效半衰期、参考日期和候选范围。

## 前置条件与 Web 会话

- X 检索需要 `../xfetch/scripts/xfetch.py` 及其 Python 环境；可用 `--xfetch`、`--python` 指定。
- 显式 URL 素材核验需要 `yt-dlp`；媒体合并需要 `ffmpeg`。URL 核验不依赖 xfetch。
- 浏览器：`--web-session chrome --browser-profile Default`。
- Cookie 文件：`--cookies-file ~/Downloads/cookies.txt`，与 `--web-session` 二选一。
- 默认 `--web-session off`，yt-dlp 使用 `--ignore-config --no-cookies-from-browser`，避免隐式使用其他会话。
- X 兼容 `--auth-file` 和 `XFETCH_AUTH_TOKEN` / `XFETCH_CT0`，优先级为 `auth-file > cookies-file > web-session > 既有 xfetch 会话`。
- macOS Chromium 原生提取失败自动回退 CDP，CDP 需要可选 `websocket-client`。
- 只记录会话方式、浏览器和 Profile，不输出 Cookie 值。临时 X 凭据随进程清理。
- 用户未明确选择浏览器/Profile 或 Cookie 文件时，不要自行扩大读取范围，不绕过访问控制。

## 用法

### Codex 今日公众号候选，按综合热度排序

```bash
python3 .agents/skills/web-content-fetcher/scripts/web_content.py \
  --source x --topic "OpenAI Codex" \
  --topic-alias "Codex CLI" --topic-alias "Codex Desktop" \
  --query '("OpenAI Codex" OR "Codex CLI" OR "Codex Desktop")' \
  --type latest --since 2026-11-10 --limit 100 --sort heat \
  --min-wechat-score 70 \
  --web-session chrome --browser-profile Default \
  --out work/codex-wechat-latest
```

`--query` 默认推断为 `--source x`，无需再写 YouTube source。只要最近一天可加 `--since YYYY-MM-DD`；产品热帖可用 `--type top --sort heat`。本地综合排序只影响已抓取且通过筛选的候选，不代表 X 全量榜单。

### 显式 URL 素材核验

```bash
python3 .agents/skills/web-content-fetcher/scripts/web_content.py \
  --url "https://x.com/example/status/123" \
  --web-session chrome --out work/source-check
```

只有用户明确要求下载时才加 `--download`。多个 `--url` 可重复，不能与 `--query` 混用。

## 主要选项

| 选项 | 默认 | 说明 |
|---|---|---|
| `--source` | 按输入推断 | `x` / `url`；不再接受 `youtube` |
| `--topic` | 从 query 推断 | 规范主题，建议明确提供 |
| `--topic-alias` | 从 query 推断 | 额外主题别名，可重复 |
| `--min-wechat-score` | `70` | 公众号适配最低分，0–100 |
| `--query` | 无 | X 检索式，可重复 |
| `--url` | 无 | 显式素材 URL，可重复 |
| `--limit` | `10` | 每组检索数量，1–100；主题筛选建议 50–100 |
| `--sort` | X=`heat`，URL=`none` | `heat` / `replies` / `likes` / `reposts` / `views` / `none` |
| `--type` | `top` | X 的 `top` / `latest` / `media` 视图 |
| `--since` | 无 | X 起始日期 |
| `--web-session` | `off` | 显式浏览器会话 |
| `--browser-profile` | 浏览器默认 | 例如 `Default` / `Profile 1` |
| `--cookies-file` | 无 | Netscape cookies.txt |
| `--auth-file` | 无 | X 专用 KEY=VALUE 文件 |
| `--download` | 关 | 下载入选内容或显式 URL 的媒体 |
| `--out` | `work/web-content` | 输出目录 |

## 输出与结果判断

```text
<out>/
├── items.jsonl       # 只包含通过双层筛选的内容
├── rejected.jsonl    # 淘汰内容及具体原因
├── manifest.json     # 候选数、入选数、淘汰数、错误、分数、热度配置和下载结果
└── videos/           # 仅 --download 时生成
```

除统一内容字段外，每条结果还包含：

- `topic_matches`：命中的主题别名。
- `topic_in_lead`：主题是否位于前两句。
- `wechat_score` / `wechat_reasons`：适配分及加减分原因。
- `heat_score` / `heat_breakdown` / `heat_age_days`：综合热度及解释字段，仅 `heat` 模式生成。
- `selection`：`selected` / `rejected`。
- `reject_reasons`：`主题缺失`、`非主题主导` 或低于适配门槛。

`candidate_count = item_count + rejected_count`。没有抓到候选时 CLI 返回 1；有候选但全部被淘汰属于正常筛选结果，返回 0，Agent 应如实说明“没有合适内容”，不能拿淘汰帖凑数。任一下载失败返回 1。

## Agent 工作流

1. 明确规范主题和别名，再生成 X 检索式；专题新闻优先 `latest`，热帖用 `top`，两者默认都使用 `--sort heat`。
2. 使用用户指定的浏览器/Profile 或 cookies.txt；先小批量验证，再扩大到 50–100 条候选。
3. 执行主题相关性和公众号适配双层过滤，检查 `errors`，不要直接按热度交付原始帖子。
4. 对入选内容做语义复核，确保主题主导、新闻点清楚、适合公众号展开；复核淘汰清单以减少误判。
5. 默认按 `heat_score` 推荐候选，结合 `heat_breakdown` 说明日期、赞、评论、转发和浏览的贡献；用户指定其他排序时尊重其选择。
6. 只推荐 `items.jsonl` 中的内容，说明候选范围、淘汰数量、互动数据抓取时间和热度参考日期，不把结果称为 X 全量榜单。
7. 公众号选题优先考虑产品更新、真实故障与修复、技术实践、数据对比和有证据的行业分析；不照搬引流话术，不夸大收益或未经核实的传闻。
8. 提醒事实核查、版权、频率限制和平台条款，只处理当前账号有权访问的内容。

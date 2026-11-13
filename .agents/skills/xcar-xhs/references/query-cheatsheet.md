# 检索式速查（汽车内容）

## X 搜索操作符（xfetch 原样透传）

| 操作符 | 作用 | 示例 |
|---|---|---|
| `filter:media` | 只要带图/视频的帖子 | `cybertruck filter:media` |
| `filter:native_video` | 只要 X 原生视频 | `"car review" filter:native_video` |
| `filter:links` / `filter:replies` / `filter:verified` | 链接 / 回复 / 认证账号 | — |
| `min_faves:N` | 点赞下限 | `porsche min_faves:1000` |
| `min_retweets:N` | 转推下限 | — |
| `since:YYYY-MM-DD` / `until:YYYY-MM-DD` | 时间范围 | `since:2026-09-01` |
| `from:user` / `to:user` / `@user` | 限定作者/回复 | `from:Tesla` |
| `"exact phrase"` | 精确短语 | `"car meet"` |
| `(a OR b)` / `-word` | 或 / 排除 | `-crypto -nft` |
| `lang:en` / `lang:zh` | 语言 | `lang:en` |

> `min_replies:` 是否被 X 接受未实测；按评论数排序请用 finder 的 `--sort replies`。

## 汽车类目关键词（中英对照，直接拼进检索式）

| 中文 | 英文 |
|---|---|
| 汽车 / 跑车 / 超跑 | car / sportscar / supercar |
| 越野 / 皮卡 | offroad / off-road / pickup / truck |
| 车展 / 车聚 | car show / car meet |
| 车评 / 试驾 | car review / test drive / first drive |
| 改装 | tuned / modified / build |
| 漂移 / 直线加速 | drift / drag race |
| 赛车 / 拉力 | race car / rally / motorsport |
| 品牌 | Tesla / Porsche / Ferrari / Lamborghini / McLaren / BMW / Mercedes / Audi / Rivian / Ford |
| 车型 | Cybertruck / GT3 RS / Mustang / Corvette / GTR / Supra |

## 提示词 → 检索式 的展开套路

1. **主题词优先**：提示词里的车型/品牌先成一组，如 `("cybertruck" OR "tesla")`。
2. **场景词补充**：越野加 `offroad OR desert`，超跑加 `POV OR exhaust OR "car spotting"`，车评加 `review OR "test drive"`。
3. **对照组**：固定补 2 组泛汽车检索（车聚/车展、车评/试驾），防止主题词太窄没结果。
4. **中文兜底**：X 上中文车内容少，保留一组 `(汽车 OR 越野 OR 超跑)` 即可，其他用英文。
5. **过滤条件**：默认 `filter:media` + `min_faves`（建议 200–1000）+ `since:`（近 1–3 个月），并加 `-crypto -nft -giveaway` 排除垃圾内容。

## 排序说明

- X 服务端：只有 `--type top`（综合互动榜）/ `latest` / `media`，**没有**按点赞/评论排序的参数。
- finder 本地排序：`--sort likes|replies|reposts|views`，对应 `favorite_count` / `reply_count` / `retweet_count` / `view_count`。
- 想要“评论数榜”：`--sort replies`；想两者兼顾可先 `--sort likes` 下载，再对 `candidates.csv` 自行排序。

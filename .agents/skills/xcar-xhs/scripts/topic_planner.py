#!/usr/bin/env python3
"""按小红书内容组合和发布历史生成可复现的汽车选题计划。"""

import argparse
import json
from pathlib import Path

from content_policy import SCORE_RUBRIC, SCORE_THRESHOLD
from publish_history import ACTIVE_STATUSES, PublishedHistory, default_history_path

STRATEGY_VERSION = 5
CONTENT_MIX = {"rare_models": 0.60, "mechanical_contrast": 0.25, "visual_experiment": 0.15}
PILLAR_SCHEDULE = (
    "rare_models", "rare_models", "mechanical_contrast", "rare_models", "visual_experiment",
    "rare_models", "mechanical_contrast", "rare_models", "rare_models", "visual_experiment",
    "rare_models", "mechanical_contrast", "rare_models", "rare_models", "mechanical_contrast",
    "rare_models", "visual_experiment", "rare_models", "mechanical_contrast", "rare_models",
)
NEGATIVE_FILTERS = (
    '-"car meet" -"car show" -"auto show" -"owner story" -"driving POV" '
    '-"road trip" -Goodwood -SEMA -crypto -nft -giveaway'
)
PILLARS = {
    "rare_models": {
        "label": "稀有车型连续图鉴",
        "duration": "15–45",
        "series": (
            {
                "id": "rare_jdm",
                "label": "JDM 稀有车型",
                "query_groups": (
                    '("Honda Beat" OR "Autozam AZ-1" OR "Suzuki Cappuccino" OR "Honda S660")',
                    '("Nissan Figaro" OR "Nissan Pao" OR "Toyota Sera" OR "Daihatsu Copen")',
                    '("Mazda Cosmo" OR "Eunos Cosmo" OR "Mitsubishi GTO" OR "Honda NSX")',
                ),
            },
            {
                "id": "limited_supercars",
                "label": "限量超跑与稀有量产车",
                "query_groups": (
                    '("Apollo IE" OR "Apollo Evo" OR "Gordon Murray T.50")',
                    '("McMurtry Speirling" OR "Aston Martin Valkyrie" OR "Mercedes AMG One")',
                    '("Koenigsegg Regera" OR "Pagani Huayra" OR "Bugatti Divo")',
                ),
            },
        ),
    },
    "mechanical_contrast": {
        "label": "改装与机械反差",
        "duration": "20–60",
        "series": (
            {
                "id": "engine_swaps",
                "label": "发动机移植与机械反差",
                "query_groups": (
                    '("engine swap" OR "motor swap" OR "powertrain swap")',
                    '("LS swap" OR "K swap" OR "2JZ swap")',
                    '("rotary swap" OR "V8 swap" OR "electric conversion")',
                ),
            },
            {
                "id": "custom_fabrication",
                "label": "宽体与定制工艺",
                "query_groups": (
                    '("widebody build" OR "custom widebody" OR "wide body fabrication")',
                    '("custom fabrication" OR "hand built car" OR "coachbuilt")',
                    '("exhaust fabrication" OR "custom exhaust" OR "titanium exhaust")',
                ),
            },
        ),
    },
    "visual_experiment": {
        "label": "视觉实验选题",
        "duration": "15–45",
        "series": (
            {
                "id": "microcars",
                "label": "极小汽车与奇特结构",
                "query_groups": (
                    '("microcar" OR "bubble car" OR "tiny car")',
                    '("Peel P50" OR "Peel Trident" OR "BMW Isetta")',
                    '("three wheel car" OR "three wheeled car" OR "cabin scooter")',
                ),
            },
            {
                "id": "experimental_vehicles",
                "label": "罕见原型与实验车辆",
                "query_groups": (
                    '("flying car concept" OR "amphibious car" OR "experimental vehicle")',
                    '("one-off concept" OR "concept prototype" OR "rolling prototype")',
                    '("unusual car design" OR "strange car concept" OR "radical concept car")',
                ),
            },
            {
                "id": "mountain_driving",
                "label": "山路驾驶场景",
                "query_groups": (
                    '("mountain road" OR "mountain pass" OR "mountain drive") (car OR supercar OR JDM)',
                    '("touge" OR "canyon drive" OR "canyon road") (car OR supercar OR JDM)',
                    '("mountain road POV" OR "mountain drive POV" OR "touge POV") (car OR supercar OR JDM)',
                ),
            },
        ),
    },
}


def active_records(records):
    return [record for record in records if record.get("status", "published") in ACTIVE_STATUSES]


def build_topic_plan(records):
    active = active_records(records)
    schedule_index = len(active) % len(PILLAR_SCHEDULE)
    pillar_id = PILLAR_SCHEDULE[schedule_index]
    pillar = PILLARS[pillar_id]
    pillar_records = [record for record in active if record.get("topic_pillar") == pillar_id]
    series_index = (len(pillar_records) // 5) % len(pillar["series"])
    series = pillar["series"][series_index]
    episode = len(pillar_records) % 5 + 1
    queries = ["%s filter:media %s" % (group, NEGATIVE_FILTERS) for group in series["query_groups"]]
    recent_titles = [record.get("title") for record in active[-5:] if record.get("title")]
    recent_brands = []
    for record in reversed(active):
        brand = record.get("brand")
        if brand and brand not in recent_brands:
            recent_brands.append(brand)
    return {
        "strategy_version": STRATEGY_VERSION,
        "mode": "auto",
        "pillar": pillar_id,
        "pillar_label": pillar["label"],
        "content_mix": CONTENT_MIX,
        "schedule_cycle": len(active) // len(PILLAR_SCHEDULE) + 1,
        "schedule_slot": schedule_index + 1,
        "series_id": series["id"],
        "series_label": series["label"],
        "series_episode": episode,
        "series_batch_size": 5,
        "target_duration_seconds": pillar["duration"],
        "prompt": "自动选题｜%s｜%s｜第 %d 期；推荐成片时长 %s 秒" % (
            pillar["label"], series["label"], episode, pillar["duration"]),
        "queries": queries,
        "score_threshold": SCORE_THRESHOLD,
        "score_rubric": SCORE_RUBRIC,
        "source_engagement": "weak_reference_only",
        "verified_selection_count": 3,
        "model_cooldown_days": 30,
        "brand_limit_per_batch": 1,
        "recent_titles": recent_titles,
        "recent_brands": recent_brands[:5],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-file", default=str(default_history_path()))
    args = parser.parse_args(argv)
    history = PublishedHistory(Path(args.history_file))
    print(json.dumps(build_topic_plan(history.records()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""按小红书内容组合和发布历史生成可复现的汽车选题计划。"""

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from content_policy import SCORE_RUBRIC, SCORE_THRESHOLD, parse_datetime
from publish_history import ACTIVE_STATUSES, PublishedHistory, default_history_path

STRATEGY_VERSION = 10
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
            {
                "id": "screen_game_cars",
                "label": "电影与游戏名车的实车细节",
                "query_groups": (
                    '("DeLorean DMC-12" OR "Aston Martin DB5") (walkaround OR details OR replica)',
                    '("AE86" "Initial D" OR "Peugeot 406" "Taxi") (replica OR build OR details)',
                    '("Porsche 911 Turbo" "Cyberpunk" OR "BMW M3 GTR" "Need for Speed") (replica OR build OR "real life")',
                ),
            },
            {
                "id": "rare_performance_wagons",
                "label": "少见的原厂性能旅行车",
                "query_groups": (
                    '("Volvo 850 T-5R" OR "Audi RS2 Avant") (walkaround OR details OR engine)',
                    '("RS2 Avant" OR "Stagea 260RS" OR "Legnum VR-4" OR "Volvo 850 T-5R")',
                    '("MG ZT-T 260" OR "Subaru Legacy GT-B") (walkaround OR details OR engine)',
                    '("Mitsubishi Legnum VR-4" OR "Nissan Stagea 260RS") (walkaround OR details OR engine)',
                ),
            },
            {
                "id": "regional_production_cars",
                "label": "各地特有的量产车",
                "query_groups": (
                    '("Holden Ute" OR "Holden Maloo" OR "Ford Falcon Ute")',
                    '("Renault Avantime" OR "Citroën C6" OR "Citroen C6")',
                    '("Hyundai Pony" OR "Kia Pride" OR "Isuzu VehiCROSS")',
                    '("Tatra 603" OR Tatra603 OR "Tatra T603" OR "太脱拉603")',
                ),
            },
            {
                "id": "rally_road_cars",
                "label": "拉力赛车的公路版",
                "query_groups": (
                    '("Lancia Stratos Stradale" OR "Stratos HF Stradale" OR "Lancia 037 Stradale" OR "Rally 037 Stradale")',
                    '("Subaru Impreza 22B" OR "Subaru 22B" OR "Impreza 22B-STi") -replica -game',
                    '("Ford RS200" OR "Peugeot 205 Turbo 16" OR "Audi Sport quattro") ("road car" OR "road version" OR stradale OR Straßenversion)',
                    '("Celica GT-Four RC" OR "Celica GT-Four Carlos Sainz" OR "Celica GT-Four Group A Rallye")',
                    '("Delta S4 Stradale" OR "Delta HF Integrale Evoluzione" OR "Renault 5 Turbo 2") -replica -game',
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
            {
                "id": "car_mechanisms",
                "label": "汽车机关图鉴",
                "query_groups": (
                    '("Maybach GLS" OR "GLS 600" OR "GLS600") (bouncing OR "bounce mode")',
                    '("Bugatti Veyron" OR "McLaren 720S") (airbrake OR "air brake")',
                    '("Ferrari F40" OR "Porsche 928" OR "Miata") ("pop-up headlights" OR "headlight wink")',
                    '("Ferrari Superamerica" OR "Ferrari 575 Superamerica") (roof OR tetto) (rotates OR rotating OR opening OR flips OR opens)',
                    '("Koenigsegg Regera" OR "Koenigsegg Gemera") (doors OR hood OR bonnet) (open OR opening OR opens OR autoskin)',
                    '("Porsche 911 GT3 RS" OR "992 GT3 RS") (wing OR DRS) (opens OR opening OR moving OR deploys OR activation)',
                    '("BMW Z1" OR "Toyota Sera") (doors OR door) (sliding OR lowering OR lifts OR opening)',
                    '("Citroën DS" OR "Citroen DS" OR "Citroen CX") (suspension OR hydropneumatic) (rises OR raising OR lowering OR lifting)',
                    '("Honda Prelude" OR "Nissan Skyline") ("rear wheels" OR "rear axle steering") (turning OR demonstration OR parking)',
                    '("Lancia Delta" OR "Ford Escort RS Cosworth") ("dog box" OR "sequential gearbox") ("gear change" OR shifting)',
                ),
            },
            {
                "id": "classic_restoration",
                "label": "经典车修复前后",
                "query_groups": (
                    '("barn find restoration" OR "first start after years") (car OR "classic car")',
                    '("dry ice blasting" OR "dry ice cleaning") (undercarriage OR "classic car")',
                    '("paint correction" OR "car restoration") ("before after" OR "before and after" OR timelapse)',
                ),
            },
            {
                "id": "offroad_equipment",
                "label": "越野装备怎么工作",
                "query_groups": (
                    '("differential lock" OR "locking differential") (articulation OR "cross axle") (car OR SUV OR 4x4)',
                    '("Land Rover Defender" OR "Suzuki Jimny") ("suspension articulation" OR "cross axle")',
                    '("Jeep Wrangler" OR "Ford Bronco") ("sway bar disconnect" OR "stabilizer bar disconnect")',
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
            {
                "id": "small_car_space",
                "label": "小车大空间",
                "query_groups": (
                    '("Honda N-VAN" OR "Daihatsu Atrai") ("flat floor" OR "seat folding" OR "folding seats")',
                    '("Suzuki Every" OR "Honda N-VAN") ("camper conversion" OR "camper interior")',
                    '("Toyota Roomy" OR "Daihatsu Tanto" OR "Honda N-Box") ("sliding door" OR "seat folding" OR "interior space")',
                ),
            },
        ),
    },
}


def series_queries(series):
    return [f"{group} filter:{kind} {NEGATIVE_FILTERS}"
            for kind in ("videos", "media") for group in series["query_groups"]]


def active_records(records):
    # 历史是事件日志：补链接和状态更正不能重复消耗发布期数。
    latest = {}
    for record in records:
        key = str(record.get("source_id") or record.get("title") or "")
        if key:
            latest[key] = record
    return sorted((r for r in latest.values() if r.get("status", "published") == "published"),
                  key=lambda r: r.get("published_at") or r.get("recorded_at") or "")


STALLED_JOBS = {'blocked', 'budget_exhausted', 'exhausted'}
STARTUP_LEASE = timedelta(minutes=10)


def reserved_source_ids(job, now):
    """保护已取得的素材。一次中断在下载之前的占位带有效期。"""
    status = job.get('status')
    if status in ('complete', 'cancelled'):
        return set()
    state = job.get('checkpoint') or {}
    if 'items' not in state:
        return set() if status in STALLED_JOBS else set(map(str, job.get('source_ids', [])))
    eligible = {'approved'}
    if status not in STALLED_JOBS:
        eligible.update(('downloaded', 'awaiting_review', 'frame_failed'))
        updated = parse_datetime(job.get('updated_at'))
        if updated and timedelta(0) <= now - updated < STARTUP_LEASE:
            eligible.add('downloading')
    items = state['items']
    return {str(sid) for sid in job.get('source_ids', items) if items.get(sid, {}).get('status') in eligible}


def production_inventory(queue, jobs, now, published_ids=()):
    """库存按唯一原帖计算。保留项单列；只有刚启动的搜索任务可以暂估尚无原帖的名额。"""
    inventory, counts = [], Counter(available=0, held=0, in_production=0, reserved=0)
    seen = set(published_ids) | {str(e['source_id']) for e in queue if e.get('source_id')}
    for entry in queue:
        if entry.get('status') not in ('queued', 'planned'):
            continue
        if entry.get('publish_hold'):
            counts['held'] += 1
            continue
        inventory.append(entry.get('item', entry))
        counts['available'] += 1

    for job in jobs:
        status = job.get('status')
        if status in ('complete', 'cancelled'):
            continue
        state = job.get('checkpoint') or {}
        plan = job['plan']
        specs = plan.get('series_specs') or [{'pillar': plan['pillar'], 'id': plan['series_id']}]
        pillars = {spec['id']: spec['pillar'] for spec in specs}
        stalled = status in STALLED_JOBS
        items = state.get('items') or {}
        if not items and not state and len(specs) == 1:
            # 旧登记只有实际占用 ID，没有证据支持按整个批次目标预约。
            items = {sid: {'status': 'awaiting_review'} for sid in job.get('source_ids', [])}
        for source_id, entry in items.items():
            if str(source_id) in seen:
                continue
            stage = entry['status']
            if stage != 'approved' and (stalled or stage not in ('downloaded', 'awaiting_review')):
                continue
            item = entry.get('item') or {}
            series = entry.get('series_id') or item.get('topic_series_id') or plan['series_id']
            pillar = pillars.get(series) or item.get('topic_pillar')
            if pillar in PILLARS:
                seen.add(str(source_id))
                inventory.append({'topic_pillar': pillar, 'topic_series_id': series})
                counts['in_production'] += 1

        created = parse_datetime(state.get('created_at'))
        if (status == 'searching' and not items and not job.get('source_ids') and created
                and timedelta(0) <= now - created < STARTUP_LEASE):
            for slot in range(job.get('target', 3)):
                spec = specs[slot % len(specs)]
                inventory.append({'topic_pillar': spec['pillar'], 'topic_series_id': spec['id']})
                counts['reserved'] += 1
    return inventory, dict(counts)


def build_topic_plan(records, queue_records=None, reservations=None, now=None):
    active = active_records(records)
    schedule_index = len(active) % len(PILLAR_SCHEDULE)
    pillar_id = PILLAR_SCHEDULE[schedule_index]
    inventory = []
    inventory_counts = {}
    if queue_records is not None:
        inventory, inventory_counts = production_inventory(
            queue_records, reservations or [], now or datetime.now(timezone.utc),
            (str(row['source_id']) for row in active if row.get('source_id')))
        counts = {key: sum(item.get("topic_pillar") == key for item in inventory) for key in PILLARS}
        scheduled = pillar_id
        pillar_id = min(PILLARS, key=lambda key: (counts[key] / CONTENT_MIX[key], key != scheduled))
    pillar = PILLARS[pillar_id]
    pillar_records = [record for record in active if record.get("topic_pillar") == pillar_id]
    series_index = (len(pillar_records) // 5) % len(pillar["series"])
    ranked = [series_index]
    if queue_records is not None:
        ranked = sorted(range(len(pillar["series"])), key=lambda index: (
            sum(item.get("topic_series_id") == pillar["series"][index]["id"] for item in inventory),
            index != series_index))
        series_index = ranked[0]
    series = pillar["series"][series_index]
    episode = len(pillar_records) % 5 + 1
    queries = series_queries(series)
    recent_titles = [record.get("title") for record in active[-5:] if record.get("title")]
    recent_brands = []
    for record in reversed(active):
        brand = record.get("brand")
        if brand and brand not in recent_brands:
            recent_brands.append(brand)
    return {
        "strategy_version": STRATEGY_VERSION,
        "mode": "auto",
        "inventory_aware": queue_records is not None,
        "inventory_counts": inventory_counts,
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
        "refill_series": [pillar["series"][i]["id"] for i in ranked[:3]],
        "ranked_series": [pillar["series"][i]["id"] for i in ranked],
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

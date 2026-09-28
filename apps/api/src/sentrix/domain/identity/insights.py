"""核心人物排序与代表照选择。

逐行移植 1.0 ``backend/person_insights.py:21-112`` 的 ``cosine`` /
``core_person_score`` / ``rank_core_people`` / ``select_representatives``。
1.0 的模块 docstring 说这两个函数是 "pure, deterministic"，理由是管线要能拿同一份
``album3-max`` 证据反复调参而不碰导入路径——**deterministic 是成立的，pure 不是**：
两个函数都会改写传进来的字典（见下）。2.0 照搬这个副作用，不趁机"修好"——
它是 1.0 的既有行为，改掉就是行为变更，得单独裁决。

**两处副作用，都写在这里并有用例钉住：**

1. ``core_person_score`` 往 ``row["score_breakdown"]`` 里写八个分项；
2. ``select_representatives`` 往选中的 ``item["selection_json"]`` 里写选择依据。

**``cosine`` 与 ``faces.cosine`` 同名但不是一回事**（1.0 里就是两个不同实现）：
``faces.cosine`` 是裸点积，**假定入参已归一化**（``FaceSample`` 在构造期就归一了）；
这一个**自己算模长**。调用方若把未归一化的向量喂给前者，得到的不是余弦。两个都保留，
名字冲突留着——1.0 就是这样，改名的收益抵不上与 1.0 对照的成本。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

# 1.0 ``person_insights.py:32-40`` 的权重。**它们是产品行为不是实现细节**：
# 0.30 日数 / 0.20 事件数 / 0.15 覆盖 / 0.10 同框 / 0.10 场景 / 0.15 质量。
WEIGHT_DATE = 0.30
WEIGHT_EVENT = 0.20
WEIGHT_COVERAGE = 0.15
WEIGHT_CO_PERSON = 0.10
WEIGHT_SCENE = 0.10
WEIGHT_QUALITY = 0.15

# 各分项「给满」的除数（``:32-36`` 的 ``min(x / N, 1.0)``）。
DATE_FULL = 8.0
EVENT_FULL = 6.0
COVERAGE_FULL = 20.0
CO_PERSON_FULL = 4.0
SCENE_FULL = 4.0

# 已确认人物加权、只出现过一天的人物降权（``:37-38``）。
CONFIRMED_BOOST = 0.20
SINGLE_DAY_PENALTY = 0.35

# ``rank_core_people`` 的硬上限：limit 传再大也只取 10（``:64`` 的 ``min(limit, 10)``）。
# 这是导入后自动跑画像时的模型调用上限的由来——每人物最多 2 次调用。
CORE_PEOPLE_LIMIT = 10
# 进 core 的门槛：已确认，或跨过至少 2 天（``:65``）。
CORE_MIN_DATE_COUNT = 2

# ``select_representatives`` 的默认值（``:78-79``）。
REPRESENTATIVE_LIMIT = 12
REPRESENTATIVE_PER_EVENT = 2
DUPLICATE_THRESHOLD = 0.94
SELECTION_VERSION = "representative-v1"


def cosine(left: Sequence[float] | None, right: Sequence[float] | None) -> float:
    """带模长的余弦。**与 ``faces.cosine`` 不同**，那个是裸点积、假定已归一化。"""
    if not left or not right:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=False))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _count(row: Mapping[str, Any], key: str) -> int:
    return int(row.get(key) or 0)


def core_person_score(row: dict[str, Any]) -> float:
    """六项加权 + 已确认加成 − 单日惩罚，钳到 ``[0, 1]``。

    **会写 ``row["score_breakdown"]``**（1.0 原样，见模块 docstring）。
    """
    date_score = min(_count(row, "date_count") / DATE_FULL, 1.0)
    event_score = min(_count(row, "event_count") / EVENT_FULL, 1.0)
    coverage_score = min(_count(row, "member_count") / COVERAGE_FULL, 1.0)
    co_person_score = min(_count(row, "co_person_count") / CO_PERSON_FULL, 1.0)
    scene_score = min(_count(row, "scene_count") / SCENE_FULL, 1.0)
    quality_score = max(0.0, min(float(row.get("quality") or 0), 1.0))
    confirmed_boost = CONFIRMED_BOOST if row.get("confirmed") else 0.0
    single_day_penalty = SINGLE_DAY_PENALTY if _count(row, "date_count") <= 1 else 0.0
    total = (
        WEIGHT_DATE * date_score
        + WEIGHT_EVENT * event_score
        + WEIGHT_COVERAGE * coverage_score
        + WEIGHT_CO_PERSON * co_person_score
        + WEIGHT_SCENE * scene_score
        + WEIGHT_QUALITY * quality_score
        + confirmed_boost
        - single_day_penalty
    )
    row["score_breakdown"] = {
        "date_score": round(date_score, 6),
        "event_score": round(event_score, 6),
        "coverage_score": round(coverage_score, 6),
        "co_person_score": round(co_person_score, 6),
        "scene_score": round(scene_score, 6),
        "quality_score": round(quality_score, 6),
        "confirmed_boost": round(confirmed_boost, 6),
        "single_day_penalty": round(single_day_penalty, 6),
    }
    return round(max(0.0, min(total, 1.0)), 6)


def rank_core_people(rows: Sequence[dict[str, Any]], limit: int = CORE_PEOPLE_LIMIT) -> list[dict[str, Any]]:
    """排序并给每人一个 ``tier``（core / common / incidental）。

    排序键是**四段**（``:59-63``）：已确认的排前面 → 分数降序 → 天数降序 →
    ``person_id`` 升序。最后那一段让结果在分数与天数都相同时**仍然确定**——
    少了它，同一份输入两次跑出来的顺序可以不同。

    **会写每一行的 ``score_breakdown``**（经 ``core_person_score``）。
    """
    values = [{**row, "core_score": core_person_score(row)} for row in rows]
    values.sort(
        key=lambda item: (
            not bool(item.get("confirmed")),
            -item["core_score"],
            -_count(item, "date_count"),
            str(item["person_id"]),
        )
    )
    core_ids = {
        item["person_id"]
        for item in values[: max(1, min(int(limit), CORE_PEOPLE_LIMIT))]
        if item.get("confirmed") or _count(item, "date_count") >= CORE_MIN_DATE_COUNT
    }
    return [
        {
            **item,
            "tier": "core"
            if item["person_id"] in core_ids
            else ("common" if _count(item, "date_count") >= CORE_MIN_DATE_COUNT else "incidental"),
        }
        for item in values
    ]


def select_representatives(
    candidates: Sequence[dict[str, Any]],
    # 键是资产的 id，**不声明成 str**：调用方拿到的是从行里 `item.get("asset_id")`
    # 取出来的 Any|None，而 1.0 就是原样拿去查表（查不到返回 None，与缺失同义）。
    # 写成 str 会逼调用方加一次它并不需要的转换。
    vector_by_asset: Mapping[Any, Sequence[float]],
    limit: int = REPRESENTATIVE_LIMIT,
    per_event: int = REPRESENTATIVE_PER_EVENT,
    duplicate_threshold: float = DUPLICATE_THRESHOLD,
) -> list[dict[str, Any]]:
    """挑代表照：**先在事件之间铺开，再在事件内部去重**。

    两个约束的次序是产品行为（``:81-96``）：

    1. 同一个 ``event_id`` 最多 ``per_event`` 张——保证代表照覆盖不同场合，
       而不是同一次出行里最能看的十二张；
    2. 与已选中的任一张余弦 ≥ ``duplicate_threshold`` 的**跳过**——同一套衣服、
       同一个角度连拍十几张时，去重比排序更决定最终观感。

    排序键是四段（``:82-87``）：体态可见度 → 质量 → 拍摄时间 → ``face_instance_id``。
    最后一段同样是为了**确定性**。

    **会往选中的每一项写 ``selection_json``**（1.0 原样）。
    """
    selected: list[dict[str, Any]] = []
    event_counts: dict[Any, int] = {}
    ordered = sorted(
        candidates,
        key=lambda item: (
            -float(item.get("body_visibility") or 0),
            -float(item.get("quality") or 0),
            str(item.get("captured_at") or ""),
            str(item["face_instance_id"]),
        ),
    )
    for item in ordered:
        event_id = item.get("event_id")
        if event_counts.get(event_id, 0) >= per_event:
            continue
        vector = vector_by_asset.get(item.get("asset_id")) or []
        if vector and any(
            cosine(vector, vector_by_asset.get(chosen.get("asset_id")) or []) >= duplicate_threshold
            for chosen in selected
        ):
            continue
        selected.append(item)
        event_counts[event_id] = event_counts.get(event_id, 0) + 1
        if len(selected) >= limit:
            break
    for item in selected:
        item["selection_json"] = {
            "version": SELECTION_VERSION,
            "quality": round(float(item.get("quality") or 0), 6),
            "body_visibility": round(float(item.get("body_visibility") or 0), 6),
            "duplicate_threshold": duplicate_threshold,
            "event_coverage": event_counts.get(item.get("event_id"), 0),
        }
    return selected

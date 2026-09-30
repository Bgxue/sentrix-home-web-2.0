"""回合预算。数值取自 ``docs/refactor/behaviour-baseline.md`` 的冻结项。

1.0 的 ``ProfileConfig``（``agent_runtime/profile.py:13``）把这些值连同 6 套 profile
放在一个 dict 里，由 ``SENTRIX_AGENT_PROFILE`` 环境变量选；未知名字**静默回退**到默认
profile，不打日志。2.0 只保留生产在跑的那一套，写死成常量——没有 env、没有「选错了也
不报错」的入口。

两处与 1.0 的刻意差异：

1. ``wall_time_s`` 不再被就地改写。1.0 在检测到 OCR / 多图意图时把预算对象里的墙钟
   放宽到 240s，而轨迹里记的仍是 profile 的 180s（``runtime.py:2130``）——事后看数据
   永远对不上。要改预算就改这个文件，那是一次有版本、可审查的变更。
2. ``final_reserve_s`` 只留给「写答案」。1.0 的 ``can_tool_call`` 检查它、
   ``can_model_step`` 不检查（``budget_manager.py:38-51``），这个非对称是对的，保留。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TurnProfile:
    name: str
    max_steps: int
    max_tool_calls: int
    max_search_calls: int
    # 一轮里最多几次「精读」（``ToolSpec.cost_class == EXPENSIVE``）。1.0
    # ``budget_manager.py:17`` 的 ``max_inspections = 1``。
    #
    # **它是 T123 与 T124 共用的**：1.0 的 ``inspect_photo`` 与 ``read_photo_text``
    # 都是 ``cost_class="expensive"``，共用一个 ``inspections`` 计数器——所以这条
    # 约束是「一轮总共只能精读一张图一次」，不是「每个工具各一次」。
    # 1.0 的工具描述里那句「同一张图最多复核一次，不要反复检查同一张」的**执行机构**
    # 就是它；少了它，那句话只是一句劝告。
    max_inspections: int
    wall_time_s: float
    final_reserve_s: float


# goal_driven_candidate：1.0 的生产默认 profile。
DEFAULT_PROFILE = TurnProfile(
    name="goal_driven_candidate",
    max_steps=8,
    max_tool_calls=6,
    # 1.0 runtime.py:1895 的硬收敛。实测小模型会反复改 query 重搜，把工具预算耗光后
    # 以空答案收场（e8c9d4 的 35 题里 52%）；收敛到 1 次后逼它翻页或直接作答。
    max_search_calls=1,
    # 1.0 budget_manager.py:17。一轮总共一次精读，inspect_photo 与 read_photo_text 共用。
    max_inspections=1,
    wall_time_s=180.0,
    final_reserve_s=10.0,
)

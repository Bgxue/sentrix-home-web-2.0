"""1.0 与 2.0 两份实现的**逐字段差分**（S10a 的验收证据，可复跑）。

**为什么不是「打两个 HTTP 端点比」。** 那比的是「两个后端 + 两个环境」：1.0 的 Sentrix
与 2.0 的不是同一份数据，同一个原因（资产清单取不到）在两边会走到**不同的分支**
——「取不到」回 ``unavailable``，「取到了是空的」走工具轨迹回退。比出来的是环境。

所以这里把环境按住：**把两边取资产清单那一步都钉成「取到了、是空的」**，逼它们走同一条
回退路径，再逐字段比输出。剩下的任何差异都只可能来自实现。

用法：

    .venv/bin/python scripts/diff_photobench_reports.py <run_id> [<run_id> ...]

**路径可配**（迁移期专用，所以默认值是这台机器上的 1.0 检出）：

    SENTRIX_LEGACY_BACKEND   1.0 的 services/photobench/backend
    SENTRIX_LEGACY_RESULTS   1.0 的 services/photobench/results（270 个 run，不进仓库）
"""

from __future__ import annotations

import copy
import os
import sys
import unittest.mock as mock
from pathlib import Path
from typing import Any

LEGACY_BACKEND = os.environ.get(
    "SENTRIX_LEGACY_BACKEND", "/home/asus/Github/Sentrix-Home-Web/services/photobench/backend"
)
LEGACY_RESULTS = os.environ.get(
    "SENTRIX_LEGACY_RESULTS", "/home/asus/Github/Sentrix-Home-Web/services/photobench/results"
)
MODERN_SRC = os.environ.get(
    "SENTRIX_MODERN_SRC", str(Path(__file__).resolve().parents[1] / "apps/photobench/src")
)


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """摊平成 ``路径 -> 值``，这样差异能报到具体某一格而不是「整个 dict 不一样」。"""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return out
    if isinstance(value, list):
        out = {f"{prefix}.__len__": len(value)}
        for index, item in enumerate(value):
            out.update(flatten(item, f"{prefix}[{index}]"))
        return out
    return {prefix: value}


def compare(label: str, legacy: Any, modern: Any) -> int:
    left, right = flatten(legacy), flatten(modern)
    keys = sorted(set(left) | set(right))
    bad = 0
    for key in keys:
        if left.get(key) != right.get(key):
            bad += 1
            if bad <= 12:
                print(
                    f"    **差异** {key}\n        1.0 = {left.get(key)!r}\n        2.0 = {right.get(key)!r}"
                )
    print(f"  {label}: {len(keys)} 个字段，{'一致' if not bad else f'{bad} 处不一致'}")
    return bad


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if not Path(LEGACY_BACKEND).is_dir():
        print(f"1.0 后端目录不存在：{LEGACY_BACKEND}（用 SENTRIX_LEGACY_BACKEND 覆盖）")
        return 2

    sys.path.insert(0, LEGACY_BACKEND)
    import benchmark_orchestrator as bo  # noqa: PLC0415

    sys.path.insert(0, MODERN_SRC)
    # mypy strict 的 ``no_implicit_reexport`` 不允许从包隐式取子模块（``domain/runs``
    # 的 ``__init__.py`` 只导出包本身），所以按模块路径显式导入。
    import photobench.domain.runs.keyframe_analysis as keyframe_analysis  # noqa: PLC0415
    import photobench.domain.runs.memory_effectiveness as memory_effectiveness  # noqa: PLC0415

    repo = bo.OrchestratorRepository(Path(LEGACY_RESULTS))
    print(f"1.0 仓储载入 {len(repo.runs)} 个 run")

    total = 0
    for run_id in sys.argv[1:]:
        run = repo.runs.get(run_id)
        if run is None:
            print(f"{run_id}: 1.0 侧没有这个 run，跳过")
            continue
        state = copy.deepcopy(run.state if isinstance(run, bo.BenchmarkRun) else run)

        # 两边都钉成「取到了是空的」：强制走同一条工具轨迹回退路径。
        with mock.patch.object(bo, "request_json", return_value={"assets": []}):
            legacy_kf = repo.get_keyframe_analysis(run_id)
            legacy_me = repo.get_memory_effectiveness(run_id)
        modern_kf = keyframe_analysis.keyframe_analysis(state, [], error="", video_facts={})
        modern_me = memory_effectiveness.memory_effectiveness(state, [])

        print(f"\n===== {run_id} =====")
        total += compare("keyframe-analysis", legacy_kf, modern_kf)
        total += compare("memory-effectiveness", legacy_me, modern_me)

    print()
    if total:
        print(f"共 {total} 处未解释的差异")
        return 1
    print("逐字段一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())

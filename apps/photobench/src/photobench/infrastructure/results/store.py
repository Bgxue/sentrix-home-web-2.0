"""``RunStore`` 的文件实现：``results/<run_id>/{run.json,reviews.json,gpu_samples.jsonl}``。

目录形状与 1.0 完全一致（1.0 ``results/`` 下每个 run 一个目录、三件文件），所以把
2.0 指向 1.0 的 results 目录就能直接读——**2.0 不为结果换格式**：那是数据，不是代码。

**``iter_entries`` 里那条 ``except`` 是照搬 1.0 的**（``_load_existing`` ``:5043``）：
一个坏掉的 run.json 只跳过它自己。扫描是后台跑的，一条坏文件让整张列表空掉的话，
运维会以为 270 个 run 全丢了。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from photobench.domain.runs.analysis import run_list_entry


class FileRunStore:
    def __init__(self, results_root: Path) -> None:
        self._root = results_root

    def _run_dir(self, run_id: str) -> Path:
        """run_id 直接来自 URL，所以先挡住路径穿越。

        1.0 是 ``self.results_root / run_id``（``:4974``），``run_id`` 里带 ``..``
        就能读到别处的 JSON。这里把非法形状当成「这个 run 不存在」，与 1.0 对一个
        普通未知 run_id 的结局（404）一致。
        """
        if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
            raise KeyError(run_id)
        return self._root / run_id

    def iter_entries(self) -> Iterator[dict[str, Any]]:
        if not self._root.exists():
            return
        for path in sorted(self._root.glob("*/run.json")):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(state, dict) or not state.get("run_id"):
                continue
            yield run_list_entry(state)

    def load_state(self, run_id: str) -> dict[str, Any]:
        path = self._run_dir(run_id) / "run.json"
        if not path.is_file():
            raise KeyError(run_id)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise KeyError(run_id)
        return value

    def read_reviews(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            raise KeyError(run_id)
        path = run_dir / "reviews.json"
        if not path.is_file():
            return {"reviews": {}}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return {"reviews": value.get("reviews") or {}, "updated_at": value.get("updated_at")}
        except (OSError, json.JSONDecodeError, AttributeError):
            return {"reviews": {}}

    def read_gpu_sample_lines(self, run_id: str) -> list[str]:
        run_dir = self._run_dir(run_id)
        if not run_dir.is_dir():
            raise KeyError(run_id)
        path = run_dir / "gpu_samples.jsonl"
        if not path.is_file():
            return []
        return path.read_text(encoding="utf-8").splitlines()


class FileResultWriter:
    """``RunResultWriter`` 的文件实现：与 ``FileRunStore`` 同一个目录形状。

    **写 ``run.json`` 是原子的**（临时文件 + ``os.replace``，1.0 的 ``atomic_json``
    ``:576``）：run.json 是读侧唯一的入口，写到一半被读到就是「整个 run 损坏」。
    1.0 的 ``atomic_json`` 为什么不直接改 ``json.dump(f)``，就是这条。

    **``write_snapshot`` 不删 items**。1.0 的 ``_write_persisted_snapshot``（``:2049``）
    把 items 一起写进 run.json 与 results.jsonl 两份——447 MB 的 run.json 是从这里来的。
    2.0 的读侧已经能把这两份分开用（``analysis.list_summary`` 不碰 items），但**本切片
    不改变落盘格式**：换了格式，指到 1.0 ``results/`` 的那个能力就断了。体量的收敛是
    行为基线里登记的问题，不是这里顺手改的。
    """

    def __init__(self, results_root: Path) -> None:
        self._root = results_root

    def _run_dir(self, run_id: str) -> Path:
        """与 ``FileRunStore._run_dir`` 同一条路径穿越拦截。写入侧更需要它：
        读到一个越界的路径最多是读错文件，写进去是**覆盖别人的文件**。"""
        if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
            raise KeyError(run_id)
        return self._root / run_id

    def write_snapshot(self, run_id: str, state: dict[str, Any]) -> None:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        items = state.get("items") or []
        payload = json.dumps(state, ensure_ascii=False)
        target = run_dir / "run.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)
        (run_dir / "results.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in items), encoding="utf-8"
        )

    def reset_gpu_samples(self, run_id: str) -> None:
        path = self._run_dir(run_id) / "gpu_samples.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def append_gpu_sample(self, run_id: str, sample: dict[str, Any]) -> None:
        path = self._run_dir(run_id) / "gpu_samples.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")

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

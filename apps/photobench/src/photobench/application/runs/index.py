"""``results/`` 的进程内索引：一次后台扫描的进度与结果。

**它解决的是 1.0 的启动阻塞**（计划书 T914）。实测（2026-10-04，153 上 267 个
``run.json``、11.07 GB）：

===========================  =========
只读字节（不解析）全部         2.9 s
``json.loads`` 全部            75.0 s
单个最大 run.json（447 MB）    2.03 s
===========================  =========

差距全在解析。1.0 在 ``OrchestratorRepository.__init__``（``:4873``）里**同步**解析完
所有 run 才让 ``main`` 去绑端口——所以它要等到第 75 秒才可服务，进程里还同时压着
11 GB 的 JSON 对象。2.0 把同一件工作挪到**后台线程**：端口立刻绑，``GET /api/runs``
立刻能答（先少后多），扫描完成的那一刻起答案与 1.0 逐字段相同。

**「懒加载」被实测否掉了**，这是本设计里唯一一个被数据推翻的直觉：曾想「列表只要
元信息，那就跳过 ``items`` 字段只读其余键」，做法是逐字节扫括号配对。实测对那个
447 MB 的文件：``json.loads`` **2.03 s**，纯 Python 逐字节跳过 ``items`` 要 **24.8 s**
——CPython 的 C 解析器比 Python 循环快一个数量级，**跳过反而更贵**。所以照常解析，
只是把结果里 ``items`` 的部分丢掉（``run_list_entry`` 只留列表页要的字段）。

索引里**不保留 items**：那是「懒加载」真正的落点——单个 run 的完整状态由
``ReadRuns`` 按需加载并只缓存最近两个。
"""

from __future__ import annotations

import threading
from typing import Any

from photobench.domain.results.ports import RunStore


class RunIndex:
    """扫描进度 + 瘦条目表。

    S9b-1 时它是**只读**的（写侧属后续切片）；S9b-3 的写侧路由通过 ``upsert`` /
    ``remove`` 增量维护它——**增量而不是重扫**，见 ``upsert`` 的 docstring。
    """

    #: 1.0 ``:5014`` 的 ``dirty_statuses`` 里那几个「进程还在时才算活着」的状态。
    _ACTIVE_STATUSES = {"running", "pending", "cancelling"}

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._state = "pending"
        self._scanned = 0
        self._error: str | None = None

    def refresh(self, store: RunStore) -> None:
        """同步扫一遍。**调用方负责放进后台线程**（见 ``photobench.main``）。

        扫描中途单条 run 解析失败只跳过它（1.0 ``_load_existing`` 的
        ``except (OSError, KeyError, json.JSONDecodeError): continue``，``:5043``）：
        一个坏掉的 run 不该让另外 266 个也看不见。
        """
        with self._lock:
            self._state = "running"
        try:
            for entry in store.iter_entries():
                run_id = str(entry.get("run_id") or "")
                if not run_id:
                    continue
                with self._lock:
                    self._entries[run_id] = entry
                    self._scanned += 1
        except Exception as exc:  # noqa: BLE001 —— 扫描线程的兜底：状态要能报出来，不能静默死掉
            with self._lock:
                self._state = "failed"
                self._error = str(exc)
            return
        with self._lock:
            self._state = "ready"

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries.values())

    def upsert(self, entry: dict[str, Any]) -> None:
        """写侧在 run 结束时把新条目推进来（`RunSuites`）。

        **不重扫目录**：一次 suite 只会新增几个 run，重扫 267 个 ``run.json`` 是
        75 秒的工作（见本模块 docstring）。这条路径只补上刚变的那一个。
        """
        run_id = str(entry.get("run_id") or "")
        if not run_id:
            return
        with self._lock:
            self._entries[run_id] = entry

    def remove(self, run_id: str) -> None:
        """`DELETE /api/runs/{id}` 之后把条目从索引里去掉。"""
        with self._lock:
            self._entries.pop(run_id, None)

    def status(self) -> dict[str, Any]:
        """给 ``GET /api/runs`` 的 ``scan`` 字段：调用方据此知道答案是否还残缺。

        1.0 没有这个概念——它压根不回答，直到扫完。多这一个字段是为了让「先少后多」
        这件事**可观测**，否则前端会把「扫到一半」误当成「只有这么多 run」。
        """
        with self._lock:
            return {
                "state": self._state,
                "scanned": self._scanned,
                "total": len(self._entries),
                "error": self._error,
            }

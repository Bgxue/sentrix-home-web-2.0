"""``results/`` 读侧的端口：列出 run、读单个 run 的完整状态、读评审。

对应 1.0 ``OrchestratorRepository``（``:4872``）里 **只读** 的那几个入口：
``_load_existing``（``:5013``）/ ``list_runs``（``:5343``）/ ``get_run``（``:5354``）/
``get_reviews``（``:4976``）。

**为什么是「流式产出」而不是「一次返回一张表」。** 1.0 在 ``__init__`` 里把 ``results/``
下 **270 个 run 目录全部解析进内存**，然后才绑端口——实测（2026-10-04，267 个
``run.json``、11.07 GB）：**读字节只要 2.9 s，``json.loads`` 要 75.0 s**，差距全在解析。
1.0 因此要等到第 75 秒才开始服务，而进程里同时压着 11 GB 的 JSON 对象。
2.0 拆成两件事：``iter_entries`` 在**后台**逐个产出列表页要的瘦条目（见
``domain.runs.analysis.run_list_entry``，不带 items），``load_state`` 只在**某个 run
被真的点开时**才解析它。启动因此不必阻塞，内存也不必装下全部结果。

**条目里只有列表页需要的字段**，这是有意的：``get_run`` 要的完整状态（含 items）
走 ``load_state``，两者不共用一份缓存——共用就会把 447 MB 的单个 run 常驻在内存里。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RunStore(Protocol):
    """结果目录的只读面。**本切片不含写侧**（建 run / 取消 / 重跑 / 评审写入）。"""

    def iter_entries(self) -> Iterator[dict[str, Any]]:
        """逐个产出 run 的**瘦列表条目**（``run_list_entry`` 的结果）。

        顺序不作保证——``GET /api/runs`` 的排序（``started_at`` 倒序）在 application 里
        做，因为那是接口的排序契约，不是目录扫描的属性。
        """
        ...

    def load_state(self, run_id: str) -> dict[str, Any]:
        """解析该 run 的 ``run.json``（含 ``items``）。

        未登记的 run 抛 ``KeyError``——1.0 的 ``get_run`` / ``get_run_items`` 就是这么
        表达 404 的（``:5357`` / ``:6447``），HTTP 层把它映射成 404 + ``{"error": ...}``。
        """
        ...

    def read_reviews(self, run_id: str) -> dict[str, Any]:
        """读 ``reviews.json``；文件不存在或损坏时返回 ``{"reviews": {}}``。

        与 1.0 ``get_reviews``（``:4976``）逐字一致：损坏的评审文件**不当错误**，只当
        「还没有评审」——它是个只读的展示维度，一个坏文件不该让整个 run 打不开。
        run 不存在时抛 ``KeyError``。
        """
        ...

    def read_gpu_sample_lines(self, run_id: str) -> list[str]:
        """读 ``gpu_samples.jsonl`` 的**原始行**；文件不存在时返回空表。

        解析留给 ``domain.runs.analysis.attach_gpu_sample_history``——1.0 是逐行
        ``json.loads`` 且坏行跳过（``:5412-5417``），那条「坏行不是错误」的口径要与
        解析放在一起，不能拆到 IO 层。run 不存在时抛 ``KeyError``。
        """
        ...

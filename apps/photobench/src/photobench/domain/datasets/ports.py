"""数据集侧的只读端口：QA 元数据。

**为什么读侧需要它。** 1.0 的 ``get_run_items``（``:6441``）把每个 item 先过一遍
``_hydrate_qa_metadata``（``:4919``），用 **数据集里** 的 ``qa_id`` 元信息补齐历史 run
当时没存下的字段（``task_type`` / ``difficulty`` / ``answerability`` / ``tags`` …）。
那份元信息来自 ``data/*/manifest.json`` 指向的 qa ``.jsonl``，加上
``config/qa_task_actions.json`` 的 QA 集级默认值（``apply_task_action_defaults`` ``:1629``）。

所以「列出某个 run 的题目」这件事依赖数据集目录，**不是**纯结果读取。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class QaMetadataSource(Protocol):
    """``qa_id`` → 数据集侧元信息的只读映射。"""

    def metadata_by_qa_id(self) -> dict[str, dict[str, Any]]:
        """返回 ``{qa_id: {字段: 值}}``。

        1.0 在 ``OrchestratorRepository.__init__`` 里建这张表（``:4885``），一次扫描、
        进程内常驻；2.0 保持「进程内建一次」的语义，但由 application 在需要时惰性触发
        （见 ``application.runs.read_runs.ReadRuns.qa_metadata``），因为不使用题库元信息的
        列表页不该为它付一次全库扫描。
        """
        ...

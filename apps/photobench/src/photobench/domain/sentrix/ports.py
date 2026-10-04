"""``SentrixClient``：评测主链要主产品做的那几件事。

对应 1.0 ``benchmark_orchestrator.py`` 里散在各相位实现中的 ``request_json`` /
``upload_files`` 调用点，逐条映射到 2.0 的接口（**2.0 的，不是 1.0 的**）：

======================  ============================  ==========================
1.0 的调用点             2.0 的对应                     本端口的方法
======================  ============================  ==========================
``POST /api/memory-spaces``  **没有**：导入时内部 ensure   （相位里只记 id）
``POST /api/people/seed-batch``  ``/v1/people/seed``（单人）  ``seed_person``
``POST /api/relationships/batch``  ✅ 同路径加 ``/v1``    ``import_relationships``
``POST /api/import``      ``/v1/imports``（服务端目录）   ``import_album``
``POST /ingest-batches/{id}/complete``  **没有**：并入 imports  （相位里不做）
``GET /api/assets``       ✅ ``/v1/assets``               ``list_assets``
``POST /api/assistant/turn``  **两段式**：建会话 + 发起回合  ``start_conversation`` /
                                                          ``start_turn``
``GET /api/assistant/turn/{id}``  ``/turns/{turn_id}``     ``get_turn``
======================  ============================  ==========================

**三处形状差异不是口味，是 2.0 的接口事实（每一条都核过代码）：**

1. **导入按「服务端目录」而不是按「上传字节」**。``POST /api/v1/imports`` 收的是
   ``{directory, scope_id, limit}``，``ImportAlbum.execute`` 用 ``root.iterdir()``
   **非递归**扫后缀（``application/media/import_album.py:100-102``）。所以这个方法的
   入参是**目录**，返回体的键名沿用 2.0 的响应
   （``{scope_id, directory, imported, deduplicated, events, ...}``）。1.0 那种
   「manifest 列了哪些文件就传哪些」的能力 2.0 的端点给不了——**目录里的文件集合
   才是权威**，这条差异记在行为基线里。
2. **种身份是单人一次**。1.0 的 ``seed-batch`` 一次带 N 个人 + N 张参考图；2.0 的
   ``POST /people/seed`` 是 multipart 单人（``api/routes/identity.py:904``）。批量语义
   落在调用方（主链按 manifest 的 ``faces`` 逐个调），不在端口上——**端口照着 2.0 的
   端点形状长，不照着 1.0 的**（这是本切片的范围调整：直接按 2.0 写，S9c 不再返工）。
3. **问答是两段式**，而且回合是**异步**的：``start_turn`` 返回 ``turn_id`` 之后要靠
   ``get_turn`` 轮询到终态（``TurnStatus.is_terminal``，``domain/assistant/models.py:40``）。
   1.0 的 ``wait_for_assistant_turn``（``:909``）是同一条轮询，只是打的是单数路径。

**返回形状一律是「2.0 的响应体」，端口不做 1.0 归一化**：把 2.0 的 ``{"items": [...]}``
在适配器里拆成 list 是适配器的活（调用方只想要「有哪些资产」），但把
``imported`` 改名成 1.0 的 ``accepted_count`` 就是无中生有——那是行为差异，不是形状差异。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SentrixClient(Protocol):
    """主产品侧的一次评测主链所需的全部调用。"""

    def seed_person(
        self,
        *,
        scope_id: str,
        name: str,
        family_role: str,
        aliases: Sequence[str],
        file_name: str,
        content: bytes,
        timeout: int = 120,
    ) -> dict[str, Any]:
        """``POST /api/v1/people/seed``（1.0 ``:2691`` 的单人投影）。

        multipart 字段名与类型照 2.0：``name`` 必填，``family_role`` / ``aliases`` 可空，
        ``files`` 收参考图（``api/schemas/identity.py`` 的 ``Body_seed_person_identity_*``）。
        ``aliases`` 在 2.0 是**顿号/逗号分隔的一个字符串**，不是数组——逗号拼接由适配器做。
        至少要有 ``files`` 或 ``face_instance_ids`` 之一，否则 2.0 回 400。
        """
        ...

    def import_relationships(
        self,
        *,
        scope_id: str,
        relationships: Sequence[Mapping[str, Any]],
        entity_by_name: Mapping[str, str],
        timeout: int = 60,
    ) -> dict[str, Any]:
        """``POST /api/v1/relationships/batch``（1.0 ``:2713``）。逐条失败不中断。"""
        ...

    def import_album(self, *, scope_id: str, directory: str, limit: int, timeout: int) -> dict[str, Any]:
        """``POST /api/v1/imports``（1.0 ``:2760`` 的整册对应）。

        **同步长请求**：2.0 没有任务队列，这个调用会串行走完「落盘 → EXIF → 地理 →
        视觉向量 → 观察 → 人物 → 事件 → 事实 → 词法」整条链，再跑批末总结
        （``api/routes/timeline.py:130-142``）。超时必须由调用方给足。
        """
        ...

    def list_assets(self, *, scope_id: str, limit: int, timeout: int = 60) -> list[dict[str, Any]]:
        """``GET /api/v1/assets?scope_id=&limit=``，**拆掉响应信封后**的资产表。

        2.0 的响应是 ``{"items": [...]}`` 而 1.0 是 ``{"assets": [...]}``
        （``api/schemas/media.py:136``）——这个差异由适配器吸收，因为调用方用的是
        「一批资产」，不是某个信封的键名。资产条目**没有 ``created_at``**、文件名键叫
        ``file_name``（1.0 也是 ``file_name``）。
        """
        ...

    def start_conversation(self, *, scope_id: str, title: str = "", timeout: int = 30) -> dict[str, Any]:
        """``POST /api/v1/assistant/conversations``。会话 id 的字段名是 ``id``。

        1.0 没有这个调用——它的 ``POST /api/assistant/turn`` 自己拿 ``conversation_id``
        （``:3406`` 是本地 ``uuid4()``）。2.0 把会话变成**服务端实体**，回合的 scope 由
        会话承载（``api/routes/assistant.py:213``），所以必须先建会话。
        """
        ...

    def start_turn(self, *, conversation_id: str, message: str, timeout: int = 300) -> dict[str, Any]:
        """``POST /api/v1/assistant/conversations/{cid}/turns``，返回 ``{turn_id, status}``。

        **不是同步返回答案**：状态码 202，执行在后台线程。scope **不在请求体里**
        （``StartTurnRequest`` 只有 ``message``），它继承会话。
        """
        ...

    def get_turn(self, *, turn_id: str, timeout: int = 30) -> dict[str, Any]:
        """``GET /api/v1/assistant/turns/{turn_id}``（**复数**；1.0 是单数 ``:914``）。

        返回 ``{turn_id, status, public_progress, result, error}``。终态由调用方判
        （``pending`` / ``running`` 之外都是终态）——这个端口不替调用方决定要等多久。
        """
        ...

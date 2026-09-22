"""四个工具的执行器：把工具调用接到真正的检索、资产与人物目录上。

对应 1.0 ``agent_runtime/tools.py`` 的四个 handler（``_search_memories`` 1513 行起、
``_get_result_page`` 2044、``_get_original_photos`` 1966、``_get_person_profile`` 2651）。

**每个工具产出两份投影，这是本模块最重要的设计。** 1.0 在 handler 与模型之间夹了两层
过滤（``ToolPolicy._sanitize`` 的键白名单 + ``runtime.py:131-180`` 的
``_model_visible_observation``），当时没有人解释为什么需要两层。原因在 ``runtime.py:116``
的注释里：检索工具一旦把 ``total`` 交给模型，模型就会把候选池的数量当成统计答案报出去。
2.0 把这件事写成一份显式的 ``ToolOutcome``：

- ``observation``：内部用。含 ``asset_id``、``total``、``degraded`` 这些**不该给模型看**
  的东西，供门禁、账本与交付使用；
- ``model_visible``：发回模型的那一份。只有 handle、文件名、拍摄时间和一句「还有没有更多」。

两者的差集就是这个工具的信息边界。``ToolOutcome`` 上没有「把两份合并」的入口，所以
新增字段时必须先回答它属于哪一边。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sentrix.application.retrieval.retrieve_evidence import EvidenceRequest, RetrieveEvidence
from sentrix.domain.assistant.evidence_ledger import EvidenceType
from sentrix.domain.assistant.models import Attachment
from sentrix.domain.assistant.ports import PersonDirectory, ResultSetRepository
from sentrix.domain.assistant.result_set import ResultSet
from sentrix.domain.media.ports import AssetRepository

log = logging.getLogger("sentrix.assistant.tools")

DEFAULT_SEARCH_LIMIT = 12
MAX_SEARCH_LIMIT = 12


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    observation: Mapping[str, Any]
    model_visible: Mapping[str, Any]
    # 这次调用确认了「某类证据本轮拿不到」——账本据此封口，门禁不再逼模型重试。
    blocks: EvidenceType | None = None
    attachments: tuple[Attachment, ...] = ()
    failure_reason: str = ""

    @property
    def ok(self) -> bool:
        return not self.failure_reason


def _failure(reason: str, message: str) -> ToolOutcome:
    """失败也是一份正常的观察。1.0 用异常表达工具失败，再由
    ``ToolPolicy.execute`` 的 ``except Exception`` 兜成一条 decision——那样工具内部的
    真实原因（参数写错 vs 结果集过期）就糊成了一句 ``tool_execution_error``。"""
    return ToolOutcome(
        observation={"failure_reason": reason},
        model_visible={"error": reason, "message": message},
        failure_reason=reason,
    )


def _clamp_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SEARCH_LIMIT
    return min(MAX_SEARCH_LIMIT, max(1, parsed))


class AssistantToolbox:
    def __init__(
        self,
        *,
        retrieval: RetrieveEvidence,
        assets: AssetRepository,
        result_sets: ResultSetRepository,
        people: PersonDirectory,
    ) -> None:
        self._retrieval = retrieval
        self._assets = assets
        self._result_sets = result_sets
        self._people = people

    def execute(
        self, name: str, arguments: Mapping[str, Any], *, scope_id: str, now: datetime
    ) -> ToolOutcome:
        if name == "search_memories":
            return self._search(arguments, scope_id=scope_id, now=now)
        if name == "get_result_page":
            return self._page(arguments, scope_id=scope_id)
        if name == "get_original_photos":
            return self._deliver(arguments, scope_id=scope_id)
        if name == "get_person_profile":
            return self._person(arguments, scope_id=scope_id)
        return _failure("unknown_tool", f"没有名为 {name} 的工具。")

    # ---- search_memories ----------------------------------------------------

    def _search(self, arguments: Mapping[str, Any], *, scope_id: str, now: datetime) -> ToolOutcome:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return _failure("empty_query", "query 不能为空，请用用户问题里的实词。")

        result = self._retrieval.execute(
            EvidenceRequest(text=query, scope_id=scope_id, limit=_clamp_limit(arguments.get("limit")))
        )
        result_set = ResultSet(
            id=f"rs_{uuid.uuid4().hex[:12]}",
            scope_id=scope_id,
            query=query,
            asset_ids=tuple(hit.asset_id for hit in result.hits),
            created_at=now,
        )
        self._result_sets.add(result_set)
        return self._page_outcome(
            result_set,
            page_no=1,
            extra={
                "backend": result.backend,
                "degraded": result.degraded,
                "degraded_reason": result.degraded_reason,
                "authoritative_vectors": result.authoritative_vectors,
            },
        )

    # ---- get_result_page ----------------------------------------------------

    def _page(self, arguments: Mapping[str, Any], *, scope_id: str) -> ToolOutcome:
        result_set = self._result_sets.get(str(arguments.get("result_set_id") or ""))
        if result_set is None:
            return _failure("unknown_result_set", "结果集不存在。请重新调用 search_memories。")
        if result_set.scope_id != scope_id:
            return _failure("scope_mismatch", "这个结果集不属于当前相册。")
        try:
            page_no = max(1, int(arguments.get("page") or 1))
        except (TypeError, ValueError):
            page_no = 1
        return self._page_outcome(result_set, page_no=page_no)

    def _page_outcome(
        self, result_set: ResultSet, *, page_no: int, extra: Mapping[str, Any] | None = None
    ) -> ToolOutcome:
        page = result_set.page(page_no)
        preview = [self._preview(entry.handle, entry.asset_id) for entry in page.entries]
        model_visible: dict[str, Any] = {
            "result_set_id": result_set.id,
            "query": result_set.query,
            "page": page.page,
            "page_size": page.page_size,
            "has_more": page.has_more,
            "preview": preview,
        }
        # ``total`` 刻意**不进** model_visible：1.0 实测过模型会把候选池的数量直接当成
        # 答案里的统计数字（``runtime.py:116``）。模型要判断「还有没有更多」看 has_more
        # 就够了，那是个布尔，不会被当成计数。
        observation: dict[str, Any] = {
            "result_set_id": result_set.id,
            "query": result_set.query,
            "total": page.total,
            "has_more": page.has_more,
            "asset_ids": list(result_set.asset_ids),
        }
        if extra:
            observation.update(extra)
        return ToolOutcome(observation=observation, model_visible=model_visible)

    def _preview(self, handle: str, asset_id: str) -> Mapping[str, Any]:
        asset = self._assets.get(asset_id)
        return {
            "handle": handle,
            "file_name": asset.file_name if asset else "",
            "captured_at": asset.captured_at.isoformat() if asset and asset.captured_at else "",
        }

    # ---- get_original_photos ------------------------------------------------

    def _deliver(self, arguments: Mapping[str, Any], *, scope_id: str) -> ToolOutcome:
        result_set = self._result_sets.get(str(arguments.get("result_set_id") or ""))
        if result_set is None:
            return _failure("unknown_result_set", "结果集不存在。请先调用 search_memories。")
        if result_set.scope_id != scope_id:
            return _failure("scope_mismatch", "这个结果集不属于当前相册。")

        raw_handles = arguments.get("handles")
        if not isinstance(raw_handles, (list, tuple)) or not raw_handles:
            return _failure("missing_handle", 'handles 必须是非空的 handle 数组，例如 ["photo_1"]。')

        attachments: list[Attachment] = []
        rejected: list[str] = []
        for item in raw_handles:
            handle = str(item or "").strip()
            asset_id = result_set.resolve(handle)
            if asset_id is None:
                rejected.append(handle)
                continue
            asset = self._assets.get(asset_id)
            if asset is None:
                # 向量还在、资产没了。产品硬约束第 5 条：只返回**可打开的**原图。
                log.warning("交付的 handle 指向一个已不存在的资产，已丢弃: %s", asset_id)
                rejected.append(handle)
                continue
            attachments.append(Attachment(handle=handle, asset_id=asset_id, file_name=asset.file_name))

        if not attachments:
            return _failure(
                "bad_handle",
                "这些 handle 都不在当前结果集里。请只用 search_memories / get_result_page 返回的 handle。",
            )
        return ToolOutcome(
            observation={
                "result_set_id": result_set.id,
                "delivered": len(attachments),
                "rejected": rejected,
            },
            model_visible={
                "result_set_id": result_set.id,
                "delivered": len(attachments),
                "rejected": rejected,
                "note": "这些照片已经展示给用户了。",
            },
            attachments=tuple(attachments),
        )

    # ---- get_person_profile -------------------------------------------------

    def _person(self, arguments: Mapping[str, Any], *, scope_id: str) -> ToolOutcome:
        name = str(arguments.get("person") or "").strip()
        if not name:
            return _failure("empty_person", "person 不能为空。")
        profile = self._people.lookup(name, scope_id)
        if profile is None or not profile.confirmed:
            return ToolOutcome(
                observation={"person": name, "found": False},
                model_visible={
                    "person": name,
                    "found": False,
                    "note": "没有找到这位已确认人物的档案，请如实说明无法确认，不要编造。",
                },
                # 身份层还没建起来（S5）。封口而不是让门禁反复逼模型去查一个查不到的东西。
                blocks=EvidenceType.PERSON_PROFILE,
            )
        return ToolOutcome(
            observation={"person": profile.name, "found": True, "family_role": profile.family_role},
            model_visible={"person": profile.name, "family_role": profile.family_role},
        )


def asset_ids_of(outcome: ToolOutcome) -> Sequence[str]:
    """内部投影里的资产 id。交付与统计走这里，不走 ``model_visible``。"""
    raw = outcome.observation.get("asset_ids")
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def is_empty_result(outcome: ToolOutcome) -> bool:
    total = outcome.observation.get("total")
    return isinstance(total, int) and total == 0


@dataclass(frozen=True, slots=True)
class DeliveredPhotos:
    """本回合累计交付的照片。只由模型**显式**调用 ``get_original_photos`` 产生——
    1.0 的交付口径（``runtime.py:1404``）明确不做回填：「模型没选、或答案本就没有依据图
    （拒答/闲聊）→ 交付为空，这是合法状态」。"""

    items: tuple[Attachment, ...] = field(default_factory=tuple)

    def merge(self, attachments: Sequence[Attachment]) -> DeliveredPhotos:
        seen = {item.asset_id for item in self.items}
        return DeliveredPhotos(
            items=self.items + tuple(item for item in attachments if item.asset_id not in seen)
        )

    @property
    def asset_ids(self) -> tuple[str, ...]:
        return tuple(item.asset_id for item in self.items)

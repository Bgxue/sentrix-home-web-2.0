"""五个工具的执行器：把工具调用接到真正的检索、资产、人物目录与人脸绑定上。

对应 1.0 ``agent_runtime/tools.py`` 的五个 handler（``_search_memories`` 1513 行起、
``_get_result_page`` 2044、``_get_original_photos`` 1966、``_get_person_profile`` 2651、
``_query_photo_people`` 2555）。

**交付不在这里的工具表里。** 1.0 的 ``get_original_photos`` 工具在生产上被调用 **0 次**
——239 道图片题全部靠 ``final`` 动作的 ``selected_image_handles`` 字段交付。2.0 一度把它
做成必须单独调用的一次工具，代价是多一个模型步、而模型并没有因此更愿意交付；现在按
1.0 的形状收回到终止动作的字段上（``deliver()`` 只被收尾分支调用，不接 ``execute()``）。

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

import dataclasses
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sentrix.application.assistant.query_memory_facts import QueryMemoryFacts
from sentrix.application.retrieval.retrieve_evidence import EvidenceRequest, RetrieveEvidence
from sentrix.domain.assistant.evidence_ledger import EvidenceType
from sentrix.domain.assistant.models import Attachment
from sentrix.domain.assistant.ports import PersonDirectory, ResultSetRepository
from sentrix.domain.assistant.result_set import ResultSet
from sentrix.domain.identity.ports import FaceRepository, PersonCropRenderer
from sentrix.domain.media.inspection import CERTAINTY_SUPPORTED
from sentrix.domain.media.ports import AssetRepository, ObservationRepository, PhotoInspector
from sentrix.domain.retrieval.fact_query import FactRejection

log = logging.getLogger("sentrix.assistant.tools")

# 1.0 ``tools.py:2125`` 的 ``arguments.get("question") or "请描述这张照片"``。
DEFAULT_INSPECT_QUESTION = "请描述这张照片"

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
        memory_facts: QueryMemoryFacts,
        faces: FaceRepository,
        observations: ObservationRepository,
        photo_inspector: PhotoInspector,
        person_crops: PersonCropRenderer,
    ) -> None:
        self._retrieval = retrieval
        self._assets = assets
        self._result_sets = result_sets
        self._people = people
        self._memory_facts = memory_facts
        # ``query_photo_people`` 的两条读路径（T122）。两者都是**只读**，
        # 且都只被这一个工具用——不是通用能力。
        self._faces = faces
        self._observations = observations
        # ``inspect_photo`` 的两件（T123）：把问题问给图的那一层，与目标人物的
        # 头肩裁剪件。裁剪走端口而不是在这里 import PIL——domain-purity 把 PIL 点名禁了。
        self._photo_inspector = photo_inspector
        self._crops = person_crops

    def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        scope_id: str,
        now: datetime,
        question: str = "",
    ) -> ToolOutcome:
        if name == "search_memories":
            return self._search(arguments, scope_id=scope_id, now=now)
        if name == "get_result_page":
            return self._page(arguments, scope_id=scope_id)
        if name == "query_photo_people":
            return self._photo_people(arguments, scope_id=scope_id)
        if name == "inspect_photo":
            return self._inspect_photo(arguments, scope_id=scope_id, question=question)
        if name == "read_photo_text":
            return self._read_photo_text(arguments, scope_id=scope_id)
        if name == "get_person_profile":
            return self._person(arguments, scope_id=scope_id)
        if name == "query_memory_facts":
            return self._facts(arguments, scope_id=scope_id, now=now)
        return _failure("unknown_tool", f"没有名为 {name} 的工具。")

    # ---- query_memory_facts -------------------------------------------------

    def _facts(self, arguments: Mapping[str, Any], *, scope_id: str, now: datetime) -> ToolOutcome:
        """精确统计。**与检索工具相反：这里的 ``total`` 是答案本身，要给模型看。**

        ``is_empty_result`` 读的是 ``observation["total"]``，看上去会让「一共 0 张」
        被当成「检索没找到」。不会——``final_guard`` 先按 ``RETRIEVAL_TOOLS`` 过滤工具名，
        而本工具不在那个集合里，所以「拒绝」与「空结果」在那一边是分开的。
        """
        answer = self._memory_facts.execute(
            arguments,
            scope_id=scope_id,
            viewer_id="owner",
            conversation_id="tool_loop",
            now=now,
        )
        if isinstance(answer, FactRejection):
            return ToolOutcome(
                observation={"status": answer.status, "reason": answer.reason},
                model_visible={"status": answer.status, "reason": answer.reason},
                failure_reason=answer.reason,
            )
        return ToolOutcome(
            observation={
                "fact_type": answer.fact_type,
                "value": answer.value,
                "total": answer.total,
                "filters_applied": dict(answer.filters_applied),
            },
            model_visible=answer.visible(),
        )

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

    # ---- 交付（终止动作上的一个字段）-----------------------------------------

    def deliver(self, result_set_id: str, handles: Sequence[Any], *, scope_id: str) -> ToolOutcome:
        """把终止动作里的 ``selected_image_handles`` 解成可打开的附件。

        **它不是一次工具调用**：调用方是 ``run_turn`` 的收尾分支，交付与作答在同一步里
        完成（1.0 ``runtime.py:309`` 的 ``final`` 动作字段）。走 ``execute()`` 那条路
        等于把它重新变成一次独立的工具调用——那正是这一版要修掉的东西。
        """
        result_set = self._result_sets.get(result_set_id)
        if result_set is None:
            return _failure("unknown_result_set", "结果集不存在。请先调用 search_memories。")
        if result_set.scope_id != scope_id:
            return _failure("scope_mismatch", "这个结果集不属于当前相册。")

        raw_handles = handles
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

    # ---- query_photo_people -------------------------------------------------

    def _photo_people(self, arguments: Mapping[str, Any], *, scope_id: str) -> ToolOutcome:
        """一张照片自己的已确认人物 + 未确认同行者。1.0 ``tools.py:2555-2608``。

        **这个工具只回答「这张照片里有谁」，不做跨照片的人名搬运**——1.0 的工具描述
        把这条写死了（``:2724``），因为模型很容易从候选集里另一张照片借一个名字过来。

        三处判据逐条照 1.0：

        1. ``resolve`` 失败与「结果集不属于本 scope」都**各自**回一句——1.0 是
           ``bad_handle`` 与 ``scope_mismatch`` 两个码，合起来会让「handle 打错了」
           与「结果集过期了」在轨迹里长得一样；
        2. 未确认同行者 = ``people_json`` 里**没有 entity_id** 的条目数
           ``- 已绑定的脸数``，下限 0。减这一下是必须的：命名流程会把已确认的人
           **追加**进同一份快照，不减就会把已经认出来的人再数一遍
           （1.0 ``:2582-2588`` 的注释写的就是这件事）；
        3. ``summary`` 那一句是给模型看的，未确认时明说「没有已确认姓名」，
           而不是留空——空串会让模型以为这个字段坏了。
        """
        result_set_id = str(arguments.get("result_set_id") or "").strip()
        handle = str(arguments.get("asset_handle") or "").strip()
        result_set = self._result_sets.get(result_set_id)
        if result_set is None:
            return _failure("unknown_result_set", "结果集不存在。请先调用 search_memories。")
        if result_set.scope_id != scope_id:
            return _failure("scope_mismatch", "这个结果集不属于当前相册。")

        asset_id = result_set.resolve(handle)
        if asset_id is None:
            return _failure(
                "bad_handle",
                "这个 handle 不在当前结果集里。请只用 search_memories / get_result_page 返回的 handle。",
            )

        identities = self._faces.list_photo_identities(asset_id)
        snapshot = self._observations.latest_people_snapshot(asset_id)
        described = sum(1 for entry in snapshot if not entry.entity_id)
        unconfirmed = max(0, described - len(identities))

        named = [
            {
                "person_name": row.person_name,
                # 端口那层叫 ``family_role``，工具契约那一格叫 ``membership``（1.0 的名字）。
                # 映射写在这里，不写进仓储——仓储不该知道助手工具的字段名。
                "membership": row.family_role or "unknown",
                "identity_status": "bound",
                "asset_id": row.asset_id,
            }
            for row in identities
            if row.person_name
        ]
        summary = "、".join(row["person_name"] for row in named) or "没有已确认姓名"
        if unconfirmed:
            summary += f"；另有 {unconfirmed} 名未确认身份同行者"

        return ToolOutcome(
            observation={
                "result_set_id": result_set.id,
                "asset_id": asset_id,
                "people": named,
                "unconfirmed_people_count": unconfirmed,
                "described_people_count": described,
            },
            model_visible={
                "result_set_id": result_set.id,
                "people": [
                    {
                        "person_name": row["person_name"],
                        "membership": row["membership"],
                        "identity_status": row["identity_status"],
                    }
                    for row in named
                ],
                "unconfirmed_people_count": unconfirmed,
                "summary": summary,
            },
        )

    # ---- inspect_photo ------------------------------------------------------

    def _inspect_photo(self, arguments: Mapping[str, Any], *, scope_id: str, question: str) -> ToolOutcome:
        """拿一个问题去复核一张照片。1.0 ``tools.py:2123-2267``。

        **这个工具的产出是「一次观察」，不是一条记忆**：1.0 的返回里写死
        ``"persisted": False``（``:2266``）——它看一眼就完了，不落库、不产生新事实。
        2.0 的 ``ToolOutcome`` 没有 ``persisted`` 这一格：**不落库是默认行为**，
        没有第二个工具会顺手落，所以那个字段在这里是多余的。

        三处与 1.0 的差别，都是 2.0 的结构决定的：

        1. **裁剪件的坐标不做归一化。** 1.0 有一段 ``max(bbox) <= 1.0`` 就按图宽高换算的
           防御（``:2178-2183``），而它要 PIL 才能拿到图宽高——2.0 的 domain-purity
           不许 application 碰 PIL。**2.0 的框本来就是像素**（``observe_appearance``
           把 ``instance.bbox`` 直接交给 ``render_person_crop``，没有换算），
           所以那一段在 2.0 没有对应的输入；真来了归一化的框，
           ``render_person_crop`` 会抛 ``ValueError``，落进下面那个 ``not_located``。
        2. **``target_person`` 的来源少一路。** 1.0 还看 ``task_state.active_person``
           （``:2127``），2.0 的 ``TaskState`` 没有这一格——**不补**，那是 1.0 主循环的
           可变状态，与「策略无状态」的分界冲突。留下的两路是显式参数与「问题里提到的
           已确认人名」（``:2162-2165``），后者才是常用的那一路。
        3. **模型不可用与文件不可用会封口 ``VISUAL_OBSERVATION``。** 1.0 只回一个
           ``blocked`` 列表，不做别的；2.0 的账本认这个封口，好让门禁不再逼模型重试
           一件这一轮拿不到的东西。``bad_handle`` **不封**——换一张图还有机会。
        """
        result_set_id = str(arguments.get("result_set_id") or "").strip()
        result_set = self._result_sets.get(result_set_id)
        if result_set is None:
            return _failure("unknown_result_set", "结果集不存在。请先调用 search_memories。")
        if result_set.scope_id != scope_id:
            return _failure("scope_mismatch", "这个结果集不属于当前相册。")

        # 1.0 ``:2128-2131``：不给 handle 就用预览第一张。``photo_1`` 就是预览第一张
        # （``ResultSet.resolve`` 的序号口径），所以这里不必先去翻一页。
        handle = str(arguments.get("asset_handle") or "").strip() or "photo_1"
        asset_id = result_set.resolve(handle)
        if asset_id is None:
            return _failure(
                "bad_handle",
                "这个 handle 不在当前结果集里。请只用 search_memories / get_result_page 返回的 handle。",
            )

        asset = self._assets.get(asset_id)
        if asset is None or not Path(asset.path).is_file():
            return ToolOutcome(
                observation={"failure_reason": "file_unavailable", "asset_id": asset_id},
                model_visible={"error": "file_unavailable", "message": "照片文件不可用。"},
                failure_reason="file_unavailable",
                blocks=EvidenceType.VISUAL_OBSERVATION,
            )

        asked = str(arguments.get("question") or "").strip() or DEFAULT_INSPECT_QUESTION
        identities = self._faces.list_photo_identities(asset_id)
        manifest = self._faces.list_face_manifest(asset_id)
        described = sum(
            1 for entry in self._observations.latest_people_snapshot(asset_id) if not entry.entity_id
        )

        target_person = str(arguments.get("target_person") or "").strip()
        if not target_person:
            # 1.0 ``:2162-2165``：问题（或用户目标）里提到的、且这张照片里已确认的人。
            mentioned = question + asked
            target_person = next(
                (row.person_name for row in identities if row.person_name and row.person_name in mentioned),
                "",
            )

        target_identity = next(
            (row for row in identities if target_person and row.person_name == target_person), None
        )
        target_face = next(
            (row for row in manifest if target_person and row.person_name == target_person), None
        )

        crop_path: str | None = None
        target_status = "not_requested"
        target_bbox: list[float] | None = None
        if target_person:
            target_status = "not_located"
            if target_identity is not None and target_identity.bbox:
                try:
                    crop_path, crop_bbox = self._crops.render(asset.path, target_identity.bbox)
                    target_status = "located"
                    target_bbox = list(crop_bbox)
                except (OSError, ValueError, TypeError):
                    # 1.0 ``:2186-2200``：裁不出来**不让整次检查失败**——原图照样发出去，
                    # 提示词里那句「第一张图（如有）」就是为这一路写的。
                    crop_path = None
                    target_bbox = None

        try:
            parsed = self._photo_inspector.inspect(
                asset.path,
                asked,
                faces=[dataclasses.asdict(row) for row in manifest],
                target_person=target_person,
                crop=crop_path,
            )
        finally:
            # 临时件由调用方删（``render_person_crop`` 的契约）。
            if crop_path:
                Path(crop_path).unlink(missing_ok=True)

        if parsed is None:
            return ToolOutcome(
                observation={"failure_reason": "model_unavailable", "asset_id": asset_id},
                model_visible={"error": "model_unavailable", "message": "模型不可用，这次没能复核这张照片。"},
                failure_reason="model_unavailable",
                blocks=EvidenceType.VISUAL_OBSERVATION,
            )

        # 1.0 ``:2242-2248``：没人脸时退回「快照里说不上名字的条目数」。
        # 有清单就以清单为准——清单是**这次真的检出来的脸**，比模型当时写的描述更可信。
        unconfirmed = (
            max(0, len(manifest) - len(identities)) if manifest else max(0, described - len(identities))
        )
        photo_identities = [
            {
                "person_name": row.person_name,
                "membership": row.family_role or "unknown",
                "identity_status": "bound",
            }
            for row in identities
            if row.person_name
        ]
        face_candidates = [
            {
                "face_id": row.face_id,
                "identity_status": row.identity_status,
                "person_name": row.person_name,
            }
            for row in manifest
        ]
        certainty = str(parsed.get("certainty") or CERTAINTY_SUPPORTED)
        return ToolOutcome(
            observation={
                "result_set_id": result_set.id,
                "asset_handle": handle,
                "asset_id": asset_id,
                "question": asked,
                "certainty": certainty,
                "selected_face_id": str(parsed.get("target_face_id") or ""),
                "target_person": target_person,
                "target_face_id": target_face.face_id if target_face else "",
                "target_face_status": target_status,
                "target_bbox": target_bbox,
                "confirms_visual_only": not identities,
                "unconfirmed_people_count": unconfirmed,
            },
            model_visible={
                "asset_handle": handle,
                "question": asked,
                "observation": str(parsed.get("observation") or parsed.get("scene") or ""),
                "certainty": certainty,
                "target_person": target_person,
                "target_face_status": target_status,
                "photo_identities": photo_identities,
                "face_candidates": face_candidates,
                "unconfirmed_people_count": unconfirmed,
            },
        )

    # ---- read_photo_text ----------------------------------------------------

    def _read_photo_text(self, arguments: Mapping[str, Any], *, scope_id: str) -> ToolOutcome:
        """读这张照片**已经存下来的**图内文字。1.0 ``ocr_tool.py:544-559``。

        **这条路是 1.0 的第一条路，也是它唯一跑过的那条之外剩下的那条。**
        1.0 的 ``_ocr_single_asset`` 在任何 OCR 引擎之前先查 ``observations.ocr_text``，
        命中就返回、零推理；2.0 的导入期 ``OpenAIVlmObserver`` 本来就往这一列写
        「图中文字，1000 字内」，所以这一路在 2.0 一直有数据。

        **没有第二条路。** 1.0 的模块注释承诺「读不到回退 VLM」，而那两个 VLM OCR 提示词
        （``_OCR_PROMPT`` / ``_OCR_PROMPT_FULL``）在全仓**从未被引用过**，``ocr_tool.py``
        整条链上一次模型调用都没有——那段回退是死代码（2026-09-30 实测，记在
        ``behaviour-baseline.md``）。1.0 真正跑的是 PaddleOCR，而 2.0 没有它。
        所以这里**只有**「读已落库的那份」，读不到就如实说读不到。

        **读不到不是「图上没有字」。** 这条区分很重要：模型拿到一个空的 ``full_text``
        会倾向于回答「照片里没有文字」。返回的措辞专门挡住了它。
        """
        result_set_id = str(arguments.get("result_set_id") or "").strip()
        result_set = self._result_sets.get(result_set_id)
        if result_set is None:
            return _failure("unknown_result_set", "结果集不存在。请先调用 search_memories。")
        if result_set.scope_id != scope_id:
            return _failure("scope_mismatch", "这个结果集不属于当前相册。")

        handle = str(arguments.get("asset_handle") or "").strip() or "photo_1"
        asset_id = result_set.resolve(handle)
        if asset_id is None:
            return _failure(
                "bad_handle",
                "这个 handle 不在当前结果集里。请只用 search_memories / get_result_page 返回的 handle。",
            )

        text = self._observations.latest_ocr_text(asset_id)
        if not text:
            return _failure(
                "no_text_detected",
                "这张照片里没有存下文字。可能是画面里本来就没有字，也可能是导入时没读出来"
                "——不要据此断言照片里没有字。",
            )
        return ToolOutcome(
            observation={
                "result_set_id": result_set.id,
                "asset_handle": handle,
                "asset_id": asset_id,
                "source": "ingestion_ocr",
                "full_text": text,
            },
            model_visible={
                "asset_handle": handle,
                # 1.0 ``ocr_tool.py:551`` 的 ``offline_text[:1600]``——上限照抄。
                "full_text": text[:1600],
                # 1.0 ``:552-553``：最多 8 行、每行 150 字。1.0 的 ``text_regions``
                # 是给界面画框用的，2.0 没有那个界面，但**保留这一格**：它是「哪些行是
                # 真的读出来的」的证据，模型据此判断文本完整性比看一坨全文准。
                "text_regions": [
                    {"text": line[:150], "source": "ingestion_ocr"} for line in text.splitlines()[:8]
                ],
                "certainty": CERTAINTY_SUPPORTED,
                "source": "ingestion_ocr",
                "note": "这些字是导入时从画面里读出来并保存的。",
            },
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
            # 两份投影的差集就是这个工具的信息边界（见模块 docstring）：内部那份多一个
            # 「有几条关系」——那是给账本与门禁看的量，模型不需要它，给了反而会拿数量
            # 当答案。关系本身逐条进 model_visible，因为模型要答的就是「谁是谁的谁」。
            observation={
                "person": profile.name,
                "found": True,
                "membership": profile.membership,
                "relationship_count": len(profile.relationships),
            },
            model_visible={
                "person": profile.name,
                "membership": profile.membership,
                "membership_source": profile.membership_source,
                "summary": profile.summary,
                "relationships": [
                    {
                        "predicate": item.predicate,
                        "other_name": item.other_name,
                        "source": item.source,
                    }
                    for item in profile.relationships
                ],
            },
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
    """本回合累计交付的照片。只由模型在终止动作里**显式**填的 ``selected_image_handles``
    产生——1.0 的交付口径（``runtime.py:1404``）明确不做回填：「模型没选、或答案本就没有
    依据图（拒答/闲聊）→ 交付为空，这是合法状态」。"""

    items: tuple[Attachment, ...] = field(default_factory=tuple)

    def merge(self, attachments: Sequence[Attachment]) -> DeliveredPhotos:
        seen = {item.asset_id for item in self.items}
        return DeliveredPhotos(
            items=self.items + tuple(item for item in attachments if item.asset_id not in seen)
        )

    @property
    def asset_ids(self) -> tuple[str, ...]:
        return tuple(item.asset_id for item in self.items)

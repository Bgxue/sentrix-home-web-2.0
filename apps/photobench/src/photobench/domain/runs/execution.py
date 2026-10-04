"""run-execution 主链的**全部纯计算**（S9b-2）。

与 ``domain.runs.analysis`` 的分工：``analysis`` 是**读侧**（把已落盘的 run 变成列表页
与详情页要的东西），这里是**写侧**（把主产品的一次问答变成要落盘的 item）。
两者都落在 domain，因为两侧都不碰 IO，而 infrastructure 的结果写入要能用这里的形状。

**这一层与 1.0 的关系是一半照搬、一半重算，分界线是「2.0 是否还暴露那个信息」：**

照搬（2.0 有等价物的）：

- ``normalize_media_refs``  1.0 ``:955``——题面里的 GT 媒体引用，两代都是数据集里的字段。
- ``resolve_predicted_media``  1.0 ``:1129``——把 asset_id 解成带类型/文件名的记录。
- ``resolve_gt_media``  1.0 ``:1364``——把 GT 引用按已入库资产解出映射状态。
- ``modality_metrics``  1.0 ``:1206``——GT 与交付集的 precision/recall/f1。
- ``safe_slug`` / ``album_media_entries``  1.0 ``:1581`` / ``:1587``。

**不能照搬、只能重算的**（1.0 有、2.0 根本没有的信息）：

1.0 的 item 里有一整套模型侧轨迹——``model_call_metrics``（每次调用的 ttft/token）、
``execution_trace`` / ``tool_trace``（工具入参、结果、``debug_asset_ids``）、
``retrieval_trace``、``agent2_trace``、``answer_grounding``、``tool_perf``，
以及由它们算出的 ``image_ranking`` / ``timing_breakdown``。**2.0 一个都没有**：
``GET /assistant/turns/{id}`` 只给 ``{status, termination_reason, answer, attachments,
requirements, public_progress}``，``public_progress`` 的 payload 里只有**工具名与
tool_call_id**，没有参数、没有结果、没有 asset_id、没有耗时、没有 token
（S9b-2 逐行核过 ``domain/assistant/task_state.py`` 的 ``emit`` 与 ``run_turn.py`` 的
全部发射点，并实跑一轮确认）。2.0 也没有任何别的端点能把它们读出来——
``agent_turn_events`` 表的唯一读取者是这两个轮询/SSE 端点。

所以本层**不发明**这些字段：``predicted_media`` 与召回指标只对齐到 2.0 真的给了的
**交付集**（``result.attachments[].asset_id``），``retrieved_candidate_media`` /
``evidence_source_media`` / ``image_ranking`` **留空并说明原因**——把候选集与交付集
混成一个数字，正是 1.0 ``_extract_media_sets`` 那段注释反复警告的事；而在 2.0 里
连候选集都拿不到，编一个只会让指标看起来完整。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from photobench.domain.runs.analysis import (
    VIDEO_EXTENSIONS,
    infer_media_type,
    media_key,
    metric_triplet,
)

#: 2.0 的 ``POST /api/v1/imports`` 用 ``Path.iterdir()`` **非递归**扫目录
#: （``application/media/import_album.py:100-102``），所以导入的单位是「一个扁平目录」，
#: 不是「一份 manifest 里的文件清单」。这条是由 2.0 的端点形状决定的，不是选择。
IMPORT_DIRECTORY_IS_FLAT = True


def safe_slug(value: str, fallback: str = "x") -> str:
    """1.0 ``:1581``。给 scope 名与 run 目录名用，所以只留 ``[a-z0-9._-]``。"""
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._").lower()
    return slug or fallback


def album_media_entries(manifest: dict[str, Any]) -> list[str]:
    """1.0 ``:1587``：manifest 里 photo 在前、video 在后，去重保序。"""
    entries: list[str] = []
    seen: set[str] = set()
    for key in ("photos", "videos"):
        for item in manifest.get(key) or []:
            relative = str(item).strip()
            if relative and relative not in seen:
                seen.add(relative)
                entries.append(relative)
    return entries


def import_directories(manifest: dict[str, Any]) -> list[str]:
    """manifest 的媒体条目落在哪几个**扁平目录**里（相对路径，保序）。

    2.0 的整册导入一次只吃一个目录，而一个 album 的 manifest 里 photos 与 videos
    可能在不同的子目录（``photos/`` 与 ``videos/``）。所以主链按这个函数的返回值
    **逐目录导一次**，scope 相同、结果累加——1.0 没有这一步，它的 ``/api/import``
    是按文件收的，2026-10-04 的范围调整把这一层差异落在这里。

    没有条目的 album 返回空表（主链据此判「没有可导的东西」，与 1.0
    ``_phase_photo_import`` 对 ``missing`` 的处理是同一条路）。
    """
    directories: list[str] = []
    seen: set[str] = set()
    for relative in album_media_entries(manifest):
        parent = str(Path(relative).parent)
        if parent in {".", ""}:
            parent = ""
        if parent not in seen:
            seen.add(parent)
            directories.append(parent)
    return directories


def index_assets_by_name(assets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """1.0 ``_phase_qa_eval`` ``:3108``：按**文件名 basename** 归组。

    用 list 而不是单值：同一张照片在同一个 scope 里只该有一行，但跨 scope 查询
    （``scope_id`` 为空）会拿到重名——那时 ``resolve_gt_media`` 会把映射标成
    ``ambiguous`` 而不是随手挑一张。
    """
    index: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        name = Path(str(asset.get("file_name") or "")).name
        index.setdefault(name, []).append(asset)
    return index


def normalize_media_refs(record: dict[str, Any], prefix: str = "retrieval") -> list[dict[str, str]]:
    """1.0 ``:955``：``{prefix}_media_refs`` 优先，否则从 ``_image_ids`` / ``_video_ids`` 升级。

    ``_video_ids`` 那一路显式给 ``"video"`` 类型——那是 1.0 的兼容路径，光看 id 猜不出
    是视频（视频 id 常常就是一个普通文件名）。这条不能省：2.0 的评测题库还是同一批
    ``.jsonl``，老题只有 ``retrieval_image_ids`` 与新题有 ``retrieval_media_refs``。
    """
    refs = record.get(f"{prefix}_media_refs")
    candidates: list[tuple[object, object]] = []
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, dict):
                candidates.append((ref.get("media_type"), ref.get("media_id") or ref.get("id")))
    else:
        for value in record.get(f"{prefix}_image_ids") or []:
            candidates.append((None, value))
        for value in record.get(f"{prefix}_video_ids") or []:
            candidates.append(("video", value))

    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for explicit_type, value in candidates:
        media_id = str(value or "").strip()
        if not media_id:
            continue
        media_type = infer_media_type(media_id, explicit_type)
        key = media_key(media_type, media_id)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"media_type": media_type, "media_id": media_id})
    return normalized


def selected_asset_ids(turn_result: dict[str, Any]) -> list[str]:
    """2.0 的**交付集**：``result.attachments[].asset_id``。

    对应 1.0 ``_extract_media_sets``（``:984``）里的 ``selected_asset_ids`` 那一支。
    1.0 那一支有六七个来源（``image_delivery`` / ``answer_grounding.selected_delivery``
    / ``debug_preview_handles`` 的映射…），2.0 只有一个：``AttachmentSummary``
    （``api/schemas/assistant.py:47``）。**这不是简化，是 2.0 的权威定义**——
    交付给用户的就是这三个字段那一份，别的都没有对外。

    保序去重（1.0 用 ``dict.fromkeys``）：交付顺序是模型的选择顺序，后面
    ``delivery_matched_file_names`` 的排序才是另一回事。
    """
    attachments = turn_result.get("attachments")
    ids = [
        str(item.get("asset_id"))
        for item in (attachments or [])
        if isinstance(item, dict) and item.get("asset_id")
    ]
    return list(dict.fromkeys(ids))


def resolve_predicted_media(
    asset_ids: list[str], assets_by_name: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """1.0 ``:1129``：asset_id → 带 ``media_type`` / ``file_name`` / ``media_id`` 的记录。

    **视频关键帧回落到父视频那一段照搬**：模型交付的常常是抽出来的关键帧
    （``derived_kind == "video_keyframe"``），而 GT 标的是那段视频。不回落的后果是
    同一件事在指标里算成「没召回」。这条是 1.0 的口径（``:1135-1152``），2.0 的
    ``/videos/{id}/scenes`` 造出来的关键帧同样是 ``video_keyframe``。
    """
    assets_by_id = {
        str(asset["id"]): (file_name, asset)
        for file_name, assets in assets_by_name.items()
        for asset in assets
        if asset.get("id")
    }
    resolved: list[dict[str, Any]] = []
    for asset_id in asset_ids:
        match = assets_by_id.get(str(asset_id))
        if not match:
            continue
        file_name, asset = match
        raw_metadata = asset.get("metadata_json")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        derived_kind = str(asset.get("derived_kind") or metadata.get("derived_kind") or "")
        parent_asset_id = str(asset.get("parent_asset_id") or metadata.get("parent_asset_id") or "")
        if derived_kind in {"video_keyframe", "video_keyframe_webp"} and parent_asset_id in assets_by_id:
            source_file_name, source_asset = assets_by_id[parent_asset_id]
            source_type = infer_media_type(
                source_file_name, source_asset.get("media_type") or source_asset.get("asset_type")
            )
            if source_type == "video":
                media: dict[str, Any] = {
                    "asset_id": parent_asset_id,
                    "file_name": source_file_name,
                    "media_type": "video",
                    "media_id": Path(source_file_name).stem,
                    "source_timestamp_sec": asset.get("source_timestamp_sec")
                    or metadata.get("source_timestamp_sec"),
                    "source_keyframe_asset_id": str(asset_id),
                }
                if source_asset.get("media_url"):
                    media["media_url"] = source_asset["media_url"]
                resolved.append(media)
                continue
        media_type = infer_media_type(file_name, asset.get("media_type") or asset.get("asset_type"))
        media = {
            "asset_id": str(asset_id),
            "file_name": file_name,
            "media_type": media_type,
            "media_id": Path(file_name).stem if media_type == "video" else file_name,
        }
        if asset.get("media_url"):
            media["media_url"] = asset["media_url"]
        resolved.append(media)
    return resolved


def resolve_gt_media(
    gt_refs: list[dict[str, str]],
    assets_by_name: dict[str, list[dict[str, Any]]],
    retrieved_media: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """1.0 ``:1364``：GT 引用 → 已入库资产，带 ``mapping_status``。

    ``mapping_status`` 三态（``ok`` / ``missing`` / ``ambiguous``）是 1.0 就有的诊断：
    GT 在库里找不到时，召回率的分母仍然算它，但**原因要留下来**——否则「题目标错了」
    与「导入漏了这张」看起来一模一样。
    """
    asset_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for file_name, assets in assets_by_name.items():
        for asset in assets:
            media_type = infer_media_type(file_name, asset.get("media_type") or asset.get("asset_type"))
            asset_index.setdefault(media_key(media_type, file_name), []).append(asset)
    retrieved_keys = {
        media_key(item.get("media_type"), item.get("media_id") or item.get("file_name"))
        for item in retrieved_media
    }
    result: list[dict[str, Any]] = []
    for ref in gt_refs:
        media_type = ref["media_type"]
        media_id = ref["media_id"]
        key = media_key(media_type, media_id)
        candidates = asset_index.get(key, [])
        file_name = Path(media_id).name
        if media_type == "video" and Path(file_name).suffix.lower() not in VIDEO_EXTENSIONS:
            file_name = f"{file_name}.mp4"
        result.append(
            {
                "media_type": media_type,
                "media_id": media_id,
                "image_id": media_id if media_type == "image" else None,
                "video_id": media_id if media_type == "video" else None,
                "file_name": file_name,
                "asset_id": candidates[0].get("id") if len(candidates) == 1 else None,
                "matched": key in retrieved_keys,
                "mapping_status": (
                    "ok" if len(candidates) == 1 else "missing" if not candidates else "ambiguous"
                ),
            }
        )
    return result


def modality_metrics(
    gt_refs: list[dict[str, str]], predicted_media: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """1.0 ``:1206``：总量、图片、视频三组 triplet。

    ``predicted_media`` 是**交付集**（2.0 只有这一个集），所以这里算出来的就是
    1.0 口径里的 delivery 指标——1.0 把那三个集分开算正是因为它的候选集与交付集
    是两份数据；2.0 只有一份，那就只用一份，不假装有两个。
    """
    gt_keys = {media_key(ref.get("media_type"), ref.get("media_id")) for ref in gt_refs}
    predicted_keys = {
        media_key(item.get("media_type"), item.get("media_id") or item.get("file_name"))
        for item in predicted_media
    }
    result = {"media": metric_triplet(gt_keys, predicted_keys)}
    for media_type in ("image", "video"):
        result[media_type] = metric_triplet(
            {key for key in gt_keys if key[0] == media_type},
            {key for key in predicted_keys if key[0] == media_type},
        )
    return result


def summarize_progress(public_progress: list[Any]) -> dict[str, Any]:
    """把 2.0 的 ``public_progress`` 收成一份可读的轨迹摘要。

    **这是本切片对「轨迹」能做的全部**——原始事件在 item 的 ``trace`` 里逐条落盘，
    这里只是给读侧一个不用自己解析 JSON 的入口。四项都直接从事件本身取，不做推断：

    - ``steps``：事件条数（``step_index`` 的最大值，1.0 没有对应物）；
    - ``stages`` / ``kinds``：阶段与种类的计数（规划/工具循环/终态各几次）；
    - ``tools``：**工具名与出现次数**——2.0 的 payload 只给 ``tool`` 与
      ``tool_call_id``（``run_turn.py:651-657``），没有入参与结果。1.0 的
      ``tool_trace`` 有入参、结果与 ``debug_asset_ids``，**2.0 没有**，所以这里
      不编 ``tool_trace``：一个只有工具名的列表，叫 ``tools`` 才是诚实的名字。

    ``tool_result`` 的对账按 ``tool_call_id`` 配对：2.0 会为同一次调用发
    ``model_action``（发出调用）与 ``tool_result``/``tool_error``（收结果）两条，
    后者才是「真的执行了」。1.0 的 ``_extract_tool_perf`` 同样按结果计数。
    """
    stages: dict[str, int] = {}
    kinds: dict[str, int] = {}
    tools: dict[str, int] = {}
    errors: dict[str, int] = {}
    max_step = 0
    executed: dict[str, int] = {}
    for event in public_progress or []:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "")
        stage = str(event.get("stage") or "")
        if kind:
            kinds[kind] = kinds.get(kind, 0) + 1
        if stage:
            stages[stage] = stages.get(stage, 0) + 1
        step_index = event.get("step_index")
        if isinstance(step_index, int):
            max_step = max(max_step, step_index)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        tool = str(payload.get("tool") or "") if payload else ""
        if tool:
            tools[tool] = tools.get(tool, 0) + 1
            if kind in {"tool_result", "tool_error"}:
                executed[tool] = executed.get(tool, 0) + 1
        if kind in {"tool_error", "guard"} and payload:
            code = str(payload.get("code") or "unknown")
            errors[code] = errors.get(code, 0) + 1
    return {
        "steps": max_step,
        "stages": stages,
        "kinds": kinds,
        "tools": tools,
        "executed_tools": executed,
        "error_codes": errors,
    }

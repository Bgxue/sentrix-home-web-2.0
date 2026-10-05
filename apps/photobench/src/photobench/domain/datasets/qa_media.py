"""``GET /api/qa-dataset`` 的媒体解析（1.0 ``_resolve_qa_media_rows`` ``:1423``）。

**纯的部分在这里，打 HTTP 的那两行在 application**（``application/datasets/service.py``）：
spaces 与 assets 由调用方取回来，这里只做「把题目里的媒体引用对上库里的资产」。
分界点的判据是「1.0 哪一段用了 ``request_json``」——那两行之前与之后都是纯计算。

``_qa_scope_id_for_album``（1.0 ``:1404``）一并搬来：它是同一段逻辑里「这个相册对应
哪个 scope」的判据，分开放会让「按名字挑 scope」的口径活在两个文件里。

行号注释一律指 1.0 ``services/photobench/backend/benchmark_orchestrator.py``。
"""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import quote

from photobench.domain.runs.analysis import infer_media_type, media_key
from photobench.domain.runs.execution import normalize_media_refs, safe_slug


def qa_media_refs(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, str]]:
    """1.0 ``:1425-1432``：题目里出现的全部媒体引用，按 ``media_key`` 去重。

    三个来源都要看：``retrieval`` / ``answer_evidence`` 两个前缀，以及 ``answer_claims``
    里每条 claim 的 ``evidence``。少看一处，那一类引用在结果里就成了「没解析」而不是
    「库里没有」——前端对这两者给的是两种提示。
    """
    refs: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        for prefix in ("retrieval", "answer_evidence"):
            for ref in normalize_media_refs(row, prefix):
                refs[media_key(ref["media_type"], ref["media_id"])] = ref
        for claim in row.get("answer_claims") or []:
            if isinstance(claim, dict):
                for ref in normalize_media_refs(claim, "evidence"):
                    refs[media_key(ref["media_type"], ref["media_id"])] = ref
    return refs


def qa_scope_id_for_album(spaces: list[dict[str, Any]], album_id: str) -> str | None:
    """1.0 ``:1404``：挑名字里带这个 album slug 的、最新的一个 scope。

    ``exact`` 这一格是「按词边界命中」的加权：``album3`` 不该命中 ``album3-max`` 的
    分数与命中 ``album3`` 一样高。排序键是 ``(exact, created_at, id)`` 逆序——
    1.0 就是这么写的，``created_at`` 只作次级判据。

    **2.0 的上游可能没有 ``created_at``**：``apps/api`` 的 ``GET /api/v1/memory-spaces``
    只回 ``{id, name, kind, status}``（``api/routes/spaces.py:20``）。缺了它这一格就是
    空串，于是「最新的一个」退化成「id 最大的一个」——这是上游的数据形状差异，
    不是这里能补的（编一个时间戳等于发明事实）。登记在行为基线里。
    """
    target = safe_slug(album_id).casefold()
    candidates: list[tuple[int, str, str]] = []
    for space in spaces or []:
        if not isinstance(space, dict) or not space.get("id"):
            continue
        name = str(space.get("name") or "")
        slug = safe_slug(name).casefold()
        if not target or target not in slug:
            continue
        exact = 1 if re.search(rf"(?:^|[-_]){re.escape(target)}(?:[-_]|$)", slug) else 0
        candidates.append((exact, str(space.get("created_at") or ""), str(space["id"])))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def _index_assets(assets: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """1.0 ``:1445-1451``：资产按 ``(类型, 文件名)`` 归组。

    用 list 而不是单值：同一个 scope 里同名文件只该有一行，但查不到 scope 时
    （``scope_id`` 为空）会跨 scope 拿到重名——那时映射标成 ``ambiguous``，
    而不是随手挑一张。
    """
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for asset in assets or []:
        if not isinstance(asset, dict) or not asset.get("id"):
            continue
        name = str(asset.get("file_name") or "")
        kind = infer_media_type(name, asset.get("media_type") or asset.get("asset_type"))
        index.setdefault(media_key(kind, name), []).append(asset)
    return index


def _attach(record: dict[str, Any], prefix: str, resolved: dict[tuple[str, str], dict[str, Any]]) -> None:
    """1.0 ``:1478-1482`` 的 ``attach``：把解析结果挂回 ``{prefix}_media_refs``。

    ``normalize_media_refs`` 在这里**再跑一次**（1.0 也跑两次）：第一次是为了收集
    去重表，这一次是为了确定这条记录里引用的顺序与去重。键一路相同，所以不会漏。
    """
    refs = normalize_media_refs(record, prefix)
    if refs:
        record[f"{prefix}_media_refs"] = [
            resolved[media_key(ref["media_type"], ref["media_id"])] for ref in refs
        ]


def resolve_qa_rows(
    rows: list[dict[str, Any]],
    refs: dict[tuple[str, str], dict[str, str]],
    assets: list[dict[str, Any]],
    assets_base: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """1.0 ``:1443-1499`` 的纯部分：``assets`` 已取回，``assets_base`` 是资产文件的基址。

    ``assets_base`` 由调用方拼（``{sentrix}/api/v1/assets``）——**API 前缀不属于这里**，
    它是「怎么和主产品说话」，归 adapter 那一侧；这一层只负责把
    ``{assets_base}/{asset_id}/file`` 填进 ``media_url``。

    返回 ``(enriched_rows, counts)``；``counts`` 就是 1.0 那三个计数，``status`` /
    ``scope_id`` / ``total_refs`` 由调用方补（它们在取数据之前就定了）。
    """
    index = _index_assets(assets)
    resolved: dict[tuple[str, str], dict[str, Any]] = {}
    counts = {"resolved_count": 0, "missing_count": 0, "ambiguous_count": 0}
    for key, ref in refs.items():
        candidates = index.get(key, [])
        item: dict[str, Any] = {
            **ref,
            "mapping_status": "ok" if len(candidates) == 1 else "missing" if not candidates else "ambiguous",
        }
        if len(candidates) == 1:
            asset = candidates[0]
            item.update(
                {
                    "asset_id": str(asset["id"]),
                    "file_name": asset.get("file_name") or ref["media_id"],
                    "media_url": f"{assets_base}/{quote(str(asset['id']))}/file",
                }
            )
            counts["resolved_count"] += 1
        elif candidates:
            item["candidate_asset_ids"] = [str(asset["id"]) for asset in candidates]
            counts["ambiguous_count"] += 1
        else:
            counts["missing_count"] += 1
        resolved[key] = item

    enriched: list[dict[str, Any]] = []
    for row in rows:
        item = copy.deepcopy(row)
        _attach(item, "retrieval", resolved)
        _attach(item, "answer_evidence", resolved)
        for claim in item.get("answer_claims") or []:
            if isinstance(claim, dict):
                _attach(claim, "evidence", resolved)
        enriched.append(item)
    return enriched, counts

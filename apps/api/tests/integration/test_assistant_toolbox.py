"""四个工具执行器的行为：真 SQLite 向量库 + 真结果集仓储 + 脚本化嵌入。

这里钉的是每个工具的**信息边界**——``observation``（内部）与 ``model_visible``（发回模型的）
两份投影差在哪。1.0 靠 ``ToolPolicy._sanitize`` 与 ``_model_visible_observation`` 两层过滤
做同一件事，但没有任何一处解释为什么需要两层；这里把它写成可断言的事实。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sentrix.application.assistant.tools import AssistantToolbox, is_empty_result
from sentrix.application.retrieval.retrieve_evidence import RetrieveEvidence
from sentrix.application.retrieval.retrieve_lexical import RetrieveLexical
from sentrix.application.retrieval.retrieve_metadata import RetrieveMetadata
from sentrix.application.retrieval.retrieve_visual import RetrieveVisual
from sentrix.domain.assistant.evidence_ledger import EvidenceType
from sentrix.domain.assistant.models import PersonProfile
from sentrix.domain.media.models import Asset, AssetStatus, GpsPoint, MediaType
from sentrix.domain.places.aliases import EMPTY_ALIASES
from sentrix.domain.retrieval import scheme
from sentrix.domain.retrieval.ports import VectorRecord
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.result_sets import SqliteResultSetRepository
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
SCOPE = "album1"


class ScriptedEmbedder:
    """文本与图片都映射到指定向量——让「哪张图排第一」是构造出来的。"""

    model_id = scheme.IMAGE_MODEL
    dimension = 4

    def embed_query(self, text: str) -> Sequence[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [digest[0] / 255, digest[1] / 255, 0.0, 0.0]

    def embed_image(self, path: Path) -> Sequence[float]:
        digest = hashlib.sha256(Path(path).read_bytes()).digest()
        return [digest[0] / 255, digest[1] / 255, 0.0, 0.0]


class StubPeople:
    def __init__(self, profiles: dict[str, PersonProfile] | None = None) -> None:
        self._profiles = profiles or {}

    def lookup(self, name: str, scope_id: str) -> PersonProfile | None:
        return self._profiles.get(name)


@pytest.fixture
def toolbox(migrations_ready_db, tmp_path):
    assets = SqliteAssetRepository(migrations_ready_db)
    vectors = SqliteVectorRepository(migrations_ready_db)
    result_sets = SqliteResultSetRepository(migrations_ready_db)
    media = tmp_path / "media"
    media.mkdir()
    for index, word in enumerate(("海边", "雪山")):
        path = media / f"{word}.jpg"
        path.write_bytes(f"图片字节 {word}".encode())
        asset = Asset(
            id=f"asset_{index}",
            scope_id=SCOPE,
            file_name=f"{word}.jpg",
            media_type=MediaType.IMAGE,
            path=str(path),
            mime_type="image/jpeg",
            size_bytes=path.stat().st_size,
            status=AssetStatus.PROCESSED,
            content_sha256=f"hash{index}",
            captured_at=datetime(2019, 8, index + 1, tzinfo=UTC),
            captured_location=GpsPoint(30.0, 120.0),
            created_at=NOW,
            updated_at=NOW,
        )
        assets.add(asset)
        vectors.upsert(
            VectorRecord(
                scope_id=SCOPE,
                space=scheme.VISUAL_SPACE,
                source_type="asset",
                source_id=asset.id,
                model_name=scheme.IMAGE_MODEL,
                vector=tuple(ScriptedEmbedder().embed_query(word)),
            )
        )

    observations = SqliteObservationRepository(migrations_ready_db)
    # 四条召回通道与 HTTP 端点用的是同一套编排（1.0 里两个入口共用同一个 Kernel）。
    # 这个夹具不落 Observation，所以词法与元数据两条通道恒空——被测的是工具的信息
    # 边界，不是召回；通道自己的行为在 test_retrieve_evidence.py。
    build = AssistantToolbox(
        retrieval=RetrieveEvidence(
            visual=RetrieveVisual(embedder=ScriptedEmbedder(), vectors=vectors, index=None),
            lexical=RetrieveLexical(index=SqliteLexicalIndex(migrations_ready_db, observations)),
            metadata=RetrieveMetadata(
                assets=assets,
                places=SqliteAssetPlaceRepository(migrations_ready_db),
                aliases=EMPTY_ALIASES,
            ),
        ),
        assets=assets,
        result_sets=result_sets,
        people=StubPeople({"妈妈": PersonProfile(name="妈妈", family_role="母亲", confirmed=True)}),
    )
    return build, result_sets


def run(toolbox, name: str, **arguments: object):
    build, _result_sets = toolbox
    return build.execute(name, arguments, scope_id=SCOPE, now=NOW)


# ---- search_memories --------------------------------------------------------


def test_search_returns_a_result_set_with_a_preview(toolbox):
    """S2 没有相关性阈值，所以两行全在结果集里——顺序才是被测的东西。"""
    outcome = run(toolbox, "search_memories", query="海边")

    assert outcome.ok
    assert outcome.observation["total"] == 2
    assert outcome.observation["asset_ids"][0] == "asset_0"
    preview = outcome.model_visible["preview"]
    assert [item["handle"] for item in preview] == ["photo_1", "photo_2"]
    assert preview[0]["file_name"] == "海边.jpg"


def test_the_model_never_sees_asset_ids_or_the_candidate_total(toolbox):
    """两份投影的差集就是这个工具的信息边界。

    ``total`` 只进 ``observation``：1.0 实测过模型会把候选池的数量直接当成答案里的统计
    数字（``runtime.py:116``）。``asset_id`` 同理——模型只需要 handle，真正可交付的资产
    由运行时解引用。
    """
    outcome = run(toolbox, "search_memories", query="海边")

    assert "total" in outcome.observation
    assert "asset_ids" in outcome.observation
    assert "total" not in outcome.model_visible
    assert "asset_ids" not in outcome.model_visible
    assert "photo_1" not in str(outcome.observation.get("asset_ids"))


def test_search_records_the_retrieval_backend_for_the_internal_projection(toolbox):
    outcome = run(toolbox, "search_memories", query="海边")

    assert outcome.observation["backend"] == "sqlite_exact"
    assert outcome.observation["degraded"] is True
    assert "degraded" not in outcome.model_visible


def test_a_blank_query_fails_without_touching_retrieval(toolbox):
    outcome = run(toolbox, "search_memories", query="   ")

    assert not outcome.ok
    assert outcome.failure_reason == "empty_query"
    assert outcome.model_visible["error"] == "empty_query"


def test_an_empty_scope_is_reported_as_zero(toolbox):
    """空库与「没搜到」是两件事。S2 没有相关性阈值，所以只会因为**一条向量都没有**
    而返回空——这正是门禁要认的那个事实（``fabrication_from_empty`` 的依据）。"""
    build, _result_sets = toolbox

    outcome = build.execute("search_memories", {"query": "海边"}, scope_id="空相册", now=NOW)

    assert outcome.observation["total"] == 0
    assert is_empty_result(outcome) is True
    assert outcome.model_visible["preview"] == []


# ---- get_result_page --------------------------------------------------------


def test_paging_requires_a_known_result_set(toolbox):
    outcome = run(toolbox, "get_result_page", result_set_id="rs_nope", page=1)

    assert not outcome.ok
    assert outcome.failure_reason == "unknown_result_set"


def test_paging_returns_the_requested_page(toolbox):
    search = run(toolbox, "search_memories", query="海边")
    result_set_id = search.model_visible["result_set_id"]

    outcome = run(toolbox, "get_result_page", result_set_id=result_set_id, page=1)

    assert outcome.ok
    assert outcome.model_visible["page"] == 1
    assert outcome.model_visible["result_set_id"] == result_set_id


def test_a_page_past_the_end_is_empty_not_an_error(toolbox):
    search = run(toolbox, "search_memories", query="海边")
    result_set_id = search.model_visible["result_set_id"]

    outcome = run(toolbox, "get_result_page", result_set_id=result_set_id, page=99)

    assert outcome.ok
    assert outcome.model_visible["preview"] == []


# ---- get_original_photos ----------------------------------------------------


def test_delivery_turns_handles_into_openable_attachments(toolbox):
    search = run(toolbox, "search_memories", query="海边")
    result_set_id = search.model_visible["result_set_id"]

    outcome = run(toolbox, "get_original_photos", result_set_id=result_set_id, handles=["photo_1"])

    assert outcome.ok
    assert [item.asset_id for item in outcome.attachments] == ["asset_0"]
    assert outcome.attachments[0].file_name == "海边.jpg"
    assert outcome.model_visible["delivered"] == 1


def test_delivery_rejects_a_handle_outside_the_result_set(toolbox):
    """模型只能交付它真的看过的图——这是结构性事实，不是提示词约束。"""
    search = run(toolbox, "search_memories", query="海边")
    result_set_id = search.model_visible["result_set_id"]

    outcome = run(toolbox, "get_original_photos", result_set_id=result_set_id, handles=["photo_7"])

    assert not outcome.ok
    assert outcome.failure_reason == "bad_handle"
    assert outcome.attachments == ()


def test_delivery_needs_an_explicit_handle_list(toolbox):
    """不做回填：模型没选图就是没交付，交付为空是合法状态。"""
    search = run(toolbox, "search_memories", query="海边")
    result_set_id = search.model_visible["result_set_id"]

    outcome = run(toolbox, "get_original_photos", result_set_id=result_set_id)

    assert not outcome.ok
    assert outcome.failure_reason == "missing_handle"


def test_delivery_requires_a_known_result_set(toolbox):
    outcome = run(toolbox, "get_original_photos", result_set_id="rs_nope", handles=["photo_1"])

    assert not outcome.ok
    assert outcome.failure_reason == "unknown_result_set"


# ---- get_person_profile -----------------------------------------------------


def test_a_known_person_returns_the_family_role(toolbox):
    outcome = run(toolbox, "get_person_profile", person="妈妈")

    assert outcome.ok
    assert outcome.model_visible == {"person": "妈妈", "family_role": "母亲"}
    assert outcome.blocks is None


def test_an_unknown_person_returns_a_gap_and_blocks_the_requirement(toolbox):
    """S3 还没有身份层（S5 落地）。查不到就如实封口，而不是让门禁一遍遍逼模型去查。"""
    outcome = run(toolbox, "get_person_profile", person="隔壁老王")

    assert outcome.model_visible["found"] is False
    assert outcome.blocks is EvidenceType.PERSON_PROFILE
    assert "不要编造" in outcome.model_visible["note"]


def test_a_blank_person_name_fails(toolbox):
    outcome = run(toolbox, "get_person_profile", person="")

    assert not outcome.ok
    assert outcome.failure_reason == "empty_person"


# ---- 未知工具 ---------------------------------------------------------------


def test_an_unknown_tool_is_reported_not_raised(toolbox):
    outcome = run(toolbox, "inspect_photo", asset_handle="photo_1")

    assert not outcome.ok
    assert outcome.failure_reason == "unknown_tool"

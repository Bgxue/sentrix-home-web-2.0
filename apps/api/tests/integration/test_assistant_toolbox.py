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

from sentrix.application.assistant.query_memory_facts import QueryMemoryFacts
from sentrix.application.assistant.tools import AssistantToolbox, is_empty_result
from sentrix.application.retrieval.retrieve_entity import RetrieveEntity
from sentrix.application.retrieval.retrieve_evidence import RetrieveEvidence
from sentrix.application.retrieval.retrieve_lexical import RetrieveLexical
from sentrix.application.retrieval.retrieve_metadata import RetrieveMetadata
from sentrix.application.retrieval.retrieve_text_ann import RetrieveTextAnn
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
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.result_sets import SqliteResultSetRepository
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex
from sentrix.infrastructure.persistence.repositories.structured import (
    SqliteStructuredQueryRepository,
)
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


class ScriptedTextEmbedder:
    """文本通道的假嵌入器。**维度与 ScriptedEmbedder 故意不同**（3 vs 4）——
    两个通道共用一个假件时，把维度写成一样会让「查错空间」这件事测不出来。
    """

    model_id = scheme.TEXT_MODEL
    dimension = 3

    def embed_query(self, text: str) -> Sequence[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [digest[0] / 255, digest[1] / 255, digest[2] / 255]


class StubInspector:
    """`inspect_photo` 的模型入口。这个夹具不调它，所以只要求它叫得应。"""

    def inspect(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("这个夹具不该调 inspect_photo")


class StubCrops:
    """目标人物的裁剪件。同上。"""

    def render(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("这个夹具不该裁图")


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
    places = SqliteAssetPlaceRepository(migrations_ready_db)
    # 三条召回通道与 HTTP 端点用的是同一套编排（1.0 里两个入口共用同一个 Kernel）。
    # 这个夹具不落 Observation，所以词法与元数据两条通道恒空——被测的是工具的信息
    # 边界，不是召回；通道自己的行为在 test_retrieve_evidence.py。
    build = AssistantToolbox(
        retrieval=RetrieveEvidence(
            entity=RetrieveEntity(
                index=SqliteLexicalIndex(migrations_ready_db, observations, assets=assets, places=places),
                observations=observations,
            ),
            visual=RetrieveVisual(embedder=ScriptedEmbedder(), vectors=vectors, index=None),
            lexical=RetrieveLexical(
                index=SqliteLexicalIndex(migrations_ready_db, observations, assets=assets, places=places)
            ),
            metadata=RetrieveMetadata(assets=assets, places=places, aliases=EMPTY_ALIASES),
            text=RetrieveTextAnn(embedder=ScriptedTextEmbedder(), vectors=vectors),
        ),
        assets=assets,
        result_sets=result_sets,
        people=StubPeople({"妈妈": PersonProfile(name="妈妈", family_role="母亲", confirmed=True)}),
        memory_facts=QueryMemoryFacts(
            structured=SqliteStructuredQueryRepository(
                migrations_ready_db,
                # 与 metadata 通道用同一份空别名表：这个夹具测的是工具的信息边界，
                # 不是地点匹配；别名桥接自己的行为在 test_structured_query_repository.py。
                EMPTY_ALIASES,
            ),
            entities=SqliteEntityRepository(migrations_ready_db),
        ),
        # `query_photo_people` 的两条读路径（T122）。这个夹具不落脸也不落观察，
        # 所以它们恒空——被测的是别的工具的信息边界。它们自己的行为在
        # test_query_photo_people.py。
        faces=SqliteFaceRepository(migrations_ready_db),
        observations=observations,
        # `inspect_photo` 的两件（T123）。这个夹具不调它——被测的是别的工具的信息边界。
        photo_inspector=StubInspector(),
        person_crops=StubCrops(),
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
    """**名字要挑一个永远不会存在的。** 这条用例原来借的是 ``inspect_photo``——
    那时它确实不存在（S6 的 T123 才做）。它落地之后，同一次调用变成了
    ``unknown_result_set``（参数齐了、只是结果集对不上），用例当场红。
    """
    outcome = run(toolbox, "no_such_tool", asset_handle="photo_1")

    assert not outcome.ok
    assert outcome.failure_reason == "unknown_tool"


# ---- query_memory_facts -----------------------------------------------------


def test_memory_facts_counts_the_album(toolbox):
    """统计工具走的是 SQL，不是检索——所以它数出来的就是资产数本身。

    夹具里有 2 张照片（``asset_0`` / ``asset_1``），搜索那条用例也断言了同一个数。
    """
    outcome = run(toolbox, "query_memory_facts", operation="count", subject="asset")

    assert outcome.ok
    assert outcome.model_visible["value"] == 2
    assert outcome.model_visible["fact_type"] == "asset_count"
    assert outcome.model_visible["unit"] == "original_asset"
    assert outcome.model_visible["coverage"]["complete"] is True


def test_memory_facts_never_leaks_an_asset_id_to_the_model(toolbox):
    """``samples`` 里只有 ``captured_at`` / ``media_type`` / ``caption``。

    1.0 ``_fact_samples`` 的键白名单，也是这个工具与模型之间唯一的边界。
    """
    outcome = run(toolbox, "query_memory_facts", operation="first", subject="asset")

    assert outcome.ok
    samples = outcome.model_visible["samples"]
    assert samples
    for sample in samples:
        assert set(sample) == {"captured_at", "media_type", "caption"}
    assert "asset_id" not in str(outcome.model_visible)


def test_memory_facts_reports_a_rejection_as_a_normal_answer(toolbox):
    """**被拒是一次正常返回，不是异常。** 模型看到 ``reason`` 才知道下一步怎么改参数。"""
    outcome = run(toolbox, "query_memory_facts", operation="count", subject="妈妈")

    assert not outcome.ok
    assert outcome.failure_reason == "unsupported_subject"
    assert outcome.model_visible["status"] == "unsupported_subject"


def test_memory_facts_groups_by_month(toolbox):
    """两张照片分别在 2019-08-01 与 2019-08-02，所以是同一个分组、计数为 2。"""
    outcome = run(toolbox, "query_memory_facts", operation="group", subject="asset", group_by="month")

    assert outcome.ok
    assert outcome.model_visible["value"] == [{"group": "2019-08", "count": 2}]
    assert outcome.model_visible["unit"] == "original_asset_breakdown"
    # 分组结果本身已经把内容摊开了，不再附样本——1.0 也是只在非分组时附。
    assert outcome.model_visible["samples"] == []


def test_memory_facts_exists_and_last(toolbox):
    """夹具里两张照片是 2019-08-01 与 2019-08-02。"""
    exists = run(toolbox, "query_memory_facts", operation="exists", subject="asset")
    assert exists.ok
    assert exists.model_visible["value"] is True

    last = run(toolbox, "query_memory_facts", operation="last", subject="asset")
    assert last.ok
    assert last.model_visible["value"] == "2019-08-02T00:00:00+00:00"
    assert last.model_visible["unit"] == "captured_at"


def test_memory_facts_unit_follows_the_media_filter(toolbox):
    """``unit`` 是按媒体过滤定的，不是按结果定的——问「多少张视频」得到 0，
    但单位仍然是 ``original_video``。写成「结果为空就用默认单位」会让
    「0 个视频」和「0 张照片」看起来是同一句话。"""
    image = run(toolbox, "query_memory_facts", operation="count", subject="asset", filters={"media": "image"})
    assert (image.model_visible["unit"], image.model_visible["value"]) == ("original_image", 2)

    video = run(toolbox, "query_memory_facts", operation="count", subject="asset", filters={"media": "video"})
    assert (video.model_visible["unit"], video.model_visible["value"]) == ("original_video", 0)


def test_memory_facts_without_a_scope_is_unavailable(toolbox):
    """相册范围由系统注入。走到这里说明**装配错了，不是模型错了**——
    所以是 ``unavailable`` 而不是某个 ``unsupported_*``：那两类要模型改参数，
    这一类改参数没用。"""
    build, _result_sets = toolbox

    outcome = build.execute(
        "query_memory_facts", {"operation": "count", "subject": "asset"}, scope_id="", now=NOW
    )

    assert outcome.failure_reason == "missing_runtime_scope"


def test_memory_facts_rejects_a_person_that_was_never_confirmed(toolbox):
    """**与「这个人不存在」不同，所以不是一个 0 条的统计结果。** 返回 0 会让模型
    理直气壮地说「没有他/她的照片」，而事实是这个名字在本相册里根本没被确认过。"""
    outcome = run(
        toolbox,
        "query_memory_facts",
        operation="count",
        subject="person_appearance",
        filters={"person": "查无此人"},
    )

    assert outcome.failure_reason == "person_not_named_in_current_album"
    assert outcome.model_visible["status"] == "unresolved_person"

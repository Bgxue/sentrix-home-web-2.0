"""``read_photo_text``（T124）：读这张照片**已经存下来的**图内文字。

**这个工具在 2.0 只有一条路，而那是裁决的结果，不是遗漏。** 1.0 的模块注释承诺
「PaddleOCR 小模型优先，读不到回退 VLM」，2026-09-30 实测推翻了它：

- 两个 VLM OCR 提示词 ``_OCR_PROMPT`` / ``_OCR_PROMPT_FULL``（``ocr_tool.py:28-29``）
  在全仓**除了定义处一次都没被引用过**；
- ``ocr_tool.py`` 整条链上没有任何一次模型调用（grep ``gamma`` 只命中一句注释）；
- 而 ``paddleocr`` 确实装在 1.0 的生产 venv 里——所以那 2,867 次调用跑的都是小模型，
  VLM 那一路**从来没跑过**。

2.0 没有 PaddleOCR。裁决：**只做第一路**——读导入期已落库的 ``ocr_text``
（1.0 ``ocr_tool.py:544-559``，它本来也是第一条路）。零新依赖、不新增任何 1.0 没跑过的路径。

所以本文件的两条主线是：**取值口径与 1.0 逐条对齐**（截断长度、行数、来源标记）、
以及 **「读不到」不许被说成「图上没有字」**。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentrix.application.assistant.tools import AssistantToolbox
from sentrix.domain.assistant.evidence_ledger import EvidenceType
from sentrix.domain.assistant.result_set import ResultSet
from sentrix.domain.assistant.tools import TOOLS_BY_NAME, CostClass
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.result_sets import SqliteResultSetRepository

NOW = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
SCOPE = "album-ocr"
ASSET_ID = "asset_ocr"


class StubRetrieval:
    def execute(self, request: object) -> None:  # pragma: no cover
        raise AssertionError("本用例不该走检索")


class StubPeople:
    def lookup(self, name: str, scope_id: str) -> None:
        return None


def _observation(observation_id: str, *, updated_at: datetime, ocr_text: str = "") -> Observation:
    return Observation(
        id=observation_id,
        scope_id=SCOPE,
        asset_id=ASSET_ID,
        source_type="import",
        captured_at=NOW,
        confidence=1.0,
        created_at=NOW,
        updated_at=updated_at,
        ocr_text=ocr_text,
    )


@pytest.fixture
def bundle(migrations_ready_db, tmp_path):
    assets = SqliteAssetRepository(migrations_ready_db)
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpeg")
    assets.add(
        Asset(
            id=ASSET_ID,
            scope_id=SCOPE,
            file_name="photo.jpg",
            media_type=MediaType.IMAGE,
            path=str(photo),
            mime_type="image/jpeg",
            size_bytes=4,
            status=AssetStatus.PROCESSED,
            content_sha256="h",
            captured_at=NOW,
            captured_location=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    observations = SqliteObservationRepository(migrations_ready_db)
    result_sets = SqliteResultSetRepository(migrations_ready_db)
    result_sets.add(ResultSet(id="rs_o", scope_id=SCOPE, query="招牌", asset_ids=(ASSET_ID,), created_at=NOW))
    return {
        "db": migrations_ready_db,
        "assets": assets,
        "observations": observations,
        "result_sets": result_sets,
        "photo": photo,
    }


def build(bundle) -> AssistantToolbox:
    return AssistantToolbox(
        retrieval=StubRetrieval(),
        assets=bundle["assets"],
        result_sets=bundle["result_sets"],
        people=StubPeople(),
        memory_facts=None,
        faces=SqliteFaceRepository(bundle["db"]),
        observations=bundle["observations"],
        photo_inspector=None,
        person_crops=None,
    )


def run(bundle, **arguments):
    return build(bundle).execute(
        "read_photo_text", {"result_set_id": "rs_o", **arguments}, scope_id=SCOPE, now=NOW
    )


# ---- 契约 ------------------------------------------------------------------------


def test_the_tool_satisfies_visible_text():
    """1.0 ``tools.py:2768`` 的 ``produces_evidence=("visible_text",)``。"""
    assert TOOLS_BY_NAME["read_photo_text"].satisfies == (EvidenceType.VISIBLE_TEXT,)


def test_read_photo_text_costs_a_photo_slot_like_1_0():
    """**T124 的降级已被推翻，回到 1.0 的 ``EXPENSIVE``。**（W5）

    T124 曾把它降成 ``CHEAP``，理由是「2.0 读的是一列已落库的文本，照抄 ``expensive``
    会让一次免费的读库挤掉 ``inspect_photo`` 的精读预算」。**那条理由的前提是
    ``max_inspections = 1``。** 现在是 3（1.0 生产 profile 的值），代价翻到了另一面：
    ``CHEAP`` 的工具**不受任何单轮次数约束**，只受 6 次工具总预算管——实测
    ``album3v4-026`` 连读 6 次，把工具预算与模型步一起用光。

    两者共用「每轮 3 次照片级取证」不是硬凑：它们本来就是同一件事——**对某一张照片
    做一次取证**（1.0 的 ``budget_unit="image"`` 说的就是这句）。
    """
    assert TOOLS_BY_NAME["read_photo_text"].cost_class is CostClass.EXPENSIVE


def test_the_tool_does_not_take_a_question():
    """**另一处偏离。** 1.0 的 ``required_inputs`` 里有 ``question``，而它唯一的用途是
    决定要不要对小模型的结果做一次补偿重识别（``_HARD_VALUE_QUESTION_RE``）——
    2.0 不重识别，收一个用不上的参数只会让模型多花几个 token 去填它。
    """
    spec = TOOLS_BY_NAME["read_photo_text"]

    assert "question" not in spec.parameters["properties"]
    assert spec.required == ("result_set_id",)


# ---- 读已落库的那份 ---------------------------------------------------------------


def test_it_returns_the_stored_text(bundle):
    bundle["observations"].add(_observation("obs_1", updated_at=NOW, ocr_text="大圣葱油拌面\n电话 22048084"))

    outcome = run(bundle)

    assert outcome.ok
    assert outcome.model_visible["full_text"] == "大圣葱油拌面\n电话 22048084"
    assert outcome.model_visible["source"] == "ingestion_ocr"
    assert outcome.model_visible["certainty"] == "supported"
    assert outcome.observation["source"] == "ingestion_ocr"


def test_the_latest_observation_wins(bundle):
    """1.0 ``ocr_tool.py:545-546`` 的 ``ORDER BY updated_at DESC LIMIT 1``——
    一张照片可以有多条观察（换过模型、补全过），文字以最新那条为准。"""
    bundle["observations"].add(
        _observation("obs_old", updated_at=datetime(2026, 1, 1, tzinfo=UTC), ocr_text="旧")
    )
    bundle["observations"].add(
        _observation("obs_new", updated_at=datetime(2026, 12, 1, tzinfo=UTC), ocr_text="新")
    )

    outcome = run(bundle)

    assert outcome.model_visible["full_text"] == "新"


def test_the_handle_defaults_to_the_first_preview_item(bundle):
    bundle["observations"].add(_observation("obs_1", updated_at=NOW, ocr_text="字"))

    outcome = run(bundle)

    assert outcome.observation["asset_handle"] == "photo_1"


def test_the_full_text_is_capped_at_1600_characters(bundle):
    """1.0 ``ocr_tool.py:551`` 的 ``offline_text[:1600]``。上限是**给模型看的**那一份，
    内部的 ``observation`` 留全文。"""
    long_text = "字" * 2000
    bundle["observations"].add(_observation("obs_1", updated_at=NOW, ocr_text=long_text))

    outcome = run(bundle)

    assert len(outcome.model_visible["full_text"]) == 1600
    assert len(outcome.observation["full_text"]) == 2000


def test_the_regions_are_at_most_eight_lines_of_150_characters(bundle):
    """1.0 ``:552-553``：``text.splitlines()[:8]``、每行 ``[:150]``。"""
    bundle["observations"].add(_observation("obs_1", updated_at=NOW, ocr_text="\n".join(["行" * 200] * 12)))

    regions = run(bundle).model_visible["text_regions"]

    assert len(regions) == 8
    assert all(len(item["text"]) == 150 for item in regions)
    assert all(item["source"] == "ingestion_ocr" for item in regions)


def test_a_blank_ocr_column_counts_as_no_text(bundle):
    """全是空白等于没有——1.0 那一步是 ``str(... or "").strip()``。"""
    bundle["observations"].add(_observation("obs_1", updated_at=NOW, ocr_text="   \n  "))

    assert run(bundle).failure_reason == "no_text_detected"


# ---- 「读不到」不许被说成「图上没有字」 --------------------------------------------


def test_no_stored_text_is_reported_as_unreadable_not_as_absent(bundle):
    """**这条是这个工具的措辞纪律。** 模型拿到一个空的 ``full_text`` 会倾向回答
    「照片里没有文字」——而「我们没读到」与「图上没有」是两件事。

    1.0 的 G6 纪律也是这个方向：OCR 失败降级成 ``partial``，
    绝不变成 ``tool_execution_error`` 或「这次处理没有完成」式工程错误。
    """
    outcome = run(bundle)

    assert outcome.failure_reason == "no_text_detected"
    assert "不要据此断言照片里没有字" in str(outcome.model_visible["message"])
    assert outcome.blocks is None, "换一张图还有机会，不封口"


def test_no_observation_at_all_is_the_same_result(bundle):
    """没有观察行与「有观察但没文字」返回同一个结果——1.0 也是这么收的
    （``observation`` 为 ``None`` 时 ``offline_text`` 是空串）。"""
    assert run(bundle).failure_reason == "no_text_detected"


# ---- 失败码 -----------------------------------------------------------------------


def test_the_three_failure_codes_are_distinct(bundle):
    """三个码分开，与 T122/T123 同一条口径：合起来会让「handle 打错了」与
    「结果集过期了」在轨迹里长得一样。"""
    assert run(bundle, result_set_id="rs_nope").failure_reason == "unknown_result_set"
    assert run(bundle, asset_handle="photo_9").failure_reason == "bad_handle"

    bundle["result_sets"].add(
        ResultSet(id="rs_other", scope_id="别的相册", query="x", asset_ids=(ASSET_ID,), created_at=NOW)
    )
    other = build(bundle).execute("read_photo_text", {"result_set_id": "rs_other"}, scope_id=SCOPE, now=NOW)
    assert other.failure_reason == "scope_mismatch"


def test_the_asset_id_stays_internal(bundle):
    bundle["observations"].add(_observation("obs_1", updated_at=NOW, ocr_text="字"))

    outcome = run(bundle)

    assert outcome.observation["asset_id"] == ASSET_ID
    assert "asset_id" not in outcome.model_visible


def test_the_model_is_told_the_text_was_read_at_import_time(bundle):
    """``note`` 是给模型的：这些字不是**这次**读出来的，是导入时存的。
    差别在于「这次读不到」不等于「图上没有」——与上面那条是同一件事的两面。"""
    bundle["observations"].add(_observation("obs_1", updated_at=NOW, ocr_text="字"))

    assert "导入时" in str(run(bundle).model_visible["note"])

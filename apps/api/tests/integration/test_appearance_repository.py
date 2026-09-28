"""外观证据仓储。

移植自 1.0 `backend/db.py:4482` / `:4529` / `:4535`。

**写入口的三重守卫是这份用例的重点**，每一重挡掉的是不同的一件事——而第三重
（实例真的经 `entity_mentions` 确认给了这个人）挡的是「把 A 的衣服记到 B 头上」，
那是这个功能最要紧的一种错。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.appearance import SqliteAppearanceRepository

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC).isoformat()


def seed(db: Database, *, person_status: str = "confirmed", link: bool = True) -> dict[str, str]:
    """造齐四张父表（空间 / 人 / 资产 / 观察 / 实例），并按需建那条提及桥。"""
    ids = {
        "person": "person_a",
        "other": "person_b",
        "asset": "asset_a",
        "observation": "obs_a",
        "face": "face_a",
    }
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, kind, status, created_at, updated_at)"
            " VALUES ('album1', 'album1', 'household', 'active', ?, ?) ON CONFLICT(id) DO NOTHING",
            (NOW, NOW),
        )
        for entity_id, status in ((ids["person"], person_status), (ids["other"], "confirmed")):
            connection.execute(
                "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                " summary, confidence, created_at, updated_at)"
                " VALUES (?, 'album1', 'person', '妈妈', ?, '', 1.0, ?, ?)",
                (entity_id, status, NOW, NOW),
            )
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
            " VALUES (?, 'album1', 'a.jpg', 'image', '/tmp/a.jpg', ?, ?)",
            (ids["asset"], NOW, NOW),
        )
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
            " VALUES (?, 'album1', ?, 'vlm', ?, ?)",
            (ids["observation"], ids["asset"], NOW, NOW),
        )
        connection.execute(
            "INSERT INTO face_instances (id, asset_id, observation_id, bbox_json, embedding_json,"
            " detection_confidence, quality, pose_json, pose_bucket, embedding_model,"
            " embedding_version, created_at)"
            " VALUES (?, ?, ?, ?, ?, 0.9, 0.8, ?, 'front', 'buffalo_l', 'w600k_r50', ?)",
            (
                ids["face"],
                ids["asset"],
                ids["observation"],
                json.dumps([0, 0, 10, 10]),
                json.dumps([1.0, 0.0]),
                json.dumps({}),
                NOW,
            ),
        )
        if link:
            connection.execute(
                "INSERT INTO entity_mentions (id, entity_id, observation_id, face_instance_id,"
                " confidence, created_at) VALUES ('mention_a', ?, ?, ?, 0.9, ?)",
                (ids["person"], ids["observation"], ids["face"], NOW),
            )
    return ids


@pytest.fixture
def db(migrations_ready_db: Database) -> Database:
    return migrations_ready_db


@pytest.fixture
def repo(db: Database) -> SqliteAppearanceRepository:
    return SqliteAppearanceRepository(db)


def test_recording_takes_the_observation_and_asset_from_the_instance(
    repo: SqliteAppearanceRepository, db: Database
) -> None:
    """两个外键**从人脸实例带出来**，不要调用方再传一遍——传进来的可能与实例对不上，
    而那种不一致事后从证据行上看不出来。"""
    ids = seed(db)
    record = repo.record(
        person_id=ids["person"],
        face_instance_id=ids["face"],
        crop_bbox=[1.0, 2.0, 3.0, 4.0],
        clothing=["红色外套"],
        confidence=0.8,
        model_name="vlm-1",
    )

    assert record.observation_id == ids["observation"]
    assert record.asset_id == ids["asset"]
    assert record.clothing == ("红色外套",)
    assert record.status == "confirmed"


def test_an_unconfirmed_person_is_rejected(repo: SqliteAppearanceRepository, db: Database) -> None:
    """**第一重守卫。** 未确认的候选人物不该有外观记忆——那会让人物画像里出现
    一个用户从没认过的「人」的衣着描述。"""
    ids = seed(db, person_status="pending")
    with pytest.raises(ValueError, match="confirmed person"):
        repo.record(
            person_id=ids["person"],
            face_instance_id=ids["face"],
            crop_bbox=[],
            clothing=["红"],
            confidence=0.5,
            model_name="vlm-1",
        )


def test_a_missing_instance_is_rejected(repo: SqliteAppearanceRepository, db: Database) -> None:
    """**第二重守卫。**"""
    ids = seed(db)
    with pytest.raises(ValueError, match="face instance not found"):
        repo.record(
            person_id=ids["person"],
            face_instance_id="查无此人脸",
            crop_bbox=[],
            clothing=["红"],
            confidence=0.5,
            model_name="vlm-1",
        )


def test_an_unlinked_instance_is_rejected(repo: SqliteAppearanceRepository, db: Database) -> None:
    """**第三重守卫，也是这三重里唯一一重挡「记错人」的。**

    前两重都过得了：人存在且已确认、实例也存在。但这条实例没有经 `entity_mentions`
    确认给这个人——放过去就等于把某张脸的衣服记到了另一个头上。
    """
    ids = seed(db, link=False)
    with pytest.raises(ValueError, match="not confirmed for this person"):
        repo.record(
            person_id=ids["person"],
            face_instance_id=ids["face"],
            crop_bbox=[],
            clothing=["红"],
            confidence=0.5,
            model_name="vlm-1",
        )


def test_clothing_is_cleaned_and_deduplicated_in_order(
    repo: SqliteAppearanceRepository, db: Database
) -> None:
    """去空白、丢空串、去重且**保序**（1.0 的 `dict.fromkeys`）。顺序会在证据包里出现。"""
    ids = seed(db)
    record = repo.record(
        person_id=ids["person"],
        face_instance_id=ids["face"],
        crop_bbox=[1.0],
        clothing=[" 红色外套 ", "", "蓝色帽子", "红色外套", "   "],
        confidence=0.8,
        model_name="vlm-1",
    )

    assert record.clothing == ("红色外套", "蓝色帽子")


def test_the_same_triple_updates_instead_of_duplicating(
    repo: SqliteAppearanceRepository, db: Database
) -> None:
    """唯一键是 `(人, 实例, 模型)`。"""
    ids = seed(db)
    first = repo.record(
        person_id=ids["person"],
        face_instance_id=ids["face"],
        crop_bbox=[1.0],
        clothing=["红"],
        confidence=0.4,
        model_name="vlm-1",
    )
    second = repo.record(
        person_id=ids["person"],
        face_instance_id=ids["face"],
        crop_bbox=[2.0],
        clothing=["蓝"],
        confidence=0.9,
        model_name="vlm-1",
    )

    assert second.id == first.id
    assert second.clothing == ("蓝",)
    assert len(repo.list_for(ids["person"])) == 1


def test_a_different_model_is_a_different_evidence_row(
    repo: SqliteAppearanceRepository, db: Database
) -> None:
    """**换模型是另一条证据，不覆盖旧模型的结论**——否则换模型重跑一次就把可比性抹掉了
    （`AppearanceEvidenceRecord` 的 docstring 写明这条）。"""
    ids = seed(db)
    repo.record(
        person_id=ids["person"],
        face_instance_id=ids["face"],
        crop_bbox=[1.0],
        clothing=["红"],
        confidence=0.4,
        model_name="vlm-1",
    )
    repo.record(
        person_id=ids["person"],
        face_instance_id=ids["face"],
        crop_bbox=[1.0],
        clothing=["蓝"],
        confidence=0.6,
        model_name="vlm-2",
    )

    assert len(repo.list_for(ids["person"])) == 2


def test_empty_clothing_is_excluded_by_default(repo: SqliteAppearanceRepository, db: Database) -> None:
    """**空 `clothing` 不是「没穿衣服」，是「模型没能可靠归属给目标人物」**
    （见 `analyze_person_appearance` 的提示词）。默认排除它们；要统计模型判不出来的
    比例时才 `include_empty=True`。"""
    ids = seed(db)
    repo.record(
        person_id=ids["person"],
        face_instance_id=ids["face"],
        crop_bbox=[1.0],
        clothing=[],
        confidence=0.9,
        model_name="vlm-1",
    )

    assert repo.list_for(ids["person"]) == []
    assert len(repo.list_for(ids["person"], include_empty=True)) == 1


def test_reading_an_unknown_evidence_is_none(repo: SqliteAppearanceRepository) -> None:
    assert repo.get("查无此证据") is None
    assert repo.list_for("查无此人") == []

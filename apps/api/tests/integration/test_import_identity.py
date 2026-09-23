"""导入路径的人脸接线（**先于事件归并**）与身份预置。

两条都在这一份里，因为它们共用同一组真仓储：用假仓储的话，「提及到底写没写」就变成
一个由测试自己编出来的答案。

**第一条钉的是一个不会报错的顺序。** 1.0 在 ``pipeline.py:316-320`` 用注释写过：
与已确认的人相像的脸会在登记时写下 ``entity_mentions``，而事件归并的 ``person_score``
读的正是它。顺序写反时提及是空的、``person_score`` 恒 0、``corroborated`` 更容易为假，
**事件照分照建，只是分错了**。所以断言不是「调用了两个协作者」，而是「事件归并那一刻，
提及已经在库里」——把顺序倒过来，这条断言必然红。

**第二条钉的是预置真的生效。** ``seed`` 建完之后，导入里出现的一张相似的脸必须**直接
落到那个簇上**（而不是又开一个新簇）——那是这个功能存在的全部理由。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sentrix.application.identity.enroll_face import EnrollFace
from sentrix.application.identity.seed_identity import (
    SEED_SUMMARY,
    NoSeedFacesError,
    SeedIdentity,
    SeedPhoto,
)
from sentrix.application.media.import_assets import ImportAssets, ImportRequest
from sentrix.domain.identity.models import FaceDetection
from sentrix.domain.media.models import GpsPoint
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

NOW = "2026-09-23T10:00:00+00:00"
SCOPE = "album1"


# ---- 造数 --------------------------------------------------------------------------


def unit(*values: float) -> tuple[float, ...]:
    """把几个数补零、归一化成一条可比的向量。"""
    numbers = list(values) + [0.0] * (8 - len(values))
    norm = sum(value * value for value in numbers) ** 0.5
    return tuple(value / norm for value in numbers)


def face(
    embedding: tuple[float, ...],
    *,
    quality: float = 0.9,
    bbox: tuple[float, ...] = (10.0, 10.0, 110.0, 110.0),
    pose_bucket: str = "frontal",
    confidence: float = 0.95,
) -> FaceDetection:
    return FaceDetection(
        embedding=embedding,
        bbox=bbox,
        confidence=confidence,
        quality=quality,
        pose_bucket=pose_bucket,
        area_ratio=0.1,
        sharpness=0.3,
        embedding_model="buffalo_l",
        embedding_version="buffalo_l",
        validity="verified",
        identity_ready=True,
        identity_eligible=True,
    )


class StubDetector:
    """按**文件名**给脸的假检测器。预置那条链路的两个入口都指到同一张表。

    按文件名而不是按完整路径匹配，是因为导入路径落盘时会改名：``ImportAssets`` 存的是
    ``<asset_id>-<file_name>``（``MediaStorage`` 的约定），所以测试里给的
    ``"imported.jpg"`` 与磁盘上的真路径对不上，而**这层差异与被测行为无关**。
    """

    def __init__(self, faces_by_path: dict[str, list[FaceDetection]]) -> None:
        self._faces = faces_by_path

    def detect(self, path: str) -> list[FaceDetection]:
        name = Path(path).name
        for key, faces in self._faces.items():
            if key == str(path) or name.endswith(Path(key).name):
                return list(faces)
        return []

    def detect_identity_photo(self, path: str) -> list[FaceDetection]:
        return self.detect(path)


class FakeStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def store(self, asset_id: str, file_name: str, content: bytes) -> Path:
        path = self._root / f"{asset_id}-{file_name}"
        path.write_bytes(content)
        return path


class FakeMetadata:
    def read(self, path: Path) -> tuple[Any, GpsPoint | None, Any]:
        from datetime import UTC, datetime

        return datetime(2017, 10, 5, 12, 0, tzinfo=UTC), None, None


def seed_photo(db: Database, asset_id: str, *, path: str) -> SeedPhoto:
    """预置照片必须先有 asset 与 observation 行：实例表对两者都有外键。"""
    observation_id = f"obs_{asset_id}"
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
            " VALUES (?, ?, ?, 'image', ?, ?, ?)",
            (asset_id, SCOPE, f"{asset_id}.jpg", path, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO observations (id, scope_id, asset_id, source_type, created_at, updated_at)"
            " VALUES (?, ?, ?, 'import', ?, ?)",
            (observation_id, SCOPE, asset_id, NOW, NOW),
        )
    return SeedPhoto(asset_id=asset_id, observation_id=observation_id, path=path)


@pytest.fixture
def seeded_db(migrations_ready_db: Database) -> Database:
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES (?, ?, 1, ?, ?)",
            (SCOPE, SCOPE, NOW, NOW),
        )
    return migrations_ready_db


@pytest.fixture
def repos(seeded_db: Database) -> dict[str, Any]:
    return {
        "faces": SqliteFaceRepository(seeded_db),
        "entities": SqliteEntityRepository(seeded_db),
        "vectors": SqliteVectorRepository(seeded_db),
        "assets": SqliteAssetRepository(seeded_db),
        "observations": SqliteObservationRepository(seeded_db),
    }


def add_confirmed_person_with_cluster(
    db: Database, *, entity_id: str, cluster_id: str, name: str, embedding: tuple[float, ...]
) -> None:
    """一个已确认的人 + 一个挂着它的已确认簇。簇只带代表向量、不带原型——

    ``EnrollFace`` 在没有原型时会退回代表向量（``_representatives``），这条路本身
    也由 ``test_enroll_face.py`` 钉着。
    """
    import json

    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO entities"
            " (id, scope_id, entity_type, canonical_name, status, confidence, created_at, updated_at)"
            " VALUES (?, ?, 'person', ?, 'confirmed', 1.0, ?, ?)",
            (entity_id, SCOPE, name, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO face_clusters"
            " (id, scope_id, status, entity_id, representative_embedding_json, member_count,"
            "  confidence, created_at, updated_at)"
            " VALUES (?, ?, 'confirmed', ?, ?, 1, 1.0, ?, ?)",
            (cluster_id, SCOPE, entity_id, json.dumps(list(embedding)), NOW, NOW),
        )


# ---- 导入路径：人脸先于事件 --------------------------------------------------------


def test_a_matching_face_is_already_a_mention_when_the_event_is_decided(
    tmp_path: Path, seeded_db: Database, repos: dict[str, Any]
) -> None:
    """**这一条就是「人脸先于事件」的门禁。**

    事件归并那一刻去查 ``entity_mentions``：换成「先归并、后登记」，这里必然是 0。
    """
    matched = unit(1.0)
    add_confirmed_person_with_cluster(
        seeded_db, entity_id="ent_mom", cluster_id="clu_mom", name="妈妈", embedding=matched
    )
    enroll = EnrollFace(
        faces=repos["faces"],
        entities=repos["entities"],
        vectors=repos["vectors"],
        assets=repos["assets"],
    )
    detector = StubDetector({"imported.jpg": [face(matched)]})
    order: list[str] = []
    mentions_when_deciding: list[int] = []

    def enroll_faces(asset, observation) -> int:  # noqa: ANN001
        order.append("faces")
        faces = detector.detect(asset.path)
        for item in faces:
            enroll.enroll(asset.id, observation.id, item)
        return len(faces)

    def assign_event(observation) -> str:  # noqa: ANN001
        order.append("events")
        with seeded_db.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM entity_mentions WHERE observation_id = ?",
                (observation.id,),
            ).fetchone()
        mentions_when_deciding.append(int(row[0]))
        return "evt_1"

    importer = ImportAssets(
        assets=repos["assets"],
        observations=repos["observations"],
        storage=FakeStorage(tmp_path),
        metadata=FakeMetadata(),
        face_enroller=enroll_faces,
        event_assigner=assign_event,
    )
    result = importer.execute(
        ImportRequest(scope_id=SCOPE, file_name="imported.jpg", content_type="image/jpeg", content=b"x")
    )

    assert order == ["faces", "events"]
    assert mentions_when_deciding == [1], "事件归并时这条观察还没有提及——人脸排到事件之后了"
    assert result.faces == 1


def test_the_face_enroller_is_optional(tmp_path: Path, seeded_db: Database, repos: dict[str, Any]) -> None:
    """不传就退化成纯导入，人脸计数是 0——「没有人脸能力」与「这张图没有脸」因此可分。"""
    importer = ImportAssets(
        assets=repos["assets"],
        observations=repos["observations"],
        storage=FakeStorage(tmp_path),
        metadata=FakeMetadata(),
        event_assigner=lambda observation: "evt_1",
    )

    result = importer.execute(
        ImportRequest(scope_id=SCOPE, file_name="plain.jpg", content_type="image/jpeg", content=b"y")
    )

    assert result.faces == 0
    assert result.event_id == "evt_1"


# ---- 身份预置 ----------------------------------------------------------------------


@pytest.fixture
def seeder(seeded_db: Database, repos: dict[str, Any]) -> Any:
    def build(faces_by_path: dict[str, list[FaceDetection]]) -> SeedIdentity:
        return SeedIdentity(
            detector=StubDetector(faces_by_path),
            faces=repos["faces"],
            entities=repos["entities"],
            vectors=repos["vectors"],
        )

    return build


def test_seeding_builds_a_confirmed_person_behind_a_confirmed_cluster(
    seeded_db: Database, repos: dict[str, Any], seeder: Any
) -> None:
    first = seed_photo(seeded_db, "seed_1", path="/tmp/seed_1.jpg")
    second = seed_photo(seeded_db, "seed_2", path="/tmp/seed_2.jpg")
    seeder_ = seeder(
        {
            "/tmp/seed_1.jpg": [face(unit(1.0), quality=0.7)],
            "/tmp/seed_2.jpg": [face(unit(1.0, 0.1), quality=0.95)],
        }
    )

    result = seeder_.execute(
        scope_id=SCOPE,
        name="爸爸",
        family_role="father",
        aliases=["爸", "老爸"],
        photos=[first, second],
    )

    entity = repos["entities"].get(result.entity_id)
    cluster = repos["faces"].get_cluster(result.cluster_id)

    assert entity is not None
    assert entity.canonical_name == "爸爸"
    assert entity.status == "confirmed"
    assert entity.family_role == "father"
    assert entity.summary == SEED_SUMMARY
    assert entity.confidence == 1.0
    assert repos["entities"].person_aliases(entity.id) == ("爸", "老爸")
    assert cluster is not None and cluster.status == "confirmed"
    assert cluster.entity_id == entity.id
    assert cluster.member_count == 2
    assert result.face_count == 2
    assert result.aliases == ("爸", "老爸")


def test_the_best_photo_represents_the_cluster_and_every_photo_becomes_an_instance(
    seeded_db: Database, repos: dict[str, Any], seeder: Any
) -> None:
    """代表向量取**质量最高**的那张；每张有脸的照片各落一条实例。"""
    low = seed_photo(seeded_db, "seed_low", path="/tmp/seed_low.jpg")
    high = seed_photo(seeded_db, "seed_high", path="/tmp/seed_high.jpg")
    seeder_ = seeder(
        {
            "/tmp/seed_low.jpg": [face(unit(1.0), quality=0.6)],
            "/tmp/seed_high.jpg": [face(unit(0.0, 1.0), quality=0.95)],
        }
    )

    result = seeder_.execute(scope_id=SCOPE, name="妈妈", family_role=None, aliases=[], photos=[low, high])

    cluster = repos["faces"].get_cluster(result.cluster_id)
    assert cluster is not None
    assert cluster.representative_embedding == pytest.approx(unit(0.0, 1.0))
    instances = repos["faces"].list_instances(result.cluster_id)
    assert len(instances) == 2
    assert {item.asset_id for item in instances} == {"seed_low", "seed_high"}
    assert all(item.cluster_id == result.cluster_id for item in instances)
    # 实例里落的是**归一化后**的向量：余弦阈值只有在单位向量上才等于点积。
    for item in instances:
        assert sum(value * value for value in item.embedding) == pytest.approx(1.0)
    # 原型也要跟着建出来：在线匹配读的是它，没有它这条预置只是躺着的几行数据。
    prototypes = repos["faces"].list_prototypes(result.cluster_id)
    assert len(prototypes) == 1
    assert prototypes[0].pose_bucket == "frontal"


def test_seeded_instances_carry_the_uncertain_validity(
    seeded_db: Database, repos: dict[str, Any], seeder: Any
) -> None:
    """1.0 的 INSERT 不含 ``validity``，落的是 DDL 默认值 ``'uncertain'``。

    「用户说这是谁」与「检测器复核过这张脸」是两种证据强度，不该在同一个字段上抹平。
    """
    photo = seed_photo(seeded_db, "seed_v", path="/tmp/seed_v.jpg")
    seeder_ = seeder({"/tmp/seed_v.jpg": [face(unit(1.0))]})

    result = seeder_.execute(scope_id=SCOPE, name="小明", family_role=None, aliases=[], photos=[photo])

    assert [item.validity for item in repos["faces"].list_instances(result.cluster_id)] == ["uncertain"]


def test_photos_without_faces_are_counted_not_silently_dropped(
    seeded_db: Database, repos: dict[str, Any], seeder: Any
) -> None:
    """一张检不出脸的照片不参与，但**必须在返回里数出来**。

    静默跳过与「全都检不出」在只有 ``face_count`` 一个数的时候长得一样。
    """
    good = seed_photo(seeded_db, "seed_good", path="/tmp/seed_good.jpg")
    blank = seed_photo(seeded_db, "seed_blank", path="/tmp/seed_blank.jpg")
    seeder_ = seeder({"/tmp/seed_good.jpg": [face(unit(1.0))]})

    result = seeder_.execute(scope_id=SCOPE, name="奶奶", family_role=None, aliases=[], photos=[good, blank])

    assert result.face_count == 1
    assert result.photos_without_faces == 1


def test_a_photo_without_a_single_face_creates_nothing(
    seeded_db: Database, repos: dict[str, Any], seeder: Any
) -> None:
    """一张脸都没有 → 整体失败，**一行都不写**（1.0 在路由上抛 422，写库之前就拦下了）。"""
    blank = seed_photo(seeded_db, "seed_none", path="/tmp/seed_none.jpg")

    with pytest.raises(NoSeedFacesError):
        seeder({}).execute(scope_id=SCOPE, name="无名", family_role=None, aliases=[], photos=[blank])

    with seeded_db.connect() as connection:
        entities = connection.execute(
            "SELECT COUNT(*) FROM entities WHERE canonical_name = '无名'"
        ).fetchone()
    assert int(entities[0]) == 0


def test_a_face_imported_later_lands_in_the_seeded_cluster(
    seeded_db: Database, repos: dict[str, Any], seeder: Any
) -> None:
    """**预置的目的**：之后导入的相册里，相像的脸直接落到这个簇上，不再开新簇。

    这是「预置生效」唯一说得通的验收——只断言行数的话，一个把簇建成 ``pending``、
    或者向量没写进 ``memory_vectors`` 的实现同样能通过。
    """
    photo = seed_photo(seeded_db, "seed_face", path="/tmp/seed_face.jpg")
    seeded = seeder({"/tmp/seed_face.jpg": [face(unit(1.0))]}).execute(
        scope_id=SCOPE, name="妈妈", family_role=None, aliases=[], photos=[photo]
    )

    later = seed_photo(seeded_db, "album_photo", path="/tmp/album_photo.jpg")
    enroll = EnrollFace(
        faces=repos["faces"],
        entities=repos["entities"],
        vectors=repos["vectors"],
        assets=repos["assets"],
    )
    outcome = enroll.enroll(
        "album_photo", later.observation_id, face(unit(0.99, 0.1)), model_name="buffalo_l"
    )

    assert outcome.cluster_id == seeded.cluster_id
    assert outcome.score >= 0.30
    # 命中已确认的人 → 这条观察里留下了提及，事件归并因此看得见「谁在场」。
    with seeded_db.connect() as connection:
        mention = connection.execute(
            "SELECT COUNT(*) FROM entity_mentions WHERE entity_id = ? AND observation_id = ?",
            (seeded.entity_id, later.observation_id),
        ).fetchone()
    assert int(mention[0]) == 1

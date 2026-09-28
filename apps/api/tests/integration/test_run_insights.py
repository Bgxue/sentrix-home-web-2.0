"""人物画像流水线（T86g）：``RunInsights`` 五个阶段的顺序与三次校验。

被测对象 ``application/identity/run_insights.py`` 把 1.0 的 ``PersonInsightService`` 收成
一个**同步跑完五个阶段的函数**（``rank`` → ``select`` → ``moments`` → ``compile`` →
``write``），没有 run 表、没有断点续跑，失败数作为返回值交给调用方。它引用的两个域函数
（``rank_core_people`` / ``select_representatives``）与三个画像函数（``compile_portrait_evidence``
/ ``validate_portrait`` / ``deterministic_portrait``）各有自己的单元用例，这份用例只管
**编排**：阶段之间的数据流，以及三处「照搬 1.0、不自己修」的决定。

**最要紧的一条是 ``test_the_deterministic_fallback_is_validated_as_deterministic``。**
2.0 修的缺陷 2 是「1.0 的兜底画像通不过自己的校验」，判据是校验收 ``source``、长度与主题
条数这两条只对模型输出成立。少了它，三级降级的最后一格永远落不了库，而失败是静默的
（既没有异常，也没有一行日志）。

三处照搬 1.0 的决定各有一条用例钉住，有人顺手「修好」它们就会红：``select_representatives``
收到的空向量表、``core`` 只取 ``tier == "core"``、以及 ``selection_json`` 一路传到底。

最后那条是这份切片唯一一条**跨三个阶段的接线**（``s5-deviations.md`` 第 135 条）——``select``
把它写在选项上、``moments`` 读它、仓储写进列。用例因此必须用真的 ``ExtractMoments``（假的
那份是为了可观测阶段顺序而存在的），而 ``person_moments`` 的读侧不含这一列，所以断言直接
读原始列，照 ``test_extract_moments.py`` 的做法。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from sentrix.application.identity.extract_moments import ExtractMoments, MomentExtraction
from sentrix.application.identity.run_insights import InsightRun, RunInsights
from sentrix.domain.identity.insights import (
    REPRESENTATIVE_LIMIT,
    REPRESENTATIVE_PER_EVENT,
    SELECTION_VERSION,
    select_representatives,
)
from sentrix.domain.identity.models import (
    PORTRAIT_SOURCE_DETERMINISTIC,
    PORTRAIT_SOURCE_MODEL,
    PORTRAIT_TRIGGER_PIPELINE,
)
from sentrix.domain.identity.moments import PERSON_MOMENT_PROMPT_VERSION
from sentrix.domain.identity.portrait import (
    PERSON_PORTRAIT_PROMPT_VERSION,
    compile_portrait_evidence,
    deterministic_portrait,
    validate_portrait,
)
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import SqliteAssetRepository
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.moments import SqliteMomentRepository
from sentrix.infrastructure.persistence.repositories.portraits import SqlitePortraitRepository
from sentrix.infrastructure.persistence.repositories.relationships import SqliteRelationshipRepository

NOW = "2026-09-28T10:00:00+00:00"
DAY1 = "2026-09-20T18:30:00+00:00"
DAY2 = "2026-09-21T18:30:00+00:00"
HOME = "album1"
OTHER_SPACE = "album2"

# 一份合格的模型画像：85 个汉字（下限 80）、两个主题（要求 2–6），引用由回复现造。
# 不含任何角色词、也不含 SENSITIVE_TERMS 里的词——那些是**与来源无关**的约束，不该
# 在这条用例里被触发，否则就分不清拒它的到底是哪一条。
VALID_PORTRAIT_TEXT = (
    "他常出现在家庭聚餐的桌边，照片里总在张罗饭菜，把每个人的碗都添满。"
    "从照片看，他喜欢热闹，也愿意把时间花在陪伴上，逢年过节总会提前好几天准备。"
    "这些瞬间反复出现，让相册里的日子显得踏实而温暖。"
    "他就是这样把平常的日子过成了值得记住的样子。"
)

# 一条动作都没有时兜底模板的固定文案（``deterministic_portrait`` 的第一条分支）。
FALLBACK_WITHOUT_ACTIONS = "从照片看，这位家庭成员在相册中反复出现，但具体瞬间证据不足，无法形成完整画像。"


class ScriptedVision:
    """假的 ``PersonVision``：按队列回话，记下每次调用。

    队列里的项可以是字典（直接回），也可以是 ``(pack) -> dict`` 的可调用对象——画像的
    ``evidence_refs`` 必须指向证据包里真有的瞬间，而那些瞬间 id 只有这趟跑起来才知道。
    """

    def __init__(
        self,
        *,
        journal: list[str] | None = None,
        portraits: Sequence[Any] | None = None,
        moments: Sequence[Any] | None = None,
        model_id: str = "scripted-portrait-model",
    ) -> None:
        self.model_id = model_id
        self._journal = journal if journal is not None else []
        self._portraits = list(portraits or [])
        self._moments = list(moments or [])
        self.portrait_calls: list[dict[str, Any]] = []
        self.moment_calls: list[tuple[str, list[str], dict[str, Any]]] = []

    def write_person_portrait(self, pack: Mapping[str, object]) -> dict[str, object] | None:
        self._journal.append("vision.write_person_portrait")
        self.portrait_calls.append(dict(pack))
        if not self._portraits:
            return None
        reply = self._portraits.pop(0)
        return reply(dict(pack)) if callable(reply) else reply

    def analyze_person_moments(self, path, labels, context=None):
        self.moment_calls.append((str(path), list(labels), dict(context or {})))
        return self._moments.pop(0) if self._moments else {"moments": []}

    def analyze_person_appearance(self, path, metadata=None):
        raise AssertionError("这条链路不该碰外观")


class FakeExtractMoments:
    """假的 ``moments`` 阶段：只记下 ``select`` 交给它的那包 selections。

    这一个不落库，所以它唯一能证明的就是**顺序**——空 selections 意味着 ``select``
    没跑或跑在它后面，而剩下的阶段拿不到任何数据却不会报错。
    """

    def __init__(self, *, journal: list[str] | None = None, moments: int = 0, failures: int = 0) -> None:
        self._journal = journal if journal is not None else []
        self._moments = moments
        self._failures = failures
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, scope_id: str, selections: Sequence[Mapping[str, Any]]) -> MomentExtraction:
        self._journal.append("extract_moments")
        self.calls.append({"scope_id": scope_id, "selections": [dict(item) for item in selections]})
        return MomentExtraction(moments=self._moments, failures=self._failures)


class FakePreviewRenderer:
    """假的 ``PersonPreviewRenderer``：写一个真文件，按 ``faces`` 顺序编号。

    不真画图（真实现要 PIL 渲染整张预览），但保留真实现的两条要紧行为：``label_map``
    是归属的唯一依据（逐项带全五个 id），以及读不了图时抛 ``OSError``。
    """

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self._n = 0

    def render(self, asset_path, faces):
        data = Path(asset_path).read_bytes()
        if not data.startswith(b"\xff\xd8"):
            raise OSError(f"cannot identify image file {asset_path!r}")
        self._n += 1
        preview = self.tmp_path / f"preview_{self._n}.jpg"
        preview.write_bytes(b"\xff\xd8\xff\xd9")
        label_map = {
            f"P{index}": {
                "face_instance_id": face["face_instance_id"],
                "person_id": face["person_id"],
                "cluster_id": face["cluster_id"],
                "observation_id": face["observation_id"],
                "event_id": face["event_id"],
            }
            for index, face in enumerate(faces, start=1)
        }
        return str(preview), label_map


class Recording:
    """把真仓储包一层，把每次方法调用的名字追加进共享 journal。

    ``RunInsights`` 内部直接调仓储（``rank`` / ``select`` / ``compile`` 三步都只通过仓储
    读），所以把仓储当探针是唯一能看见阶段**何时**发生的位置——假件看不见 ``select``，
    它不在端口上。
    """

    def __init__(self, inner: object, journal: list[str], label: str) -> None:
        self._inner = inner
        self._journal = journal
        self._label = label

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def logged(*args: Any, **kwargs: Any) -> Any:
            self._journal.append(f"{self._label}.{name}")
            return attribute(*args, **kwargs)

        return logged


class Album:
    """一个装了真 JPEG、真人、真簇的库：每造一样东西都真的落一行。"""

    def __init__(self, db: Database, tmp_path: Path) -> None:
        self.db = db
        self.tmp_path = tmp_path
        self._n = 0
        self.observations: dict[str, str] = {}
        self.scopes: dict[str, str] = {}
        with db.connect() as connection, connection:
            for space in (HOME, OTHER_SPACE):
                connection.execute(
                    "INSERT INTO memory_spaces (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (space, space, NOW, NOW),
                )

    def _next(self) -> int:
        self._n += 1
        return self._n

    def entity(
        self,
        entity_id: str,
        *,
        scope_id: str = HOME,
        name: str = "某位成员",
        status: str = "confirmed",
    ) -> str:
        with self.db.connect() as connection, connection:
            connection.execute(
                "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                " summary, confidence, created_at, updated_at)"
                " VALUES (?, ?, 'person', ?, ?, '', 1.0, ?, ?)",
                (entity_id, scope_id, name, status, NOW, NOW),
            )
        return entity_id

    def event(self, event_id: str, *, scope_id: str = HOME) -> str:
        with self.db.connect() as connection, connection:
            connection.execute(
                "INSERT INTO events (id, scope_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (event_id, scope_id, NOW, NOW),
            )
        return event_id

    def photo(self, *, scope_id: str = HOME, captured_at: str = DAY1, broken: bool = False) -> str:
        n = self._next()
        asset_id, observation_id = f"asset_{n}", f"obs_{n}"
        path = self.tmp_path / f"p{n}.jpg"
        if broken:
            path.write_bytes(b"not an image at all")
        else:
            Image.new("RGB", (8, 8), (200, 120, 40)).save(path, format="JPEG")
        with self.db.connect() as connection, connection:
            connection.execute(
                "INSERT INTO assets (id, scope_id, file_name, media_type, path, created_at, updated_at)"
                " VALUES (?, ?, ?, 'image', ?, ?, ?)",
                (asset_id, scope_id, f"p{n}.jpg", str(path), NOW, NOW),
            )
            connection.execute(
                "INSERT INTO observations (id, scope_id, asset_id, source_type, captured_at,"
                " created_at, updated_at) VALUES (?, ?, ?, 'vlm', ?, ?, ?)",
                (observation_id, scope_id, asset_id, captured_at, NOW, NOW),
            )
        self.observations[asset_id] = observation_id
        self.scopes[asset_id] = scope_id
        return asset_id

    def cluster(self, *, entity_id: str | None, scope_id: str = HOME, status: str = "confirmed") -> str:
        cluster_id = f"cluster_{self._next()}"
        with self.db.connect() as connection, connection:
            connection.execute(
                "INSERT INTO face_clusters (id, scope_id, status, entity_id, member_count,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (cluster_id, scope_id, status, entity_id, NOW, NOW),
            )
        return cluster_id

    def face(
        self,
        asset_id: str,
        cluster_id: str,
        *,
        event_id: str | None = None,
        quality: float = 0.9,
        area_ratio: float = 0.6,
        embedding: Sequence[float] = (),
    ) -> str:
        instance_id = f"ins_{self._next()}"
        with self.db.connect() as connection, connection:
            connection.execute(
                "INSERT INTO face_instances (id, asset_id, observation_id, cluster_id, bbox_json,"
                " embedding_json, detection_confidence, quality, pose_json, area_ratio, sharpness,"
                " pose_bucket, embedding_model, embedding_version, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 0.9, ?, '[]', ?, 1.0, 'front', 'buffalo_l',"
                " 'w600k_r50', ?)",
                (
                    instance_id,
                    asset_id,
                    self.observations[asset_id],
                    cluster_id,
                    json.dumps([10.0, 20.0, 40.0, 60.0]),
                    json.dumps([float(value) for value in embedding]),
                    quality,
                    area_ratio,
                    NOW,
                ),
            )
            if event_id is not None:
                connection.execute(
                    "INSERT OR IGNORE INTO event_observations (event_id, observation_id, created_at)"
                    " VALUES (?, ?, ?)",
                    (event_id, self.observations[asset_id], NOW),
                )
        return instance_id

    def moment(
        self,
        *,
        person_id: str,
        asset_id: str,
        face_instance_id: str,
        cluster_id: str,
        event_id: str,
        action_text: str = "在餐桌旁",
        confidence: float = 0.8,
    ) -> str:
        moment_id = f"moment_{self._next()}"
        with self.db.connect() as connection, connection:
            connection.execute(
                "INSERT INTO person_moments (id, scope_id, person_id, cluster_id, event_id,"
                " observation_id, asset_id, face_instance_id, action_text, confidence,"
                " model_name, prompt_version, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scripted', ?, ?, ?)",
                (
                    moment_id,
                    self.scopes[asset_id],
                    person_id,
                    cluster_id,
                    event_id,
                    self.observations[asset_id],
                    asset_id,
                    face_instance_id,
                    action_text,
                    confidence,
                    PERSON_MOMENT_PROMPT_VERSION,
                    NOW,
                    NOW,
                ),
            )
        return moment_id


def core_person_with_a_moment(
    album: Album, person_id: str = "person_a", *, status: str = "confirmed", action_text: str = "在餐桌旁"
) -> tuple[str, str, str]:
    """造一个 core 人 + 一条瞬间，返回 ``(person_id, cluster_id, moment_id)``。

    同一个簇上只放一张脸：``person_candidate_features`` 按簇 GROUP BY，多开簇会把
    ``ranked`` 数成两行。
    """
    album.entity(person_id, status=status)
    cluster = album.cluster(entity_id=person_id)
    event_id = f"event_{person_id}"
    album.event(event_id)
    asset = album.photo(captured_at=DAY1)
    instance = album.face(asset, cluster, event_id=event_id)
    moment_id = album.moment(
        person_id=person_id,
        asset_id=asset,
        face_instance_id=instance,
        cluster_id=cluster,
        event_id=event_id,
        action_text=action_text,
    )
    return person_id, cluster, moment_id


def model_portrait_from_pack(pack: dict[str, Any], *, model_name: str | None = None) -> dict[str, Any]:
    """按证据包现造一份合格画像。

    瞬间 id 只有证据包造出来之后才知道（``moments`` 阶段在这趟里才写库），预先写死一个
    id 会让画像通不过引用校验——那条校验恰恰是本文件要区分的东西之一。
    """
    refs = [{"kind": "person_moment", "id": moment["id"]} for moment in pack["moments"]]
    assert refs, "证据包里没有瞬间，这份用例的模型回复无从引用"
    portrait: dict[str, Any] = {
        "portrait_text": VALID_PORTRAIT_TEXT,
        "themes": [
            {
                "title": "反复张罗饭菜",
                "summary": "从照片看，他常把餐桌照看得妥帖。",
                "evidence_refs": refs[:1],
            },
            {
                "title": "愿意陪伴",
                "summary": "从照片看，他愿意把时间花在陪伴上。",
                "evidence_refs": refs[:1],
            },
        ],
    }
    if model_name is not None:
        portrait["model"] = model_name
    return portrait


def moment_reply(action_text: str = "在餐桌旁") -> dict[str, object]:
    return {"moments": [{"label": "P1", "action_text": action_text}]}


def build_run(
    db: Database,
    *,
    extract: Any,
    vision: Any,
    journal: list[str] | None = None,
) -> RunInsights:
    def repo(factory: Any, label: str) -> Any:
        inner = factory(db)
        return inner if journal is None else Recording(inner, journal, label)

    return RunInsights(
        faces=repo(SqliteFaceRepository, "faces"),
        entities=repo(SqliteEntityRepository, "entities"),
        moments=repo(SqliteMomentRepository, "moments_repo"),
        relationships=repo(SqliteRelationshipRepository, "relationships"),
        portraits=repo(SqlitePortraitRepository, "portraits"),
        extract_moments=extract,
        vision=vision,
    )


def real_extract_moments(
    db: Database, previews: FakePreviewRenderer, vision: ScriptedVision
) -> ExtractMoments:
    return ExtractMoments(
        assets=SqliteAssetRepository(db),
        faces=SqliteFaceRepository(db),
        entities=SqliteEntityRepository(db),
        moments=SqliteMomentRepository(db),
        previews=previews,
        vision=vision,
    )


def calls_per_person(vision: ScriptedVision) -> dict[str, int]:
    counts: dict[str, int] = {}
    for pack in vision.portrait_calls:
        key = str(pack["person"]["id"])
        counts[key] = counts.get(key, 0) + 1
    return counts


def stored_selection_json(db: Database) -> dict[str, dict[str, object]]:
    """直读 ``person_moments.selection_json``：读侧不含这一列，只能查原始列。"""
    with db.connect() as connection:
        rows = connection.execute("SELECT face_instance_id, selection_json FROM person_moments").fetchall()
    return {row["face_instance_id"]: json.loads(row["selection_json"]) for row in rows}


def row_count(db: Database, table: str) -> int:
    with db.connect() as connection:
        return int(connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])


@pytest.fixture
def album(migrations_ready_db: Database, tmp_path: Path) -> Album:
    return Album(migrations_ready_db, tmp_path)


@pytest.fixture
def db(album: Album) -> Database:
    return album.db


@pytest.fixture
def previews(album: Album) -> FakePreviewRenderer:
    return FakePreviewRenderer(album.tmp_path)


# ---- 1. 阶段顺序是数据流 -------------------------------------------------------------


def test_the_five_stages_are_a_data_flow_not_a_set(album: Album, db: Database) -> None:
    """rank → select → moments → compile → write：断言数据，不是断言「都被调过」。

    两个最直接的可观察点：``moments`` 收到的 selections 必须非空（那是 ``select`` 的
    产物），``write`` 拿到的包必须与 ``compile_portrait_evidence`` 现算出来的一字不差。

    只断言「每个阶段都跑过」是分不出顺序的——把 ``select`` 挪到 ``moments`` 后面，
    五个阶段照样各自跑一次，而 ``moments`` 拿到一个空表、``write`` 拿到一个空包，
    两边都不报错（``run_insights`` 的模块 docstring 写明了这一点）。所以这里既查
    内容，也查仓储探针记下的先后。
    """
    person = album.entity("person_a")
    cluster = album.cluster(entity_id=person)
    album.event("event_1")
    album.event("event_2")
    album.face(album.photo(captured_at=DAY1), cluster, event_id="event_1")
    second = album.photo(captured_at=DAY2)
    instance = album.face(second, cluster, event_id="event_2")
    album.moment(
        person_id=person,
        asset_id=second,
        face_instance_id=instance,
        cluster_id=cluster,
        event_id="event_2",
    )

    journal: list[str] = []
    extract = FakeExtractMoments(journal=journal)
    # 给一份合格回话，让模型只被调一次——两次调用会让下面那条「包等于 compile 产物」
    # 的断言撞上一个长度 2 的列表，反而看不出包对不对。
    vision = ScriptedVision(journal=journal, portraits=[model_portrait_from_pack])

    build_run(db, extract=extract, vision=vision, journal=journal)(HOME)

    at = journal.index
    assert at("faces.person_candidate_features") < at("faces.representative_candidates")
    assert at("faces.representative_candidates") < at("extract_moments")
    assert at("extract_moments") < at("moments_repo.list_for")
    assert at("moments_repo.list_for") < at("vision.write_person_portrait")

    assert len(extract.calls) == 1
    selections = extract.calls[0]["selections"]
    assert selections, "moments 收到空的 selections——select 没跑，或者跑在它后面"
    assert {item["person_id"] for item in selections} == {person}
    assert all(item["observation_id"] for item in selections), "select 没回查实例"

    expected_pack = compile_portrait_evidence(
        SqliteEntityRepository(db).get(person),
        SqliteMomentRepository(db).list_for(person_id=person, scope_id=HOME),
        SqliteRelationshipRepository(db).list_for_entity(person, HOME),
    )
    assert vision.portrait_calls == [expected_pack], "write 拿到的不是 compile 的产物"


# ---- 2. person_candidate_features 的两条过滤 ------------------------------------------


def test_person_candidate_features_drops_rejected_clusters_and_other_spaces(
    album: Album, db: Database
) -> None:
    """``status='rejected'`` 的簇与别的记忆空间的簇都不进特征表。

    三个簇都真存在（下面那条计数），所以「只有 mine 出现」是过滤器的功劳，不是造数据
    时就没造出来。被否掉的簇挂着已确认实体——少了 ``c.status != 'rejected'``，它会以
    一个核心人物的身份进管线；少了 ``c.scope_id = ?``，另一个空间的人会进来。
    """
    mine = album.entity("person_mine")
    dropped = album.entity("person_dropped")
    elsewhere = album.entity("person_elsewhere", scope_id=OTHER_SPACE)

    kept = album.cluster(entity_id=mine)
    rejected = album.cluster(entity_id=dropped, status="rejected")
    foreign = album.cluster(entity_id=elsewhere, scope_id=OTHER_SPACE)

    album.face(album.photo(), kept)
    album.face(album.photo(), rejected)
    album.face(album.photo(scope_id=OTHER_SPACE), foreign)

    assert row_count(db, "face_clusters") == 3
    features = {row["person_id"] for row in SqliteFaceRepository(db).person_candidate_features(HOME)}
    assert features == {"person_mine"}


# ---- 3. representative_candidates 的 LEFT JOIN ----------------------------------------


def test_representative_candidates_keeps_a_photo_with_no_event(album: Album, db: Database) -> None:
    """没归到任何事件的照片也要出现在候选里，``event_id`` 是 ``None``。

    ``event_observations`` 是 LEFT JOIN：``select`` 是「挑代表照」，不是「挑有事件归属的
    代表照」。改成 INNER JOIN，那张照片会从候选里凭空消失，而管线不会报错——少一张
    代表照只是让画像少一段证据。
    """
    person = album.entity("person_a")
    cluster = album.cluster(entity_id=person)
    album.event("event_1")
    linked_photo = album.photo(captured_at=DAY1)
    orphan_photo = album.photo(captured_at=DAY2)
    linked = album.face(linked_photo, cluster, event_id="event_1")
    orphan = album.face(orphan_photo, cluster)

    rows = {
        row["face_instance_id"]: row for row in SqliteFaceRepository(db).representative_candidates(person)
    }

    assert set(rows) == {linked, orphan}
    assert rows[linked]["event_id"] == "event_1"
    assert rows[orphan]["event_id"] is None


def test_representative_candidates_drops_a_retired_cluster_that_still_names_the_person(
    album: Album, db: Database
) -> None:
    """退休（``rejected``）的簇不再供图，哪怕它的 ``entity_id`` 还指着同一个人。

    ``ReviewCluster._merge_into_person`` 并簇时走的正是 ``retire_cluster(源簇, entity_id=目标)``
    ——状态转 rejected、归属改成被并入的那位。于是同一个人可以同时拥有一个正常簇与一个
    退休簇，退休簇的照片仍然属于他、但这一簇本身已经交出归属、不该再供图。少了
    ``c.status != 'rejected'``，那张「已经搬走了」的脸会被当成代表照候选。

    两半都断言，缺一不可：退休簇的脸不在候选里，正常簇的脸**仍在**。只查前者，分不清
    「挡住了退休簇」与「整条查询断了」。退休簇本身也得先证明真的存在、真的带着同一个
    ``entity_id``，否则「它没出现」可能只是造数时就没造出来。
    """
    person = album.entity("person_a")
    active = album.cluster(entity_id=person)
    # 并簇前的源簇：还没归属到任何人，正是 ``_merge_into_person`` 面对的那种簇。
    merged_away = album.cluster(entity_id=None, status="pending")
    kept_face = album.face(album.photo(captured_at=DAY1), active)
    album.face(album.photo(captured_at=DAY2), merged_away)

    SqliteFaceRepository(db).retire_cluster(merged_away, entity_id=person)

    stored = {cluster.id: cluster for cluster in SqliteFaceRepository(db).list_clusters_for_entity(person)}
    assert set(stored) == {active, merged_away}, "造数没成：两个簇都得挂在同一个人名下"
    assert stored[merged_away].status == "rejected"
    assert stored[merged_away].entity_id == person

    rows = {row["face_instance_id"] for row in SqliteFaceRepository(db).representative_candidates(person)}
    assert rows == {kept_face}, "正常簇那张必须在，退休簇那张必须不在"


# ---- 4. 空向量表：照搬 1.0 的那个显式选择 ---------------------------------------------


def test_select_gets_an_empty_vector_table_so_twins_are_not_deduped(album: Album, db: Database) -> None:
    """``select_representatives`` 收到 ``{}``，所以向量相同的两张都会被选中。

    这条钉的是 ``_select`` 里 ``select_representatives(candidates, {}, ...)`` 那个决定
    （照搬 1.0，见 docstring）：去重分支在生产上从不执行。用例的前半先证明这张夹具真的
    会触发去重——把向量接上去，两张就只剩一张——后半再断言管线里两张都在。
    有人顺手把向量接上，前半照样绿，后半会红。
    """
    person = album.entity("person_a")
    cluster = album.cluster(entity_id=person)
    album.event("event_1")
    album.event("event_2")
    embedding = [1.0, 0.0, 0.0]
    album.face(album.photo(captured_at=DAY1), cluster, event_id="event_1", embedding=embedding)
    album.face(album.photo(captured_at=DAY2), cluster, event_id="event_2", embedding=embedding)

    candidates = SqliteFaceRepository(db).representative_candidates(person)
    vectors = {row["asset_id"]: embedding for row in candidates}
    assert len(vectors) == 2
    deduped = select_representatives(
        [dict(row) for row in candidates],
        vectors,
        limit=REPRESENTATIVE_LIMIT,
        per_event=REPRESENTATIVE_PER_EVENT,
    )
    assert len(deduped) == 1, "两张的向量完全相同——接上向量表就会被去重，这条用例才咬得住"

    run = build_run(db, extract=FakeExtractMoments(), vision=ScriptedVision())(HOME)

    assert run.selections == 2


# ---- 5. 只有 tier == "core" 的人写画像 ------------------------------------------------


def test_only_core_tier_people_get_a_portrait(album: Album, db: Database) -> None:
    """够核心门槛的那位落画像，不够的那位一步都不走。

    ``tier`` 由 ``rank_core_people`` 判：排进前 N **且**（已确认或跨够 2 天）。不够的那位
    仍然在 ``ranked`` 里（1.0 把非核心留在值域里给别的阶段看），所以把 ``core`` 换成
    ``ranked`` 的实现会让它也多一次模型调用——这条用例正是钉那个。
    """
    core, _, _ = core_person_with_a_moment(album, "person_core")
    minor, _, _ = core_person_with_a_moment(album, "person_minor", status="pending")

    vision = ScriptedVision(portraits=[model_portrait_from_pack])
    run = build_run(db, extract=FakeExtractMoments(), vision=vision)(HOME)

    assert run.ranked == 2
    assert run.core == 1
    assert calls_per_person(vision) == {core: 1}
    assert run.portraits == 1
    assert [row.person_id for row in SqlitePortraitRepository(db).list_for_person(core)] == [core]
    assert SqlitePortraitRepository(db).list_for_person(minor) == []


# ---- 6. 写画像的三级阶梯 --------------------------------------------------------------


def test_the_portrait_ladder_climbs_model_then_retry_then_deterministic(album: Album, db: Database) -> None:
    """模型一次过 / 重写一次过 / 两次都不过走兜底——三格各一个人。

    排进 core 的次序由 ``rank_core_people`` 的排序键决定（三人分数与天数都相同，按
    ``person_id`` 升序），所以回话队列是 ``a / b / c`` 逐个消费的。第三格最要紧的是
    **模型只调两次**：兜底不调模型，多出来的那第三张回话会把计数打乱。
    """
    people = ["person_a", "person_b", "person_c"]
    for person in people:
        core_person_with_a_moment(album, person)

    vision = ScriptedVision(
        portraits=[
            model_portrait_from_pack,  # a：第一次就过
            None,
            model_portrait_from_pack,  # b：第一次不过、第二次过
            None,
            None,  # c：两次都不过 -> 兜底，不再调模型
        ]
    )
    run = build_run(db, extract=FakeExtractMoments(), vision=vision)(HOME)

    assert calls_per_person(vision) == {"person_a": 1, "person_b": 2, "person_c": 2}
    assert run.portraits == 3


# ---- 7. 缺陷 2 的回归门禁：兜底那版用 source="deterministic" 送校验 ---------------------


def test_the_deterministic_fallback_is_validated_as_deterministic(album: Album, db: Database) -> None:
    """**这份用例里最重要的一条。** 兜底画像只过得了一版校验，落的必须是那一版。

    夹具是「只有一条瞬间、一个动作」——兜底模板因此只有 1 个主题、约 37 个汉字，两条都由
    模板唯一决定，正是 ``validate_portrait`` 里 ``source`` 分支跳过的那两条。用例先把这
    件事证明出来（同一份稿子按模型标准不过、按确定性标准过），再断言它落库了。

    把兜底那次的 ``source`` 写回 ``PORTRAIT_SOURCE_MODEL``（1.0 的缺陷 2），画像落不了库
    而失败是静默的：``_write`` 只是 ``continue``，结果里少一个人，没有异常。所以这里断言
    的是「落库的正文等于兜底模板的正文」，不是「没抛异常」。
    """
    person, _, _ = core_person_with_a_moment(album, "person_a", action_text="在餐桌旁")

    vision = ScriptedVision(portraits=[None, None])
    run = build_run(db, extract=FakeExtractMoments(), vision=vision)(HOME)

    assert len(vision.portrait_calls) == 2, "兜底不该再调模型"
    pack = vision.portrait_calls[-1]
    fallback = deterministic_portrait(pack)
    assert len(fallback["themes"]) == 1 and fallback["portrait_text"] != FALLBACK_WITHOUT_ACTIONS
    model_ok, model_errors = validate_portrait(dict(pack), fallback, source=PORTRAIT_SOURCE_MODEL)
    assert not model_ok, f"兜底画像竟然过了模型标准，这条用例就钉不住 source 分支：{model_errors}"
    assert validate_portrait(dict(pack), fallback, source=PORTRAIT_SOURCE_DETERMINISTIC)[0]

    assert run.portraits == 1
    active = SqlitePortraitRepository(db).get_active(person)
    assert active is not None
    assert active.portrait_text == fallback["portrait_text"]


# ---- 8. 三次都不过 -> 不落库 ----------------------------------------------------------


def test_three_failed_validations_leave_no_portrait(album: Album, db: Database) -> None:
    """兜底也要守产品规则：动作里带敏感词，最后一格同样过不了。

    兜底不等于豁免（``deterministic_portrait`` 的 docstring 写明这条有意为之），而
    ``InsightRun.portraits`` 数的是**真正落库的版本**——三个人里少一个，计数就得少。
    """
    person, _, _ = core_person_with_a_moment(album, "person_a", action_text="生病住院")

    vision = ScriptedVision(portraits=[None, None])
    run = build_run(db, extract=FakeExtractMoments(), vision=vision)(HOME)

    assert len(vision.portrait_calls) == 2
    assert run.portraits == 0
    assert SqlitePortraitRepository(db).list_for_person(person) == []
    assert row_count(db, "person_portrait_revisions") == 0


# ---- 9. 查不到实体 -> 跳过该人 --------------------------------------------------------


def test_a_cluster_without_an_entity_is_skipped_without_raising(album: Album, db: Database) -> None:
    """``entities`` 里查不到这个人 -> 跳过，不把 ``None`` 递进纯函数。

    ``person_candidate_features`` 用 ``COALESCE(c.entity_id, c.id)`` 把没有实体的簇报成
    簇自己的 id，所以这样一个人确实会被排进 core、也确实会走进 ``_compile``。没有那道
    ``continue``，``compile_portrait_evidence`` 会在 ``person.canonical_name`` 上炸掉。
    """
    cluster = album.cluster(entity_id=None)
    album.event("event_1")
    album.face(album.photo(captured_at=DAY1), cluster, event_id="event_1")
    album.face(album.photo(captured_at=DAY2), cluster, event_id="event_1")

    vision = ScriptedVision()
    run = build_run(db, extract=FakeExtractMoments(), vision=vision)(HOME)

    assert run == InsightRun(ranked=1, core=1, selections=0, moments=0, moment_failures=0, portraits=0)
    assert vision.portrait_calls == []


# ---- 10–12. 落库的三个常量 ------------------------------------------------------------


def test_the_model_name_comes_from_the_port_not_from_the_reply(album: Album, db: Database) -> None:
    """``model_name`` 取自 ``vision.model_id``，不是模型回复里的那个键。"""
    person, _, _ = core_person_with_a_moment(album, "person_a")
    vision = ScriptedVision(portraits=[lambda pack: model_portrait_from_pack(pack, model_name="冒牌模型")])

    run = build_run(db, extract=FakeExtractMoments(), vision=vision)(HOME)

    assert run.portraits == 1
    active = SqlitePortraitRepository(db).get_active(person)
    assert active is not None
    assert active.model_name == "scripted-portrait-model"


def test_the_prompt_version_is_the_domain_constant(album: Album, db: Database) -> None:
    """``prompt_version`` 是 ``PERSON_PORTRAIT_PROMPT_VERSION``，不是随手字面量。

    它是这一版画像「由哪套提示词产出」的唯一凭证，写死一个字面量就意味着改了提示词
    之后两版结论分不开。
    """
    person, _, _ = core_person_with_a_moment(album, "person_a")
    vision = ScriptedVision(portraits=[model_portrait_from_pack])

    build_run(db, extract=FakeExtractMoments(), vision=vision)(HOME)

    active = SqlitePortraitRepository(db).get_active(person)
    assert active is not None
    assert active.prompt_version == PERSON_PORTRAIT_PROMPT_VERSION


def test_the_trigger_type_is_the_pipeline_constant(album: Album, db: Database) -> None:
    """``trigger_type`` 是 ``PORTRAIT_TRIGGER_PIPELINE``——导入后自动跑出来的这一版，
    与用户在界面上点出来的那一版要分得开。"""
    person, _, _ = core_person_with_a_moment(album, "person_a")
    vision = ScriptedVision(portraits=[model_portrait_from_pack])

    build_run(db, extract=FakeExtractMoments(), vision=vision)(HOME)

    active = SqlitePortraitRepository(db).get_active(person)
    assert active is not None
    assert active.trigger_type == PORTRAIT_TRIGGER_PIPELINE


# ---- 13. selection_json 一路传到底（第 135 条接上的断线） ------------------------------


def test_the_selection_pack_flows_from_select_into_the_moment_row(
    album: Album, db: Database, previews: FakePreviewRenderer
) -> None:
    """``select`` 写在选项上的那包元数据，最终进了 moment 的 ``selection`` 列。

    跨三个阶段：``select_representatives`` 产出它 -> ``_select`` 把它抄进传给 moments 的
    选项 -> ``ExtractMoments`` 读 ``selection_json`` 交给仓储。1.0 断在最后一步的**键名**
    上（算的是 ``selection_json``、读的是 ``selection``），所以那包数据从来没进过库。

    这里必须用真的 ``ExtractMoments``：假的那份不落库，这条断线就看不见。断言绕过记录
    类型直接读原始列——``person_moments`` 的读侧不含这一列。
    """
    person = album.entity("person_a")
    cluster = album.cluster(entity_id=person)
    album.event("event_1")
    album.event("event_2")
    for captured_at, event_id in ((DAY1, "event_1"), (DAY2, "event_2")):
        asset = album.photo(captured_at=captured_at)
        album.face(asset, cluster, event_id=event_id)

    vision = ScriptedVision(moments=[moment_reply(), moment_reply()])
    extract = real_extract_moments(db, previews, vision)
    run = build_run(db, extract=extract, vision=vision)(HOME)

    assert run.moments == 2
    expected = {
        item["face_instance_id"]: item["selection_json"]
        for item in select_representatives(
            SqliteFaceRepository(db).representative_candidates(person),
            {},
            limit=REPRESENTATIVE_LIMIT,
            per_event=REPRESENTATIVE_PER_EVENT,
        )
    }
    stored = stored_selection_json(db)
    assert set(stored) == set(expected)
    for face_instance_id, pack in expected.items():
        assert pack["version"] == SELECTION_VERSION
        assert stored[face_instance_id] == pack, "select 算的那包没进 moment 的 selection 列"


# ---- 14. 六个计数 --------------------------------------------------------------------


def test_the_six_counters_match_the_rows_in_the_database(
    album: Album, db: Database, previews: FakePreviewRenderer
) -> None:
    """``InsightRun`` 的六个计数与库里的真实行数对得上。

    世界里刻意放了三样「不该被算进去」的东西：一个被否掉的簇（不进 ``ranked``）、一张
    坏图（进 ``moment_failures``、不进 ``moments``）、以及没归到任何事件的照片——最后
    那条在别的用例里单独钉。
    """
    person_a, cluster_a, _ = core_person_with_a_moment(album, "person_a")
    person_b, _, _ = core_person_with_a_moment(album, "person_b")

    album.event("event_broken")
    broken = album.photo(captured_at=DAY2, broken=True)
    album.face(broken, cluster_a, event_id="event_broken")

    dropped = album.entity("person_dropped")
    album.cluster(entity_id=dropped, status="rejected")

    vision = ScriptedVision(
        moments=[moment_reply(), moment_reply()],
        portraits=[model_portrait_from_pack, model_portrait_from_pack],
    )
    extract = real_extract_moments(db, previews, vision)
    run = build_run(db, extract=extract, vision=vision)(HOME)

    assert run == InsightRun(ranked=2, core=2, selections=3, moments=2, moment_failures=1, portraits=2)
    assert run.ranked == len(SqliteFaceRepository(db).person_candidate_features(HOME))
    assert run.moments == row_count(db, "person_moments")
    assert run.portraits == row_count(db, "person_portrait_revisions")
    assert run.moment_failures == 1, "坏图只该算一次失败，且不该算进 moments"
    assert run.selections == sum(
        len(
            select_representatives(
                SqliteFaceRepository(db).representative_candidates(person_id),
                {},
                limit=REPRESENTATIVE_LIMIT,
                per_event=REPRESENTATIVE_PER_EVENT,
            )
        )
        for person_id in (person_a, person_b)
    )

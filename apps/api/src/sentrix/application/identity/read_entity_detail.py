"""实体详情用例 ``ReadEntityDetail``（S6 · T128b）。

对应 1.0 ``db.py:4570-4627`` 的 ``MemoryStore.get_entity_detail``。那是一个跨四个域的
复合读——身份（实体 / 属性 / 簇 / 外观证据）、记忆（事实 / 事件）、媒体（观察 / 资产）——
所以按 2.0 的分层落在 **application 用例**里：``read_timeline.py`` 是既有的先例
（关键字注入多个仓储、返回模块内的 frozen dataclass、HTTP 映射留在路由）。
**不在路由里拼**——路由那条路是给「把记录字段搬进响应模型」用的，跨四个域的不是。

**1.0 一共返回十二个键，这里搬十个，两个不搬**：

- ``profile`` / ``claims``：``semantic_profiles`` / ``semantic_claims`` 在 2.0 **零读路径**，
  而这两张表的读法归 **T130**（``GET /knowledge`` 就是 profiles + claims + spaces）。
  1.0 前端渲染实体详情弹窗时也不读这两个键（``src/app.js:1026-1039``）。

（``relationships`` 在 S6 时是第三个留白键，**S7a · T201 已补上**——它的行为见下面第 4 条。）

**四处照搬的 1.0 行为，每一处都反直觉：**

1. ``facts`` 按 **``canonical_name`` 字符串**匹配，不按 entity_id。1.0（``db.py:4588``）
   是拉 ``list_facts(1000)``——**全库、无 scope 过滤**——再在 Python 里过一遍。
   2.0 的 ``facts`` 表 ``subject`` / ``object`` 存的也是名字而不是 id
   （``entities.py`` 删实体时同样按名字删），所以按名字算**是照搬，不是将就**。
2. ``clusters`` **没有状态过滤**：被拒的簇也会出现在详情里（1.0 ``db.py:4580``）。
3. ``observations`` 在 ``captured_at`` 相同时没有第二排序键（1.0 ``:4590-4594``）。
4. ``relationships`` **不过滤 ``status``**：``retracted`` / ``superseded`` 的行也返回
   （1.0 ``db.py:5590-5597`` 的实体分支）。同一个响应里 ``properties`` 排 superseded、
   ``clusters`` 不排 rejected，这里是第三种规矩——**而且与同仓的
   ``list_person_relationships``（``:5601``，有 ``status != 'retracted'``）正好相反**。
   消费方拿 ``status`` 自己区分。传不传 ``scope_id`` 也不一样：1.0 ``get_entity_detail``
   调 ``list_relationships(entity_id)`` **不带 scope**，2.0 照搬不带。

**一处同名不同义的坑**：``evidence_count`` 在这个响应里是
``len(observations)``——**取回来的**观察条数，上限 100；而 ``GET /entities``（T128a）
里那个同名键是 ``COUNT(*) FROM entity_observations`` 的原始行数。**1.0 就是两个数**
（``db.py:4601`` 对 ``db.py:4083``）。不要把它们统一。
"""

from __future__ import annotations

from dataclasses import dataclass

from sentrix.domain.identity.models import (
    CLUSTER_SAMPLES_LIMIT,
    AppearanceEvidenceRecord,
    EntityPropertyRecord,
    EntityRecord,
    FaceClusterRecord,
    FaceInstanceRecord,
    RelationshipRecord,
)
from sentrix.domain.identity.ports import (
    AppearanceRepository,
    EntityRepository,
    FaceRepository,
    RelationshipRepository,
)
from sentrix.domain.media.models import Asset, Observation
from sentrix.domain.media.ports import AssetRepository, ObservationRepository
from sentrix.domain.memories.events import EventRecord
from sentrix.domain.memories.facts import Fact
from sentrix.domain.memories.ports import EventRepository, FactRepository

# 1.0 ``db.py:4588`` 的 ``list_facts(1000)``：**先取 1000 条全库事实，再按名字过滤**。
# 这个数不是「这个实体最多有几条事实」，是「扫描窗口有多大」——照搬，别按需改成
# 「只查这个实体的」：那会改变结果（过滤在取数**之后**）。
FACTS_SCAN_LIMIT = 1000

# 1.0 ``db.py:4594`` 的 ``LIMIT 100``。
OBSERVATION_LIMIT = 100

# 1.0 ``db.py:4607`` / ``:4612`` 的两个 kind 标记。它们是**交付契约**：前端按它区分
# 「这条证据来自观察」还是「来自外观取样」。
EVIDENCE_KIND_OBSERVATION = "observation"
EVIDENCE_KIND_APPEARANCE = "appearance"


@dataclass(frozen=True, slots=True)
class DetailObservation:
    """一条观察 + 它指向的资产。1.0 是把 asset 整行挂在 ``observation["asset"]`` 上。"""

    observation: Observation
    asset: Asset | None


@dataclass(frozen=True, slots=True)
class DetailCluster:
    """一个簇 + 它的样本脸（1.0 是把 ``samples`` 就地塞进 cluster 那一行）。"""

    cluster: FaceClusterRecord
    samples: tuple[FaceInstanceRecord, ...]


@dataclass(frozen=True, slots=True)
class EvidenceFile:
    """``evidence_files`` 的一项：**四**个键，1.0 ``db.py:4604-4612`` 逐字。"""

    evidence_id: str
    asset_id: str
    file_name: str
    kind: str


@dataclass(frozen=True, slots=True)
class EntityDetail:
    entity: EntityRecord
    avatar_face_instance_id: str | None
    evidence_count: int
    properties: tuple[EntityPropertyRecord, ...]
    property_history: tuple[EntityPropertyRecord, ...]
    clusters: tuple[DetailCluster, ...]
    facts: tuple[Fact, ...]
    events: tuple[EventRecord, ...]
    observations: tuple[DetailObservation, ...]
    appearance_evidence: tuple[AppearanceEvidenceRecord, ...]
    evidence_files: tuple[EvidenceFile, ...]
    relationships: tuple[RelationshipRecord, ...]


class ReadEntityDetail:
    def __init__(
        self,
        *,
        entities: EntityRepository,
        faces: FaceRepository,
        facts: FactRepository,
        events: EventRepository,
        observations: ObservationRepository,
        assets: AssetRepository,
        appearances: AppearanceRepository,
        relationships: RelationshipRepository,
    ) -> None:
        self._entities = entities
        self._faces = faces
        self._facts = facts
        self._events = events
        self._observations = observations
        self._assets = assets
        self._appearances = appearances
        self._relationships = relationships

    def execute(self, entity_id: str) -> EntityDetail | None:
        """实体不存在时返回 ``None``（1.0 ``db.py:4571-4573``），由路由翻成 404。"""
        entity = self._entities.get(entity_id)
        if entity is None:
            return None
        decoration = self._entities.decorate_entities([entity_id])[entity_id]
        observations = self._observations_of(entity_id)
        appearances = tuple(self._appearances.list_for(entity_id, include_empty=True))
        return EntityDetail(
            entity=entity,
            avatar_face_instance_id=decoration.avatar_face_instance_id,
            # **不是** ``decoration.evidence_count``——见模块 docstring 里那条同名不同义。
            evidence_count=len(observations),
            properties=tuple(self._entities.list_properties(entity_id)),
            property_history=tuple(self._entities.list_properties(entity_id, include_history=True)),
            clusters=self._clusters_of(entity_id),
            facts=self._facts_of(entity),
            events=self._events_of(entity_id),
            observations=observations,
            appearance_evidence=appearances,
            evidence_files=_evidence_files(observations, appearances),
            relationships=tuple(self._relationships.list_for_entity(entity_id)),
        )

    def _clusters_of(self, entity_id: str) -> tuple[DetailCluster, ...]:
        """1.0 ``db.py:4580-4586``：全部簇（**不排 rejected**），每簇带最多 12 张样本脸。

        逐簇取实例是 N+1——**1.0 也是**（``:4583`` 在循环里），而且
        ``GET /people/{person_id}`` 早就是这个形状。样本的排序（``quality DESC,
        created_at ASC``）由 ``FaceRepository.list_instances`` 给，与 1.0 的
        ``ORDER BY fi.created_at DESC`` **不同**——那是一处已存在的 2.0 差异，
        不在本次改动范围内（见 behaviour-baseline）。
        """
        return tuple(
            DetailCluster(
                cluster=cluster,
                samples=tuple(self._faces.list_instances(cluster.id)[:CLUSTER_SAMPLES_LIMIT]),
            )
            for cluster in self._faces.list_clusters_for_entity(entity_id)
        )

    def _facts_of(self, entity: EntityRecord) -> tuple[Fact, ...]:
        """1.0 ``db.py:4588``：**先按窗口取，再按名字过滤**，两步的顺序是行为的一部分。"""
        return tuple(
            fact
            for fact in self._facts.list_facts(None, limit=FACTS_SCAN_LIMIT)
            if fact.subject == entity.canonical_name or fact.object == entity.canonical_name
        )

    def _events_of(self, entity_id: str) -> tuple[EventRecord, ...]:
        """1.0 ``db.py:4624``：事件 id 逐个取本体，取不到的跳过。

        N+1 与 1.0 同形（它那边的 ``get_event`` 甚至每条调两次——``if`` 里一次、
        取值一次）。2.0 调一次就够，那两次是 1.0 的重复调用，不是行为。
        """
        found = (self._events.get(event_id) for event_id in self._entities.event_ids(entity_id))
        return tuple(event for event in found if event is not None)

    def _observations_of(self, entity_id: str) -> tuple[DetailObservation, ...]:
        """1.0 ``db.py:4595-4600``：逐条取观察（取不到的跳过），再把 asset 整行挂上去。"""
        found = (
            self._observations.get(observation_id)
            for observation_id in self._entities.recent_observation_ids(entity_id, limit=OBSERVATION_LIMIT)
        )
        return tuple(
            DetailObservation(observation=item, asset=self._assets.get(item.asset_id))
            for item in found
            if item is not None
        )


def _evidence_files(
    observations: tuple[DetailObservation, ...],
    appearances: tuple[AppearanceEvidenceRecord, ...],
) -> tuple[EvidenceFile, ...]:
    """1.0 ``db.py:4602-4613``：观察与外观证据合成一张 **id → 文件** 的表。

    **两类证据共用一张表、按 id 去重**（1.0 用一个 dict，后写覆盖先写）。顺序也有意义：
    1.0 先放观察、再放外观，所以 id 撞车时**外观那条赢**。照搬。

    ``file_name`` 取不到时回落成 ``asset_id``（1.0 的 ``or``）——对观察那一类，
    这发生在资产读不回来的时候。
    """
    merged: dict[str, EvidenceFile] = {}
    for item in observations:
        asset = item.asset
        merged[item.observation.id] = EvidenceFile(
            evidence_id=item.observation.id,
            asset_id=item.observation.asset_id,
            file_name=(asset.file_name if asset else "") or item.observation.asset_id,
            kind=EVIDENCE_KIND_OBSERVATION,
        )
    for appearance in appearances:
        merged[appearance.id] = EvidenceFile(
            evidence_id=appearance.id,
            asset_id=appearance.asset_id,
            file_name=appearance.file_name or appearance.asset_id,
            kind=EVIDENCE_KIND_APPEARANCE,
        )
    return tuple(merged.values())

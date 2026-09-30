"""语义实体分组的读用例 ``ReadEntityGroups``（S6 · T129）。

对应 1.0 ``db.py:4194-4236`` 的 ``list_semantic_entity_groups`` 与 ``:4249-4266`` 的
``get_semantic_entity_group``。**两条都是现算的投影，不落表**——组没有 id 以外的身份，
它的 id 是 ``semantic_group:{scope}:{type}:{label}`` 拼出来的，所以同一个 scope 下
同一个概念永远是同一个 id。

分组的**判断**在 ``domain/identity/semantics.py``（四级短路，可穷举）；这里做遍历、
聚合与排序——那是 1.0 里与取数混在一起的那一半。

**两处不进响应，各有理由**：

- ``relationship_count``：``relationships`` 的读路径是 **S7**。1.0 的这个键是**成员级
  ``relationship_count`` 的累加**，而 2.0 的 ``decorate_entities`` 本来就不产出它
  （T128a 的决定）——留一个恒 0 的键，读起来像「这组实体确实没有关系」。
- 组详情里扁平合并的 ``relationships``：同上（1.0 ``:4260`` 把各成员的合并进来）。

**一处照搬的 1.0 不一致**：``semantic_details`` 来自实体的 ``semantic_details`` **属性**，
而 2.0 的抽取（T127）**不写那个属性**——1.0 把它关在 ``has_semantic_place`` 里面，
2.0 的输入下那个合取恒为假。所以这个键在 2.0 **今天是恒空的**。保留它是因为它是
1.0 的 API 契约、前端拿它渲染标签；但**它恒空这件事是真的**，不粉饰。
"""

from __future__ import annotations

from dataclasses import dataclass

from sentrix.application.identity.read_entity_detail import EntityDetail, ReadEntityDetail
from sentrix.domain.identity.decoration import private_place
from sentrix.domain.identity.models import (
    DEFAULT_SCOPE_ID,
    ENTITY_STATUS_REJECTED,
    ENTITY_STATUS_SUPERSEDED,
    ENTITY_TYPE_PERSON,
    ENTITY_TYPE_PLACE,
    EntityDecoration,
    EntityRecord,
)
from sentrix.domain.identity.ports import EntityRepository
from sentrix.domain.identity.semantics import group_entity_type, semantic_entity_key

# 1.0 ``db.py:4210`` 的 id 前缀与分隔符，逐字。
GROUP_ID_PREFIX = "semantic_group:"
GROUP_ID_SEPARATOR = ":"

# 1.0 ``db.py:4204`` 循环里跳过的两种状态（SQL 层已经排过一遍，这是第二道）。
SKIPPED_STATUSES = frozenset({ENTITY_STATUS_REJECTED, ENTITY_STATUS_SUPERSEDED})

SEMANTIC_DETAILS_KEY = "semantic_details"
SEMANTIC_PRIMARY_KEY = "semantic_primary"


@dataclass(frozen=True, slots=True)
class EntityGroupMember:
    """组里的一个成员：实体本体 + 它的派生字段 + **投影后的**对外名字。

    ``canonical_name`` 与 ``entity.canonical_name`` **可以不同**——私密地点在
    ``public_entity`` 那一层被换成别名或「私密地点」（1.0 ``db.py:4226`` 追加进
    members 的就是投影过的那个 dict）。
    """

    entity: EntityRecord
    canonical_name: str
    # 非 place 是 ``None`` 而不是 ``False``：1.0 的 ``public_entity`` 对非 place 原样返回，
    # 那个键**根本不存在**。与 ``GET /entities`` 的投影保持同一条规矩。
    private: bool | None
    decoration: EntityDecoration
    semantic_details: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EntityGroup:
    id: str
    scope_id: str
    entity_type: str
    canonical_name: str
    members: tuple[EntityGroupMember, ...]
    member_entity_ids: tuple[str, ...]
    source_labels: tuple[str, ...]
    semantic_details: tuple[str, ...]
    evidence_count: int
    confidence: float
    preview_asset_id: str | None
    preview_file_name: str | None
    preview_media_type: str | None
    rationale_strategy: str
    is_semantic_cluster: bool


@dataclass(frozen=True, slots=True)
class EntityGroupDetail:
    """一个组 + 各成员的实体详情。

    详情版的 ``members`` 是**每个成员的实体详情**（1.0 ``:4254``），不是投影过的那个 dict。
    **扁平合并后的观察与事件不在这里**：1.0 把它们（以及关系）合并进同一个返回体，
    而那一步的产物就是响应里的数组——去重放路由侧，用例只负责取到每个成员的详情。
    """

    group: EntityGroup
    members: tuple[EntityDetail, ...]


class ReadEntityGroups:
    def __init__(self, *, entities: EntityRepository, details: ReadEntityDetail) -> None:
        self._entities = entities
        self._details = details

    def execute(self, *, scope_id: str | None = None) -> tuple[EntityGroup, ...]:
        """全部组，按 1.0 的三键排序返回。

        **私密地点的投影不在这里，而是逐条在追加成员时做**（1.0 ``db.py:4226`` 的
        ``public_entity``）。1.0 的取数是 ``list_entities(..., public=False)``；2.0 的
        ``list_entities`` 在 T128a 之后**本来就不做投影**（那是路由层的事），所以这里
        拿到的就是原始记录——两边在这一步是同一个东西。两处别弄反：在取数那一层投影的话，
        ``source_labels`` 会拿到别名，而 1.0 的 ``source_labels`` 是投影**之后**重算的。
        """
        buckets: dict[tuple[str, str, str], list[EntityGroupMember]] = {}
        rationales: dict[tuple[str, str, str], str] = {}
        for entity in self._entities.list_entities(scope_id=scope_id):
            if entity.entity_type == ENTITY_TYPE_PERSON or entity.status in SKIPPED_STATUSES:
                continue
            properties = {item.property_key: item.value for item in self._entities.list_properties(entity.id)}
            key = semantic_entity_key(
                entity_type=entity.entity_type,
                name=entity.canonical_name,
                semantic_primary=_first_text(properties.get(SEMANTIC_PRIMARY_KEY)),
            )
            group_key = (
                entity.scope_id or DEFAULT_SCOPE_ID,
                group_entity_type(entity.entity_type),
                key.label,
            )
            # ``rationale`` 取**第一个**成员的那次判定（1.0 是 ``setdefault``）。
            rationales.setdefault(group_key, key.strategy)
            # **只对 place 做私密投影**（1.0 ``public_entity`` 对别的类型原样返回）。
            if entity.entity_type == ENTITY_TYPE_PLACE:
                name, private = private_place(entity.canonical_name, properties)
            else:
                name, private = entity.canonical_name, None
            buckets.setdefault(group_key, []).append(
                EntityGroupMember(
                    entity=entity,
                    canonical_name=name,
                    private=private,
                    decoration=self._entities.decorate_entities([entity.id])[entity.id],
                    semantic_details=_multi_text(properties.get(SEMANTIC_DETAILS_KEY)),
                )
            )
        return tuple(
            sorted(
                (self._build(key, members, rationales[key]) for key, members in buckets.items()),
                key=lambda group: (group.entity_type, -group.evidence_count, group.canonical_name),
            )
        )

    def get(self, group_id: str, *, scope_id: str | None = None) -> EntityGroupDetail | None:
        """一个组 + 各成员的实体详情。1.0 ``db.py:4249-4266``。

        **找组是线性扫描**（1.0 就是 ``for group in self.list_semantic_entity_groups()``），
        不是按 id 查表——组的 id 是拼出来的，没有表可查。
        """
        for group in self.execute(scope_id=scope_id):
            if group.id != group_id:
                continue
            found = (self._details.execute(entity_id) for entity_id in group.member_entity_ids)
            return EntityGroupDetail(group=group, members=tuple(item for item in found if item is not None))
        return None

    def _build(
        self, key: tuple[str, str, str], members: list[EntityGroupMember], strategy: str
    ) -> EntityGroup:
        scope_id, entity_type, label = key
        # 1.0 ``:4229-4235`` 的三处排序，各自不同：成员按 ``(-evidence_count, name)``、
        # id 表按字符串、``source_labels`` 由**排完序的**成员重算（所以它不是追加顺序）。
        ordered = sorted(members, key=lambda item: (-item.decoration.evidence_count, item.canonical_name))
        # 预览取**追加顺序**里第一个有预览的成员——不是排完序之后的那个：1.0 是在循环里
        # 边加边挑的（``:4228``），那一步在排序（``:4230``）之前。
        preview = next((item for item in members if item.decoration.preview_asset_id is not None), None)
        return EntityGroup(
            id=GROUP_ID_PREFIX + GROUP_ID_SEPARATOR.join((scope_id, entity_type, label)),
            scope_id=scope_id,
            entity_type=entity_type,
            canonical_name=label,
            members=tuple(ordered),
            member_entity_ids=tuple(sorted(item.entity.id for item in members)),
            source_labels=tuple(item.canonical_name for item in ordered),
            semantic_details=_semantic_details(members),
            evidence_count=sum(item.decoration.evidence_count for item in members),
            confidence=max((item.entity.confidence for item in members), default=0.0),
            preview_asset_id=preview.decoration.preview_asset_id if preview else None,
            preview_file_name=preview.decoration.preview_file_name if preview else None,
            preview_media_type=preview.decoration.preview_media_type if preview else None,
            rationale_strategy=strategy,
            is_semantic_cluster=len(members) > 1,
        )


def _first_text(value: object) -> str | None:
    """``semantic_primary`` 的取值。它可能是串，也可能是单元素表（属性值的类型不固定）。"""
    if isinstance(value, str):
        return value or None
    if isinstance(value, (list, tuple)) and value:
        return str(value[0])
    return None


def _multi_text(value: object) -> tuple[str, ...]:
    """一个多值属性的取值。1.0 ``:4221-4225``：不是表的包成单元素表，逐个 ``str()``，
    **丢掉转出来是空白的那些**，再按出现顺序去重。"""
    values = value if isinstance(value, (list, tuple)) else [value]
    cleaned = [str(item) for item in values if str(item).strip()]
    return tuple(dict.fromkeys(cleaned))


def _semantic_details(members: list[EntityGroupMember]) -> tuple[str, ...]:
    """组的多值细节：把成员的摊平再按出现顺序去重。

    1.0 是在循环里边加边去重的（``dict.fromkeys(已有 + 新的)``），所以顺序是**成员顺序**，
    不是排序后的成员顺序。
    """
    collected: list[str] = []
    for member in members:
        collected.extend(member.semantic_details)
    return tuple(dict.fromkeys(collected))

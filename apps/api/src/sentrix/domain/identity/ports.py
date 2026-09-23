"""身份域端口：人脸仓储与实体仓储。

domain 只声明形状，SQL 在 infrastructure。两个仓储分开不是因为表多，而是因为**写侧
是两条不同的链路**：``FaceRepository`` 的写入者是在线匹配与簇审阅（Task 72/73），
``EntityRepository`` 的写入者是命名与属性维护（Task 73/74）。合成一个
``IdentityRepository`` 会让「谁改了实体的名字」这件事在类型上消失。

**成员一律声明成方法，只读属性用 ``@property``。** 沿用
``domain/retrieval/ports.py`` 里 ``VisualEmbedder`` 的注解：适配器把只读属性实现成
property 时，mypy 的 ``arg-type`` 不允许一个只读 property 去满足「可写变量」的协议约定。
本文件目前没有属性成员，这条备忘留给下一个往这里加属性的人——写成裸属性名会红。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sentrix.domain.identity.models import (
    PROPERTY_SOURCE_DERIVED,
    EntityMentionRecord,
    EntityPropertyRecord,
    EntityRecord,
    EntityRevisionRecord,
    FaceClusterRecord,
    FaceInstanceRecord,
    FacePrototypeRecord,
    PersonNameMatch,
)


class FaceRepository(Protocol):
    """人脸簇 / 实例 / 原型。

    ``list_clusters`` 强制 ``memory_spaces.include_in_people = 1``——这是端口的契约，
    不是某个调用方的过滤：不给这一条，任何人写一次 ``/api/people`` 都会把评测空间
    聚出来的人脸当家庭成员展示。
    """

    def list_clusters(self, *, status: str | None = None) -> Sequence[FaceClusterRecord]:
        """``status`` 为空时返回除 ``rejected`` 外的全部簇，且只含进人物视图的空间。"""
        ...

    def get_instance(self, instance_id: str) -> FaceInstanceRecord | None:
        """实例带出它所属 asset 的路径 / 文件名 / 媒体类型与 scope。"""
        ...

    def list_prototypes(self, cluster_id: str) -> Sequence[FacePrototypeRecord]: ...

    def refresh_prototypes(self, cluster_id: str) -> Sequence[FacePrototypeRecord]:
        """按当前实例重算该簇的原型并回写 ``member_count``。

        先删后插：同一 ``pose_bucket`` 上留着旧行时，新行会撞 ``UNIQUE(cluster_id,
        pose_bucket)``。返回重算后的原型，省掉调用方再查一次。
        """
        ...


class EntityRepository(Protocol):
    """实体 / 修订 / 属性 / 提及。

    属性写入分两条路，这是 1.0 验证过的契约：``set_property`` 是**用户**写下当前值，
    ``maintain_property`` 是**派生**值入账。派生永远不许顶掉用户值。
    """

    def get(self, entity_id: str) -> EntityRecord | None: ...

    def find_confirmed_person_by_name(self, scope_id: str, name: str) -> PersonNameMatch | None:
        """本 scope 内 ``entity_type='person' AND status='confirmed'`` 的人。

        **先精确名，再遍历 aliases**：反过来的话，某人的别名会抢走与之同名的另一个人。
        """
        ...

    def record_revision(
        self,
        entity_id: str,
        field_name: str,
        old_value: str | None,
        new_value: str | None,
        *,
        source: str,
        evidence_ids: Sequence[str] = (),
    ) -> EntityRevisionRecord:
        """追加一行审计。调用它一次就写一行，不做去重。"""
        ...

    def list_properties(
        self, entity_id: str, *, include_history: bool = False
    ) -> Sequence[EntityPropertyRecord]:
        """``include_history`` 为假时只返回 ``active`` / ``pending`` 的行。"""
        ...

    def maintain_property(
        self,
        entity_id: str,
        property_key: str,
        value: object,
        *,
        confidence: float = 0.0,
        evidence_ids: Sequence[str] = (),
        source: str = PROPERTY_SOURCE_DERIVED,
    ) -> EntityPropertyRecord:
        """写入一个派生值，返回**写入后当前生效**的那一行。

        当前值是用户值 → 原样返回它、不写任何行；与当前派生值相同 → 只抬置信度并合并
        证据；不同 → 新起一行 ``status='pending'``，把被取代的那行记进
        ``supersedes_property_id``。
        """
        ...

    def set_property(
        self,
        entity_id: str,
        property_key: str,
        value: object,
        *,
        evidence_ids: Sequence[str] = (),
    ) -> EntityPropertyRecord:
        """用户写下当前值：旧行转 ``superseded``、新行 ``source='user'``，并留一条修订。"""
        ...

    def person_aliases(self, entity_id: str) -> tuple[str, ...]: ...

    def set_person_aliases(self, entity_id: str, aliases: Sequence[str]) -> tuple[str, ...]:
        """整表替换别名，空串与重复项被清掉。实体不存在时返回空。"""
        ...

    def rename_person(self, entity_id: str, new_name: str) -> EntityRecord | None:
        """改一个 person 实体的本名：把旧名折进 aliases、``name_state`` 转 confirmed、
        写一条 ``source='user_rename'`` 的修订。非 person 实体返回 ``None``，空名字抛
        ``ValueError``——空名字不是「不改名」，是调用方出错。"""
        ...

    def link_confirmed_mention(
        self,
        entity_id: str,
        observation_id: str,
        *,
        face_instance_id: str | None = None,
        confidence: float = 0.0,
    ) -> EntityMentionRecord:
        """写一条提及，并把这个人追加进观察的人物快照。

        只接受 ``status='confirmed'`` 的实体，未确认的候选人物抛 ``ValueError``：
        这个快照是事件 ``person_score`` 与 ``person_bridge`` 词法投影的输入，混进一个
        尚未确认的候选，等于让聚类的一次猜测变成检索事实。重复调用幂等，返回既有行。
        """
        ...

    def event_ids(self, entity_id: str) -> Sequence[str]:
        """这个人牵涉到的事件：提及桥 / 事件参与者投影 / 实体-观察关联三条来源的并集。"""
        ...

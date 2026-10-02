"""建关系 ``CreateRelationship``（S7a · T206）。

1.0 的 ``create_relationship``（``db.py:5970``）在落库之后还有一步：``status`` 变成
``active`` 时调 ``maintain_relationship_claim``（``:5950``）把这条关系写成 **subject
实体的一条 semantic claim**（``dimension="relationship"``）。1.0 前端那三处建关系
（``src/app.js:1732`` / ``:1765`` / ``:1831``）传的都是 ``status: "active"``，
保存后的提示语就是「已保存家庭关系：X，**并写入长期记忆**」——所以这一步是**产品行为**，
不是内部记账。

**为什么它在这里而不是仓储里**（用户 2026-09-28 裁决 A）：``infrastructure-purity``
契约不允许基础设施层调用例，而仓储层的职责是「只写关系行」。所以那一半由 application
编排。**且不回滚**：claim 维护失败时关系行留着——「关系已确认但事实层没有对应声明」
是可能出现且被接受的中间状态。

**判据是落库之后的 ``status``，不是入参。** 1.0 的合并分支里
``next_status = "active" if status == "active" else existing["status"]``，而写 claim 的
条件是 ``next_status == "active"``——所以**入参 pending、命中的旧行已经是 active 时，
claim 照样要写**。取返回值的 ``status`` 与那个表达式逐字等价。
"""

from __future__ import annotations

from collections.abc import Sequence

from sentrix.domain.identity.models import RelationshipRecord
from sentrix.domain.identity.ports import EntityRepository, RelationshipRepository, SemanticRepository

# 1.0 ``db.py:5960`` 的 ``dimension="relationship"``。``semantic_claims`` 的
# ``predicate`` 归一表里没有它，原样落库。
RELATIONSHIP_DIMENSION = "relationship"

# 1.0 ``db.py:5957`` 的 ``object_entity.get("canonical_name") or "家人"``。对方实体
# 名字为空时用这个兜底——**有名字就用名字**，别把它当默认值。
FALLBACK_VALUE_TEXT = "家人"

# 1.0 ``db.py:5959`` 的 ``float(relationship.get("confidence") or 0.75)``：
# 置信度是 0 时兜底成 0.75（``or`` 而不是 ``is None``）。
FALLBACK_CLAIM_CONFIDENCE = 0.75

# 1.0 ``db.py:5961`` 的 ``confidence_source="user-confirmed"``。它是 claim 的**来源标记**：
# ``maintain_semantic_claim`` 的合并分支会把它覆盖成新值，所以后写的那次说了算。
USER_CONFIRMED_SOURCE = "user-confirmed"

# 1.0 ``db.py:6010`` 的 ``status == "active"``。
ACTIVE_STATUS = "active"


class CreateRelationship:
    def __init__(
        self,
        *,
        entities: EntityRepository,
        relationships: RelationshipRepository,
        claims: SemanticRepository,
    ) -> None:
        self._entities = entities
        self._relationships = relationships
        self._claims = claims

    def execute(
        self,
        *,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str,
        evidence_ids: Sequence[str] = (),
        confidence: float = 0.5,
        status: str = "pending",
    ) -> RelationshipRecord:
        """落一条关系；如果它（或它并掉的那条）是 ``active``，再写一条 claim。

        1.0 的顺序就是先落关系再写 claim（``db.py:5993-6005``），中间**没有事务包着**
        ——见模块 docstring 的「且不回滚」。
        """
        record = self._relationships.create(
            subject_entity_id=subject_entity_id,
            predicate=predicate,
            object_entity_id=object_entity_id,
            evidence_ids=evidence_ids,
            confidence=confidence,
            status=status,
        )
        if record.status == ACTIVE_STATUS:
            self._write_claim(record)
        return record

    def _write_claim(self, record: RelationshipRecord) -> None:
        """1.0 ``maintain_relationship_claim``（``db.py:5950-5968``）。

        三处照搬：**claim 挂在 subject 上**（不是 object，也不是两边各一条）；
        ``value_text`` 是**对方实体的 canonical_name**（空则 ``"家人"``）；谓词或任一端
        读不回来时**什么都不写**直接返回（``:5954`` 的 ``if not predicate or not subject
        or not object_entity: return None``）。
        """
        predicate = (record.predicate or "").strip()
        subject = self._entities.get(record.subject_entity_id)
        object_entity = self._entities.get(record.object_entity_id)
        if not predicate or subject is None or object_entity is None:
            return
        self._claims.maintain_claim(
            person_id=subject.id,
            scope_id=subject.scope_id,
            dimension=RELATIONSHIP_DIMENSION,
            predicate=predicate,
            value_text=object_entity.canonical_name or FALLBACK_VALUE_TEXT,
            evidence_ids=record.evidence_ids,
            confidence=record.confidence or FALLBACK_CLAIM_CONFIDENCE,
            confidence_source=USER_CONFIRMED_SOURCE,
        )

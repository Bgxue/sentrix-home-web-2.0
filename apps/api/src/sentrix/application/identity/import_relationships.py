"""批量导入关系 ``ImportRelationships``（S7a · T203）。

对应 1.0 ``app.py:2120-2167`` 的 ``POST /api/relationships/batch``。它的调用方是
**PhotoBench 的 identity_seed 阶段**（``services/photobench/backend/benchmark_orchestrator.py:2718``）：
manifest 里的 ``family_relationships`` 一次灌进来，名字先经 ``/api/people/seed-batch``
换成 id（``entity_by_name``）。1.0 前端不调这个端点。

**为什么它是一个用例而不是路由里的一个循环。** 名字解析不是搬字段：它要在
``list_entities`` 的结果上建**两张**索引（``canonical_name`` 与 ``family_role``），
先信调用方给的 ``entity_by_name``，再回退到索引；解析不到的那一条**不让整批失败**，
逐条记 ``error`` 后继续。这是编排与部分失败语义——1.0 写在路由里只是因为它没有分层。

**1.0 的三条照搬，每一处都能悄悄改错：**

1. **``family_role`` 也进索引**（``app.py:2132``）：``subject="妈妈"`` 在没人叫「妈妈」
   但有人 ``family_role="妈妈"`` 时能解析到。这条别名规则没有任何提示，也没有第二个
   地方这么用 ``family_role``。
2. **解析不到的失败串逐条记，值是 ``"unresolved entity"``**（``app.py:2148``）；
   ``imported`` 数的是**没有 error 的那些**（``:2165`` 的 ``not r.get("error")``）。
3. **每条恒以 ``status="pending"``、``evidence_ids=[]`` 落库**（``app.py:2153``），
   ``confidence`` 走 ``float(... or 0.5)``——``0`` 也会变成 ``0.5``。批量进来的边
   一律是待确认的，确认与否由人决定。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sentrix.domain.identity.ports import EntityRepository, RelationshipRepository

# 1.0 ``app.py:2148`` 的字面量。前端与 PhotoBench 会把它当「名字没换成 id」读。
UNRESOLVED = "unresolved entity"

# 1.0 ``app.py:2158`` 的 ``str(exc)[:200]``。
ERROR_LIMIT = 200

# 1.0 ``app.py:2154`` 的 ``float(rel.get("confidence") or 0.5)``。
DEFAULT_CONFIDENCE = 0.5

# 1.0 ``app.py:2153`` 硬编码的 ``"pending"``——批量导入的边一律待确认。
IMPORTED_STATUS = "pending"


@dataclass(frozen=True, slots=True)
class RelationshipRequest:
    """manifest 里的一条。**名字**（不是 id）——解析在用例里做。"""

    subject: str
    predicate: str
    object: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ImportedRelationship:
    """逐条结论。``id`` 与 ``error`` **二选一**（1.0 是键的缺席，2.0 用 ``None``）。"""

    subject: str
    predicate: str
    object: str
    id: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RelationshipImport:
    requested: int
    imported: int
    results: tuple[ImportedRelationship, ...]


class ImportRelationships:
    def __init__(
        self,
        *,
        entities: EntityRepository,
        relationships: RelationshipRepository,
    ) -> None:
        self._entities = entities
        self._relationships = relationships

    def execute(
        self,
        *,
        scope_id: str,
        entity_by_name: Mapping[str, str],
        requests: Sequence[RelationshipRequest],
    ) -> RelationshipImport:
        """``scope_id`` 为空串时**不按空间过滤**——1.0 的路由把缺失的 scope 归一成
        ``""``（``app.py:2122`` 的 ``str(payload.get("scope_id") or "")``），而
        ``list_entities`` 的守卫是 ``if scope_id:``，所以空串等于「全部空间」。
        """
        index = self._index_by_name(scope_id)
        results: list[ImportedRelationship] = []
        for request in requests:
            subject_id = self._resolve(request.subject, entity_by_name, index)
            object_id = self._resolve(request.object, entity_by_name, index)
            if not subject_id or not object_id or not request.predicate:
                results.append(
                    ImportedRelationship(request.subject, request.predicate, request.object, error=UNRESOLVED)
                )
                continue
            try:
                created = self._relationships.create(
                    subject_entity_id=subject_id,
                    predicate=request.predicate,
                    object_entity_id=object_id,
                    evidence_ids=(),
                    confidence=request.confidence,
                    status=IMPORTED_STATUS,
                )
            except Exception as error:  # 1.0 就是逐条接住——一条坏了不拖垮整批
                results.append(
                    ImportedRelationship(
                        request.subject,
                        request.predicate,
                        request.object,
                        error=str(error)[:ERROR_LIMIT],
                    )
                )
                continue
            results.append(
                ImportedRelationship(request.subject, request.predicate, request.object, id=created.id)
            )
        return RelationshipImport(
            requested=len(requests),
            # 1.0 是 ``not r.get("error")``——空串也算成功。照搬。
            imported=sum(1 for item in results if not item.error),
            results=tuple(results),
        )

    def _index_by_name(self, scope_id: str) -> dict[str, str]:
        """1.0 ``app.py:2126-2137``：``canonical_name`` 与 ``family_role`` **都进索引**。

        两边都是 ``setdefault``，所以同名实体取 ``list_entities`` 先给的那个
        （它按 ``updated_at DESC`` 排），且**名字的优先级高于角色**。
        """
        index: dict[str, str] = {}
        for entity in self._entities.list_entities(scope_id=scope_id or None):
            name = entity.canonical_name.strip()
            if name:
                index.setdefault(name, entity.id)
            role = (entity.family_role or "").strip()
            if role:
                index.setdefault(role, entity.id)
        return index

    @staticmethod
    def _resolve(
        name: str,
        entity_by_name: Mapping[str, str],
        index: Mapping[str, str],
    ) -> str | None:
        """1.0 ``app.py:2139-2145``：**先信调用方给的映射**，再回退到名字索引。

        空名字直接判失败——1.0 是 ``if not key: return None``。
        """
        key = name.strip()
        if not key:
            return None
        if key in entity_by_name:
            return entity_by_name[key]
        return index.get(key)

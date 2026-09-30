"""导入用例：把一次上传变成 Asset + 地点 + 视觉向量 + Observation + 事件 + 词法投影。

业务规则（都来自 1.0 的既有行为）：
- 内容 SHA-256 去重，**按 scope 隔离**，不跨相册去重
- 去重命中时不重复落盘
- 拍摄时间与 GPS 来自 EXIF，抽不到就留空，**不阻断导入**

S2 追加一条：**导入即向量化**（1.0 ``pipeline.py`` 的 ``_commit_fast_image`` 同样
时机）。``visual_indexer`` 是可选协作者——不传就退化成纯导入，这样单元测试与不关心
视觉记忆的调用方都不必构造嵌入器。

S5 追加一条：**导入即建记忆空间**。落点照 1.0 ``db.py:1462`` 的 ``create_asset``
——单张 ``/api/ingest`` 与批量 ``/api/import`` 都从它过，2.0 的同一个公共落点是这里
（``ImportAlbum`` 也走它，两条入口一次覆盖）。没有这一步，全新部署导入一整册之后
**审阅页的「待命名成员」列表恒空**：``FaceRepository.list_clusters`` **内连**
``memory_spaces``，而全仓没有第二个写入者（``s5-deviations`` 第 97 条；措辞由第 103 条
纠正——``EntityRepository.list_people`` 用的是 ``scope_id NOT IN (SELECT ...)`` 子查询，
不受影响）。名字与类型取 1.0 的 ``create_memory_space(scope_id, scope_id)``：``name``
与 ``id`` 同值，``kind`` 走签名默认 ``household``——批量端点里那个 ``benchmark`` 是
1.0 的评测旁路，按第 6/30 条不移植进生产代码。

S4 追加一条**顺序是硬约束**（``s4-deviations.md`` §2.3）：

    存储 → EXIF/GPS → 地点解析 → 视觉向量 → 观察 → 语义富集 → 人脸登记 → 事件归属 → 事实写入 → 词法投影

- **视觉向量先于事件归属**：``score_event_candidate`` 的 ``visual_boost``（权重 0.05）
  与 ``split_guard`` 都读 ``memory_vectors`` 里 ``space='visual' & source_type='asset'``
  的向量。顺序写反**不报错**——``_event_visual_similarity`` 返回 ``(0.0, False)``，
  ``visual_boost`` 恒 0、``split_guard`` 因 ``not visual_available`` 更易命中，事件
  照分照建，只是分错了。1.0 在 ``pipeline.py:315-325`` 用注释钉过这件事，
  ``tests/integration/test_import_pipeline.py`` 用调用序断言钉住它。
- **地点解析先于事件归属**：事件的 ``place`` / ``admin_district`` 取的是 ``asset_places``
  那一行（``AssignEvents._create`` / ``_fold``），解析没做完时事件里就是空的。
- **词法投影挂在观察写入之后逐条写**：1.0 的 ``refresh_from_observation`` 存在但生产
  零调用，2.0 不做懒补建。
- **人脸登记先于事件归属**（S5 追加）。1.0 在 ``pipeline.py:316-320`` 用注释钉过这件事：
  与某个已确认的人相像的脸会在登记时写下 ``entity_mentions``，而事件归并读的正是它——
  ``_confirmed_entity_ids_for_observation`` 拿它算 ``person_score``。顺序写反**不报错**：
  提及是空的，``person_score`` 恒 0、``corroborated`` 更容易为假，事件照分照建，只是
  **分错了**（两张同一个人出现的照片会被拆成两个事件）。与「视觉向量先于事件归属」
  同一条纪律，由 ``tests/integration/test_import_identity.py`` 的对照用例钉住。
- **事实写入排在事件归属之后**：1.0 ``pipeline.process()`` 的顺序就是
  ``merge_observation_into_event`` → ``maintain_fact``（``pipeline.py:340-350``，
  计划 Task 52 的「52 在 51 之后」也是这个依据）。事实是**观察级**的：一条观察一组
  候选事实，逐条处理，不等批末。

协作者全是可选的：不传就退化成纯导入（本文件的既有调用方与单元测试不必构造整条链）。

**去重命中时只重跑幂等的那几步**（视觉向量 + 地点）。事件归属不重跑：它不是幂等的，
对同一条 Observation 再跑一次可能给它开出第二个事件。1.0 有过 223 张图没有任何视觉
向量却毫无征兆的记录，重新导入是使用者唯一能想到的补救动作，它必须真的有效——
但有效的那部分只包括「重算一遍属性」。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.media.ports import (
    AssetObserver,
    AssetRepository,
    MediaStorage,
    MetadataReader,
    ObservationRepository,
)
from sentrix.domain.places.ports import AssetPlaceIndex
from sentrix.domain.retrieval.ports import AssetVisualIndexer, LexicalIndex
from sentrix.domain.spaces.ports import SpaceRepository


@dataclass(frozen=True, slots=True)
class ImportRequest:
    scope_id: str
    file_name: str
    content_type: str | None
    content: bytes


@dataclass(frozen=True, slots=True)
class ImportResult:
    asset_id: str
    observation_id: str | None
    media_type: MediaType
    status: AssetStatus
    deduplicated: bool
    indexed: bool = False
    placed: bool = False
    event_id: str | None = None
    lexical_terms: int = 0
    # 本次导入登记的人脸实例数。没有协作者时是 0——「没有人脸能力」与「这张图没有脸」
    # 是两件事，但要分开它们得先有个可观测面，这个计数就是。
    faces: int = 0
    # 1.0 把这份 id 表写进 ``assets.metadata_json["fact_ids"]``。2.0 不往 asset 上挂
    # JSON blob，但要留下同一个可观测面：调用方与测试都靠它区分「门禁关了」与「没接线」。
    fact_ids: tuple[str, ...] = ()
    # 本次写进去的文本向量行数（0 或 3）。与 ``faces`` 同一个理由：
    # 「这台机器没有文本能力」与「这段文本拼出来是空的」要能分开，
    # 而它们都表现为「没有文本向量」。
    text_rows: int = 0
    # 本次建/找到的非人物实体 id（S6 T127）。与 ``fact_ids`` 同一个理由：
    # 「没接这个协作者」与「这张图里没有地点/物件」都表现为「实体数没变」，
    # 要分开它们就得有个可观测面。**它在下游有用**：``retrieve_entity`` 的
    # ``person_bridge`` 与 ``entity_groups`` 都按实体 id 聚合。
    entity_ids: tuple[str, ...] = ()


def _new_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ImportAssets:
    def __init__(
        self,
        *,
        assets: AssetRepository,
        observations: ObservationRepository,
        storage: MediaStorage,
        metadata: MetadataReader,
        visual_indexer: AssetVisualIndexer | None = None,
        place_indexer: AssetPlaceIndex | None = None,
        observer: AssetObserver | None = None,
        event_assigner: Callable[[Observation], str] | None = None,
        face_enroller: Callable[[Asset, Observation], int] | None = None,
        fact_maintainer: Callable[[Observation], tuple[str, ...]] | None = None,
        text_indexer: Callable[[Observation, str | None], int] | None = None,
        # 非人物实体抽取（S6 T127）。**入参带 event_id**：1.0 ``pipeline.py:336`` 在事件
        # 判定之后才调它，实体要挂到那个事件上（``event_entities``）。
        entity_extractor: Callable[[Observation, str], tuple[str, ...]] | None = None,
        lexical_index: LexicalIndex | None = None,
        spaces: SpaceRepository | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._assets = assets
        self._observations = observations
        self._storage = storage
        self._metadata = metadata
        self._visual_indexer = visual_indexer
        self._place_indexer = place_indexer
        self._observer = observer
        self._event_assigner = event_assigner
        self._face_enroller = face_enroller
        self._fact_maintainer = fact_maintainer
        self._text_indexer = text_indexer
        self._entity_extractor = entity_extractor
        self._lexical_index = lexical_index
        self._spaces = spaces
        self._clock = clock

    def execute(self, request: ImportRequest) -> ImportResult:
        # 空间行排在最前：簇列表内连它，没有它这次导入在审阅页上等于没发生。
        # 放在去重判断**之前**——去重命中同样是一条「这个 scope 被用到了」的证据。
        self._ensure_space(request.scope_id)

        content_sha256 = hashlib.sha256(request.content).hexdigest()
        media_type = MediaType.from_upload(request.content_type, request.file_name)

        existing = self._assets.find_by_hash(request.scope_id, content_sha256)
        if existing is not None:
            return ImportResult(
                asset_id=existing.id,
                observation_id=None,
                media_type=existing.media_type,
                status=existing.status,
                deduplicated=True,
                indexed=self._index_visual(existing),
                placed=self._index_place(existing),
            )

        asset_id = _new_id("asset")
        stored_path = self._storage.store(asset_id, request.file_name, request.content)

        captured_at, gps, device = (None, None, None)
        if media_type is MediaType.IMAGE:
            captured_at, gps, device = self._metadata.read(stored_path)

        now = self._clock()
        asset = Asset(
            id=asset_id,
            scope_id=request.scope_id,
            file_name=request.file_name,
            media_type=media_type,
            path=str(stored_path),
            mime_type=request.content_type,
            size_bytes=len(request.content),
            status=AssetStatus.PROCESSED,
            content_sha256=content_sha256,
            captured_at=captured_at or now,
            captured_location=gps,
            created_at=now,
            updated_at=now,
        )
        self._assets.add(asset)

        placed = self._index_place(asset)
        indexed = self._index_visual(asset)

        observation = Observation(
            id=_new_id("obs"),
            scope_id=request.scope_id,
            asset_id=asset_id,
            source_type="import",
            captured_at=asset.captured_at,
            confidence=0.0,
            created_at=now,
            updated_at=now,
        )
        self._observations.add(observation)

        observation = self._observe(asset, observation) or observation
        faces = self._enroll_faces(asset, observation)
        event_id = self._assign_event(observation)
        # 文本向量**必须在事件归并之后**：1.0 ``pipeline.py:324`` 的注释
        # 「Update the same vector with its final event link after selection」。
        # 写在前面则 event_id 恒为空，而那条 metadata 只在召回时用得到——
        # 错了不报错，只是事件维度的扩展永远失效。
        text_rows = self._index_text(observation, event_id)
        entity_ids = self._extract_entities(observation, event_id)
        fact_ids = self._maintain_facts(observation)
        lexical_terms = self._project_lexical(observation)

        return ImportResult(
            asset_id=asset_id,
            observation_id=observation.id,
            media_type=media_type,
            status=asset.status,
            deduplicated=False,
            indexed=indexed,
            placed=placed,
            event_id=event_id,
            lexical_terms=lexical_terms,
            entity_ids=entity_ids,
            fact_ids=fact_ids,
            faces=faces,
            text_rows=text_rows,
        )

    def _extract_entities(self, observation: Observation, event_id: str | None) -> tuple[str, ...]:
        """非人物实体抽取（S6 T127）。协作者缺失就静默跳过——与其余可选协作者同形。

        **排在事件判定之后**，与 1.0 ``pipeline.py:336`` 的位置一致：抽出来的地点/物件/
        时间要挂到那个事件上，事件还没定就挂不上去。
        """
        if self._entity_extractor is None:
            return ()
        return self._entity_extractor(observation, event_id or "")

    # ---- 可选协作者 ------------------------------------------------------------

    def _ensure_space(self, scope_id: str) -> None:
        """保证这个 scope 在 ``memory_spaces`` 里有一行。1.0 ``db.py:1462``。

        不传协作者就静默跳过——与其余可选协作者同一个形状。代价写在模块注释里：
        那条路径上人物页仍然是空的，用例把这两种情形分开钉住。
        """
        if self._spaces is None:
            return
        self._spaces.ensure(scope_id, scope_id)

    def _index_visual(self, asset: Asset) -> bool:
        if self._visual_indexer is None:
            return False
        return self._visual_indexer.index(asset).indexed

    def _index_text(self, observation: Observation, event_id: str | None) -> int:
        """写文本向量，返回写了几行。与 ``_index_visual`` / ``_index_place`` 同形：
        协作者缺失就静默跳过，成败在用例内部判定。

        失败原因（空文本 / 模型不可用 / 维度不符）**不在这里分流**——
        与上面两个索引器同一个缺口：``ImportResult`` 只有 ``indexed`` / ``placed``
        两个布尔，分不出「没有文本能力」与「这段文本拼出来是空的」。
        那是要在 ``ImportResult`` 上开字段的事，本任务不动它。
        """
        if self._text_indexer is None:
            return 0
        return self._text_indexer(observation, event_id)

    def _index_place(self, asset: Asset) -> bool:
        if self._place_indexer is None:
            return False
        return self._place_indexer.index(asset)

    def _observe(self, asset: Asset, observation: Observation) -> Observation | None:
        if self._observer is None:
            return None
        return self._observer.observe(asset, observation)

    def _enroll_faces(self, asset: Asset, observation: Observation) -> int:
        """登记这张图里检出的每一张脸。**必须排在事件归并之前**，理由见模块注释第 4 条。"""
        if self._face_enroller is None:
            return 0
        return self._face_enroller(asset, observation)

    def _assign_event(self, observation: Observation) -> str | None:
        if self._event_assigner is None:
            return None
        return self._event_assigner(observation)

    def _maintain_facts(self, observation: Observation) -> tuple[str, ...]:
        if self._fact_maintainer is None:
            return ()
        return self._fact_maintainer(observation)

    def _project_lexical(self, observation: Observation) -> int:
        if self._lexical_index is None:
            return 0
        return self._lexical_index.refresh_from_observation(observation)

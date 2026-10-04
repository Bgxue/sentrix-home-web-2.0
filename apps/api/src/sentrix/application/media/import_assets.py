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
纠正——``EntityRepository.list_entities`` 用的是 ``scope_id NOT IN (SELECT ...)`` 子查询，
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

S8a 追加一条：**视频不再静默空转**。原先只有 ``media_type is IMAGE`` 才读元数据
（本文件旧版 :179-180），视频拿 ``(None, None, None)`` 却照样入库，于是「收下了、
什么都没发生」——与 W1 那次同类：看着正常，实际什么都没有，而且不报错。现在视频会：

1. 走 ffprobe 读时长/编码/拍摄时间/GPS（``VideoMetadataReader``）；
2. 把拍摄时间与 GPS 接到与 EXIF 同一条路上（``captured_at`` / ``captured_location``）；
3. 需要时转码出浏览器预览（``VideoTranscoder``，非 H.264 才转）；
4. 把整包结果写进 ``assets.metadata_json``（``VideoRepository.save_probe``）。

**探测失败与转码失败都不阻断导入**（与「EXIF 读不出来不阻断」同一条既有口径），
但失败原因落在资产上（``video_probe_error`` / ``browser_preview_error``），
并且 ``ImportResult.video_probed`` 把「这次到底有没有探测」变成响应里看得见的一位数。
**它不建场景、不抽帧**——那是 S8b。

**去重命中时只重跑幂等的那几步**（视觉向量 + 地点）。事件归属不重跑：它不是幂等的，
对同一条 Observation 再跑一次可能给它开出第二个事件。1.0 有过 223 张图没有任何视觉
向量却毫无征兆的记录，重新导入是使用者唯一能想到的补救动作，它必须真的有效——
但有效的那部分只包括「重算一遍属性」。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation
from sentrix.domain.media.ports import (
    AssetObserver,
    AssetRepository,
    MediaStorage,
    MetadataReader,
    ObservationRepository,
    VideoMetadataReader,
    VideoProbe,
    VideoRepository,
    VideoTranscoder,
)
from sentrix.domain.places.ports import AssetPlaceIndex
from sentrix.domain.retrieval.ports import AssetVisualIndexer, LexicalIndex
from sentrix.domain.spaces.ports import SpaceRepository

log = logging.getLogger("sentrix.media.import_assets")


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
    # 本次导入的视频有没有真的走过 ffprobe（S8a T505）。与 ``indexed`` / ``placed``
    # 同一条理由：1.0/2.0 原先「视频收下了但什么都没发生」在响应上完全不可见。
    # 视频以外一律 False（图片走 EXIF，不走这条路）。
    video_probed: bool = False
    # 本次导入**建起来**的视频场景数（S8b T512）。与 ``video_probed`` 同一条理由：
    # 「视频导入成功但一个场景都没有」必须在响应上看得见，否则又回到静默空转。
    # 非视频、或抽取链没接、或探测不到时长时都是 0。
    video_scenes: int = 0


@dataclass(frozen=True, slots=True)
class ProcessedAsset:
    """一张图片走完流水线之后各步的结果（``ImportAssets.process_image`` 的返回）。

    从 ``execute`` 里拆出来是因为**关键帧也要走这条链**（S8b T513）：视频的派生帧与
    上传的照片经历的是同一组步骤与同一套顺序，唯一差别是事件归属带了个
    ``forced_event_id``。字段与 ``ImportResult`` 的那几位一一对应，只是少了
    「导入本身」的几个（去重、探测、媒体类型）。
    """

    observation_id: str
    indexed: bool = False
    placed: bool = False
    event_id: str | None = None
    lexical_terms: int = 0
    faces: int = 0
    fact_ids: tuple[str, ...] = ()
    text_rows: int = 0
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
        # 视频三个协作者（S8a）。**成组出现**：只有 reader 没有 repository 时探测结果
        # 没有地方落，只有 repository 没有 reader 时没有东西可落——`_probe_video`
        # 因此要求前两个同时在，缺任一个就整条跳过（与其余可选协作者同一个形状）。
        video_metadata: VideoMetadataReader | None = None,
        video_transcoder: VideoTranscoder | None = None,
        video_repository: VideoRepository | None = None,
        # 关键帧挂到场景事件上的那条路（S8b T513）。**没有它就不能强制归属**：
        # 关键帧的观察属于它那一秒的源片场景，按 6 小时窗去折进某张照片的事件是错的。
        event_attacher: Callable[[str, Observation], None] | None = None,
        # 抽帧链（S8b T511–T513）。入参是源片 id，返回**建起来的场景数**。
        # 不传就退化成「只入库、不建场景」——正是 S8a 的行为，测试与不需要视频
        # 场景的调用方都不必构造它。
        video_scene_extractor: Callable[[str], int] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._assets = assets
        self._observations = observations
        self._storage = storage
        self._metadata = metadata
        self._video_metadata = video_metadata
        self._video_transcoder = video_transcoder
        self._video_repository = video_repository
        self._event_attacher = event_attacher
        self._video_scene_extractor = video_scene_extractor
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

        # 视频：探测 + 转码（S8a T505）。**排在这里、而不是排到资产落库之后**，
        # 因为拍摄时间/GPS 要跟 EXIF 一样在**建资产之前**拿到——否则视频的
        # captured_at 只能退回导入时刻，而导入时刻与拍摄时间不是一回事。
        video_probe: VideoProbe | None = None
        if (
            media_type is MediaType.VIDEO
            and self._video_metadata is not None
            and self._video_repository is not None
        ):
            video_probe = self._probe_video(asset_id, stored_path)
            if video_probe.metadata is not None:
                captured_at = video_probe.metadata.captured_datetime() or captured_at
                gps = video_probe.metadata.gps() or gps
                device = video_probe.metadata.device or device

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

        # 探测结果落库**必须在资产行之后**：它写的是 ``assets.metadata_json``。
        if video_probe is not None and self._video_repository is not None:
            self._video_repository.save_probe(asset.id, video_probe)

        processed = self.process_image(asset)
        # 场景抽取排在图片流水线**之后**：它自己会再建一批资产与观察（关键帧），
        # 先跑会把派生物的数混进「这次导入」的结果里。
        video_scenes = self._extract_video_scenes(asset)

        return ImportResult(
            asset_id=asset.id,
            observation_id=processed.observation_id,
            media_type=media_type,
            status=asset.status,
            deduplicated=False,
            indexed=processed.indexed,
            placed=processed.placed,
            event_id=processed.event_id,
            lexical_terms=processed.lexical_terms,
            entity_ids=processed.entity_ids,
            fact_ids=processed.fact_ids,
            faces=processed.faces,
            text_rows=processed.text_rows,
            video_probed=video_probe is not None and video_probe.metadata is not None,
            video_scenes=video_scenes,
        )

    def process_image(self, asset: Asset, *, forced_event_id: str | None = None) -> ProcessedAsset:
        """把一张**已经落库的图片资产**走完整条图片流水线。

        **为什么是公开方法。** 视频派生的关键帧走的就是这一条（S8b T513，对应 1.0
        ``video/processor.py:188`` 的 ``pipeline.process(keyframe_id, ...)``），所以它
        不能埋在 ``execute`` 里——那一半要负责去重、落盘、探测，关键帧三样都不需要。
        不另写一遍的理由是下面每一步的顺序都是硬约束（见模块注释里的顺序表）：
        两处各写一遍迟早有一处漏一步，而漏掉的那一步**不报错**，只是算错。

        ``forced_event_id`` 不为空时**跳过事件候选打分**，把这条观察直接挂到那条事件上
        （1.0 ``pipeline.py:333`` 的 ``attach_observation_to_event``）。关键帧属于它那一
        秒的源片场景，让 6 小时窗去替它挑事件会把它折进某张同时段的照片——那是错的，
        而且不报错。
        """
        now = self._clock()
        placed = self._index_place(asset)
        indexed = self._index_visual(asset)

        observation = Observation(
            id=_new_id("obs"),
            scope_id=asset.scope_id,
            asset_id=asset.id,
            source_type="import",
            captured_at=asset.captured_at,
            confidence=0.0,
            created_at=now,
            updated_at=now,
        )
        self._observations.add(observation)

        observation = self._observe(asset, observation) or observation
        faces = self._enroll_faces(asset, observation)
        event_id = self._assign_event(observation, forced_event_id)
        # 文本向量**必须在事件归并之后**：1.0 ``pipeline.py:324`` 的注释
        # 「Update the same vector with its final event link after selection」。
        # 写在前面则 event_id 恒为空，而那条 metadata 只在召回时用得到——
        # 错了不报错，只是事件维度的扩展永远失效。
        text_rows = self._index_text(observation, event_id)
        entity_ids = self._extract_entities(observation, event_id)
        fact_ids = self._maintain_facts(observation)
        lexical_terms = self._project_lexical(observation)

        return ProcessedAsset(
            observation_id=observation.id,
            indexed=indexed,
            placed=placed,
            event_id=event_id,
            lexical_terms=lexical_terms,
            entity_ids=entity_ids,
            fact_ids=fact_ids,
            faces=faces,
            text_rows=text_rows,
        )

    def _extract_video_scenes(self, asset: Asset) -> int:
        """导入一个视频之后建场景（S8b T512），返回建起来的场景数。

        **不阻断导入**：与 ``_probe_video`` 同一条口径（探测/转码失败都只记下来）。
        差别是这里只留一条日志——把失败原因落到资产上要再开一个 ``metadata_json`` 键，
        本切片不做（登记在 ``s8-deviations.md`` §4）。协作链没接时静默跳过，与其余
        可选协作者同形。
        """
        if asset.media_type is not MediaType.VIDEO or self._video_scene_extractor is None:
            return 0
        try:
            return int(self._video_scene_extractor(asset.id))
        except Exception as error:  # noqa: BLE001 —— 场景是增强，不该让整次导入 500
            log.warning("视频场景抽取失败 asset=%s: %s", asset.id, error)
            return 0

    def _probe_video(self, asset_id: str, path: Path) -> VideoProbe:
        """一次视频探测：ffprobe 元数据 + 必要时转码预览。

        与 ``PillowMetadataReader.read`` 同一条既有口径——**失败不阻断导入**：
        EXIF 读不出来降级成 ``(None, None, None)``，这里降级成「元数据为空 + 一条原因」。
        两者都只是记下来，资产照收；区别是这里**必须**把原因留在资产上，
        因为元数据本身就是这一路要交付的东西，没有它「收下了但什么都没发生」。
        """
        assert self._video_metadata is not None
        probe_error = ""
        metadata = None
        try:
            metadata = self._video_metadata.read(path)
        except Exception as error:  # noqa: BLE001 —— 坏文件不阻断导入，见 docstring
            probe_error = f"{type(error).__name__}: {error}"
            log.warning("视频元数据读取失败 asset=%s: %s", asset_id, probe_error)

        preview_path = ""
        preview_error = ""
        # 探测失败就不转码：连编码都不知道，转出来也没有判据说它该不该转。
        if metadata is not None and self._video_transcoder is not None:
            try:
                target = self._video_transcoder.browser_preview(asset_id, path, metadata.codec)
                preview_path = str(target) if target is not None else ""
            except Exception as error:  # noqa: BLE001 —— 预览是增强，回落原片（1.0 亦然）
                preview_error = f"{type(error).__name__}: {error}"
                log.warning("浏览器预览转码失败 asset=%s: %s", asset_id, preview_error)

        return VideoProbe(
            metadata=metadata,
            browser_preview_path=preview_path,
            browser_preview_error=preview_error,
            probe_error=probe_error,
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

    def _assign_event(self, observation: Observation, forced_event_id: str | None) -> str | None:
        if forced_event_id is not None:
            # 1.0 ``db.py:2371`` 的 ``attach_observation_to_event``：插入 event_observations，
            # 不做候选打分。``event_attacher`` 缺失是装配错误（调用方给了 forced_event_id
            # 却没接这条路），所以抛出来而不是静默退回候选打分——后者会把关键帧塞进一个
            # 不相干的事件，而且看起来一切正常。
            if self._event_attacher is None:
                raise RuntimeError("forced_event_id 需要 event_attacher 协作者")
            self._event_attacher(forced_event_id, observation)
            return forced_event_id
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

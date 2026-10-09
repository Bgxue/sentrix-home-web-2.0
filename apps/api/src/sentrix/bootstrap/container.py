"""The single wiring point.

Nothing else in the codebase constructs an adapter.  Domain and application code
receive ports; only this module knows which concrete class implements them.

字段一律按 **domain 端口** 声明。写在类型上的端口才是可替换性的凭证：只写在
``build()`` 里的替换能力，调用方看不见，于是很容易顺手用上协议之外的方法。
这条不变量由 ``apps/api/tests/unit/test_container_wiring.py`` 钉住。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sentrix.application.identity.person_directory import EntityPersonDirectory
from sentrix.config import Settings
from sentrix.domain.assistant.ports import (
    ChatModel,
    ConversationRepository,
    PersonDirectory,
    ResultSetRepository,
    TurnEventBroker,
    TurnRepository,
)
from sentrix.domain.identity.ports import (
    AppearanceRepository,
    EntityRepository,
    FaceDetector,
    FaceRepository,
    FamilyGraphRepository,
    MomentRepository,
    PersonCropRenderer,
    PersonPreviewRenderer,
    PersonVision,
    PortraitRepository,
    RelationshipRepository,
    SemanticRepository,
)
from sentrix.domain.media.ports import (
    AssetRepository,
    AudioMetadataReader,
    AudioRepository,
    MediaStorage,
    MetadataReader,
    ObservationRepository,
    PhotoInspector,
    PreviewEncoder,
    VideoKeyframeExtractor,
    VideoMetadataReader,
    VideoRepository,
    VideoTranscoder,
    VlmObserver,
)
from sentrix.domain.memories.ports import (
    EventParticipantRepository,
    EventRepository,
    FactRepository,
)
from sentrix.domain.places.aliases import PlaceAliases
from sentrix.domain.places.ports import AssetPlaceRepository, PlaceResolver
from sentrix.domain.retrieval.ports import (
    LexicalIndex,
    LexicalSearch,
    StructuredQueryRepository,
    TextEmbedder,
    VectorIndex,
    VectorRepository,
    VisualEmbedder,
)
from sentrix.domain.spaces.ports import SpaceRepository
from sentrix.infrastructure.assistant.chat_client import OpenAIChatModel
from sentrix.infrastructure.assistant.events import InProcessTurnEventBroker
from sentrix.infrastructure.assistant.model_endpoint import ServedModel
from sentrix.infrastructure.media.audio_metadata import FfprobeAudioMetadataReader
from sentrix.infrastructure.media.image_io import PillowPreviewEncoder
from sentrix.infrastructure.media.metadata import PillowMetadataReader
from sentrix.infrastructure.media.person_vision import (
    PillowPersonCropRenderer,
    PillowPersonPreviewRenderer,
)
from sentrix.infrastructure.media.person_vision_model import OpenAIPersonVision
from sentrix.infrastructure.media.photo_inspector import OpenAIPhotoInspector
from sentrix.infrastructure.media.storage import FileSystemMediaStorage
from sentrix.infrastructure.media.video_keyframes import FfmpegVideoKeyframeExtractor
from sentrix.infrastructure.media.video_metadata import FfprobeVideoMetadataReader
from sentrix.infrastructure.media.video_transcode import FfmpegVideoTranscoder
from sentrix.infrastructure.media.vlm_observer import OpenAIVlmObserver
from sentrix.infrastructure.models.face_adapter import FaceAdapter
from sentrix.infrastructure.models.wemm import WeMMSidecarEmbedder
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.appearance import SqliteAppearanceRepository
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.audio import SqliteAudioRepository
from sentrix.infrastructure.persistence.repositories.conversations import (
    SqliteConversationRepository,
)
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository
from sentrix.infrastructure.persistence.repositories.family import SqliteFamilyGraphRepository
from sentrix.infrastructure.persistence.repositories.moments import SqliteMomentRepository
from sentrix.infrastructure.persistence.repositories.participants import (
    SqliteEventParticipantRepository,
)
from sentrix.infrastructure.persistence.repositories.portraits import (
    SqlitePortraitRepository,
)
from sentrix.infrastructure.persistence.repositories.relationships import (
    SqliteRelationshipRepository,
)
from sentrix.infrastructure.persistence.repositories.result_sets import SqliteResultSetRepository
from sentrix.infrastructure.persistence.repositories.search_terms import SqliteLexicalIndex
from sentrix.infrastructure.persistence.repositories.semantic import (
    SqliteSemanticRepository,
)
from sentrix.infrastructure.persistence.repositories.spaces import SqliteSpaceRepository
from sentrix.infrastructure.persistence.repositories.structured import (
    SqliteStructuredQueryRepository,
)
from sentrix.infrastructure.persistence.repositories.turns import SqliteTurnRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository
from sentrix.infrastructure.persistence.repositories.video import SqliteVideoRepository
from sentrix.infrastructure.places.aliases import load_place_aliases
from sentrix.infrastructure.places.composite import OfflinePlaceResolver
from sentrix.infrastructure.places.geonames import GeoNamesNearestResolver
from sentrix.infrastructure.places.pygeocn import PyGeoCNAdminResolver
from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "infrastructure" / "persistence" / "migrations"


@dataclass(frozen=True)
class Container:
    settings: Settings

    # 例外：共享连接工厂，不是任何一个端口的实现（多个仓储共用同一个连接来源），
    # 且 database_healthy() 直接需要它。domain 里没有「数据库池」这个概念可依。
    database: Database

    spaces: SpaceRepository

    asset_repository: AssetRepository
    observation_repository: ObservationRepository
    media_storage: MediaStorage
    metadata_reader: MetadataReader
    preview_encoder: PreviewEncoder
    # 视频三件（S8a）：ffprobe 探测、浏览器预览转码、血缘仓储。三者都是端口声明——
    # 具体实现（子进程 / 子进程 / SQL）全在 infrastructure，见 pyproject 的两条 purity 契约。
    video_metadata_reader: VideoMetadataReader
    video_transcoder: VideoTranscoder
    # 关键帧抽取（S8b T511）：在给定的原片秒数上取一帧。切窗规则在 domain，解码在
    # 适配器里——子进程只能落 infrastructure（purity 契约）。
    video_keyframe_extractor: VideoKeyframeExtractor
    video_repository: VideoRepository
    # 音频两件（S8c T521）：ffprobe 探测 + 探测结果落库。**没有转码器**——音频不转码
    # （用户裁决②），浏览器原生放 mp3/wav/m4a/aac/flac/ogg。
    audio_metadata_reader: AudioMetadataReader
    audio_repository: AudioRepository
    vector_repository: VectorRepository
    vector_index: VectorIndex
    visual_embedder: VisualEmbedder
    text_embedder: TextEmbedder

    # 时间线（S4）。地点是纯离线的：区县走 PyGeoCN 的面，乡镇/村庄走 GeoNames 的
    # 最近邻点，运行时零网络依赖。
    place_resolver: PlaceResolver
    asset_place_repository: AssetPlaceRepository
    # 例外：`PlaceAliases` 是 domain 的数据，不是端口的实现——它由 infrastructure 从
    # 包内数据文件读进来（那是唯一的 IO 边界），而读文件这件事不能发生在 api 层。
    place_aliases: PlaceAliases
    event_repository: EventRepository
    # 事件参与者投影（S5b）。与 ``event_repository`` 分开的理由写在
    # ``domain/memories/ports.py``：写这张表的是身份侧的确认动作，不是事件本身。
    event_participant_repository: EventParticipantRepository
    fact_repository: FactRepository
    # 词法投影的写侧与读侧由同一个 SQLite 适配器实现（它是同一张投影表的两个方向），
    # 这里按两个端口分别声明：调用方在类型上就看得出自己只需要其中一侧。
    lexical_index: LexicalIndex
    lexical_search: LexicalSearch
    # 结构化聚合（S6 T121b）。与上面两个词法端口分开：那两个是**投影**
    # （可重建的派生表），这个是对 assets / asset_places / entity_mentions
    # 直接做 SQL 聚合。
    structured_query_repository: StructuredQueryRepository

    # 身份层（S5a）。两个仓储分家的理由写在 domain/identity/ports.py 的模块注释里：
    # 人脸侧的写入者是在线匹配与簇审阅，实体侧的写入者是命名与属性维护。
    face_repository: FaceRepository
    entity_repository: EntityRepository
    # 语义画像与声明（S6 T130）。这两张表从 T127 建起来后一直没有读者，是 T130
    # 给它们的第一个——**只读**：写入侧的人像流水线在 2.0 走的是
    # ``person_portrait_revisions``，与这两张表不是一套（S6 之前就有的分叉）。
    semantic_repository: SemanticRepository
    # 人脸检测与向量（S5b）。**构造它不建 session**：权重在第一次 detect 时才加载，
    # 所以容器构造期没有模型开销，缺权重的机器上它会自己退化成空表（``available``）。
    face_detector: FaceDetector

    # 外观证据与人物画像的三个模型入口（S5c）。与 ``vlm_observer`` 共用同一个端点与
    # 同一份 ``ServedModel`` 探测；**构造它不建连接**，模型不可用时各方法返回 ``None``。
    appearance_repository: AppearanceRepository
    person_vision: PersonVision
    # 头肩裁剪件（Task 85c）。按端口声明，不写具体类——装配点的可替换性只在类型上
    # 才算数（module-map 开头那条不变量）。
    person_crop_renderer: PersonCropRenderer
    # 带编号预览图（Task 86f）。与上面那个同为 PIL 边界，都是为了让 application
    # 不 import infrastructure。
    person_preview_renderer: PersonPreviewRenderer

    # 人物画像三件套（Task 86b）：瞬间 / 关系 / 画像修订。三者都只在画像流水线
    # （Task 86g）与其读侧路由（Task 86h）上出现。
    moment_repository: MomentRepository
    relationship_repository: RelationshipRepository
    family_graph_repository: FamilyGraphRepository
    portrait_repository: PortraitRepository

    chat_model: ChatModel
    vlm_observer: VlmObserver
    # 拿一个问题去问一张图（S6 的 T123/T124 共用的上游件）。与上面那个是**同一个端点的
    # 两种问法**，不是同一件事：``vlm_observer`` 的 schema 固定七个字段、不接问题。
    photo_inspector: PhotoInspector
    conversation_repository: ConversationRepository
    turn_repository: TurnRepository
    result_set_repository: ResultSetRepository
    person_directory: PersonDirectory
    # 内存里的实时事件流。它没有持久化的一面——历史事件在 agent_turn_events 表里，
    # 两半合起来才构成完整的 SSE 语义（见 domain/assistant/ports.py 的 TurnEventBroker）。
    turn_event_broker: TurnEventBroker

    @classmethod
    def build(cls, settings: Settings) -> Container:
        place_aliases = load_place_aliases()
        database = Database(settings.database_path, MIGRATIONS_DIR)
        observations = SqliteObservationRepository(database)
        assets = SqliteAssetRepository(database)
        entities = SqliteEntityRepository(database)
        asset_places = SqliteAssetPlaceRepository(database)
        # 词法投影的 geo/time/file 锚点取自 asset 与 asset_places（不是 observation），
        # 所以写侧要先把这两个仓储拿在手上。
        lexical = SqliteLexicalIndex(database, observations, assets=assets, places=asset_places)
        # 对话与视觉是**同一个端点**（8100），所以共用同一个探测。分两个实例会让同一个
        # 进程里存两份可能不一致的答案，还要多探一轮。
        served_model = ServedModel(settings.vlm_base_url)
        # 统一多模态嵌入侧车。写入与查询共用同一个实例，见下面 visual/text 两槽的注释。
        wemm_embedder = WeMMSidecarEmbedder(settings.wemm_url)
        # 家庭图谱仓储被两个消费方用：容器自己的字段，以及人物目录——目录要靠它回答
        # 「这个人的归属与关系」（S7c）。拿在手上传两次，而不是就地 new 两个实例。
        family = SqliteFamilyGraphRepository(database, entities)
        return cls(
            settings=settings,
            database=database,
            spaces=SqliteSpaceRepository(database),
            asset_repository=assets,
            observation_repository=observations,
            media_storage=FileSystemMediaStorage(settings.media_dir),
            metadata_reader=PillowMetadataReader(),
            preview_encoder=PillowPreviewEncoder(),
            video_metadata_reader=FfprobeVideoMetadataReader(),
            # 预览转码件落在 ``media_dir/derived/video`` 下：与其余媒体文件同一个根，
            # 换数据目录就整体换（与 ``Settings`` 里 preview_dir / qdrant_dir 同一条理由）。
            video_transcoder=FfmpegVideoTranscoder(settings.media_dir),
            video_keyframe_extractor=FfmpegVideoKeyframeExtractor(settings.media_dir),
            video_repository=SqliteVideoRepository(database),
            audio_metadata_reader=FfprobeAudioMetadataReader(),
            audio_repository=SqliteAudioRepository(database),
            vector_repository=SqliteVectorRepository(database),
            vector_index=QdrantVectorIndex(settings.qdrant_dir),
            # **两个槽指向同一个实例**（W1 起方案是统一空间）。建两个实例不只是浪费
            # ——1.0 ``router.py:57-63`` 记着理由：两个实例会让侧车那侧的显存翻倍，
            # 而且两个实例的健康状态与熔断计数会各自漂移，同一个模型却有两种可用性。
            visual_embedder=wemm_embedder,
            text_embedder=wemm_embedder,
            place_resolver=OfflinePlaceResolver(
                admin=PyGeoCNAdminResolver(),
                nearest=GeoNamesNearestResolver(settings.geo_dir),
            ),
            asset_place_repository=asset_places,
            place_aliases=place_aliases,
            event_repository=SqliteEventRepository(database),
            event_participant_repository=SqliteEventParticipantRepository(database),
            fact_repository=SqliteFactRepository(database),
            lexical_index=lexical,
            lexical_search=lexical,
            structured_query_repository=SqliteStructuredQueryRepository(database, place_aliases),
            face_repository=SqliteFaceRepository(database),
            entity_repository=entities,
            semantic_repository=SqliteSemanticRepository(database),
            face_detector=FaceAdapter(),
            appearance_repository=SqliteAppearanceRepository(database),
            person_vision=OpenAIPersonVision(
                settings.vlm_base_url,
                served_model,
                timeout_s=settings.vlm_timeout_s,
            ),
            person_crop_renderer=PillowPersonCropRenderer(),
            person_preview_renderer=PillowPersonPreviewRenderer(),
            moment_repository=SqliteMomentRepository(database),
            relationship_repository=SqliteRelationshipRepository(database),
            family_graph_repository=family,
            portrait_repository=SqlitePortraitRepository(database),
            chat_model=OpenAIChatModel(
                settings.vlm_base_url,
                served_model,
                timeout_s=settings.chat_timeout_s,
                context_tokens=settings.vlm_context_tokens,
            ),
            vlm_observer=OpenAIVlmObserver(
                settings.vlm_base_url,
                served_model,
                timeout_s=settings.vlm_timeout_s,
            ),
            photo_inspector=OpenAIPhotoInspector(
                settings.vlm_base_url,
                served_model,
                timeout_s=settings.vlm_timeout_s,
            ),
            conversation_repository=SqliteConversationRepository(database),
            turn_repository=SqliteTurnRepository(database),
            result_set_repository=SqliteResultSetRepository(database),
            person_directory=EntityPersonDirectory(entities=entities, family=family),
            turn_event_broker=InProcessTurnEventBroker(),
        )

    def database_healthy(self) -> bool:
        try:
            with self.database.connect() as connection:
                connection.execute("SELECT 1 FROM schema_migrations LIMIT 1").fetchone()
        except sqlite3.Error:
            return False
        return True

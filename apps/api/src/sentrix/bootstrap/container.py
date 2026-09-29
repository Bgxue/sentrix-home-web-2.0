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
    MomentRepository,
    PersonCropRenderer,
    PersonPreviewRenderer,
    PersonVision,
    PortraitRepository,
    RelationshipRepository,
)
from sentrix.domain.media.ports import (
    AssetRepository,
    MediaStorage,
    MetadataReader,
    ObservationRepository,
    PreviewEncoder,
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
    TextEmbedder,
    VectorIndex,
    VectorRepository,
    VisualEmbedder,
)
from sentrix.domain.spaces.ports import SpaceRepository
from sentrix.infrastructure.assistant.chat_client import OpenAIChatModel
from sentrix.infrastructure.assistant.events import InProcessTurnEventBroker
from sentrix.infrastructure.assistant.model_endpoint import ServedModel
from sentrix.infrastructure.media.image_io import PillowPreviewEncoder
from sentrix.infrastructure.media.metadata import PillowMetadataReader
from sentrix.infrastructure.media.person_vision import (
    PillowPersonCropRenderer,
    PillowPersonPreviewRenderer,
)
from sentrix.infrastructure.media.person_vision_model import OpenAIPersonVision
from sentrix.infrastructure.media.storage import FileSystemMediaStorage
from sentrix.infrastructure.media.vlm_observer import OpenAIVlmObserver
from sentrix.infrastructure.models.bge_text import BgeM3TextEmbedder
from sentrix.infrastructure.models.chinese_clip import ChineseClipVisualEmbedder
from sentrix.infrastructure.models.face_adapter import FaceAdapter
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.appearance import SqliteAppearanceRepository
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.conversations import (
    SqliteConversationRepository,
)
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository
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
from sentrix.infrastructure.persistence.repositories.spaces import SqliteSpaceRepository
from sentrix.infrastructure.persistence.repositories.turns import SqliteTurnRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository
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

    # 身份层（S5a）。两个仓储分家的理由写在 domain/identity/ports.py 的模块注释里：
    # 人脸侧的写入者是在线匹配与簇审阅，实体侧的写入者是命名与属性维护。
    face_repository: FaceRepository
    entity_repository: EntityRepository
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
    portrait_repository: PortraitRepository

    chat_model: ChatModel
    vlm_observer: VlmObserver
    conversation_repository: ConversationRepository
    turn_repository: TurnRepository
    result_set_repository: ResultSetRepository
    person_directory: PersonDirectory
    # 内存里的实时事件流。它没有持久化的一面——历史事件在 agent_turn_events 表里，
    # 两半合起来才构成完整的 SSE 语义（见 domain/assistant/ports.py 的 TurnEventBroker）。
    turn_event_broker: TurnEventBroker

    @classmethod
    def build(cls, settings: Settings) -> Container:
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
        return cls(
            settings=settings,
            database=database,
            spaces=SqliteSpaceRepository(database),
            asset_repository=assets,
            observation_repository=observations,
            media_storage=FileSystemMediaStorage(settings.media_dir),
            metadata_reader=PillowMetadataReader(),
            preview_encoder=PillowPreviewEncoder(),
            vector_repository=SqliteVectorRepository(database),
            vector_index=QdrantVectorIndex(settings.qdrant_dir),
            visual_embedder=ChineseClipVisualEmbedder(),
            # 文本嵌入走 HTTP，模型在 .venv-text 里（设计 §2.1：2.0 不管理模型
            # 进程生命周期）。地址来自 Settings，与 8100 的 vlm_base_url 同一口径。
            text_embedder=BgeM3TextEmbedder(settings.text_embedder_url),
            place_resolver=OfflinePlaceResolver(
                admin=PyGeoCNAdminResolver(),
                nearest=GeoNamesNearestResolver(settings.geo_dir),
            ),
            asset_place_repository=asset_places,
            place_aliases=load_place_aliases(),
            event_repository=SqliteEventRepository(database),
            event_participant_repository=SqliteEventParticipantRepository(database),
            fact_repository=SqliteFactRepository(database),
            lexical_index=lexical,
            lexical_search=lexical,
            face_repository=SqliteFaceRepository(database),
            entity_repository=entities,
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
            portrait_repository=SqlitePortraitRepository(database),
            chat_model=OpenAIChatModel(
                settings.vlm_base_url,
                served_model,
                timeout_s=settings.chat_timeout_s,
            ),
            vlm_observer=OpenAIVlmObserver(
                settings.vlm_base_url,
                served_model,
                timeout_s=settings.vlm_timeout_s,
            ),
            conversation_repository=SqliteConversationRepository(database),
            turn_repository=SqliteTurnRepository(database),
            result_set_repository=SqliteResultSetRepository(database),
            person_directory=EntityPersonDirectory(entities=entities),
            turn_event_broker=InProcessTurnEventBroker(),
        )

    def database_healthy(self) -> bool:
        try:
            with self.database.connect() as connection:
                connection.execute("SELECT 1 FROM schema_migrations LIMIT 1").fetchone()
        except sqlite3.Error:
            return False
        return True

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

from sentrix.config import Settings
from sentrix.domain.assistant.ports import (
    ChatModel,
    ConversationRepository,
    PersonDirectory,
    ResultSetRepository,
    TurnEventBroker,
    TurnRepository,
)
from sentrix.domain.media.ports import (
    AssetRepository,
    MediaStorage,
    MetadataReader,
    ObservationRepository,
    PreviewEncoder,
)
from sentrix.domain.memories.ports import EventRepository, FactRepository
from sentrix.domain.places.aliases import PlaceAliases
from sentrix.domain.places.ports import AssetPlaceRepository, PlaceResolver
from sentrix.domain.retrieval.ports import (
    LexicalIndex,
    LexicalSearch,
    VectorIndex,
    VectorRepository,
    VisualEmbedder,
)
from sentrix.infrastructure.assistant.chat_client import OpenAIChatModel
from sentrix.infrastructure.assistant.events import InProcessTurnEventBroker
from sentrix.infrastructure.assistant.identity import NoIdentityDirectory
from sentrix.infrastructure.media.image_io import PillowPreviewEncoder
from sentrix.infrastructure.media.metadata import PillowMetadataReader
from sentrix.infrastructure.media.storage import FileSystemMediaStorage
from sentrix.infrastructure.models.chinese_clip import ChineseClipVisualEmbedder
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.asset_places import SqliteAssetPlaceRepository
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.conversations import (
    SqliteConversationRepository,
)
from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository
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

    # 例外：目前没有任何 application / api 消费者，为它补端口是投机抽象。
    # 记忆空间的用例落地时，把 MemorySpace 一并迁进 domain 再补 SpaceRepository。
    spaces: SqliteSpaceRepository

    asset_repository: AssetRepository
    observation_repository: ObservationRepository
    media_storage: MediaStorage
    metadata_reader: MetadataReader
    preview_encoder: PreviewEncoder
    vector_repository: VectorRepository
    vector_index: VectorIndex
    visual_embedder: VisualEmbedder

    # 时间线（S4）。地点是纯离线的：区县走 PyGeoCN 的面，乡镇/村庄走 GeoNames 的
    # 最近邻点，运行时零网络依赖。
    place_resolver: PlaceResolver
    asset_place_repository: AssetPlaceRepository
    # 例外：`PlaceAliases` 是 domain 的数据，不是端口的实现——它由 infrastructure 从
    # 包内数据文件读进来（那是唯一的 IO 边界），而读文件这件事不能发生在 api 层。
    place_aliases: PlaceAliases
    event_repository: EventRepository
    fact_repository: FactRepository
    # 词法投影的写侧与读侧由同一个 SQLite 适配器实现（它是同一张投影表的两个方向），
    # 这里按两个端口分别声明：调用方在类型上就看得出自己只需要其中一侧。
    lexical_index: LexicalIndex
    lexical_search: LexicalSearch

    chat_model: ChatModel
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
        lexical = SqliteLexicalIndex(database, observations)
        return cls(
            settings=settings,
            database=database,
            spaces=SqliteSpaceRepository(database),
            asset_repository=SqliteAssetRepository(database),
            observation_repository=observations,
            media_storage=FileSystemMediaStorage(settings.media_dir),
            metadata_reader=PillowMetadataReader(),
            preview_encoder=PillowPreviewEncoder(),
            vector_repository=SqliteVectorRepository(database),
            vector_index=QdrantVectorIndex(settings.qdrant_dir),
            visual_embedder=ChineseClipVisualEmbedder(),
            place_resolver=OfflinePlaceResolver(
                admin=PyGeoCNAdminResolver(),
                nearest=GeoNamesNearestResolver(settings.geo_dir),
            ),
            asset_place_repository=SqliteAssetPlaceRepository(database),
            place_aliases=load_place_aliases(),
            event_repository=SqliteEventRepository(database),
            fact_repository=SqliteFactRepository(database),
            lexical_index=lexical,
            lexical_search=lexical,
            chat_model=OpenAIChatModel(
                settings.vlm_base_url,
                settings.chat_model_name,
                timeout_s=settings.chat_timeout_s,
            ),
            conversation_repository=SqliteConversationRepository(database),
            turn_repository=SqliteTurnRepository(database),
            result_set_repository=SqliteResultSetRepository(database),
            person_directory=NoIdentityDirectory(),
            turn_event_broker=InProcessTurnEventBroker(),
        )

    def database_healthy(self) -> bool:
        try:
            with self.database.connect() as connection:
                connection.execute("SELECT 1 FROM schema_migrations LIMIT 1").fetchone()
        except sqlite3.Error:
            return False
        return True

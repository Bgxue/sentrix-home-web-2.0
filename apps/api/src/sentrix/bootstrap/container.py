"""The single wiring point.

Nothing else in the codebase constructs an adapter.  Domain and application code
receive ports; only this module knows which concrete class implements them.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sentrix.config import Settings
from sentrix.domain.media.ports import PreviewEncoder
from sentrix.infrastructure.media.image_io import PillowPreviewEncoder
from sentrix.infrastructure.media.metadata import PillowMetadataReader
from sentrix.infrastructure.media.storage import FileSystemMediaStorage
from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.assets import (
    SqliteAssetRepository,
    SqliteObservationRepository,
)
from sentrix.infrastructure.persistence.repositories.spaces import SqliteSpaceRepository
from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository
from sentrix.infrastructure.vector.qdrant_index import QdrantVectorIndex

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "infrastructure" / "persistence" / "migrations"


@dataclass(frozen=True)
class Container:
    settings: Settings
    database: Database
    spaces: SqliteSpaceRepository
    asset_repository: SqliteAssetRepository
    observation_repository: SqliteObservationRepository
    media_storage: FileSystemMediaStorage
    metadata_reader: PillowMetadataReader
    preview_encoder: PreviewEncoder
    vector_repository: SqliteVectorRepository
    vector_index: QdrantVectorIndex

    @classmethod
    def build(cls, settings: Settings) -> Container:
        database = Database(settings.database_path, MIGRATIONS_DIR)
        return cls(
            settings=settings,
            database=database,
            spaces=SqliteSpaceRepository(database),
            asset_repository=SqliteAssetRepository(database),
            observation_repository=SqliteObservationRepository(database),
            media_storage=FileSystemMediaStorage(settings.media_dir),
            metadata_reader=PillowMetadataReader(),
            preview_encoder=PillowPreviewEncoder(),
            vector_repository=SqliteVectorRepository(database),
            vector_index=QdrantVectorIndex(settings.qdrant_dir),
        )

    def database_healthy(self) -> bool:
        try:
            with self.database.connect() as connection:
                connection.execute("SELECT 1 FROM schema_migrations LIMIT 1").fetchone()
        except sqlite3.Error:
            return False
        return True

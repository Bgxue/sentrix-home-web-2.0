"""The single wiring point.

Nothing else in the codebase constructs an adapter.  Domain and application code
receive ports; only this module knows which concrete class implements them.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sentrix.config import Settings
from sentrix.infrastructure.persistence.database import Database, MigrationError

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "infrastructure" / "persistence" / "migrations"


@dataclass(frozen=True)
class Container:
    settings: Settings
    database: Database

    @classmethod
    def build(cls, settings: Settings) -> Container:
        return cls(
            settings=settings,
            database=Database(settings.database_path, MIGRATIONS_DIR),
        )

    def database_healthy(self) -> bool:
        """能连上不等于健康：schema 没建齐同样是坏状态。

        只查 ``SELECT 1 FROM schema_migrations`` 会在「迁移目录没打进包、migrate()
        静默 no-op」时返回 ok——那正是 1.0 起得来、接口有响应、第一次写入才炸的坏法。
        所以要比对已应用集合与磁盘上可用的迁移集合。
        """
        try:
            expected = {version for version, _ in self.database.migrations()}
            applied = self.database.applied_versions()
        except (sqlite3.Error, MigrationError):
            return False
        return applied == expected

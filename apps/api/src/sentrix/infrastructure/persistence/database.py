"""SQLite connection factory and versioned migration runner.

1.0 had 59 tables built by ``CREATE TABLE IF NOT EXISTS`` plus ad-hoc
``ALTER TABLE ADD COLUMN`` patching at runtime, with no version record.  This
runner replaces that: every schema change is a numbered .sql file applied once,
inside a transaction, and recorded in ``schema_migrations``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

BUSY_TIMEOUT_MS = 30_000


class Database:
    def __init__(self, path: Path, migrations_dir: Path) -> None:
        self.path = Path(path)
        self.migrations_dir = Path(migrations_dir)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        try:
            yield connection
        finally:
            connection.close()

    def migrate(self) -> list[str]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        applied: list[str] = []
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version TEXT PRIMARY KEY,"
                " applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            done = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            for script in sorted(self.migrations_dir.glob("*.sql")):
                version = script.stem.split("_", 1)[0]
                if version in done:
                    continue
                self._apply(connection, script, version)
                applied.append(version)
        return applied

    @staticmethod
    def _apply(connection: sqlite3.Connection, script: Path, version: str) -> None:
        """一个迁移一个事务：脚本里的 DDL 与版本号要么一起留下，要么一起回滚。

        不能写成 ``with connection: connection.executescript(...)``——executescript()
        会先把挂起事务隐式 COMMIT 掉，那个回滚保护覆盖不到脚本里的 DDL，失败时表会残留。
        所以事务边界由本函数显式建立，且 ``BEGIN`` 必须是脚本的第一条语句：executescript
        不接受参数绑定，只能把 BEGIN 拼在脚本文本前；``version`` 仍走参数绑定。
        """
        previous = connection.isolation_level
        connection.isolation_level = None  # 关掉 sqlite3 的隐式事务，边界由本函数管
        try:
            connection.executescript(f"BEGIN;\n{script.read_text(encoding='utf-8')}")
            connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
            connection.execute("COMMIT")
        except BaseException:
            with suppress(sqlite3.OperationalError):  # BEGIN 就没成功时没有事务可回滚
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.isolation_level = previous

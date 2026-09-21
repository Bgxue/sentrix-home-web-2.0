import sqlite3

from sentrix.infrastructure.persistence.database import Database


def test_migrations_are_applied_once_and_recorded(tmp_path):
    db = Database(tmp_path / "t.db", migrations_dir=tmp_path / "migrations")
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_a.sql").write_text("CREATE TABLE a (id TEXT PRIMARY KEY);")
    (tmp_path / "migrations" / "0002_b.sql").write_text("CREATE TABLE b (id TEXT PRIMARY KEY);")

    db.migrate()
    db.migrate()  # 幂等

    with db.connect() as conn:
        applied = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    assert applied == ["0001", "0002"]
    assert {"a", "b", "schema_migrations"} <= tables


def test_failed_migration_rolls_back(tmp_path):
    db = Database(tmp_path / "t.db", migrations_dir=tmp_path / "migrations")
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "0001_bad.sql").write_text(
        "CREATE TABLE ok (id TEXT); CREATE TABLE ok (id TEXT);"  # 第二次建表必失败
    )

    try:
        db.migrate()
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("expected OperationalError")

    with db.connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        applied = list(conn.execute("SELECT version FROM schema_migrations"))

    assert "ok" not in tables
    assert applied == []


def test_write_connection_uses_wal_and_busy_timeout(tmp_path):
    db = Database(tmp_path / "t.db", migrations_dir=tmp_path / "migrations")
    (tmp_path / "migrations").mkdir()
    db.migrate()

    with db.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000

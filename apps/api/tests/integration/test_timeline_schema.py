"""迁移 0005 的 schema：五张表 + observations 的语义列 + FTS5 虚拟表。

Task 49 原先声明的门禁是 ``test_migrations.py``——那份文件验的是迁移**执行器**
（应用一次、失败回滚、一条 ``.sql`` 都没有时报错），在 0005 写出来之前它就是绿的，
所以它验不了 0005 建了什么。这里补上真正的 schema 断言。
"""

from __future__ import annotations

from sentrix.infrastructure.persistence.database import Database

EXPECTED_TABLES = {
    "asset_places",
    "events",
    "event_observations",
    "facts",
    "observation_search_terms",
}

SEMANTIC_COLUMNS = {"place", "activity", "event_type", "objects_json", "clothing_json", "revision"}


def test_0005_creates_the_timeline_tables(migrations_ready_db: Database):
    with migrations_ready_db.connect() as connection:
        names = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master")}

    assert names >= EXPECTED_TABLES


def test_asset_places_is_its_own_table_not_columns_on_assets(migrations_ready_db: Database):
    """「没解析过」与「解析了但没结果」必须是两个可区分状态：前者没有行，后者有行。

    压在 ``assets`` 的列上时两件事都是空字符串——与 1.0 把 ``{}`` 和缺失混为一谈是
    同一个毛病，所以这条断言同时钉住「有这张表」和「assets 上没长出那几列」。
    """
    with migrations_ready_db.connect() as connection:
        asset_columns = {row["name"] for row in connection.execute("PRAGMA table_info(assets)")}
        place_columns = {row["name"] for row in connection.execute("PRAGMA table_info(asset_places)")}

    assert not {column for column in asset_columns if column.startswith("admin_")}
    assert {"admin_district", "nearest_locality", "precision", "source", "resolved_at"} <= place_columns


def test_observations_gained_the_semantic_columns(migrations_ready_db: Database):
    with migrations_ready_db.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(observations)")}

    assert columns >= SEMANTIC_COLUMNS


def test_lexical_projection_is_a_queryable_fts5_table(migrations_ready_db: Database):
    """FTS5 缺席时这里会抛 ``OperationalError``——词法通道的前提必须是门禁的一部分。"""
    with migrations_ready_db.connect() as connection:
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'observation_search_fts'"
        ).fetchone()
        connection.execute(
            "INSERT INTO observation_search_fts"
            " (tokens, scope_id, field_type, asset_id, observation_id)"
            " VALUES ('海边 边拍', 'album1', 'place', 'asset_1', 'obs_1')"
        )
        matched = connection.execute(
            "SELECT asset_id FROM observation_search_fts WHERE observation_search_fts MATCH ?",
            ('"海边"',),
        ).fetchall()

    assert definition is not None and "fts5" in definition["sql"]
    assert [item["asset_id"] for item in matched] == ["asset_1"]

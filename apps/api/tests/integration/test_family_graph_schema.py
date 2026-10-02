"""迁移 0009 的 schema：家庭图谱六张表（S7b1 · T301）。

对齐 ``test_identity_schema.py`` / ``test_timeline_schema.py`` 的理由：``test_migrations.py``
验的是迁移**执行器**（应用一次、失败回滚），它在 0009 写出来之前就是绿的，验不了 0009
建了什么。这里断言的是「建出来的东西真的能用」。

1.0 建这六张表用的是 ``CREATE TABLE IF NOT EXISTS`` 且没有版本记录（``db.py:702-781``），
所以列写错、约束漏掉，在 1.0 都要等到真有一行写进去才炸。2.0 的迁移编号化之后，
**这里就是那个「一次建对」的断言点**。

钉的四类东西，每一类都是「错了不会当场报错」的：

- **UNIQUE(scope_id, left/right_entity_id)**：漏掉它，同一对人可以堆出多个 block，
  而读侧只取一行——用户撤回过一次的关系，模型照样能恢复；
- **三个外键**：`entity_id` / `subject|object_entity_id` → ``entities``、
  `scope_id` → ``memory_spaces``。漏掉 FK，删实体/删空间会留下悬空的关系行；
- **两处默认值**：`locked` 在 block 表是 **1**、在其余表是 **0**——反了的话，
  「模型不许覆盖用户写的」这条判据整个反过来；
- **两列「可以写 NULL」**：`family_relationships.family_graph_id` 与 `supersedes_id`
  在 1.0 **恒为 NULL**（`db.py:5797` 两条 INSERT 都写死）。把列改成 NOT NULL 会让
  每一次建边都炸——而那正是 1.0 的形状，不是遗漏。
"""

from __future__ import annotations

import sqlite3

import pytest

from sentrix.infrastructure.persistence.database import Database

EXPECTED_TABLES = {
    "family_person_memberships",
    "family_relationships",
    "family_relationship_blocks",
    "family_analysis_runs",
    "family_person_portraits",
    "family_scope_groups",
}

NOW = "2026-10-02T10:00:00+00:00"


@pytest.fixture
def family_db(migrations_ready_db: Database) -> Database:
    """一个空间 + 两个人物实体。外键是开的，所以父数据必须先落。"""
    with migrations_ready_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES ('album1', '主相册', 1, ?, ?)",
            (NOW, NOW),
        )
        for entity_id in ("ent_1", "ent_2"):
            connection.execute(
                "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                " summary, confidence, created_at, updated_at)"
                " VALUES (?, 'album1', 'person', ?, 'confirmed', '', 0.0, ?, ?)",
                (entity_id, entity_id, NOW, NOW),
            )
    return migrations_ready_db


def _columns(db: Database, table: str) -> dict[str, sqlite3.Row]:
    with db.connect() as connection:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"]: row for row in rows}


def _add_block(db: Database, block_id: str) -> None:
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO family_relationship_blocks"
            " (id, scope_id, left_entity_id, right_entity_id, source, created_at, updated_at)"
            " VALUES (?, 'album1', 'ent_1', 'ent_2', 'user_override', ?, ?)",
            (block_id, NOW, NOW),
        )


def test_all_six_tables_exist(family_db: Database) -> None:
    with family_db.connect() as connection:
        found = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }

    assert found >= EXPECTED_TABLES


def test_a_duplicate_block_pair_is_rejected(family_db: Database) -> None:
    """``UNIQUE(scope_id, left_entity_id, right_entity_id)``。

    漏掉它，同一对人会堆出多行 block，而读侧 ``_family_relationship_block`` 只取一行
    ——「用户撤回过」这件事会变成偶然的。id 不同也拦得住，所以这条钉的确实是那个唯一键。
    """
    _add_block(family_db, "block_1")

    with pytest.raises(sqlite3.IntegrityError):
        _add_block(family_db, "block_2")


def test_a_relationship_must_point_at_existing_entities(family_db: Database) -> None:
    """两端都是 ``REFERENCES entities(id)``。漏掉 FK，删实体就留下悬空的关系行。"""
    with pytest.raises(sqlite3.IntegrityError), family_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO family_relationships"
            " (id, scope_id, subject_entity_id, predicate, object_entity_id,"
            "  inverse_predicate, source, created_at, updated_at)"
            " VALUES ('rel_1', 'album1', 'ent_1', '父亲', 'ent_nope', '女儿',"
            "  'user_override', ?, ?)",
            (NOW, NOW),
        )


def test_a_run_must_belong_to_an_existing_space(family_db: Database) -> None:
    """``family_analysis_runs.scope_id REFERENCES memory_spaces(id)``。

    这是六张表里**唯一**一个 scope 带外键的（其余五张的 ``scope_id`` 只是 TEXT）。
    照搬——不要因为「别的表都不带」就把它去掉。
    """
    with pytest.raises(sqlite3.IntegrityError), family_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO family_analysis_runs (id, scope_id, created_at, updated_at)"
            " VALUES ('run_1', 'album_nope', ?, ?)",
            (NOW, NOW),
        )


def test_locked_defaults_to_one_only_for_blocks(family_db: Database) -> None:
    """``locked`` 在 block 表默认 **1**、在其余表默认 **0**。

    反了的话「模型不许覆盖用户写的」这条判据整个反过来：用户写下的行会被当成模型行，
    下一轮推断就能把它顶掉。而这件事**不会报错**，只会让用户的修改静默消失。
    """
    _add_block(family_db, "block_1")
    with family_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO family_person_memberships"
            " (id, scope_id, entity_id, membership, source, created_at, updated_at)"
            " VALUES ('m_1', 'album1', 'ent_1', 'family', 'model', ?, ?)",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO family_person_portraits"
            " (id, scope_id, entity_id, portrait_text, source, created_at, updated_at)"
            " VALUES ('p_1', 'album1', 'ent_1', '一家人', 'model', ?, ?)",
            (NOW, NOW),
        )
        block = connection.execute(
            "SELECT locked FROM family_relationship_blocks WHERE id = 'block_1'"
        ).fetchone()[0]
        membership = connection.execute(
            "SELECT locked, state FROM family_person_memberships WHERE id = 'm_1'"
        ).fetchone()
        portrait = connection.execute(
            "SELECT locked FROM family_person_portraits WHERE id = 'p_1'"
        ).fetchone()[0]

    assert block == 1
    assert membership["locked"] == 0
    assert membership["state"] == "effective"
    assert portrait == 0


def test_a_scope_belongs_to_at_most_one_graph(family_db: Database) -> None:
    """``family_scope_groups.scope_id`` 是 **PRIMARY KEY**（不是普通列）。

    一个 scope 只能属于一张图——1.0 靠 ``ON CONFLICT(scope_id) DO UPDATE`` 合并
    （``db.py:5937``），那条 UPSERT 成立的前提就是这个主键。写成普通列的话，
    每个 scope 会堆出多行，``family_graph_scope_ids`` 返回的图就会随扫描顺序变。
    """
    with family_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO family_scope_groups (scope_id, graph_id, created_at, updated_at)"
            " VALUES ('album1', 'graph_1', ?, ?)",
            (NOW, NOW),
        )

    with pytest.raises(sqlite3.IntegrityError), family_db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO family_scope_groups (scope_id, graph_id, created_at, updated_at)"
            " VALUES ('album1', 'graph_2', ?, ?)",
            (NOW, NOW),
        )


def test_the_two_always_null_relationship_columns_stay_nullable(family_db: Database) -> None:
    """``family_graph_id`` 与 ``supersedes_id`` **可以写 NULL**——1.0 恒写 NULL。

    1.0 的 ``set_family_relationship``（``db.py:5797``）两条 INSERT 都把这两列写死
    ``NULL``，且**没有任何地方回填**；对比 ``family_person_memberships`` 的新行**会**
    回填 ``supersedes_id``。所以「关系侧的历史链是断的」是 1.0 的原样。

    这条用 ``PRAGMA table_info`` 而不是「插一行试试」：插一行成功只说明**这一次**能写，
    而这里要钉的是**列本身**可空——写成 ``NOT NULL`` 时，1.0 的每一次建边都会炸。
    """
    relationship = _columns(family_db, "family_relationships")

    assert relationship["family_graph_id"]["notnull"] == 0
    assert relationship["supersedes_id"]["notnull"] == 0
    # 对照组：membership 的 supersedes_id 也允许 NULL（第一次写就是 NULL）。
    assert _columns(family_db, "family_person_memberships")["supersedes_id"]["notnull"] == 0

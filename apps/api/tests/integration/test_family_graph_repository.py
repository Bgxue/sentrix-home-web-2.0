"""家庭图谱仓储的真 SQLite 用例（S7b1 · T303）。

对应 1.0 ``db.py:5631-5949`` 那一段。

**这一层最要紧的不是「写得进、读得出」，是三条「静默跳过」。** 用户写下的行
``locked = 1``，模型的推断遇到它**什么都不做**；撤回过的边在 block 表里挡着；
用户重新建边要先删 block。三条**都不抛异常、不返回错误**——调用方分不出
「刚写成功」与「被跳过」。所以每条都有正反两臂：跳过成立，和不该跳过时确实写了。

另外两条顺序约束也是静默的，各有一条用例：``set_relationship`` 必须**先 supersede
旧边再插新行**（反了会留下两代 effective 并存）；``retract`` 必须**先撤边再写 block**。
"""

from __future__ import annotations

import pytest

from sentrix.infrastructure.persistence.database import Database
from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
from sentrix.infrastructure.persistence.repositories.family import (
    SqliteFamilyGraphRepository,
)

NOW = "2026-10-02T10:00:00+00:00"

HOME = "album1"
OTHER = "album2"


@pytest.fixture
def repo(migrations_ready_db: Database) -> dict[str, object]:
    """两个空间 · 五个人物 · 一个地点（``HOME`` 三个、``OTHER`` 两个）。外键是开的。"""
    db = migrations_ready_db
    with db.connect() as connection, connection:
        for scope_id in (HOME, OTHER):
            connection.execute(
                "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
                " VALUES (?, ?, 1, ?, ?)",
                (scope_id, scope_id, NOW, NOW),
            )
        for entity_id, scope_id, kind, name in (
            ("mom", HOME, "person", "妈妈"),
            ("dad", HOME, "person", "爸爸"),
            ("kid", HOME, "person", "小明"),
            ("west", HOME, "place", "西湖"),
            ("other_mom", OTHER, "person", "隔壁妈妈"),
            ("other_dad", OTHER, "person", "隔壁爸爸"),
        ):
            connection.execute(
                "INSERT INTO entities (id, scope_id, entity_type, canonical_name, status,"
                " summary, confidence, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'confirmed', '', 0.0, ?, ?)",
                (entity_id, scope_id, kind, name, NOW, NOW),
            )
    return {"repo": SqliteFamilyGraphRepository(db, SqliteEntityRepository(db)), "db": db}


def _blocks(db: Database) -> list[dict[str, object]]:
    with db.connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM family_relationship_blocks")]


# ---- 成员归属 --------------------------------------------------------------------


def test_a_first_membership_has_no_supersedes_and_locks_only_for_users(repo) -> None:
    """第一次写没有可 supersede 的；``locked`` 只看 source。"""
    written = repo["repo"].set_membership(HOME, "mom", "family", source="user_override", confidence=0.9)

    assert written.membership == "family"
    assert written.state == "effective"
    assert written.locked is True
    assert written.supersedes_id is None
    assert written.revision == 1


def test_a_second_membership_supersedes_the_first_and_points_back_at_it(repo) -> None:
    """**membership 的新行会回填 ``supersedes_id``**（关系那边不回填，见另一条用例）。

    定点变异：把 ``supersedes_id`` 写成 ``NULL``，这条变红——而「谁顶掉了谁」正是
    读侧解释「这个人的归属变过」的唯一线索。
    """
    first = repo["repo"].set_membership(HOME, "mom", "family", source="model")
    second = repo["repo"].set_membership(HOME, "mom", "friend", source="user_override")

    assert repo["repo"].get_effective_membership(HOME, "mom").id == second.id
    assert second.supersedes_id == first.id


def test_a_model_membership_never_overwrites_a_locked_one(repo) -> None:
    """**静默跳过之一**：用户改过的归属，模型不许覆盖。

    返回的是**当前行**（id 与用户那次相同），不是新行，也不抛错——调用方分不出
    「被跳过」。定点变异：去掉 ``locked`` 那半个条件，这条变红。
    """
    user = repo["repo"].set_membership(HOME, "mom", "friend", source="user_override")

    result = repo["repo"].set_membership(HOME, "mom", "family", source="model")

    assert result.id == user.id
    assert result.membership == "friend"
    assert repo["repo"].get_effective_membership(HOME, "mom").membership == "friend"


def test_a_user_membership_always_overwrites_a_locked_one(repo) -> None:
    """用户覆盖用户是允许的——``locked`` 挡的是模型，不是用户。"""
    first = repo["repo"].set_membership(HOME, "mom", "friend", source="user_override")

    second = repo["repo"].set_membership(HOME, "mom", "family", source="user_override")

    assert second.id != first.id
    assert repo["repo"].get_effective_membership(HOME, "mom").membership == "family"


def test_an_unsupported_membership_or_source_is_rejected(repo) -> None:
    """两条白名单（1.0 的 ``_FAMILY_MEMBERSHIPS`` / ``_FAMILY_SOURCES``），话术逐字。"""
    with pytest.raises(ValueError, match="unsupported family membership"):
        repo["repo"].set_membership(HOME, "mom", "邻居", source="model")
    with pytest.raises(ValueError, match="unsupported family membership source"):
        repo["repo"].set_membership(HOME, "mom", "family", source="guess")


def test_a_non_person_or_a_foreign_scope_is_rejected(repo) -> None:
    """两条边界分先后，话术不同——「不是 person」与「不在同一个空间」是两种错误。"""
    with pytest.raises(ValueError, match="family graph requires a person entity"):
        repo["repo"].set_membership(HOME, "west", "family", source="model")
    with pytest.raises(ValueError, match="family graph entities must belong to the same memory space"):
        repo["repo"].set_membership(HOME, "other_mom", "family", source="model")


# ---- 关系边 ----------------------------------------------------------------------


def test_a_relationship_lands_as_two_rows_forward_then_inverse(repo) -> None:
    """**一次落两行**，顺序是正行在前、反行在后（返回值与库里都是）。

    定点变异：去掉循环里第二个元组，这条变红——而「只落一半」不会报错，只会让
    「问爸爸的关系」查不到任何东西。
    """
    written = repo["repo"].set_relationship(HOME, "mom", "母亲", "kid", "儿子", source="user_override")

    assert [(item.subject_entity_id, item.predicate, item.object_entity_id) for item in written] == [
        ("mom", "母亲", "kid"),
        ("kid", "儿子", "mom"),
    ]
    assert all(item.locked for item in written)
    # 1.0 原样：关系新行的这两列恒为 NULL，历史链是断的。
    assert {item.supersedes_id for item in written} == {None}
    assert {item.family_graph_id for item in written} == {None}
    assert len(repo["repo"].list_effective_relationships(HOME)) == 2


def test_a_label_pair_outside_the_whitelist_is_rejected(repo) -> None:
    """没有白名单，家庭图谱就会混进「同事」这种通用关系的词。"""
    with pytest.raises(ValueError, match="关系标签不匹配"):
        repo["repo"].set_relationship(HOME, "mom", "母亲", "kid", "同事", source="model")


def test_a_self_pair_is_rejected(repo) -> None:
    with pytest.raises(ValueError, match="family relationship requires distinct people"):
        repo["repo"].set_relationship(HOME, "mom", "朋友", "mom", "朋友", source="model")


def test_the_new_pair_supersedes_the_old_one_instead_of_piling_up(repo) -> None:
    """**顺序约束**：先把这一对的双向旧边置 ``superseded``，再插新行。

    反过来的话库里会同时留着两代 ``effective`` 的边，而读侧只按 ``locked DESC``
    取——「妈妈和孩子的当前关系」会随写入先后飘。

    定点变异：把 ``_supersede_pair`` 那一句删掉，这条变红（effective 变成 4 行）。
    """
    repo["repo"].set_relationship(HOME, "mom", "母亲", "kid", "儿子", source="user_override")
    repo["repo"].set_relationship(HOME, "mom", "朋友", "kid", "朋友", source="user_override")

    assert {(item.predicate) for item in repo["repo"].list_effective_relationships(HOME)} == {"朋友"}


def test_a_model_edge_is_skipped_after_the_user_retracted_that_pair(repo) -> None:
    """**静默跳过之二**：用户撤回过的边，模型不许恢复。

    返回值是**空列表**——那是调用方唯一的信号。定点变异：去掉 block 判断，
    这条变红（会重新长出边来）。
    """
    repo["repo"].set_relationship(HOME, "mom", "母亲", "kid", "儿子", source="user_override")
    repo["repo"].retract_relationship(HOME, "mom", "kid")

    assert repo["repo"].set_relationship(HOME, "mom", "母亲", "kid", "儿子", source="model") == []
    assert repo["repo"].list_effective_relationships(HOME) == []


def test_a_user_edge_deletes_the_block_and_then_writes(repo) -> None:
    """**用户重新建边即解锁**：那一行 block 要被删掉。

    **这条必须直接断言 block 表，不能只看边。** 第一版只断言「边重新长出来了」，
    `if source == SOURCE_USER_OVERRIDE and block is not None` 那个删除**整个删掉也照样绿**
    ——因为用户重新建的那条边自己 `locked = 1`，模型无论 block 在不在都进不来。
    **行为被 `locked` 掩盖了，只有状态没被掩盖。** 而 block 的含义就是「用户说这一对
    不该有边」；用户自己又把边建回来之后，那句话就已经收回，留着这行是陈旧状态。
    """
    repo["repo"].set_relationship(HOME, "mom", "母亲", "kid", "儿子", source="user_override")
    repo["repo"].retract_relationship(HOME, "mom", "kid")
    assert len(_blocks(repo["db"])) == 1

    repo["repo"].set_relationship(HOME, "mom", "朋友", "kid", "朋友", source="user_override")

    assert _blocks(repo["db"]) == []
    assert repo["repo"].list_effective_relationships(HOME)[0].predicate == "朋友"


def test_a_model_edge_is_skipped_when_the_current_pair_is_locked(repo) -> None:
    """**静默跳过之三**：当前边是用户写的（``locked``）时模型不写。

    这一条与上一条的差别是：这对人**没有被 block**，只是边本身是用户的。
    返回值是**当前那些行**（与「刚写成功」形状一样）。
    """
    user = repo["repo"].set_relationship(HOME, "mom", "母亲", "kid", "儿子", source="user_override")

    result = repo["repo"].set_relationship(HOME, "mom", "父亲", "kid", "女儿", source="model")

    assert {item.id for item in result} == {item.id for item in user}
    # 一落两行，所以谓词有两个（正 + 反）。
    assert {item.predicate for item in repo["repo"].list_effective_relationships(HOME)} == {
        "母亲",
        "儿子",
    }


def test_retracting_writes_a_block_and_only_touches_that_pair(repo) -> None:
    """撤回**同时**落一行 block，而且只动这一对。

    ``state`` 是 ``retracted`` 而**不是** ``superseded``——两个值别混：``retracted``
    是「用户说这条边不存在」，``superseded`` 是「被更新的版本顶掉」。

    定点变异：把 ``retracted`` 写成 ``superseded``，``list_effective_relationships``
    的结果不变（两个都被过滤），所以这条**要直接查 state**。
    """
    repo["repo"].set_relationship(HOME, "mom", "母亲", "kid", "儿子", source="user_override")
    repo["repo"].set_relationship(HOME, "dad", "父亲", "kid", "女儿", source="user_override")

    retracted = repo["repo"].retract_relationship(HOME, "mom", "kid")

    assert {item.state for item in retracted} == {"retracted"}
    block = _blocks(repo["db"])[0]
    assert (block["left_entity_id"], block["right_entity_id"]) == ("kid", "mom")
    assert block["locked"] == 1
    # 另一对没被碰——正反两行都在（谓词是正反那一对）。
    assert {item.predicate for item in repo["repo"].list_effective_relationships(HOME)} == {
        "父亲",
        "女儿",
    }


def test_retracting_twice_updates_the_block_instead_of_adding_one(repo) -> None:
    """第一次撤回落 block，第二次只是把那一行 ``updated_at`` 刷新。

    定点变异：去掉 ``if block`` 分支（永远 INSERT），第二次会撞上
    ``UNIQUE(scope_id, left, right)`` 抛错——所以这条抓的是「重复撤回会炸」。
    """
    repo["repo"].set_relationship(HOME, "mom", "母亲", "kid", "儿子", source="user_override")
    repo["repo"].retract_relationship(HOME, "mom", "kid")
    repo["repo"].retract_relationship(HOME, "mom", "kid")

    assert len(_blocks(repo["db"])) == 1


def test_listing_an_entity_returns_both_directions_and_only_effective(repo) -> None:
    """给了 ``entity_id`` 时正反两个方向都算；``superseded`` 的不返回。"""
    repo["repo"].set_relationship(HOME, "dad", "父亲", "kid", "女儿", source="user_override")

    # **一条边在这里是两行，所以按实体查也回两行**（一行他是 subject、一行他是 object）。
    # 写 1 的断言会红——而「按人查只回一条」正是把双向落库这件事忘掉之后的样子。
    assert len(repo["repo"].list_effective_relationships(HOME, "kid")) == 2
    assert len(repo["repo"].list_effective_relationships(HOME, "dad")) == 2
    assert repo["repo"].list_effective_relationships(HOME, "mom") == []


# ---- 家庭画像 --------------------------------------------------------------------


def test_a_model_portrait_never_overwrites_a_locked_one(repo) -> None:
    """与 membership 同一条跳过。**注意这里不校验 source**——1.0 的这个函数就不校验。"""
    user = repo["repo"].write_portrait(HOME, "mom", "妈妈是家里的主心骨", source="user_override")

    result = repo["repo"].write_portrait(HOME, "mom", "模型写的", source="model")

    assert result.id == user.id
    assert repo["repo"].get_active_portrait(HOME, "mom").portrait_text == "妈妈是家里的主心骨"


def test_an_unknown_portrait_source_is_written_verbatim(repo) -> None:
    """**1.0 的缺口照搬**：这个函数不校验 ``source``，非法值原样落库。

    定点变异：给它加上 ``FAMILY_SOURCES`` 校验，这条变红——那正是「顺手补全」会做的事。
    """
    written = repo["repo"].write_portrait(HOME, "mom", "随便写的", source="guess")

    assert written.source == "guess"
    assert written.locked is False


# ---- 跨 scope 的图 ---------------------------------------------------------------


def test_merging_scopes_needs_at_least_two_and_known_spaces(repo) -> None:
    with pytest.raises(ValueError, match="at least two scopes are required"):
        repo["repo"].merge_scopes([HOME])
    with pytest.raises(ValueError, match="unknown memory space"):
        repo["repo"].merge_scopes([HOME, "album_nope"])


def test_merging_is_cumulative_and_reuses_the_existing_graph(repo) -> None:
    """**已属于某张图的 scope 会被并进那张图**——所以合并是幂等且可累积的。

    断言用「同一个 ``graph_id``」而不是「返回的列表相等」：第二次合并返回的
    ``graph_id`` 必须与第一次一样，否则每次合并都会另起一张图，
    而 ``GET /family-graph`` 的 ``scope_ids`` 会越并越多。
    """
    graph_id, scopes = repo["repo"].merge_scopes([HOME, OTHER])
    assert sorted(scopes) == [HOME, OTHER]

    again, scopes_again = repo["repo"].merge_scopes([HOME, OTHER])
    assert again == graph_id
    assert sorted(scopes_again) == [HOME, OTHER]


def test_an_ungrouped_scope_is_its_own_graph(repo) -> None:
    """没并过图的 scope 返回**它自己一个**，不是空列表。"""
    assert repo["repo"].graph_scope_ids(HOME) == [HOME]


# ---- 运行表与模型边的清场（S7b2 · T311） ------------------------------------------


def test_a_new_run_is_queued_with_an_empty_stats_and_keeps_its_config(repo) -> None:
    """新 run 的 ``status`` / ``current_stage`` 都是 ``queued``、``stats`` 空。

    ``stats`` 空**不是**没写——它是「还没跑完」的唯一标志：完成时才会写进
    ``event_watermark``，而触发去抖读的正是它。失败时它也保持空，于是下次一定重跑。
    """
    run = repo["repo"].create_run(HOME, {"trigger_type": "ingest", "input_mode": "semantic_text_only"})

    assert (run.status, run.current_stage) == ("queued", "queued")
    assert dict(run.stats) == {}
    assert run.config["trigger_type"] == "ingest"
    assert run.error is None
    assert run.completed_at is None
    assert repo["repo"].get_run(run.id).id == run.id


def test_latest_run_picks_by_created_at_not_updated_at(repo) -> None:
    """``ORDER BY created_at DESC``——**不是 ``updated_at``**。

    1.0 就是按 ``created_at`` 排的。夹具造一个「时间更早、但后来被更新过」的旧 run：
    按 ``updated_at`` 排会选它，按 ``created_at`` 排会选新的那个。

    定点变异：把排序键换成 ``updated_at``，这条变红。
    """
    old = repo["repo"].create_run(HOME, {"trigger_type": "ingest"})
    # 把旧 run 的 created_at 推到很早，再把它的 updated_at 推到很晚。
    with repo["db"].connect() as connection, connection:
        connection.execute(
            "UPDATE family_analysis_runs SET created_at = ?, updated_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00", old.id),
        )
    new = repo["repo"].create_run(HOME, {"trigger_type": "user_rerun"})

    assert repo["repo"].latest_run(HOME).id == new.id


def test_latest_run_is_none_for_a_scope_that_never_ran(repo) -> None:
    assert repo["repo"].latest_run(HOME) is None


def test_updating_a_run_maps_stage_to_the_column_and_writes_completed_at(repo) -> None:
    """``stage`` → ``current_stage``、``stats`` → ``stats_json``（要序列化）。

    名字不是一一对应的，而写成 ``stage = ?`` 会直接报 SQL 错——那种错当场就发现。
    真正会漏的是 ``completed_at``：``status='completed'`` 时要额外写它，
    少了它「这趟跑了多久」就再也算不出来。
    """
    run = repo["repo"].create_run(HOME, {"trigger_type": "ingest"})

    repo["repo"].update_run(run.id, status="running", stage="infer_graph")
    updated = repo["repo"].get_run(run.id)

    assert (updated.status, updated.current_stage) == ("running", "infer_graph")
    assert updated.completed_at is None

    done = repo["repo"].update_run(run.id, status="completed", stage="done", stats={"people": 3})

    assert done.completed_at is not None
    assert dict(done.stats) == {"people": 3}


def test_an_omitted_field_means_do_not_change_not_write_null(repo) -> None:
    """``None`` 是「不改这一项」而不是「写成 NULL」。

    后果之一：**``error`` 一旦写上就清不掉**（1.0 同样做不到）。这条钉住那个语义——
    把它实现成「写成 NULL」，写上去的错误会在下一次推进时静默消失。
    """
    run = repo["repo"].create_run(HOME, {"trigger_type": "ingest"})
    repo["repo"].update_run(run.id, status="failed", error="模型不可用")

    same = repo["repo"].update_run(run.id, stage="infer_graph")

    assert same.error == "模型不可用"
    assert same.status == "failed"


def test_superseding_model_edges_leaves_user_and_retracted_ones_alone(repo) -> None:
    """**三重过滤**：``effective`` + ``source='model'`` + ``locked=0``，三条缺一不可。

    夹具四对边，各命中一个被排除的理由：模型未锁的（该被清）、用户写的（``locked=1``）、
    已经 superseded 的、已经 retracted 的。

    定点变异：去掉 ``locked = 0``，用户那条会被清掉——而它下一次读出来就没了。
    """
    repo["repo"].set_relationship(HOME, "mom", "母亲", "kid", "儿子", source="model")
    repo["repo"].set_relationship(HOME, "dad", "父亲", "kid", "女儿", source="user_override")
    repo["repo"].set_relationship(HOME, "mom", "朋友", "dad", "朋友", source="model")
    repo["repo"].retract_relationship(HOME, "mom", "dad")
    # 再写一对模型边、手工置成 superseded。
    repo["repo"].set_relationship(HOME, "kid", "其他亲属", "mom", "其他亲属", source="model")
    with repo["db"].connect() as connection, connection:
        connection.execute(
            "UPDATE family_relationships SET state = 'superseded'"
            " WHERE subject_entity_id = 'kid' AND predicate = '其他亲属'"
        )

    repo["repo"].supersede_model_relationships(HOME)

    effective = repo["repo"].list_effective_relationships(HOME)
    # 只剩下用户写的那一对（两行）。
    assert {item.id for item in effective} == {
        item.id for item in repo["repo"].list_effective_relationships(HOME) if item.locked
    }
    assert all(item.locked for item in effective)
    assert len(effective) == 2


def test_superseding_keeps_a_locked_edge_even_if_its_source_says_model(repo) -> None:
    """``locked = 0`` 那半个条件是**防御**：拦「locked 了、source 却写着 model」的行。

    写侧**产不出**这种行（``locked`` 就是按 ``source`` 定的），所以从生产路径走不到
    ——不手工插一行的话，把 ``AND locked = 0`` 删掉是**等价变异**（``source = 'model'``
    已经先把用户的边排除了）。而 1.0 写了这个条件，它挡的是「老版本留下的、或手工修过的」
    那种行：**用户锁住的边不该被一轮推断清掉**。

    这条用例存在的唯一理由就是让那个条件有人守——它测的是一个**防御性**判据，
    不是一条能走到的产品路径。
    """
    repo["repo"].set_relationship(HOME, "mom", "母亲", "kid", "儿子", source="user_override")
    with repo["db"].connect() as connection, connection:
        connection.execute(
            "UPDATE family_relationships SET source = 'model'"
            " WHERE subject_entity_id = 'mom' AND predicate = '母亲'"
        )

    repo["repo"].supersede_model_relationships(HOME)

    assert len(repo["repo"].list_effective_relationships(HOME)) == 2
    assert all(item.locked for item in repo["repo"].list_effective_relationships(HOME))


def test_superseding_only_touches_the_given_scope(repo) -> None:
    """**一个 scope 的清场不能碰另一个 scope 的边。**

    定点变异：去掉 ``WHERE scope_id = ?``，另一张图的模型边会被一起清掉——
    而那是一次静默的跨相册写入。
    """
    repo["repo"].set_relationship(HOME, "mom", "母亲", "kid", "儿子", source="model")
    repo["repo"].set_relationship(OTHER, "other_mom", "朋友", "other_dad", "朋友", source="model")

    repo["repo"].supersede_model_relationships(HOME)

    assert len(repo["repo"].list_effective_relationships(OTHER)) == 2

"""批末触发的 waterfall 判据（S7b2 · T314）。

对应 1.0 ``pipeline.py:737-763`` 的 ``_trigger_family_analysis``。

**这里钉的是判据，不是「能不能跑」**——那条链在 ``test_family_graph_service.py`` 里
已经逐段验过。判据是 **event watermark**：拿当前事件数与最近一次 run 写进
``stats['event_watermark']`` 的那个数比，没涨就不跑。

**为什么不用 ``imported > 0``**（相邻的 ``_maybe_run_insights`` 用的那个）：insights
没有 run 表，只能用「这次真导入进了照片」表达同一意图；家庭图谱有
``family_analysis_runs``，所以能用 1.0 那个判据。两者不可互换——按 ``imported`` 判的话，
「导入的全是重复照片、但事件早就变了」会漏跑。**下面第三条用例就是那个场景的反面。**

``POST /media/import`` 单张路径不触发（裁决④）——那条没有可测的钩子，因为它**没有钩子**。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sentrix.api.routes.timeline import _maybe_run_family_graph

NOW = "2026-10-02T10:00:00+00:00"
HOME = "album1"


@pytest.fixture
def trigger_client(client: TestClient) -> TestClient:
    """一个空间 + 一条事件。事件数是 watermark 的输入，所以事件必须在。"""
    db = client.app.state.container.database
    with db.connect() as connection, connection:
        connection.execute(
            "INSERT INTO memory_spaces (id, name, include_in_people, created_at, updated_at)"
            " VALUES (?, ?, 1, ?, ?)",
            (HOME, HOME, NOW, NOW),
        )
        _add_event(connection, "evt_1")
    return client


def _add_event(connection, event_id: str) -> None:  # noqa: ANN001
    connection.execute(
        "INSERT INTO events (id, scope_id, title, event_type, place, created_at, updated_at)"
        " VALUES (?, ?, '一次出游', 'outing', '公园', ?, ?)",
        (event_id, HOME, NOW, NOW),
    )


def _container(client: TestClient):  # noqa: ANN202
    return client.app.state.container


def test_a_trigger_failure_never_escapes_into_the_import(client: TestClient) -> None:
    """**家庭图谱是导入的副作用，不是它的前置。**

    这里刻意**不建 ``memory_spaces`` 行**：``family_analysis_runs.scope_id`` 有指向它的
    外键，所以 ``create_run`` 会抛 ``IntegrityError``。触发必须**接住它并返回 None**，
    而不是让异常冒到 ``POST /imports`` 变成 500。

    1.0 的触发跑在 ``threading.Thread(daemon=True)`` 里，失败根本传不到导入响应上；
    2.0 裁决成同步之后，不接住就等价于「家庭图谱出问题 → 导入失败」——那是行为倒退。

    **这条是实测撞出来的**：加上触发之后 ``test_import_insights.py`` 三条用例报
    ``FOREIGN KEY constraint failed``，而它们的 scope 本来就不是真的 memory_space
    （``POST /imports`` 也不要求它是）。
    """
    assert _maybe_run_family_graph(client.app.state.container, "album_不存在") is None


def test_the_first_trigger_runs_even_though_there_is_no_prior_run(trigger_client: TestClient) -> None:
    """没有先前的 run → 跑。1.0 ``:747-749`` 的那句判断是
    ``if latest is not None and event_count <= watermark: return None``——
    **``latest is None`` 时不进那个分支**，所以「从没跑过」这件事本身不构成跳过理由。
    """
    run = _maybe_run_family_graph(_container(trigger_client), HOME)

    assert run is not None
    assert run.run.status in {"completed", "failed"}


def test_a_second_trigger_with_no_new_events_is_skipped(trigger_client: TestClient) -> None:
    """**没涨就不跑**——这是 watermark 的全部意义。

    定点变异：把 ``event_count <= watermark`` 写成 ``event_count < watermark``，
    或干脆去掉那半句判断，这条变红（第二次照样跑）。
    """
    _maybe_run_family_graph(_container(trigger_client), HOME)

    assert _maybe_run_family_graph(_container(trigger_client), HOME) is None


def test_a_new_event_makes_it_run_again(trigger_client: TestClient) -> None:
    """事件数涨了就再跑一趟。第三条与第二条合起来才说明「比的是事件数」，
    只测「第二次跳过」的话，一个恒返回 ``None`` 的实现也能过。
    """
    _maybe_run_family_graph(_container(trigger_client), HOME)

    with _container(trigger_client).database.connect() as connection, connection:
        _add_event(connection, "evt_2")

    assert _maybe_run_family_graph(_container(trigger_client), HOME) is not None


def test_a_failed_run_leaves_no_watermark_so_the_next_trigger_reruns(
    trigger_client: TestClient,
) -> None:
    """**失败时 stats 保持空 → watermark 缺失 → 下次一定重跑**。

    这条与上一条是同一个判据的两面：它说明「跳过」只在**跑成过**的前提下成立。
    夹具直接造一行 ``failed`` + 空 stats 的 run——那是 T312 真实会留下的形状。
    """
    container = _container(trigger_client)
    with container.database.connect() as connection, connection:
        connection.execute(
            "INSERT INTO family_analysis_runs (id, scope_id, status, current_stage,"
            " stats_json, error, created_at, updated_at)"
            " VALUES ('run_failed', ?, 'failed', 'infer_graph', '{}', '模型不可达', ?, ?)",
            (HOME, NOW, NOW),
        )

    assert _maybe_run_family_graph(container, HOME) is not None


def test_the_batch_trigger_records_itself_as_an_ingest_run(trigger_client: TestClient) -> None:
    """``trigger_type`` 是**留痕不是判据**：批末触发写 ``ingest``、手工路由写
    ``user_rerun``（1.0 ``pipeline.py:750`` 对 ``app.py:1719``）。

    定点变异：两条入口都写死同一个值，这条变红——而那时「这趟是谁触发的」就查不出来了。
    """
    run = _maybe_run_family_graph(_container(trigger_client), HOME)

    assert run is not None
    assert run.run.config["trigger_type"] == "ingest"
    assert run.run.config["input_mode"] == "semantic_text_only"

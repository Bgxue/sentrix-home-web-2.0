"""事实端点的契约测试：读列表 + 确认 / 驳回。

**这一条要钉的是「确认」不是一次 UPDATE。** 写入侧一直是好的：模型改口时
``MaintainFacts`` 插一条 ``pending``、把旧值记进 ``supersedes_fact_id``。断掉的是升级——
``set_status`` / ``supersede_others`` / ``list_facts`` 三个方法写在仓储里、全仓零调用，
于是那条 pending 永远停在原地。

只把状态改成 ``active`` 也能让「新值生效」，但会留下**两条 active**——
违反 ``domain/memories/facts.py`` 模块注释第 20 行那条不变量
（同一 subject+predicate 只有一条 active）。它不报错，只让下一次 ``active_for``
取回哪一条变成看运气。所以下面有一条断言专门钉旧值被作废。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sentrix.bootstrap.container import Container
from sentrix.domain.memories.facts import Fact, FactStatus

SCOPE = "album-facts"
OTHER_SCOPE = "album-other"


def container_of(client: TestClient) -> Container:
    return client.app.state.container  # type: ignore[attr-defined]


def make_fact(
    fact_id: str,
    *,
    status: FactStatus,
    object_value: str,
    scope_id: str = SCOPE,
    subject: str = "爸爸",
    predicate: str = "职业",
    supersedes: str | None = None,
) -> Fact:
    return Fact(
        id=fact_id,
        scope_id=scope_id,
        subject=subject,
        predicate=predicate,
        object=object_value,
        status=status,
        confidence=0.9,
        evidence_ids=(),
        revision=1,
        supersedes_fact_id=supersedes,
    )


@pytest.fixture
def facts(client: TestClient):
    """真库（真迁移）+ 一条 active 旧值 + 一条 pending 新值——「模型改口」的现场。"""
    repository = container_of(client).fact_repository
    repository.insert(make_fact("fact_old", status=FactStatus.ACTIVE, object_value="老师"))
    repository.insert(
        make_fact(
            "fact_new",
            status=FactStatus.PENDING,
            object_value="医生",
            supersedes="fact_old",
        )
    )
    return repository


def test_list_returns_facts_and_filters_by_status(client: TestClient, facts):
    everything = client.get("/api/v1/facts", params={"scope_id": SCOPE})
    assert everything.status_code == 200
    assert {item["id"] for item in everything.json()["facts"]} == {"fact_old", "fact_new"}

    pending_only = client.get("/api/v1/facts", params={"scope_id": SCOPE, "status": "pending"})
    assert [item["id"] for item in pending_only.json()["facts"]] == ["fact_new"]


def test_list_without_scope_returns_every_album(client: TestClient, facts):
    """空 scope = 全部相册。1.0 的 ``list_facts(1000)`` 根本没有 scope 参数。

    这条是防回归：端口一度把 ``scope_id`` 写成必填，而当时它零调用所以没人碰得上。
    """
    container_of(client).fact_repository.insert(
        make_fact("fact_elsewhere", status=FactStatus.ACTIVE, object_value="工程师", scope_id=OTHER_SCOPE)
    )

    response = client.get("/api/v1/facts")

    assert response.status_code == 200
    assert {item["id"] for item in response.json()["facts"]} == {"fact_old", "fact_new", "fact_elsewhere"}


def test_confirm_promotes_the_new_value_and_supersedes_the_old(client: TestClient, facts):
    """**这一条是这次接线的全部理由。**"""
    response = client.post("/api/v1/facts/fact_new/confirm")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    assert facts.get("fact_new").status == FactStatus.ACTIVE
    assert facts.get("fact_old").status == FactStatus.SUPERSEDED


def test_confirm_only_supersedes_within_the_same_scope(client: TestClient, facts):
    """1.0 的作废语句不带 scope（``db.py:3428``），跨相册的同名事实会被一起作废。

    2.0 收在本 scope 内（``s4-deviations.md`` §2 第 7 项）。这条断言钉住那个收敛——
    没有它，另一个相册里同名同谓词的事实会被悄悄作废，而**没有任何界面会显示它消失了**。
    """
    facts.insert(
        make_fact("fact_other_album", status=FactStatus.ACTIVE, object_value="教师", scope_id=OTHER_SCOPE)
    )

    client.post("/api/v1/facts/fact_new/confirm")

    assert facts.get("fact_other_album").status == FactStatus.ACTIVE


def test_reject_retracts_without_touching_the_old_value(client: TestClient, facts):
    """驳回一条 pending 之后，原来那条 active 仍然生效。

    「新值不对」与「这件事没有旧值」是两回事——1.0 的 ``reject_fact`` 也只有一条 UPDATE。
    """
    response = client.post("/api/v1/facts/fact_new/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "retracted"
    assert facts.get("fact_new").status == FactStatus.RETRACTED
    assert facts.get("fact_old").status == FactStatus.ACTIVE


@pytest.mark.parametrize("verb", ["confirm", "reject"])
def test_unknown_fact_is_404(client: TestClient, verb: str):
    # 1.0 两个端点的 404 走的是两套机制（confirm 捕 KeyError、reject 先 get_fact）；
    # 2.0 统一成一条带码的领域错误。
    response = client.post(f"/api/v1/facts/fact_missing/{verb}")

    assert response.status_code == 404, response.text
    assert response.json()["code"] == "not_found"

"""装配点必须按端口声明。

import-linter 的五条契约管的是「谁不许 import 谁」；这条管的是「装配时必须按接口拿」。
``Container`` 的字段如果写成 infrastructure 的具体类，可替换性就只存在于 ``build()``
的那一行里——调用方在类型上看不见，很容易顺手用上协议之外的方法，于是「可插拔」
只剩一句口头承诺。结构上换得掉、类型上看不出来，等于没换。
"""

from __future__ import annotations

import dataclasses
import typing

from sentrix.bootstrap.container import Container

# 显式例外：类型确实落在 sentrix.infrastructure 下，而且**故意**如此。
# 一个说不出理由的例外名单会把这条不变量重新变成摆设，所以每条都必须写明为什么。
# 只剩一条：``spaces`` 那条在 Task 76 被消掉了——MemorySpace 与 SpaceRepository 都
# 进了 domain，字段改按端口声明。
EXCEPTIONS: dict[str, str] = {
    "database": (
        "共享连接工厂，不是任何一个端口的实现（多个仓储共用同一个连接来源），"
        "且 database_healthy() 直接需要它；domain 里没有「数据库池」这个概念可依。"
    ),
}


def _infrastructure_fields() -> list[str]:
    """返回「类型定义在 sentrix.infrastructure 下、又不在例外名单里」的字段。"""
    hints = typing.get_type_hints(Container)
    return [
        f"{field.name}: {hints[field.name].__qualname__}（定义在 {hints[field.name].__module__}）"
        for field in dataclasses.fields(Container)
        if str(getattr(hints[field.name], "__module__", "")).startswith("sentrix.infrastructure")
        and field.name not in EXCEPTIONS
    ]


def test_container_fields_are_declared_by_domain_ports() -> None:
    offenders = _infrastructure_fields()
    assert not offenders, (
        "Container 的字段必须按 domain 端口声明，下面这些写成了 infrastructure 的具体类：\n  "
        + "\n  ".join(offenders)
        + "\n要么改成对应的端口类型，要么加进 EXCEPTIONS 并写明理由。"
    )


def test_exceptions_still_name_real_fields() -> None:
    """例外表不能变成墓碑。

    字段改名或删掉之后，例外还留着的话，名单就悄悄变宽了——下次有人把同名新字段
    写成具体类也不会红。所以例外必须与 Container 的真实字段逐一对得上。
    """
    names = {field.name for field in dataclasses.fields(Container)}
    stale = sorted(set(EXCEPTIONS) - names)
    assert not stale, f"EXCEPTIONS 里有 Container 上不存在的字段：{stale}"

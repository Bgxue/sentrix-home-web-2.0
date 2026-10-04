"""装配点必须按端口声明。

与 ``apps/api/tests/unit/test_container_wiring.py`` 是同一条不变量：``Container`` 的字段
如果写成 infrastructure 的具体类，「提供者可替换」就只存在于 ``build()`` 的那一行里——
调用方在类型上看不见，很容易顺手用上协议之外的方法。**结构上换得掉、类型上看不出来，
等于没换。**

``docs/architecture/photobench-module-map.md`` 把这条列成 S9b 建装配层时**必须一并立起来**
的东西，这里就是它。
"""

from __future__ import annotations

import dataclasses
import typing

from photobench.bootstrap.container import Container

#: 显式例外：类型确实落在 ``photobench.infrastructure`` 下，而且**故意**如此。
#: 一个说不出理由的例外名单会把这条不变量重新变成摆设，所以每条都必须写明为什么。
#: 本切片为空——四条适配器（配置 / 结果 / HTTP / 数据集）全都只以端口类型出现在 Container 上。
EXCEPTIONS: dict[str, str] = {}


def _infrastructure_fields() -> list[str]:
    hints = typing.get_type_hints(Container)
    return [
        f"{field.name}: {hints[field.name]}（定义在 {getattr(hints[field.name], '__module__', '?')}）"
        for field in dataclasses.fields(Container)
        if str(getattr(hints[field.name], "__module__", "")).startswith("photobench.infrastructure")
        and field.name not in EXCEPTIONS
    ]


def test_container_fields_are_declared_by_domain_ports() -> None:
    offenders = _infrastructure_fields()
    assert not offenders, (
        "Container 的字段必须按 domain 端口声明，下面这些写成了 infrastructure 的具体类：\n  "
        + "\n  ".join(offenders)
        + "\n要么改成对应的端口类型，要么加进 EXCEPTIONS 并写明理由。"
    )


def test_the_two_provider_factories_are_typed_by_the_domain_protocols() -> None:
    """``lifecycle_provider`` / ``inference_provider`` 的返回类型必须是 S9a 的协议。

    这是「本切片真的按端口装配了运行时提供者」的机械证据——写成
    ``Callable[[str], ManagerLifecycleProvider]`` 就会红在这里。
    """
    hints = typing.get_type_hints(Container)
    assert "LifecycleProvider" in str(hints["lifecycle_provider"])
    assert "InferenceProvider" in str(hints["inference_provider"])

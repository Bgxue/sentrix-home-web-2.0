"""MemorySpace 归位：模型与端口在 domain，SQL 留在 infrastructure。

这条归位的**可判定形态**只有一句：同一个模型不能有两个定义。适配器里再声明一个同名
dataclass，两边的字段漂开一次，「这个空间是不是同一个」在类型上就不再成立——而它偏偏
是那种不会报错、只会让过滤条件静默失效的漂移（``include_in_people`` 是 0 还是空、
``status`` 是 'active' 还是别的，查出来都只是空集）。
"""

from __future__ import annotations

from sentrix.domain.spaces import models, ports
from sentrix.infrastructure.persistence.repositories import spaces as sqlite_spaces


def test_memory_space_is_a_domain_model():
    assert models.MemorySpace.__module__ == "sentrix.domain.spaces.models"


def test_space_repository_is_a_protocol():
    assert getattr(ports.SpaceRepository, "_is_protocol", False), "SpaceRepository 必须是 Protocol"


def test_the_model_has_exactly_one_definition():
    """适配器 import 的必须是 domain 那一个，不是它自己再声明的一份。"""
    assert sqlite_spaces.MemorySpace is models.MemorySpace


def test_the_adapter_speaks_the_port_not_the_other_way_round():
    """适配器实现端口的方法集合——端口少一个方法，装配处不会红，调用方会。

    只比名字不比签名：签名由 mypy strict 在装配那一行把关（``SqliteSpaceRepository``
    赋给 ``SpaceRepository`` 字段），这里钉的是「有没有漏掉一个方法」。
    """
    declared = {
        name
        for name in vars(ports.SpaceRepository)
        if not name.startswith("_") and callable(getattr(ports.SpaceRepository, name))
    }
    implemented = {name for name in declared if hasattr(sqlite_spaces.SqliteSpaceRepository, name)}

    assert implemented == declared

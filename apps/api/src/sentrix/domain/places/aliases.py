"""地名别名与行政区后缀归一。纯规则 + 纯数据，零 IO。

1.0 把这两样东西写死在 ``backend/geocoding.py`` 的模块级常量里（``_PLACE_ALIASES`` /
``_PLACE_ADMIN_SUFFIXES``）。那张表里混进了两条评测地标条目（``"赵州桥": ["赵县"]``、
``"三峡坝址": ["夷陵区"]``），已按设计 §3.4 删除——见 ``frozen.md``。

2.0 把**通用**的 30 条搬到数据文件 ``data/places/aliases.json``：可审查、可替换、
可测试，且不得被 agent 提示词或评判逻辑引用。本模块只负责「拿到一张表之后怎么用」，
读文件属于 infrastructure。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

DEFAULT_ADMIN_SUFFIXES: tuple[str, ...] = (
    "省",
    "市",
    "区",
    "县",
    "地区",
    "自治州",
    "盟",
    "特别行政区",
)


@dataclass(frozen=True, slots=True)
class PlaceAliases:
    """中文地名 → GeoNames 可能返回的英文/缩写形式。

    空表是合法状态（数据文件缺失时 infrastructure 就该退化成空表），此时
    ``names_for`` 恒返回空元组——别名桥接失效，但行政区匹配仍然工作。
    """

    table: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    admin_suffixes: tuple[str, ...] = DEFAULT_ADMIN_SUFFIXES

    def names_for(self, value: str) -> tuple[str, ...]:
        """``value`` 里出现了哪些别名键，就展开成它们对应的目标名（去重后有序）。"""
        text = str(value or "").strip()
        if not text:
            return ()
        names: set[str] = set()
        for key, targets in self.table.items():
            if key in text:
                names.update(targets)
        return tuple(sorted(names))

    def strip_admin_suffix(self, part: str) -> str:
        """``秦皇岛市`` → ``秦皇岛``。

        1.0 的顺序是「先匹配到哪个后缀就砍哪个」，对一个词只砍一次；这里保持同样
        行为——砍两次会把 ``自治区`` 砍成 ``自治``，反而丢了信息。
        """
        text = str(part or "").strip()
        for suffix in self.admin_suffixes:
            if text.endswith(suffix) and len(text) > len(suffix):
                return text[: -len(suffix)]
        return text


EMPTY_ALIASES = PlaceAliases()

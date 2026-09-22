"""从数据文件读地名别名表。唯一的 IO 边界。

设计 §3.4 说这张表要「移到独立数据文件（如 ``data/places/aliases.json``），可审查、
可替换、可测试」。放在仓库根的 ``data/`` 下达不到前两条：``.gitignore`` 第 12 行是
``/data/``——那是运行期数据目录（``sentrix.db`` / media / previews / qdrant 都派生自
它），整个被忽略。审查表的第一件事是「表在版本控制里」，所以 2.0 把它放进包内
``infrastructure/places/data/aliases.json``，由 ``pyproject.toml`` 的
``[tool.setuptools.package-data]`` 随包发布。见 ``s4-deviations.md`` §2 第 5 项。

文件缺失或损坏一律退化成空表并**不抛异常**：别名只是中英桥接，没有它行政区匹配
照样工作（``PlaceAliases.names_for`` 空表恒返回空元组）。让它变成启动期硬错误，
等于把一张可选的数据文件升级成单点故障。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sentrix.domain.places.aliases import DEFAULT_ADMIN_SUFFIXES, EMPTY_ALIASES, PlaceAliases

log = logging.getLogger(__name__)

DEFAULT_ALIASES_PATH = Path(__file__).resolve().parent / "data" / "aliases.json"


def _coerce_table(raw: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, dict):
        return {}
    table: dict[str, tuple[str, ...]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            continue
        targets: tuple[str, ...]
        if isinstance(value, str):
            targets = (value,)
        elif isinstance(value, (list, tuple)):
            targets = tuple(str(item) for item in value if str(item or "").strip())
        else:
            continue
        if targets:
            table[key] = targets
    return table


def load_place_aliases(path: Path | None = None) -> PlaceAliases:
    """读别名表。任何失败都退化成 ``EMPTY_ALIASES``，只记一条 warning。"""
    target = path or DEFAULT_ALIASES_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("地名别名表不存在，中英地名桥接降级为空: %s", target)
        return EMPTY_ALIASES
    except (OSError, json.JSONDecodeError) as error:
        log.warning("地名别名表读不了，中英地名桥接降级为空: %s (%s)", target, error)
        return EMPTY_ALIASES
    if not isinstance(payload, dict):
        log.warning("地名别名表顶层不是对象，降级为空: %s", target)
        return EMPTY_ALIASES

    suffixes = payload.get("admin_suffixes")
    if isinstance(suffixes, (list, tuple)) and suffixes:
        admin_suffixes = tuple(str(item) for item in suffixes if str(item or "").strip())
    else:
        admin_suffixes = DEFAULT_ADMIN_SUFFIXES
    return PlaceAliases(table=_coerce_table(payload.get("aliases")), admin_suffixes=admin_suffixes)

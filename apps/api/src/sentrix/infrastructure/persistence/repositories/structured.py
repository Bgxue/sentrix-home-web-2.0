"""结构化查询的后半：``StructuredFilter`` → WHERE，并跑 count / 极值 / 分组。

对应 1.0 ``backend/structured_memory.py`` 的 ``_base_query`` / ``_count`` /
``_first_last`` / ``_group_by`` / ``_list``。前半（「筛什么」）在
``domain/retrieval/structured_query.py``；这一半是 SQL，按分层只能待在 infrastructure。

## 为什么把地点子句单独拎出来

1.0 的 ``_place_clause`` 是一条**跨三层**的规则：它同时要读观察（``o.place``）、
资产（``captured_location``）、反地理 JSON（``reverse_geocode.*``），并且在 SQL 里
又写了一遍 ``geocoding.place_alias_names`` 的别名展开。2.0 里同一条规则有三个孪生：

    ``domain/places/matching.py:place_text_matches``    ← 融合后逐候选判定（权威口径）
    ``domain/retrieval/conditions.py:_place``           ← HARD 条件，走上面那个
    本模块的 ``place_clause``                            ← 召回/统计时的 WHERE

三者必须给出同一个判定，否则「同一个问题，走统计路径和走语义路径得到两个数」。
所以这里的每一条子句都是照着 ``place_text_matches`` 写的，不是照着 1.0 的 SQL 抄的。

## 三处与 1.0 的差异

**一、``a.captured_location`` 那一支去掉。** 2.0 的 ``assets.captured_location``
是 ``GpsPoint``（``domain/media/models.py``），把 ``30.274100,120.155100`` 拿去
``LIKE '%杭州%'`` 永远匹配不上——``test_conditions.py:295`` 已经为这件事写过用例。
1.0 拿它当地点串是因为那时候客户端可以塞一个「家中餐厅」进来，2.0 没有这条写入路径。

**二、``label`` 是拼出来的，不是存的。** 1.0 把 ``reverse_geocode.label`` 存进
``metadata_json``，2.0 按设计 §3.2 拆成了 ``asset_places`` 的五个槽位。这里的
``place_label_sql`` 逐字节复刻 ``places/matching.py:place_label`` 的 ``" ".join``——
空槽位在 SQL 的直接拼接下会留下连续空格，而 Python 那边不会，两边就差一个空格、
少一批照片。

**三、``place`` 分组的键改成确定性的。** 1.0 的 ``_GROUP_EXPR["place"]`` 直接读
``LEFT JOIN`` 上来的 ``o.place``，而一个资产可以有多个观察行——**取到哪一行由
SQLite 的扫描顺序决定**。同一个问题问两次可能给出不同的分组名。这里换成
「取该资产最近一条非空观察」的相关子查询。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from sentrix.domain.places.aliases import PlaceAliases
from sentrix.domain.retrieval.structured_query import (
    TIME_GROUP_KEYS,
    GroupCount,
    StructuredFilter,
    normalize_group_by,
)
from sentrix.infrastructure.persistence.database import Database

# ``place_label`` 参与拼接的槽位，顺序与 ``domain/places/matching.py:_LABEL`` 一致。
_LABEL_SLOTS = (
    "admin_province",
    "admin_city",
    "admin_district",
    "nearest_township",
    "nearest_locality",
)
# 1.0 ``_place_clause`` 做第 2 条规则时只看这三个（``country`` 不在其中：拿国名去
# 匹配会命中一切）。
_ADMIN_SLOTS = ("admin_province", "admin_city", "admin_district")


def _literal(value: str) -> str:
    """SQL 字符串字面量。后缀表来自 JSON 数据文件，**不是**代码里的常量。"""
    return "'" + str(value).replace("'", "''") + "'"


def place_label_sql(alias: str = "ap") -> str:
    """``place_label(place)`` 的 SQL 孪生。

    先判空再决定要不要跟一个空格，最后 ``TRIM`` 掉尾巴——等价于 Python 的
    ``" ".join(非空项)``。直接 ``a || ' ' || b || ' ' || c`` 在空槽位处会留下
    连续空格，于是 ``instr(label, '浙江省杭州市')`` 这类查询静默少算。
    """
    parts = [
        f"CASE WHEN {alias}.{slot} <> '' THEN {alias}.{slot} || ' ' ELSE '' END" for slot in _LABEL_SLOTS
    ]
    return "TRIM(" + " || ".join(parts) + ")"


def strip_admin_suffix_sql(column: str, suffixes: Sequence[str]) -> str:
    """``PlaceAliases.strip_admin_suffix`` 的 SQL 孪生：按顺序只砍**一个**后缀。

    砍两次会把 ``自治区`` 砍成 ``自治``（``aliases.py`` 的 docstring 已说明为什么
    不砍两次），所以这里也是碰到第一个命中的后缀就停——顺序跟着 ``suffixes`` 走。

    ``length()`` 与 ``substr()`` 在 SQLite 里按**字符**计数（TEXT 值），与 Python 的
    ``len()`` 一致；UTF-8 字节数在这里是个坑，所以不能用 ``length(CAST(x AS BLOB))``
    之类看起来更快的东西。
    """
    whens = []
    for suffix in suffixes:
        text = str(suffix)
        size = len(text)
        if not text:
            continue
        whens.append(
            f"WHEN length({column}) > {size} AND substr({column}, -{size}) = {_literal(text)}"
            f" THEN substr({column}, 1, length({column}) - {size})"
        )
    if not whens:
        # 空后缀表是合法状态（数据文件缺失时 alias 表会退化成空）。
        # 没有 WHEN 的 ``CASE ELSE … END`` 是语法错误，所以这里必须提前返回。
        return column
    return f"CASE {' '.join(whens)} ELSE {column} END"


def place_clause(value: str, aliases: PlaceAliases) -> tuple[str, list[object]]:
    """地点过滤的 WHERE 片段。三条规则与 ``place_text_matches`` 一一对应。

    1) 约束值整串出现在 ``observations.place`` 或地点 label 里；
    2) 存储的行政区（去后缀后）出现在约束值里（``秦皇岛`` 匹配
       ``秦皇岛如是海度假村``）；
    3) 中英别名命中（``清迈`` 匹配 ``Chiang Mai``）。

    用 ``instr`` 而不是 ``LIKE``：``LIKE`` 会把约束值里的 ``_`` / ``%`` 当通配符，
    一个地名里的下划线就能让整条子句变成「什么都匹配」。``instr`` 是纯子串，
    与 Python 的 ``in`` 逐字对应。
    """
    text = str(value or "").strip()
    if not text:
        return "1=1", []
    label = place_label_sql()
    clauses = [f"(instr(o.place, ?) > 0 OR instr({label}, ?) > 0)"]
    params: list[object] = [text, text]
    for slot in _ADMIN_SLOTS:
        stripped = strip_admin_suffix_sql(f"ap.{slot}", aliases.admin_suffixes)
        clauses.append(f"(length({stripped}) >= 2 AND instr(?, {stripped}) > 0)")
        params.append(text)
    for alias in aliases.names_for(text):
        # ``lower`` 在 SQLite 里只折 ASCII——别名是 GeoNames 给的英文/缩写，
        # 正好落在它管得着的范围里（CJK 没有大小写）。
        clauses.append(f"instr(lower({label}), lower(?)) > 0")
        params.append(alias)
    return "(" + " OR ".join(clauses) + ")", params


def build_base(filter: StructuredFilter, aliases: PlaceAliases) -> tuple[str, str, list[object]]:
    """``StructuredFilter`` → ``(joins, where, params)``。

    ``scope_id=None`` 表示**全部相册**，此时不拼 scope 子句——写成
    ``scope_id = ''`` 会一条都不匹配，而结果看起来与「这个相册真的没照片」一样。

    时间边界是**左闭右开**且用完整 ISO 串：``captured_at`` 列存的就是
    ``2017-11-05T15:05:13``，而 ``>= '2017-11-05'`` 与 ``>= '2017-11-05T00:00:00'``
    在字典序上不是一回事。
    """
    joins = []
    if filter.entity_ids:
        # 有人物条件时是内连接：没有观察行的资产本来就无从证明「这个人出现过」。
        joins.append("JOIN observations o ON o.asset_id = a.id")
        joins.append("JOIN entity_mentions em ON em.observation_id = o.id")
    else:
        joins.append("LEFT JOIN observations o ON o.asset_id = a.id")
    # 地点子句要读 ap，无论这次筛不筛地点都挂上——挂不挂由 SQL 计划决定代价，
    # 但「筛地点时忘了搭这张表」会变成一个 no such column，而不是一个错数。
    joins.append("LEFT JOIN asset_places ap ON ap.asset_id = a.id")

    clauses: list[str] = []
    params: list[object] = []
    if filter.scope_id is not None:
        clauses.append("a.scope_id = ?")
        params.append(filter.scope_id)
    if filter.media_include:
        clauses.append(f"a.media_type IN ({', '.join('?' * len(filter.media_include))})")
        params.extend(filter.media_include)
    for value in filter.media_exclude:
        clauses.append("a.media_type != ?")
        params.append(value)
    if filter.time_start:
        clauses.append("a.captured_at >= ?")
        params.append(filter.time_start)
    if filter.time_end:
        clauses.append("a.captured_at < ?")
        params.append(filter.time_end)
    if filter.place:
        clause, place_params = place_clause(filter.place, aliases)
        clauses.append(clause)
        params.extend(place_params)
    if filter.entity_ids:
        clauses.append(f"em.entity_id IN ({', '.join('?' * len(filter.entity_ids))})")
        params.extend(filter.entity_ids)
    if not clauses:
        clauses.append("1=1")
    where = " AND ".join(f"({clause})" for clause in clauses)
    return " ".join(joins), where, params


# 该资产最近一条非空观察的地点。用相关子查询而不是左连接上来的 ``o.place``：
# 一个资产可以有多个观察行，直接读 ``o.place`` 取到哪一行由扫描顺序决定。
_LATEST_OBSERVATION_PLACE = (
    "(SELECT op.place FROM observations op"
    " WHERE op.asset_id = a.id AND op.place <> ''"
    " ORDER BY op.captured_at DESC, op.id DESC LIMIT 1)"
)

_GROUP_EXPR = {
    "month": "substr(a.captured_at, 1, 7)",
    "year": "substr(a.captured_at, 1, 4)",
    "date": "substr(a.captured_at, 1, 10)",
    "media": "a.media_type",
    "place": (
        "CASE"
        " WHEN ap.admin_city <> '' THEN ap.admin_city"
        " WHEN ap.nearest_locality <> '' THEN ap.nearest_locality"
        f" WHEN COALESCE({_LATEST_OBSERVATION_PLACE}, '') <> '' THEN {_LATEST_OBSERVATION_PLACE}"
        " WHEN a.gps_latitude IS NOT NULL THEN '有GPS坐标'"
        " ELSE '未知' END"
    ),
}


def group_expression(group_by: str) -> str:
    """分组键 → SQL 表达式。认不出的键在 ``normalize_group_by`` 里就退回了月，
    所以 ``_GROUP_EXPR`` 的索引是全的——这里不留 ``.get(k, 月)`` 那种兜底，
    它会把「表写漏了一个键」变成「静默按月份分组」。"""
    return _GROUP_EXPR[normalize_group_by(group_by)]


class SqliteStructuredQueryRepository:
    """结构化聚合。筛选口径由 ``StructuredFilter`` 决定，这里只把它写成 SQL。

    ``aliases`` 是必填的：给它一个默认空表，会让「忘了接线」表现为「中英地名
    永远匹配不上」，而那是查不出来的——只有一批照片悄悄地不在结果里。
    """

    def __init__(self, database: Database, aliases: PlaceAliases) -> None:
        self._database = database
        self._aliases = aliases

    def _rows(self, sql: str, params: Sequence[object]) -> list[sqlite3.Row]:
        with self._database.connect() as connection:
            return connection.execute(sql, tuple(params)).fetchall()

    def count(self, filter: StructuredFilter) -> int:
        joins, where, params = build_base(filter, self._aliases)
        rows = self._rows(f"SELECT COUNT(DISTINCT a.id) AS n FROM assets a {joins} WHERE {where}", params)
        return int(rows[0]["n"] or 0) if rows else 0

    def captured_bound(self, filter: StructuredFilter, *, latest: bool) -> str | None:
        """最早（``latest=False``）或最晚（``latest=True``）的 ``captured_at``。

        取的是**字符串**不是 ``datetime``：``captured_at`` 落库时已经归一成
        ``isoformat()``，再解析回 ``datetime`` 只是把同一个值绕一圈，而绕的
        那一圈会顺手把「值不是 ISO」这件事变成一个异常。
        """
        joins, where, params = build_base(filter, self._aliases)
        rows = self._rows(
            f"SELECT {'MAX' if latest else 'MIN'}(a.captured_at) AS v FROM assets a {joins} WHERE {where}",
            params,
        )
        return rows[0]["v"] if rows and rows[0]["v"] else None

    def group_counts(self, filter: StructuredFilter, group_by: str) -> tuple[GroupCount, ...]:
        joins, where, params = build_base(filter, self._aliases)
        expression = group_expression(group_by)
        time_filter = " AND a.captured_at IS NOT NULL" if group_by in TIME_GROUP_KEYS else ""
        rows = self._rows(
            f"SELECT {expression} AS g, COUNT(DISTINCT a.id) AS n FROM assets a {joins}"
            f" WHERE {where}{time_filter} GROUP BY g ORDER BY n DESC, g",
            params,
        )
        return tuple(GroupCount(group=row["g"], count=int(row["n"] or 0)) for row in rows)

    def list_groups(self, filter: StructuredFilter, group_by: str) -> tuple[str, ...]:
        joins, where, params = build_base(filter, self._aliases)
        expression = group_expression(group_by)
        time_filter = " AND a.captured_at IS NOT NULL" if group_by in TIME_GROUP_KEYS else ""
        rows = self._rows(
            f"SELECT DISTINCT {expression} AS g FROM assets a {joins} WHERE {where}{time_filter} ORDER BY g",
            params,
        )
        return tuple(row["g"] for row in rows)


__all__ = [
    "SqliteStructuredQueryRepository",
    "build_base",
    "group_expression",
    "place_clause",
    "place_label_sql",
    "strip_admin_suffix_sql",
]

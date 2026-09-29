"""结构化查询的**筛选条件**：从一份 ``QuerySpec`` 派生出「到底筛什么」。纯规则，零 IO。

对应 1.0 ``backend/structured_memory.py`` 的 ``_scope`` / ``_media`` / ``_time_range`` /
``_place`` / ``_entity_ids`` —— 那五个方法**不算数、只把 spec 里的约束翻译成筛选值**，
SQL 才是执行。这里做的是前一半；后一半在 ``infrastructure`` 的仓储里。

**为什么要把这一半单独拿出来。** 1.0 把它们和 SQL 拼在同一个类里（``StructuredMemoryExecutor``），
于是「筛什么」与「怎么筛」混在一起——而前者是纯规则（可以穷举测试），后者是 SQL
（要真库对照）。分开之后，``StructuredFilter`` 的每一条都能单测，而 SQL 只需对
「同一份 filter 给出同一个数」负责。

## 三处与 1.0 的差异，每处都有理由

**一、``derived_kind`` 那一句省掉。** 1.0 的 ``_base_query`` 带
``COALESCE(a.derived_kind, '') = ''``，意思是「只要原始资产，不要派生件（预览图、转码）」。
**2.0 根本没有派生资产**：``assets`` 表没有 ``derived_kind``、也没有 ``parent_asset_id``
（实测 ``PRAGMA table_info``）。照抄那一列会直接报「no such column」；省掉它
**不改变任何结果**——因为没有派生件可筛。

**二、媒体类型的默认值与 1.0 不同点在于「空」的含义。** 1.0 的 ``_media`` 在没有
media 约束时返回空的两个列表，于是那条 WHERE 不出现——**不过滤**。这一条照抄。

**三、时间与地点都取「第一个」而不是「最后一个」。** 1.0 的 ``_place`` / ``_time_range``
都在遍历约束时**碰到第一个可用值就返回**；这里同样第一个胜。写成「后面的覆盖前面的」
在只有一条约束时看不出区别，两条时给出的区间取决于 spec 的构造顺序。

**四、时间用「解析出来的区间」而不是原文。** 1.0 在 ``_base_query`` 里现调
``parse_time_expression``；这里在派生阶段就解析好，把 ``(start, end)`` 放进 filter。
好处是**解析失败在派生阶段就暴露**（返回 ``None`` → 不加时间条件），而不是留到拼 SQL 时
才发现拿到的是 ``None``——那时它已经作为「没有时间条件」悄悄过去了。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sentrix.domain.retrieval.conditions import Condition
from sentrix.domain.retrieval.query_contracts import QuerySpec, Strictness
from sentrix.domain.retrieval.time_expression import parse_time_expression

# 1.0 ``structured_memory.py:_GROUP_EXPR`` 认得的分组键。``year`` / ``date`` 是 2.0 也有的
# （1.0 那张表里就在），``place`` / ``media`` 同。
GROUP_BY_KEYS = ("month", "year", "date", "media", "place")
DEFAULT_GROUP_BY = "month"
# 按时间分组时要求 ``captured_at`` 非空——否则会多出一个「空字符串」分组。
TIME_GROUP_KEYS = ("month", "year", "date")


@dataclass(frozen=True, slots=True)
class GroupCount:
    """一个分组键与它下面的资产数。

    放在 domain 而不是仓储旁边：端口（``domain/retrieval/ports.py``）要引用它，
    而端口不能 import infrastructure。
    """

    group: str
    count: int


@dataclass(frozen=True, slots=True)
class StructuredFilter:
    """「筛什么」。SQL 照着它拼 WHERE，不再自己判断。

    ``scope_id=None`` 表示**全部相册**（1.0 ``db.py:1514`` 的 ``if scope_id:``）。
    写成空串会让 ``WHERE scope_id = ''`` 一条都不匹配——而结果只是「一张都没有」。
    """

    scope_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    media_include: tuple[str, ...] = ()
    media_exclude: tuple[str, ...] = ()
    # 1.0 的区间是**左闭右开**（``captured_at >= start AND captured_at < end``）。
    time_start: str | None = None
    time_end: str | None = None
    place: str = ""
    applied: dict[str, object] = field(default_factory=dict)


def derive_filter(spec: QuerySpec, *, scope_id: str | None = None) -> StructuredFilter:
    """``QuerySpec`` → 筛选条件。

    ``scope_id`` 显式传入而不是从 ``spec`` 取：``spec.scope_ids`` 为空表示「全部相册」，
    而 ``scope_id`` 是**请求方**决定的。1.0 从 ``(context or {}).get("scope_id")`` 取，
    同一个口径。
    """
    media_include: list[str] = []
    media_exclude: list[str] = []
    time_start: str | None = None
    time_end: str | None = None
    place = ""
    entity_ids: list[str] = []
    applied: dict[str, object] = {}

    for constraint in spec.constraints:
        if constraint.dimension == "media":
            target = media_exclude if constraint.negated else media_include
            if constraint.value not in target:
                target.append(constraint.value)
        elif constraint.dimension == "time" and not constraint.negated:
            bounds = parse_time_expression(constraint.value)
            if bounds is not None and time_start is None:
                # ``isoformat`` 而不是 ``str()``：``captured_at`` 列存的是 ISO 串，
                # 而 ``str(datetime)`` 用空格分隔日期与时间——两者的字典序不同，
                # ``>=`` 比较会静默给出错误的边界。
                time_start = bounds[0].isoformat()
                time_end = bounds[1].isoformat()
        elif constraint.dimension == "place" and not constraint.negated and not place:
            # **不看严格度，第一条件胜。** 这是 1.0 ``structured_memory.py:_place``
            # 的行为：它遍历 ``constraints_for("place")`` 并返回第一个非空值，
            # 全程没有看过 ``strictness``。
            #
            # 一度写成了「只有 HARD 才进 WHERE」，那是错的——1.0 的工具层
            # （``agent_runtime/tools.py:_draft_from_filters``）把地点条件构造成
            # ``semantic_required``，于是那个写法会让**每一次**带地点的统计都悄悄
            # 退化成整个相册的统计，而返回值看起来完全正常。
            #
            # 两档严格度的差别作用在**融合之后逐候选判定**上（``conditions.excludes``），
            # 不作用在这里：对 count/first/last 这类问题，``filters.place`` 就是问题本身，
            # 丢掉它等于答非所问。
            place = str(constraint.value or "").strip()

    entity_ids.extend(spec.entity_ids)
    if time_start:
        applied["time"] = (time_start, time_end)
    if media_include:
        applied["media"] = tuple(media_include)
    if media_exclude:
        applied["media_not"] = tuple(media_exclude)
    if place:
        applied["place"] = place
    if entity_ids:
        applied["person"] = tuple(entity_ids)

    return StructuredFilter(
        scope_id=scope_id,
        entity_ids=tuple(dict.fromkeys(entity_ids)),
        media_include=tuple(media_include),
        media_exclude=tuple(media_exclude),
        time_start=time_start,
        time_end=time_end,
        place=place,
        applied=applied,
    )


def hard_conditions(spec: QuerySpec) -> tuple[Condition, ...]:
    """把 spec 的 HARD 约束转成 ``conditions.Condition``——供融合后的排除用。

    **两套筛选不重复**：``derive_filter`` 产出的是**召回/统计时的 WHERE**（快，作用在
    SQL 上），这一条产出的是**融合后逐候选的判定**（准，能区分 UNKNOWN 与 CONTRADICTED）。
    1.0 也是两套（``HardFilterContext`` 与 ``spec.constraints``），这里只是把
    「同一份 spec 派生出两套」写在一处，免得两边的口径漂开。
    """
    return tuple(
        Condition(
            dimension=item.dimension,
            value=item.value,
            strictness=item.strictness,
            negated=item.negated,
            key=item.key,
        )
        for item in spec.constraints
        if item.strictness is Strictness.HARD
    )


def normalize_group_by(value: str | None) -> str:
    """分组键归一。认不出的一律退回 ``month``——1.0 的 ``_GROUP_EXPR.get(k, month)``。

    **不抛**：模型经常把每个可选字段都填上，一个认不出的 group_by 把整条查询变成错误，
    代价远大于按默认分组返回。
    """
    key = str(value or "").strip()
    return key if key in GROUP_BY_KEYS else DEFAULT_GROUP_BY


__all__ = [
    "DEFAULT_GROUP_BY",
    "GroupCount",
    "GROUP_BY_KEYS",
    "TIME_GROUP_KEYS",
    "StructuredFilter",
    "derive_filter",
    "hard_conditions",
    "normalize_group_by",
]

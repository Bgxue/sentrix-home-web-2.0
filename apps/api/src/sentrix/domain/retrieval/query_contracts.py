"""查询契约：一次结构化查询长什么样，以及怎么从「draft」推出它。

对应 1.0 ``backend/query_contracts.py`` 的 ``Constraint`` / ``QueryParseDraft`` /
``QuerySpec`` / ``build_query_spec``，外加 ``agent_runtime/tools.py:80-180`` 的
``_resolve_time_expression`` / ``_media_type`` / ``_draft_from_filters`` / ``_spec_for``。

**为什么这几件要一起搬。** ``query_memory_facts``（S6 T121）吃的是一份 ``QuerySpec``，
而 ``struct_memory`` 的 executor 从头到尾只读它的两个东西：``scope_ids`` 与
``constraints``。少了契约这一层，那个 executor 就没有输入形状——它的 16 个方法全在
``spec.constraints`` 上做分派。

## 三处与 1.0 的差异，每一处都写明了理由

**一、``HARD`` / ``SEMANTIC`` 的家搬到了这里。** 1.0 定义在 ``query_contracts.py``，
``evidence_retrieval`` 引用它们。2.0 的 ``conditions.Strictness`` 先落地（T118），
本任务把它移到这里——**方向与 1.0 一致**：契约是下层，判定是上层。
反过来的话，契约模块会依赖一个判分模块，那是错的层序。

**二、不搬 ``QueryAction`` / ``QueryFacet``。** 它们是**语义解析器的产出**，
``build_query_spec`` 只是把它们抄进 spec，而 ``query_memory_facts`` 一个都不读。
现在搬进来就是「零调用的能力」——这个项目点名要防的形状（``agent_contracts.py``
392 行、静态不可达 → 不搬，是同一个先例）。等 S6 的查询解析落地时再一起建。

**三、``_resolve_time_expression`` 认不出来时返回 ``None``，不返回原文。**
1.0 的注释把理由写死了：「Unknown relative phrases must not be passed to the strict
time parser as if they were absolute expressions.」——把「上上上个月」原样丢给
``parse_time_expression``，它会解析失败，而失败在 HARD 档下会把候选**全部排除**。
丢掉这条约束只是少一层过滤，**方向是安全的**。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from sentrix.domain.retrieval.time_expression import parse_time_expression


class Strictness(StrEnum):
    """1.0 ``query_contracts.py:9-10`` 的两个字面量。

    HARD 的判据是「不满足就排除」，SEMANTIC 只排除明确矛盾——见
    ``conditions.excludes``。**两档的差别是整个条件判定存在的理由。**
    """

    HARD = "deterministic_hard"
    SEMANTIC = "semantic_required"


# ``build_query_spec`` 认得的语义维度。不在表里的一律归 ``semantic``——
# 归错只影响档位（SEMANTIC），不会让候选被排除。
SEMANTIC_DIMENSIONS = frozenset(
    {
        "place",
        "activity",
        "object",
        "clothing",
        "visual",
        "ocr",
        "person",
        "relationship",
        "time",
        "other",
        "semantic",
    }
)


@dataclass(frozen=True, slots=True)
class Constraint:
    """一条约束。``proof_policy`` 记的是「凭什么算数」，1.0 逐条写在调用点。"""

    dimension: str
    value: str
    strictness: Strictness
    proof_policy: str
    negated: bool = False
    source_text: str = ""

    @property
    def key(self) -> str:
        return f"{self.dimension}:{self.value}"


@dataclass(slots=True)
class QueryParseDraft:
    """解析器的**草稿**：请求方的字段还没填，约束也还没派生。

    它是「用户说了什么」的中间形态——``build_query_spec`` 把它变成
    ``QuerySpec``（「这次查询要什么」）。分成两层是因为 ``scope_id`` / ``viewer_id`` /
    ``conversation_id`` 属于**请求**，不属于解析结果。
    """

    intent: str = "answer"
    answer_target: str = "general"
    entity_names: list[str] = field(default_factory=list)
    time_expression: str | None = None
    media_expressions: list[str] = field(default_factory=list)
    semantic_conditions: list[dict[str, Any]] = field(default_factory=list)
    negative_conditions: list[dict[str, Any]] = field(default_factory=list)
    result_requirement: dict[str, Any] = field(default_factory=dict)
    answer_type: str = "asset_set"
    strategy_hint: str = ""
    structured: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QuerySpec:
    query_id: str
    scope_mode: str
    scope_ids: list[str]
    viewer_id: str
    conversation_id: str
    intent: str
    answer_target: str
    constraints: list[Constraint]
    entity_ids: list[str] = field(default_factory=list)
    result_requirement: dict[str, Any] = field(default_factory=dict)

    @property
    def scope_id(self) -> str | None:
        """**恰好一个 scope 时**才是它，否则 ``None``（表示跨相册）。

        1.0 原文如此。写成 ``scope_ids[0]`` 会让「全部相册」的查询悄悄变成
        「只查第一个相册」——而结果只是少几张照片，没有任何报错。
        """
        return self.scope_ids[0] if len(self.scope_ids) == 1 else None

    def constraints_for(self, dimension: str) -> list[Constraint]:
        return [item for item in self.constraints if item.dimension == dimension]


def build_query_spec(
    parsed: QueryParseDraft,
    *,
    scope_id: str | None,
    viewer_id: str,
    conversation_id: str,
    entity_resolver: Callable[[str], str | None] | None = None,
    query_id: str = "query_runtime",
) -> QuerySpec:
    """草稿 + 请求字段 → 契约。**派生出来的约束分三类**（1.0 的说法）：

    - ``time`` / ``media`` / ``person``：**HARD**，且 ``proof_policy`` 写明判据
      （``asset_metadata`` / ``confirmed_bridge``）——它们直接决定候选留不留；
    - ``semantic_conditions``：**SEMANTIC**，不满足不排除；
    - ``negative_conditions``：**HARD + negated**，命中才排除。

    ``entity_resolver`` 解不出人名时**不派生 person 约束**——那与「这个人不存在」
    是两回事：前者是这条约束没法判（HARD 档下会排除一切），后者是确实不是他。
    1.0 也是这个行为（``if entity_id:``）。
    """
    raw_scope = str(scope_id or "").strip()
    all_authorized = scope_id == ""
    scope = raw_scope or "home-default"
    constraints: list[Constraint] = []

    if parsed.time_expression and parse_time_expression(parsed.time_expression):
        constraints.append(
            Constraint(
                "time",
                parsed.time_expression,
                Strictness.HARD,
                "asset_metadata",
                source_text=parsed.time_expression,
            )
        )
    for expression in parsed.media_expressions:
        media_type = parse_media_type(expression)
        if media_type:
            constraints.append(
                Constraint("media", media_type, Strictness.HARD, "asset_metadata", source_text=expression)
            )

    entity_ids: list[str] = []
    for name in parsed.entity_names:
        entity_id = entity_resolver(name) if entity_resolver else None
        if entity_id:
            entity_ids.append(entity_id)
            constraints.append(
                Constraint("person", name, Strictness.HARD, "confirmed_bridge", source_text=name)
            )

    for item in parsed.semantic_conditions:
        dimension = item["dimension"] if item["dimension"] in SEMANTIC_DIMENSIONS else "semantic"
        constraints.append(
            Constraint(
                dimension,
                item["value"],
                Strictness.SEMANTIC,
                "direct_or_possible",
                source_text=item.get("source_text", item["value"]),
            )
        )
    for item in parsed.negative_conditions:
        constraints.append(
            Constraint(
                item["dimension"],
                item["value"],
                Strictness.HARD,
                "asset_metadata",
                negated=True,
                source_text=item["source_text"],
            )
        )

    return QuerySpec(
        query_id=query_id,
        scope_mode="all_authorized" if all_authorized else "single",
        scope_ids=[] if all_authorized else [scope],
        viewer_id=str(viewer_id or "").strip() or "owner",
        conversation_id=str(conversation_id or "").strip() or "conversation_runtime",
        intent=parsed.intent,
        answer_target=parsed.answer_target,
        constraints=constraints,
        entity_ids=list(dict.fromkeys(entity_ids)),
        result_requirement=parsed.result_requirement,
    )


# ---- 工具侧的两个解析 helper -----------------------------------------------------

_CN_MONTHS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}

_SEASONS = {
    "春天": (3, 5),
    "夏天": (6, 8),
    "秋天": (9, 11),
    "冬天": (12, 2),
    "春季": (3, 5),
    "夏季": (6, 8),
    "秋季": (9, 11),
    "冬季": (12, 2),
}

_BASE_YEAR = {"去年": -1, "今年": 0, "前年": -2}


def _cn_month(value: str) -> int:
    text = value.strip()
    if text.isdigit():
        return int(text)
    return _CN_MONTHS.get(text, 0)


def resolve_time_expression(value: str | None, *, now: datetime) -> str | None:
    """把相对时间说法解析成 ``parse_time_expression`` 认得的形式。

    1.0 ``agent_runtime/tools.py:80-131``。**认不出来返回 ``None``**——见模块注释第三条。

    ``now`` 由调用方注入而不是在函数里取：时间条件判的是「用户说的那段时间」，
    而「现在」进函数会让这个纯函数不可测。1.0 用的是 ``time_context.now()``，
    那是它的可注入时钟。
    """
    v = str(value or "").strip()
    if not v:
        return None
    year, month = now.year, now.month

    month_rel = re.search(r"(去年|今年|前年)\s*(\d{1,2}|[一二三四五六七八九十]+)\s*月", v)
    if month_rel:
        return f"{year + _BASE_YEAR[month_rel.group(1)]}年{_cn_month(month_rel.group(2))}月"

    season_rel = re.search(r"(去年|今年|前年)\s*(" + "|".join(_SEASONS) + ")", v)
    if season_rel:
        base = year + _BASE_YEAR[season_rel.group(1)]
        start_month, end_month = _SEASONS[season_rel.group(2)]
        if start_month <= end_month:
            return f"{base}年{start_month}月-{base}年{end_month}月"
        # 冬天跨年：12 月到次年 2 月
        return f"{base}年12月-{base + 1}年2月"

    if "这两年" in v or "近两年" in v or "最近两年" in v:
        return f"{year - 1}年-{year}年"
    if "最近一年" in v or "近一年" in v:
        prev_year, prev_month = (year - 1, month) if month > 1 else (year - 1, 12)
        return f"{prev_year}年{prev_month}月-{year}年{month}月"
    if "上上个月" in v:
        pm2 = (year, month - 2) if month > 2 else (year - 1, month + 10)
        return f"{pm2[0]}年{pm2[1]}月"
    if "上个月" in v:
        pm = (year, month - 1) if month > 1 else (year - 1, 12)
        return f"{pm[0]}年{pm[1]}月"
    if "去年" in v:
        return f"{year - 1}年"
    if "今年" in v:
        return f"{year}年"
    if "前年" in v:
        return f"{year - 2}年"
    if re.fullmatch(r"20\d{2}", v):
        return f"{v}年"
    if re.fullmatch(r"20\d{2}年(?:\d{1,2}月(?:\d{1,2}[日号]?)?)?", v):
        return v
    return None


def parse_media_type(value: str) -> str | None:
    """媒体类型说法 → 枚举值。1.0 ``query_contracts.py:_media_type``。

    认不出来返回 ``None``：**这与「用户说了个不存在的类型」是两回事**，
    而两者都会让那条约束不被派生。认不出的情形是「这段文本里没有媒体类型」。
    """
    text = str(value or "").strip().lower()
    if any(token in text for token in ("视频", "video", "录像")):
        return "video"
    if any(token in text for token in ("音频", "audio", "录音")):
        return "audio"
    if any(token in text for token in ("文本", "text", "文字")):
        return "text"
    if any(token in text for token in ("照片", "图片", "原图", "image")):
        return "image"
    return None


__all__ = [
    "Constraint",
    "QueryParseDraft",
    "QuerySpec",
    "SEMANTIC_DIMENSIONS",
    "Strictness",
    "build_query_spec",
    "parse_media_type",
    "resolve_time_expression",
]

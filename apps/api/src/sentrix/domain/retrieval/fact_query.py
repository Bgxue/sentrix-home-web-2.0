"""``query_memory_facts`` 的纯规则部分：参数校验 + 请求 → ``QuerySpec``。

对应 1.0 ``agent_runtime/tools.py:207-303`` 的 ``_query_memory_facts``，以及它旁边
四个 helper（``_draft_from_filters`` / ``_spec_for`` / ``_resolve_entity`` /
``_fact_samples``）。1.0 把「校验参数」「查人名」「拼 spec」「跑 SQL」「组装返回值」
写在同一个 90 行的函数里；2.0 按分层拆开，**这里只留不需要 IO 的那一半**：

    参数 → ``FactRequest``（或 ``FactRejection``）      本模块，纯规则，可以穷举
    ``FactRequest`` → ``QuerySpec``                      本模块，纯规则
    人名 → entity_id                                      application（要查库）
    跑 SQL                                                infrastructure（见 structured.py）
    组装 value / unit / coverage                          application

## 校验顺序是契约的一部分

1.0 的检查是一条瀑布，先后不能换：先看有没有多余参数，再看 ``filters`` 是不是对象，
再看每个枚举认不认得，最后才看「这个 subject 配不配这个 filter」。换掉两条的顺序，
同一个畸形输入会得到不同的 ``reason``——而 ``reason`` 是要回给模型看的，它据此决定
下一步怎么改参数。所以下面的顺序与 1.0 逐条对齐，用例也逐条钉住。

## ``group_by`` 挂在 count/exists 上是忽略，不是报错

小模型经常把 schema 里每个可选字段都填上。``{"operation": "count", "group_by": "month"}``
不是模型说错了话，只是它多填了一个用不上的字段——把它判成 ``unsupported_group_by``
会让一次本来能精确回答的查询变成一个错误。1.0 的做法是**把它清空**（``group_by = ""``），
这里照做。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sentrix.domain.retrieval.query_contracts import (
    QueryParseDraft,
    QuerySpec,
    build_query_spec,
    resolve_time_expression,
)

# 工具接受的顶层参数。多一个都拒——1.0 用它拦住模型自己发明的 ``scope_id``。
ARGUMENT_KEYS = frozenset({"operation", "subject", "group_by", "filters"})
FILTER_KEYS = frozenset({"time", "media", "place", "person"})
MEDIA_VALUES = frozenset({"image", "video"})


class FactOperation(StrEnum):
    COUNT = "count"
    EXISTS = "exists"
    FIRST = "first"
    LAST = "last"
    GROUP = "group"


class FactSubject(StrEnum):
    ASSET = "asset"
    PERSON_APPEARANCE = "person_appearance"
    PROCESSING = "processing"


# ``in`` 一个 Enum 在 3.11 与 3.12 上不是一回事：3.11 拿一个普通字符串去
# ``x in SomeEnum`` 会直接抛 ``TypeError``（3.12 才放宽成「按值判断」）。
# 用值的集合做白名单，两个版本上行为一致，也不必依赖那个版本差异。
_OPERATION_VALUES = frozenset(item.value for item in FactOperation)
_SUBJECT_VALUES = frozenset(item.value for item in FactSubject)

# 分组键白名单。``status`` 只对 ``processing`` 开放——按处理状态分组问的是导入流水线，
# 与「这个相册里有什么」不是同一个问题，两边的键因此不共用一张表。
ASSET_GROUPS = frozenset({"month", "place", "media"})
PROCESSING_GROUPS = frozenset({"status"})


@dataclass(frozen=True, slots=True)
class FactRejection:
    """一次被拒的调用。``status`` 是粗分类，``reason`` 是细原因。

    两个字段都要，因为它们回答不同的问题：模型靠 ``reason`` 改参数，而调用方
    （账本、遥测）靠 ``status`` 归类。1.0 的 ``_structured_fact_error`` 也是两个。
    """

    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class FactRequest:
    """通过校验的一次请求。字段与 ``filters`` 一一对应，**没有多余状态**。"""

    operation: FactOperation
    subject: FactSubject
    time: str = ""
    media: str = ""
    place: str = ""
    person: str = ""
    group_by: str = ""

    @property
    def groups(self) -> frozenset[str]:
        return PROCESSING_GROUPS if self.subject is FactSubject.PROCESSING else ASSET_GROUPS


def _text(value: Any) -> str:
    return str(value or "").strip()


def parse_fact_arguments(arguments: Mapping[str, Any]) -> FactRequest | FactRejection:
    """工具的入参 → ``FactRequest`` 或 ``FactRejection``。**顺序是契约**（见模块注释）。"""
    raw = arguments if isinstance(arguments, Mapping) else {}

    if set(raw) - ARGUMENT_KEYS:
        return FactRejection("unsupported_filter", "unsupported_argument")

    operation = _text(raw.get("operation"))
    subject = _text(raw.get("subject"))
    group_by = _text(raw.get("group_by"))
    filters = raw.get("filters") or {}
    if not isinstance(filters, Mapping):
        return FactRejection("unsupported_filter", "filters_must_be_object")
    if set(filters) - FILTER_KEYS:
        return FactRejection("unsupported_filter", "unsupported_filter")
    if operation not in _OPERATION_VALUES:
        return FactRejection("unsupported_operation", "unsupported_operation")
    if subject not in _SUBJECT_VALUES:
        return FactRejection("unsupported_subject", "unsupported_subject")

    media = _text(filters.get("media")).lower()
    if media and media not in MEDIA_VALUES:
        return FactRejection("unsupported_filter", "unsupported_media")

    person = _text(filters.get("person"))
    if operation == FactOperation.GROUP:
        allowed = PROCESSING_GROUPS if subject == FactSubject.PROCESSING else ASSET_GROUPS
        if group_by not in allowed:
            return FactRejection("unsupported_filter", "unsupported_group_by")
    else:
        # 多填的 group_by 清空，不报错——见模块注释。
        group_by = ""

    if subject == FactSubject.PROCESSING and person:
        return FactRejection("unsupported_filter", "processing_does_not_support_person")
    if subject == FactSubject.PERSON_APPEARANCE and not person:
        return FactRejection("unresolved_person", "person_required")

    return FactRequest(
        operation=FactOperation(operation),
        subject=FactSubject(subject),
        time=_text(filters.get("time")),
        media=media,
        place=_text(filters.get("place")),
        person=person,
        group_by=group_by,
    )


def fact_query_spec(
    request: FactRequest,
    *,
    scope_id: str,
    viewer_id: str,
    conversation_id: str,
    query_id: str,
    now: datetime,
    entity_resolver: Callable[[str], str | None] | None = None,
) -> QuerySpec:
    """``FactRequest`` → ``QuerySpec``。1.0 ``_draft_from_filters`` + ``_spec_for``。

    **地点条件进的是 ``semantic_conditions``（SEMANTIC 档），不是 HARD。** 这是 1.0
    工具层的行为，一开始照抄它并不显眼——但它是「地点条件不看严格度」那条修正的
    由来：``derive_filter`` 若按 HARD 筛，这个地点条件会被**整个丢掉**，于是每一次
    带地点的统计都悄悄退化成整个相册的统计。

    **这里不设 ``draft.answer_type``，也不设 ``draft.structured``。** 1.0 设了这两个，
    因为它的 ``StructuredMemoryExecutor.execute()`` 读 ``draft.answer_type`` 来挑操作、
    读 ``draft.structured["aggregation"]`` 来取分组键。2.0 不在 spec 上做这件事：
    操作与分组键由 ``FactRequest`` 自己带着（``operation`` / ``group_by``），
    由 application 层直接分派到对应的仓储方法。而 ``build_query_spec`` 本来也不读
    这两个字段——写进去就是一份没有读者的配置，正是这个仓库反复在删的那种东西。

    时间走 ``resolve_time_expression`` 而不是直接把原文塞进 draft：模型契约是
    「相对时间原样传进来，由工具确定性换算」。换算不出来就**不加时间条件**——
    把「上上上个月」当绝对时间塞进去，会得到一条判不出真假的条件（1.0 的注释
    写得很清楚：宁可放宽召回，也不要悄悄给一条错的时间区间）。
    """
    draft = QueryParseDraft(intent="answer", answer_target="general")
    resolved = resolve_time_expression(request.time, now=now)
    if resolved:
        draft.time_expression = resolved
    if request.place:
        draft.semantic_conditions.append(
            {"dimension": "place", "value": request.place, "strictness": "semantic_required"}
        )
    if request.person:
        draft.entity_names.append(request.person)
    if request.media:
        draft.media_expressions.append(request.media)
    return build_query_spec(
        draft,
        scope_id=scope_id,
        viewer_id=viewer_id,
        conversation_id=conversation_id,
        entity_resolver=entity_resolver,
        query_id=query_id,
    )


__all__ = [
    "ARGUMENT_KEYS",
    "ASSET_GROUPS",
    "FILTER_KEYS",
    "MEDIA_VALUES",
    "PROCESSING_GROUPS",
    "FactOperation",
    "FactRejection",
    "FactRequest",
    "FactSubject",
    "fact_query_spec",
    "parse_fact_arguments",
]

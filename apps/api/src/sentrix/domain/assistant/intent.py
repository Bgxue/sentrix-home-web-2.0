"""确定性规划：把问题映射成「最少需要哪些证据」。

这是 2.0 对 1.0 ``planner_call_error`` 的根本处理。1.0 的规划是一次模型调用
（``goal_planner.py``），它失败时返回 ``fallback_reason="planner_call_error"``——生产
数据里这个原因出现 1,974 次，最终都落成 ``planner_blocked``（1,445 条轨迹），整轮直接
以「无法可靠规划」收尾。而规划要产出的东西（这段问题需要照片证据还是人物证据）是一个
**由问句形态决定的、可以用规则判定**的问题，不需要模型。

于是 S3 的规划是纯函数：没有模型调用，就没有调用失败；``planner_call_error`` 在 2.0 里
不是「被修好了」，而是**不存在这条路径**。模型仍然要做它擅长的那部分——把「在海边的
照片」变成检索词 ``海边``——但那是在工具调用的参数里，不是在规划里。

1.0 的 ``intent.py`` / ``canonical_intent.py`` 已经是这个方向的确定性实现，规则的形态
沿用它们。
"""

from __future__ import annotations

import re

from sentrix.domain.assistant.evidence_ledger import EvidenceRequirement, EvidenceType

# 「把照片给我看」这一类意图。用户说了这些词，答案里就必须真的有照片——
# 只回一段「我找到了两张，你要看哪张？」不算交付。
DELIVERY_MARKERS = (
    "看看",
    "看一下",
    "给我看",
    "给我瞧",
    "想看",
    "看下",
    "看照片",
    "看图",
    "展示",
    "打开",
    "原图",
    "发我",
    "发给我",
)

PHOTO_MARKERS = (
    "照片",
    "相片",
    "图片",
    "相册",
    "拍",
    "图",
)

PERSON_MARKERS = (
    "是谁",
    "是谁呀",
    "介绍",
    "档案",
    "画像",
    "多大",
    "叫什么",
)

_WHITESPACE = re.compile(r"\s+")


def canonical_question(question: str) -> str:
    """问句的规范形式：去掉空白。规则匹配与它比较，避免「给 我 看 看」这类输入漏判。"""
    return _WHITESPACE.sub("", question or "")


def wants_delivery(question: str) -> bool:
    text = canonical_question(question)
    return any(marker in text for marker in DELIVERY_MARKERS)


def wants_photos(question: str) -> bool:
    text = canonical_question(question)
    return any(marker in text for marker in PHOTO_MARKERS)


def wants_person(question: str) -> bool:
    text = canonical_question(question)
    return any(marker in text for marker in PERSON_MARKERS)


def plan_requirements(question: str) -> tuple[EvidenceRequirement, ...]:
    """这个问题至少要拿到哪几类证据。

    规则刻意保守：默认给一条 ``photo_set``。宁可多取一次证据，也不要因为没识别出意图
    让模型空手作答——1.0 的 ``goal_planner`` 允许「零需求」（``goal_planner.py:228``），
    于是 JIT 提示词直接告诉模型「当前没有待确认的证据需求……可直接输出 final」，
    这正是 29% 的回合一次工具都没调的来源之一。
    """
    requirements: list[EvidenceRequirement] = []

    if wants_person(question) and not wants_photos(question):
        requirements.append(
            EvidenceRequirement(
                id="person_profile",
                evidence_type=EvidenceType.PERSON_PROFILE,
                description="读取这位已确认人物的家庭角色。",
            )
        )
    else:
        requirements.append(
            EvidenceRequirement(
                id="photo_set",
                evidence_type=EvidenceType.PHOTO_SET,
                description="从相册里取到与问题相关的照片。",
            )
        )

    if wants_delivery(question):
        requirements.append(
            EvidenceRequirement(
                id="delivered_photo",
                evidence_type=EvidenceType.DELIVERED_PHOTO,
                description="把要展示的照片交接给用户，让它们真的能打开。",
            )
        )

    return tuple(requirements)

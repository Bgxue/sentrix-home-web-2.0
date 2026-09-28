"""人物瞬间的受控词表与归一化。

逐行移植 1.0 ``backend/person_moments.py`` 的**纯的那一半**：提示词原文、
``PARTICIPATION_STYLES`` / ``SENSITIVE_TERMS`` / ``PERSONALITY_TERMS`` 三张词表，
以及 ``normalize_person_moments``。

**为什么词表住在这里而不是在画像模块里。** 1.0 里 ``SENSITIVE_TERMS`` 定义在
``person_moments.py``，被 ``person_portraits.py`` 与 ``person_graph.py`` 两边 import。
裁决① 不搬 ``person_graph.py``，2.0 的消费方只剩画像校验一处——但它**属于词表**，
不属于画像：它定义的是「哪些词不许进记忆库」，人物瞬间的 ``visible_affect`` 与
``social_role_cues`` 也按它过滤（``normalize_person_moments``）。放进 portrait.py 会让
「瞬间的过滤」反向依赖「画像的校验」，那正是本仓库一路在防的方向。

**另一半（``render_numbered_preview`` / ``PersonMomentExtractor``）不在这里。**
前者要 PIL（domain-purity 契约禁止），后者要 ``store`` 与模型调用（IO）。
它们落在 ``infrastructure/media/person_vision.py`` 与
``application/identity/extract_moments.py``。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# 1.0 ``person_moments.py:12-21`` 的提示词原文。**一字不差**：它是模型契约的一部分，
# 改一个字都要重新验一遍真实模型的输出。
PERSON_MOMENT_PROMPT = (
    "你是家庭相册中的人物瞬间观察器。图片中的 P1/P2 标签仅用于定位人物。\n"
    "只描述画面可见动作、互动、参与方式、表情状态和低风险角色线索。\n"
    "不得猜姓名；不得输出健康、收入、宗教、民族、政治、性取向；不得把年龄、\n"
    "代际或性别呈现保存为事实。看不清时留空。\n"
    "严格返回 JSON：\n"
    '{"moments":[{"label":"P1","action_text":"","interaction_labels":[],\n'
    '"interaction_text":"","participation_style":"","visible_affect":"",\n'
    '"social_role_cues":[],"narrative_note":"","confidence":0.0}]}'
)

# 1.0 ``person_moments.py:23``。归一化时白名单外的一律折成「无法判断」——
# 参与方式是受控词表，不是自由文本。
# 1.0 ``person_moments.py:186`` 的字面量。它是 ``person_moments`` 唯一键的一部分
# （``(person_id, observation_id, prompt_version)``），所以它的含义是**提示词的版本**：
# 改了 ``PERSON_MOMENT_PROMPT`` 就必须改它，否则重跑会把上一版提示词抽出来的瞬间
# 当成本版的一部分更新掉——两次结论混在同一行里，事后分不开。
PERSON_MOMENT_PROMPT_VERSION = "person-moment-v1"

PARTICIPATION_STYLES = frozenset({"组织", "照顾", "陪伴", "共同参与", "旁观", "拍摄", "无法判断"})
DEFAULT_PARTICIPATION_STYLE = "无法判断"

# 1.0 ``person_moments.py:25-34``。这张表是**产品规则**的机械判据：它同时被
# 人物瞬间的清洗（本模块）与画像校验（``portrait.py``）读，所以改动一次会同时
# 影响两条链路——这是有意的，两条链路要防的是同一件事。
SENSITIVE_TERMS = frozenset(
    {
        "健康",
        "生病",
        "疾病",
        "癌症",
        "抑郁",
        "焦虑",
        "收入",
        "富有",
        "贫穷",
        "有钱",
        "工资",
        "负债",
        "宗教",
        "佛教",
        "基督教",
        "伊斯兰",
        "民族",
        "汉族",
        "回族",
        "藏族",
        "维吾尔",
        "政治",
        "党员",
        "政见",
        "性取向",
        "同性恋",
        "年龄",
        "岁数",
        "老人",
        "中年",
        "青年",
        "老年",
        "性别",
        "男性",
        "女性",
        "辈分",
        "代际",
    }
)

# 1.0 ``person_moments.py:36-39``。比 SENSITIVE_TERMS 窄一档：只用于清 ``visible_affect``
# （表情描述里出现性格词就是把一次观察写成了一个人的稳定特质）。
PERSONALITY_TERMS = frozenset(
    {
        "内向",
        "外向",
        "开朗",
        "乐观",
        "悲观",
        "易怒",
        "敏感",
        "固执",
        "性格",
        "脾气",
        "温和",
        "暴躁",
    }
)


def contains_any(value: str, terms: frozenset[str]) -> bool:
    """1.0 ``person_moments.py:42`` 的 ``_contains_any``。子串匹配，不是分词——
    中文没有词边界，1.0 用的就是子串，照搬。"""
    return any(term in value for term in terms)


def normalize_person_moments(parsed: Any, labels: Sequence[str]) -> list[dict[str, Any]]:
    """把模型的自由输出收成受控形状。1.0 ``person_moments.py:46-88``。

    逐条丢弃的规则（每一条都对应一种模型的越界）：
    - ``label`` 不在本次预览的标签集里 -> 丢（模型认领了一个不存在的 P 编号）；
    - ``action_text`` 为空 -> 丢（「看不清时留空」的模型输出不是一条瞬间）；
    - ``participation_style`` 不在受控词表里 -> 折成「无法判断」，不丢；
    - ``social_role_cues`` 里含敏感词的单项 -> 丢那一项，不丢整条；
    - ``visible_affect`` 含敏感词或性格词 -> **整段清空**（1.0 原文），不丢整条。

    这三档处置的区别是有意的：标签与动作是**定位**，错了整条无意义；后两样是**措辞**，
    错一个词不该把整次观察扔掉。
    """
    label_set = set(labels)
    moments: list[dict[str, Any]] = []
    raw_moments = parsed.get("moments") if isinstance(parsed, dict) else []
    if not isinstance(raw_moments, list):
        raw_moments = []
    for item in raw_moments:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if label not in label_set:
            continue
        action = str(item.get("action_text") or "").strip()
        if not action:
            continue
        style = str(item.get("participation_style") or "").strip() or DEFAULT_PARTICIPATION_STYLE
        if style not in PARTICIPATION_STYLES:
            style = DEFAULT_PARTICIPATION_STYLE
        cues: list[str] = []
        for cue in item.get("social_role_cues") or []:
            value = str(cue).strip()
            if value and not contains_any(value, SENSITIVE_TERMS):
                cues.append(value)
        affect = str(item.get("visible_affect") or "").strip()
        if contains_any(affect, SENSITIVE_TERMS | PERSONALITY_TERMS):
            affect = ""
        interaction_labels: list[str] = []
        for raw in item.get("interaction_labels") or []:
            value = str(raw).strip()
            if value and value not in interaction_labels:
                interaction_labels.append(value)
        moments.append(
            {
                "label": label,
                "action_text": action,
                "interaction_labels": interaction_labels,
                "interaction_text": str(item.get("interaction_text") or ""),
                "participation_style": style,
                "visible_affect": affect,
                "social_role_cues": list(dict.fromkeys(cues)),
                "narrative_note": str(item.get("narrative_note") or ""),
                "confidence": float(item.get("confidence") or 0),
            }
        )
    return moments

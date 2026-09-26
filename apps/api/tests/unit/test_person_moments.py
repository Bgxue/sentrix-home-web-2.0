"""人物瞬间的词表与归一化（Task 86）——1.0 ``backend/person_moments.py`` 的纯的那一半。

`normalize_person_moments` 的可观察行为很密：**四种越界各有各的处置**，混起来就等于
没验。四档处置是（1.0 原文，逐条钉）：

| 越界 | 处置 |
|---|---|
| ``label`` 不在本次预览的标签集里 | **丢整条** |
| ``action_text`` 为空 | **丢整条** |
| ``participation_style`` 不在受控词表里 | 折成「无法判断」，**不丢** |
| ``social_role_cues`` 里某项含敏感词 | **丢那一项**，不丢整条 |
| ``visible_affect`` 含敏感词或性格词 | **整段清空**，不丢整条 |

前三档是「定位」错了，错了整条无意义；后两档是「措辞」错了，错一个词不该把整次观察
扔掉。**这个区别是产品规则，不是实现细节**，所以每一档一条用例。
"""

from __future__ import annotations

from sentrix.domain.identity.moments import (
    DEFAULT_PARTICIPATION_STYLE,
    PARTICIPATION_STYLES,
    PERSONALITY_TERMS,
    SENSITIVE_TERMS,
    normalize_person_moments,
)

LABELS = ["P1", "P2", "P3"]


def item(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "label": "P1",
        "action_text": "抱着孩子",
        "participation_style": "照顾",
        "visible_affect": "微笑",
        "social_role_cues": [],
        "interaction_labels": [],
        "interaction_text": "",
        "narrative_note": "",
        "confidence": 0.8,
    }
    base.update(overrides)
    return base


def test_vocabularies_are_the_1_0_literals():
    """两张词表是**产品规则的机械判据**，同时被瞬间清洗与画像校验读。
    钉住几个决定性成员，防止有人顺手"整理"词表——那等于改产品规则。"""
    assert "无法判断" in PARTICIPATION_STYLES
    assert len(PARTICIPATION_STYLES) == 7
    # 画像那条「未确认角色要带模糊措辞」的判据读的就是这张表里的词。
    assert {"生病", "收入", "宗教", "民族", "政治", "年龄", "性别"} <= SENSITIVE_TERMS
    # 性格词比敏感词窄一档：只用于清 visible_affect。
    assert "开朗" in PERSONALITY_TERMS
    assert "开朗" not in SENSITIVE_TERMS


def test_label_outside_the_preview_is_dropped_whole():
    """模型认领了一个本次预览里不存在的 P 编号——它说的是别的照片，整条不可信。"""
    assert normalize_person_moments({"moments": [item(label="P9")]}, LABELS) == []
    assert normalize_person_moments({"moments": [item(label="")]}, LABELS) == []


def test_empty_action_is_dropped_whole():
    """「看不清时留空」是提示词要求的行为，留空的输出不是一条瞬间。"""
    for blank in ("", "   ", None):
        assert normalize_person_moments({"moments": [item(action_text=blank)]}, LABELS) == []


def test_action_text_is_stripped():
    kept = normalize_person_moments({"moments": [item(action_text="  抱着孩子  ")]}, LABELS)
    assert kept[0]["action_text"] == "抱着孩子"


def test_participation_style_outside_the_vocabulary_folds_to_undecided():
    """参与方式是**受控词表**，不是自由文本。越界与缺省走同一条路：折成「无法判断」，
    **不丢整条**——模型只是用词不守规矩，这次观察本身仍然有效。"""
    for bad in ("乱写的", "", None, "组织活动"):
        kept = normalize_person_moments({"moments": [item(participation_style=bad)]}, LABELS)
        assert len(kept) == 1
        assert kept[0]["participation_style"] == DEFAULT_PARTICIPATION_STYLE
    kept = normalize_person_moments({"moments": [item(participation_style="旁观")]}, LABELS)
    assert kept[0]["participation_style"] == "旁观"


def test_sensitive_cue_is_dropped_but_the_moment_survives():
    """``social_role_cues`` 是**逐项**过滤：含敏感词的那一项丢掉，同一条瞬间的其余
    线索与动作照旧保留。丢掉整条会把一次有效观察一起扔掉。

    **构造必须用真正在词表里的词。** 第一版这里用的是「有糖尿病」——它**不在**
    ``SENSITIVE_TERMS`` 里（词表只有「生病」「疾病」「癌症」这些笼统词），于是把过滤器
    整段删掉都不会变红：用例看起来在钉过滤，其实一次都没走到过滤分支上。
    """
    kept = normalize_person_moments(
        {"moments": [item(social_role_cues=["父亲", "常年生病", "照顾者", ""])]}, LABELS
    )
    assert kept[0]["social_role_cues"] == ["父亲", "照顾者"]
    assert kept[0]["action_text"] == "抱着孩子"


def test_the_sensitive_vocabulary_is_coarse_by_design():
    """**已知的、有意的词表粒度**（1.0 原样，登记在 s5-deviations.md）。

    ``SENSITIVE_TERMS`` 是笼统词表：它拦「生病」不拦「糖尿病」，拦「收入」不拦
    「月薪三万」。所以具体病名与会具体数字的说法**会漏过去**。

    这条用例不是在庆祝这个行为，是把它**钉住**——它是一处已知边界，不是一处待修的
    缺陷：把词表改成穷举具体病名既列不完，也不是本切片该改的产品规则（真要收紧，
    该动的是提示词与审核策略，不是这张表）。
    """
    assert "糖尿病" not in SENSITIVE_TERMS
    kept = normalize_person_moments({"moments": [item(social_role_cues=["有糖尿病"])]}, LABELS)
    assert kept[0]["social_role_cues"] == ["有糖尿病"]


def test_visible_affect_is_cleared_on_sensitive_or_personality_language():
    """``visible_affect`` 是**整段清空**（不是逐词）。含性格词等于把一次表情观察写成
    这个人的稳定特质，含敏感词直接违反产品红线——两种都清空，但**瞬间本身保留**。"""
    for bad in ("开朗", "性格温和", "看起来生病了", "显得很健康"):
        kept = normalize_person_moments({"moments": [item(visible_affect=bad)]}, LABELS)
        assert len(kept) == 1, bad
        assert kept[0]["visible_affect"] == "", bad
    kept = normalize_person_moments({"moments": [item(visible_affect="微笑")]}, LABELS)
    assert kept[0]["visible_affect"] == "微笑"


def test_interaction_labels_are_deduped_and_preserve_order():
    """保序去重，**空串丢掉**。

    ``None`` 会变成字符串 ``"None"`` —— 这不是我抄错，是 1.0 的行为，实测过：
    ``str(raw).strip()`` 对 ``None`` 给出 ``"None"``，它是真值，于是被留下。
    同样的输入丢进 1.0 得到 ``['P2', 'P3', 'None']``。

    **本轮照搬，不修。** 理由：它的下游是 ``PersonMomentExtractor`` 的
    ``label_map.get(target_label)``，而 ``"None"`` 在 ``label_map`` 里查不到 ->
    静默被丢，落库的 ``interaction_target_ids`` 里不会出现垃圾。也就是说这是一条
    **无下游影响**的脏值，为它偏离 1.0 不值得（登记在 s5-deviations.md）。
    """
    kept = normalize_person_moments(
        {"moments": [item(interaction_labels=["P2", "P3", "P2", "", None])]}, LABELS
    )
    assert kept[0]["interaction_labels"] == ["P2", "P3", "None"]


def test_non_dict_and_non_list_payloads_yield_nothing():
    """模型偶尔回一个数组、裸串或 ``moments: null``。这条路径不能抛——
    抛出去就是「模型一抽风整次生成失败」。"""
    assert normalize_person_moments([], LABELS) == []
    assert normalize_person_moments("不是字典", LABELS) == []
    assert normalize_person_moments({"moments": None}, LABELS) == []
    assert normalize_person_moments({"moments": {"a": 1}}, LABELS) == []


def test_non_dict_items_inside_moments_are_skipped_not_fatal():
    kept = normalize_person_moments({"moments": ["不是字典", item(), 42]}, LABELS)
    assert len(kept) == 1


def test_confidence_is_coerced_to_float_and_defaults_to_zero():
    kept = normalize_person_moments(
        {"moments": [item(confidence="0.5"), item(label="P2", confidence=None)]}, LABELS
    )
    assert kept[0]["confidence"] == 0.5
    assert kept[1]["confidence"] == 0.0


def test_text_fields_default_to_empty_strings_not_none():
    kept = normalize_person_moments({"moments": [item(interaction_text=None, narrative_note=None)]}, LABELS)
    assert kept[0]["interaction_text"] == ""
    assert kept[0]["narrative_note"] == ""


def test_order_of_the_model_output_is_preserved():
    """保序：抽取用例按标签反查人物，顺序变了会让「每人每事件一张」的配对错位。"""
    kept = normalize_person_moments(
        {"moments": [item(label="P3", action_text="做饭"), item(label="P1"), item(label="P2")]}, LABELS
    )
    assert [m["label"] for m in kept] == ["P3", "P1", "P2"]

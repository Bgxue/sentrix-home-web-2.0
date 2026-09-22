"""确定性规划的单元测试。

1.0 的规划是一次模型调用（``goal_planner.py``），它失败时返回
``fallback_reason="planner_call_error"``——生产上这个原因出现 1,974 次，最终都落成
``planner_blocked``（1,445 条轨迹），整轮以「无法可靠规划」收尾。2.0 的规划是纯函数：
没有调用，就没有调用失败。
"""

from __future__ import annotations

from sentrix.domain.assistant.evidence_ledger import EvidenceType
from sentrix.domain.assistant.intent import (
    canonical_question,
    plan_requirements,
    wants_delivery,
    wants_person,
    wants_photos,
)


def types_of(question: str) -> list[EvidenceType]:
    return [item.evidence_type for item in plan_requirements(question)]


def test_canonical_question_strips_whitespace():
    assert canonical_question("给 我 看 看 海边 的 照片") == "给我看看海边的照片"


def test_the_acceptance_question_asks_for_a_photo_set_and_a_delivery():
    """S3 的验收问句：规划必须同时产出「要有照片」和「要交付」两条需求。

    只产出前者的话，实测 8100 上的模型会检索一次、回一段文字、一张图都不交付
    （「我为您找到了两张在海边的照片…您是想查看哪张照片的具体内容呢？」）。
    """
    assert types_of("给我看看在海边的照片") == [
        EvidenceType.PHOTO_SET,
        EvidenceType.DELIVERED_PHOTO,
    ]


def test_a_plain_question_only_needs_the_photo_set():
    assert types_of("海边那张照片是哪年拍的") == [EvidenceType.PHOTO_SET]


def test_a_whitespace_mangled_question_still_plans_a_delivery():
    """真人在输入法里打出来的句子会带空格，规则先做规范化再匹配。"""
    assert types_of("给 我 看 看 在 海 边 的 照 片") == [
        EvidenceType.PHOTO_SET,
        EvidenceType.DELIVERED_PHOTO,
    ]


def test_a_blank_question_still_plans_exactly_one_requirement():
    """规划**不允许**零需求。

    1.0 的 ``goal_planner`` 允许（``goal_planner.py:228`` 的注释：「允许零需求……不强行凑
    一个 memory_asset 需求」），而 JIT 提示词会在零需求时直接告诉模型「可直接输出 final」
    （``jit_prompt.py:200``）——29% 的零工具回合有相当一部分是这么来的。
    """
    assert types_of("") == [EvidenceType.PHOTO_SET]
    assert types_of("你好呀") == [EvidenceType.PHOTO_SET]


def test_a_person_question_asks_for_the_profile():
    assert types_of("妈妈是谁") == [EvidenceType.PERSON_PROFILE]


def test_a_person_question_that_also_wants_photos_stays_a_photo_question():
    """「给我看看爸爸的照片」是照片问题，不是画像问题——人物只是检索词。"""
    assert EvidenceType.PERSON_PROFILE not in types_of("给我看看爸爸的照片")


def test_delivery_markers_are_recognised():
    assert wants_delivery("给我看看") is True
    assert wants_delivery("打开原图") is True
    assert wants_delivery("这张照片是哪年拍的") is False


def test_photo_and_person_markers_are_recognised():
    assert wants_photos("海边那张图") is True
    assert wants_person("爸爸是谁") is True
    assert wants_person("这张照片在哪拍的") is False


def test_requirement_ids_are_stable_and_unique():
    requirements = plan_requirements("给我看看在海边的照片")

    assert [item.id for item in requirements] == ["photo_set", "delivered_photo"]
    assert all(item.required for item in requirements)

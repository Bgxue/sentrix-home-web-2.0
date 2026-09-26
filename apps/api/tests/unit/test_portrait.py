"""人物画像的证据包 / 强制校验 / 确定性兜底（Task 86）。

移植 1.0 ``backend/person_portraits.py``。本文件里**有三条用例是钉缺陷的**，
不是钉 1.0 的行为——它们对应的三处 1.0 缺陷已在实施期实测确认，处置写在
``docs/refactor/s5-deviations.md``：

- 缺陷 1 ``_evidence_ref_valid`` 的白名单后门（``relationship`` / ``semantic_claim``
  / ``appearance`` 三种 kind 直接 ``return True``，编造的引用一律放行）；
- 缺陷 2 ``deterministic_portrait`` 通不过自己的校验（兜底模板实测 41 字 < 80）；
- （缺陷 3「校验失败完全静默」的落点在 ``application/identity/run_insights.py`` 的日志，
  不在本文件。）
"""

from __future__ import annotations

from datetime import datetime

from sentrix.domain.identity.models import (
    PORTRAIT_SOURCE_DETERMINISTIC,
    PORTRAIT_SOURCE_MODEL,
    EntityRecord,
    PersonMomentRecord,
    RelationshipRecord,
)
from sentrix.domain.identity.portrait import (
    MAX_HANZI,
    MAX_MOMENTS,
    MAX_THEMES,
    MIN_HANZI,
    MIN_THEMES,
    PER_EVENT,
    ROLE_OPTIONS,
    compile_portrait_evidence,
    deterministic_portrait,
    normalize_writer_output,
    validate_portrait,
)


def moment(
    moment_id: str,
    *,
    action: str = "抱着孩子",
    event: str = "event-1",
    confidence: float = 0.8,
    created: str = "2026-01-01T00:00:00+00:00",
    targets: tuple[str, ...] = (),
) -> PersonMomentRecord:
    return PersonMomentRecord(
        id=moment_id,
        scope_id="home-default",
        person_id="person-1",
        cluster_id="cluster-1",
        event_id=event,
        observation_id=f"obs-{moment_id}",
        asset_id=f"asset-{moment_id}",
        face_instance_id=f"face-{moment_id}",
        action_text=action,
        interaction_target_ids=targets,
        interaction_text="",
        participation_style="陪伴",
        visible_affect="",
        social_role_cues=(),
        narrative_note="",
        confidence=confidence,
        model_name="fake",
        prompt_version="person-moment-v1",
        status="active",
        created_at=datetime.fromisoformat(created),
        updated_at=None,
    )


def person(*, confirmed_name: str | None = "张三", role: str | None = "父亲") -> EntityRecord:
    return EntityRecord(
        id="person-1",
        scope_id="home-default",
        entity_type="person",
        canonical_name="张三",
        status="confirmed",
        family_role=role,
        summary="",
        confidence=1.0,
        identity_state="confirmed",
        role_state="confirmed",
        name_state="confirmed",
        created_at=None,
        updated_at=None,
    )


def relationship(relationship_id: str) -> RelationshipRecord:
    return RelationshipRecord(
        id=relationship_id,
        scope_id="home-default",
        subject_entity_id="person-1",
        predicate="父亲",
        object_entity_id="person-2",
        inverse_predicate="孩子",
        status="confirmed",
        confidence=1.0,
        evidence_ids=(),
        revision=1,
        created_at=None,
        updated_at=None,
        subject_name="张三",
        object_name="小四",
    )


# ---- 证据包 ----------------------------------------------------------------------


def test_constants_are_the_1_0_literals():
    """``max_moments=12`` / ``per_event=2`` 是证据包的**预算**，直接决定提示词多大。
    1.0 ``person_portraits.py:43`` 的默认值，一字不差。"""
    assert (MAX_MOMENTS, PER_EVENT) == (12, 2)


def test_pack_samples_per_event_then_dedupes_then_truncates():
    """抽样的三步顺序是 1.0 的原文：**先每事件取前 N 条**（按 confidence 降序），
    **再按 (action_text, targets) 去重**（留 confidence 高的），**最后按 created_at
    排序截断到 max_moments**。顺序换了结果就不同，所以用一组能区分顺序的输入钉住。"""
    moments = [
        moment("m1", action="做饭", event="e1", confidence=0.9, created="2026-01-01T00:00:00+00:00"),
        moment("m2", action="做饭", event="e1", confidence=0.5, created="2026-01-02T00:00:00+00:00"),
        moment("m3", action="做饭", event="e1", confidence=0.7, created="2026-01-03T00:00:00+00:00"),
        moment("m4", action="散步", event="e2", confidence=0.4, created="2026-01-04T00:00:00+00:00"),
    ]
    pack = compile_portrait_evidence(person(), moments, ())
    ids = [item["id"] for item in pack["moments"]]
    # e1 里按 confidence 取前 2 条 -> m1(0.9), m3(0.7)；e2 -> m4。
    # m1 与 m3 的 (action, targets) 相同，去重留 confidence 高的 m1 -> m1, m4。
    assert ids == ["m1", "m4"]


def test_pack_truncates_to_max_moments_after_sorting_by_created_at():
    """截断发生在**按 created_at 排序之后**——所以留下的是最早的那些，不是最像的那些。"""
    moments = [
        moment(
            f"m{index:02d}",
            action=f"动作{index}",
            event=f"e{index:02d}",
            created=f"2026-01-{index:02d}T00:00:00+00:00",
        )
        for index in range(1, 20)
    ]
    pack = compile_portrait_evidence(person(), moments, ())
    assert len(pack["moments"]) == MAX_MOMENTS
    assert [item["id"] for item in pack["moments"]] == [f"m{index:02d}" for index in range(1, 13)]


def test_pack_recurring_patterns_are_the_top_three_actions():
    """高频动作 ``most_common(3)``，且 ``recurring_patterns`` 与 ``appearance_themes``
    是**同一份列表**（1.0 两个键指向同一个对象）。每个主题最多带 2 条引用。"""
    moments = [
        moment("m1", action="做饭", targets=("p2",)),
        moment("m2", action="做饭", event="e2", targets=("p3",)),
        moment("m3", action="做饭", event="e3", targets=("p4",)),
        moment("m4", action="散步", event="e4"),
        moment("m5", action="聊天", event="e5"),
        moment("m6", action="买菜", event="e6"),
    ]
    pack = compile_portrait_evidence(person(), moments, ())
    titles = [theme["title"] for theme in pack["recurring_patterns"]]
    assert titles == ["反复做饭", "反复散步", "反复聊天"]
    assert pack["appearance_themes"] == pack["recurring_patterns"]
    assert len(pack["recurring_patterns"][0]["evidence_refs"]) == 2


def test_pack_person_block_uses_confirmed_values_only():
    """``confirmed_role`` 只在 ``role_state == 'confirmed'`` 时给，``confirmed_name``
    只在 ``status == 'confirmed'`` 时给——未确认的名字/角色不能进证据包，否则模型会
    把「假设」当成「事实」写进画像。"""
    unconfirmed = EntityRecord(
        id="person-1",
        scope_id="home-default",
        entity_type="person",
        canonical_name="待确认人物簇 · cluster-1",
        status="pending",
        family_role=None,
        summary="",
        confidence=0.0,
        identity_state="clustered",
        role_state="unknown",
        name_state="anonymous",
        created_at=None,
        updated_at=None,
    )
    pack = compile_portrait_evidence(unconfirmed, (), ())
    assert pack["person"]["confirmed_name"] is None
    assert pack["person"]["confirmed_role"] is None
    assert pack["person"]["display_name"] == "待确认人物簇 · cluster-1"


def test_pack_has_no_suggested_relationships_key():
    """裁决① 的连带：``infer_graph`` 不移植，而它是 ``relationship_hypotheses`` 唯一的
    写入者，0006 也没建那张表。所以 1.0 证据包里的 ``suggested_relationships`` 在 2.0
    恒为空——留一个永远是 ``[]`` 的键会让读提示词的人以为那条链路是通的。"""
    pack = compile_portrait_evidence(person(), (), (relationship("r1"),))
    assert "suggested_relationships" not in pack
    assert [item["id"] for item in pack["confirmed_relationships"]] == ["r1"]


def test_pack_empty_collections_are_present_and_empty():
    """``time_changes`` / ``contradictions`` / ``unknowns`` 在 1.0 就是恒空的占位。
    它们**保留**：提示词里写了这几层，缺键会让模型自由发挥。"""
    pack = compile_portrait_evidence(person(), (), ())
    assert pack["time_changes"] == []
    assert pack["contradictions"] == []
    assert pack["unknowns"] == []


# ---- 缺陷 1：证据引用的白名单后门 --------------------------------------------------


def test_evidence_ref_must_point_at_something_real():
    """**缺陷 1 的修复。** 1.0 ``person_portraits.py:124-125`` 对 ``relationship`` /
    ``semantic_claim`` / ``appearance`` 三种 kind 直接 ``return True``，于是模型编造的
    引用一律放行——画像可以挂着一堆不存在的证据通过校验。

    2.0：``person_moment`` 与 ``relationship`` 都必须在包里查得到 id；
    ``semantic_claim`` / ``appearance`` 在 2.0 的证据包里**没有对应的数据**
    （语义断言属 S6，外观证据是另一条链路且不进画像包），所以一律不合法。
    """
    pack = compile_portrait_evidence(person(), (moment("m1"),), (relationship("r1"),))
    valid, errors = validate_portrait(
        pack,
        {
            "portrait_text": "从照片看，" + "他常把大家聚在一起照顾孩子张罗饭桌。" * 8,
            "themes": [
                {"title": "A", "summary": "a", "evidence_refs": [{"kind": "person_moment", "id": "m1"}]},
                {"title": "B", "summary": "b", "evidence_refs": [{"kind": "relationship", "id": "r1"}]},
            ],
        },
        source=PORTRAIT_SOURCE_MODEL,
    )
    assert (valid, errors) == (True, [])

    for bad in (
        {"kind": "relationship", "id": "根本不存在的id"},
        {"kind": "semantic_claim", "id": "编的"},
        {"kind": "appearance", "id": "编的"},
        {"kind": "bogus_kind", "id": "m1"},
        {"kind": "person_moment"},
        {"kind": "relationship"},
    ):
        invalid, why = validate_portrait(
            pack,
            {
                "portrait_text": "从照片看，" + "他常把大家聚在一起照顾孩子张罗饭桌。" * 8,
                "themes": [
                    {"title": "A", "summary": "a", "evidence_refs": [bad]},
                    {"title": "B", "summary": "b", "evidence_refs": [{"kind": "person_moment", "id": "m1"}]},
                ],
            },
            source=PORTRAIT_SOURCE_MODEL,
        )
        assert not invalid, f"{bad} 本该被拒"
        assert any("invalid evidence ref" in item for item in why)


# ---- 缺陷 2：兜底画像通不过自己的校验 ----------------------------------------------


def test_deterministic_portrait_passes_its_own_validation():
    """**缺陷 2 的修复。** 1.0 的兜底模板实测 41 字，低于它自己那条 80 字下限，
    于是三级降级的最后一格（writer -> repair -> 确定性兜底）**永远不会成功落库**——
    降级链的尽头是个死格。

    修法是让校验知道**来源**：长度是给模型输出定的约束，兜底文案的长度由模板唯一
    决定，拿同一条去卡它是把两个东西塞进一条校验。
    """
    pack = compile_portrait_evidence(
        person(),
        (moment("m1", action="陪爷爷散步"), moment("m2", action="抱孩子", event="e2")),
        (),
    )
    portrait = deterministic_portrait(pack)
    valid, errors = validate_portrait(pack, portrait, source=PORTRAIT_SOURCE_DETERMINISTIC)
    assert (valid, errors) == (True, [])


def test_deterministic_portrait_with_no_evidence_also_validates():
    """证据全空时兜底返回 ``themes: []``。主题数量同样是**模板决定的**（有几个不重复
    的动作就有几个主题），所以确定性来源一并豁免。文本自己说明了证据不足——
    这正是兜底该说的话，不是一条该被拒的画像。"""
    pack = compile_portrait_evidence(person(), (), ())
    portrait = deterministic_portrait(pack)
    valid, errors = validate_portrait(pack, portrait, source=PORTRAIT_SOURCE_DETERMINISTIC)
    assert (valid, errors) == (True, [])
    assert "证据不足" in portrait["portrait_text"]


def test_deterministic_exemption_does_not_leak_to_model_output():
    """豁免只对确定性来源成立。同一段短文案**以模型的名义**送进来必须被拒——
    否则这条分支就成了「模型可以随便写短」的后门，那正是 80 字下限要防的。"""
    pack = compile_portrait_evidence(person(), (moment("m1"),), ())
    portrait = deterministic_portrait(pack)
    valid, errors = validate_portrait(pack, portrait, source=PORTRAIT_SOURCE_MODEL)
    assert not valid
    assert any("too short" in item for item in errors)


def test_deterministic_exemption_still_applies_the_other_rules():
    """豁免的**只有**长度与主题条数。敏感词、引用真实性、未确认角色的模糊措辞
    在确定性来源上照样成立——兜底文案也要守这些线。"""
    pack = compile_portrait_evidence(person(), (moment("m1", action="生病住院"),), ())
    portrait = deterministic_portrait(pack)
    valid, errors = validate_portrait(pack, portrait, source=PORTRAIT_SOURCE_DETERMINISTIC)
    assert not valid
    assert any("sensitive" in item for item in errors)


# ---- 校验的其余各条 ----------------------------------------------------------------


def test_model_portrait_must_be_80_to_500_hanzi():
    pack = compile_portrait_evidence(person(), (moment("m1"),), ())
    refs = [{"kind": "person_moment", "id": "m1"}]
    themes = [
        {"title": "A", "summary": "a", "evidence_refs": refs},
        {"title": "B", "summary": "b", "evidence_refs": refs},
    ]
    short = validate_portrait(
        pack, {"portrait_text": "太短了", "themes": themes}, source=PORTRAIT_SOURCE_MODEL
    )
    assert not short[0] and any("too short" in item for item in short[1])
    long_text = "从照片看，" + "照" * 600
    over = validate_portrait(
        pack, {"portrait_text": long_text, "themes": themes}, source=PORTRAIT_SOURCE_MODEL
    )
    assert not over[0] and any("too long" in item for item in over[1])


def test_model_portrait_themes_must_be_two_to_six_and_carry_refs():
    pack = compile_portrait_evidence(person(), (moment("m1"),), ())
    text = "从照片看，" + "他常把大家聚在一起照顾孩子张罗饭桌。" * 8
    refs = [{"kind": "person_moment", "id": "m1"}]
    one_theme = validate_portrait(
        pack,
        {"portrait_text": text, "themes": [{"title": "A", "summary": "a", "evidence_refs": refs}]},
        source=PORTRAIT_SOURCE_MODEL,
    )
    assert not one_theme[0] and any("themes must be 2-6" in item for item in one_theme[1])
    no_refs = validate_portrait(
        pack,
        {
            "portrait_text": text,
            "themes": [
                {"title": "A", "summary": "a", "evidence_refs": []},
                {"title": "B", "summary": "b", "evidence_refs": refs},
            ],
        },
        source=PORTRAIT_SOURCE_MODEL,
    )
    assert not no_refs[0] and any("missing evidence_refs" in item for item in no_refs[1])


def test_unconfirmed_role_requires_hedging_language():
    """``person.confirmed_role`` 为空时，文本里出现角色词就必须带模糊措辞
    （``似乎/可能/从照片看/看起来/像是``）——这是「不许把推断写成事实」那条产品规则的
    机械判据。角色词表来自 1.0 ``person_graph.py:29`` 的 ``ROLE_OPTIONS``。"""
    assert "父亲" in ROLE_OPTIONS and "无法判断" in ROLE_OPTIONS
    unconfirmed = EntityRecord(
        id="person-1",
        scope_id="home-default",
        entity_type="person",
        canonical_name="张三",
        status="confirmed",
        family_role=None,
        summary="",
        confidence=1.0,
        identity_state="confirmed",
        role_state="unknown",
        name_state="confirmed",
        created_at=None,
        updated_at=None,
    )
    pack = compile_portrait_evidence(unconfirmed, (moment("m1"),), ())
    refs = [{"kind": "person_moment", "id": "m1"}]
    themes = [
        {"title": "A", "summary": "a", "evidence_refs": refs},
        {"title": "B", "summary": "b", "evidence_refs": refs},
    ]
    bare = validate_portrait(
        pack,
        {
            "portrait_text": "这个家庭里，" + "他是家里的父亲，常陪孩子，把大家聚在一起。" * 8,
            "themes": themes,
        },
        source=PORTRAIT_SOURCE_MODEL,
    )
    assert not bare[0] and any("without hedging" in item for item in bare[1])
    hedged = validate_portrait(
        pack,
        {
            "portrait_text": "从照片看，"
            + "他似乎像是这个家里的父亲，常陪孩子，把大家聚在一起，照顾孩子张罗饭桌。" * 8,
            "themes": themes,
        },
        source=PORTRAIT_SOURCE_MODEL,
    )
    assert hedged[0], hedged[1]


def test_normalize_writer_output_drops_refs_without_an_id():
    """``normalize_writer_output`` 把模型的自由输出收成受控形状：引用**没有 id 就丢掉**
    （1.0 ``person_portraits.py:191``），``kind`` 缺省补 ``person_moment``。"""
    parsed = {
        "portrait_text": " 正文 ",
        "themes": [
            {"title": " A ", "summary": " s ", "evidence_refs": [{"id": "m1"}, {"kind": "x"}, "不是字典"]},
            "不是字典",
        ],
    }
    normalized = normalize_writer_output(parsed)
    assert normalized["portrait_text"] == "正文"
    assert normalized["themes"][0]["evidence_refs"] == [{"kind": "person_moment", "id": "m1"}]
    # 非字典主题被**跳过**。1.0 在这一格是 `theme.get(...)` -> `AttributeError`
    # （实测），异常会一路冒到编排层把整次生成判失败。2.0 加了一条
    # `isinstance(theme, dict)` 守卫：少一条主题比整次生成失败好，而且少的那条
    # 会由「themes 必须 2-6 条」这条校验接住（条数不够就不过）。
    # 这是**偏离 1.0** 的一处加严，登记在 s5-deviations.md。
    assert len(normalized["themes"]) == 1


def test_normalize_writer_output_survives_a_non_dict_payload():
    """模型偶尔回一个 JSON 数组或裸字符串。``parsed.get`` 会炸，所以 1.0 用了
    ``isinstance(parsed, dict)`` —— 照搬，否则这条路径是「模型一抽风就 500」。"""
    assert normalize_writer_output([]) == {"portrait_text": "", "themes": []}


def _themes(count: int, refs: list[dict[str, str]]) -> list[dict[str, object]]:
    return [{"title": f"T{index}", "summary": "s", "evidence_refs": list(refs)} for index in range(count)]


def test_hanzi_bounds_are_exact_not_approximate():
    """**边界必须精确落在 80 / 500 上。**

    第一版这条用例拿「太短了」（3 字）与 601 字去卡，结果是：把 ``MIN_HANZI`` 从
    80 改成 70、``MAX_HANZI`` 从 500 改成 400，**两条变异都不变红**——因为 3 仍然小于
    70、601 仍然大于 400，用例只断言了「会报错」，从没走到边界上。

    这与 ``s5-deviations.md`` 第 60 条是同一个形状（那次是 IoU 阈值 1.0 配一对 IoU 0.68
    的框）。修法一样：把输入**精确**落在边界与边界外一格。
    """
    pack = compile_portrait_evidence(person(), (moment("m1"),), ())
    refs = [{"kind": "person_moment", "id": "m1"}]
    themes = _themes(2, refs)

    def verdict(hanzi: int) -> tuple[bool, list[str]]:
        return validate_portrait(
            pack,
            {"portrait_text": "照" * hanzi, "themes": themes},
            source=PORTRAIT_SOURCE_MODEL,
        )

    # **必须写字面量，不能从实现里读回常量。** 第一版写的是 `verdict(MIN_HANZI)` /
    # `verdict(MIN_HANZI - 1)`，于是把 MIN_HANZI 改成 70 时，**用例跟着一起移**：
    # 断言变成 `verdict(70)` 通过 / `verdict(69)` 报 "69 < 70"，自洽地全绿。
    # 从实现读回来的常量当不了边界——它和被测对象是同一次变异的产物。
    assert (MIN_HANZI, MAX_HANZI) == (80, 500)
    assert verdict(80) == (True, [])
    assert verdict(79)[1] == ["portrait too short: 79 hanzi < 80"]
    assert verdict(500) == (True, [])
    assert verdict(501)[1] == ["portrait too long: 501 hanzi > 500"]


def test_theme_count_bounds_are_exact():
    """主题条数的上界同样要走到。下界由「只有一个主题」那条用例钉住，
    上界之前没人管——把 ``MAX_THEMES`` 从 6 改成 7 不会变红。"""
    pack = compile_portrait_evidence(person(), (moment("m1"),), ())
    refs = [{"kind": "person_moment", "id": "m1"}]
    text = "照" * MIN_HANZI
    assert (MIN_THEMES, MAX_THEMES) == (2, 6)
    assert validate_portrait(
        pack, {"portrait_text": text, "themes": _themes(6, refs)}, source=PORTRAIT_SOURCE_MODEL
    ) == (True, [])
    assert validate_portrait(
        pack, {"portrait_text": text, "themes": _themes(7, refs)}, source=PORTRAIT_SOURCE_MODEL
    ) == (False, ["themes must be 2-6, got 7"])


def test_undecided_roles_are_not_role_mentions():
    """``本人`` 与 ``无法判断`` **不算**角色断言：前者是相册主人的自称，后者是模型在说
    它不知道。未确认角色的人，文本里出现这两个词不该触发「必须带模糊措辞」那条。

    没有这条用例时，把 ``if role in _UNDECIDED: continue`` 改成 ``if False:`` 不会变红
    ——因为其余用例的文本里恰好都没出现这两个词。
    """
    unconfirmed = EntityRecord(
        id="person-1",
        scope_id="home-default",
        entity_type="person",
        canonical_name="张三",
        status="confirmed",
        family_role=None,
        summary="",
        confidence=1.0,
        identity_state="confirmed",
        role_state="unknown",
        name_state="confirmed",
        created_at=None,
        updated_at=None,
    )
    pack = compile_portrait_evidence(unconfirmed, (moment("m1"),), ())
    refs = [{"kind": "person_moment", "id": "m1"}]
    # 文本里有「无法判断」与「本人」，但**没有**任何真正的角色词，也没有模糊措辞。
    text = "照" * 60 + "无法判断本人" + "照" * 20
    assert validate_portrait(
        pack, {"portrait_text": text, "themes": _themes(2, refs)}, source=PORTRAIT_SOURCE_MODEL
    ) == (True, [])


def test_confirmed_role_does_not_require_hedging():
    """角色**已确认**时，文本直接写这个角色不需要模糊措辞——那是事实，不是推断。
    没有这条用例时，把 ``not person.get("confirmed_role") and`` 整段删掉不会变红：
    其余用例里带角色词的文本恰好都含模糊措辞。"""
    pack = compile_portrait_evidence(person(role="父亲"), (moment("m1"),), ())
    refs = [{"kind": "person_moment", "id": "m1"}]
    text = "照" * 60 + "他是家里的父亲" + "照" * 20
    assert validate_portrait(
        pack, {"portrait_text": text, "themes": _themes(2, refs)}, source=PORTRAIT_SOURCE_MODEL
    ) == (True, [])

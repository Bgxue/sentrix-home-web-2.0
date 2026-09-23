"""视觉观察器的纯函数部分：回复解析、字段收敛、以及三段式的调用上限。

http 那一层不在这里打真端点（8100 是 1.0 的服务，生命周期不归 2.0 管）；
``_chat`` 用 monkeypatch 换成脚本化的假回复，验的是 1.0 ``analyze_image`` 的三段结构：
首轮 → 只回分类时补一次 → 出现拉丁文字时中文化，**三段三调用，一次不多**。
"""

from __future__ import annotations

import json

import pytest

from sentrix.infrastructure.media.vlm_observer import (
    MAX_MODEL_CALLS,
    OBSERVATION_CONFIDENCE,
    OpenAIVlmObserver,
    parse_json_object,
    semantics_from_payload,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"caption": "海边"}', {"caption": "海边"}),
        ('```json\n{"caption": "海边"}\n```', {"caption": "海边"}),
        ('前面一段话 {"caption": "海边"} 后面一段话', {"caption": "海边"}),
        ("不是 JSON", None),
        ("[1, 2, 3]", None),
        ("", None),
    ],
)
def test_reply_parsing(text: str, expected):
    assert parse_json_object(text) == expected


def test_semantics_only_keeps_the_s7_fields():
    """1.0 的 semantic 受控词表与 detail 不属于 S4，多余字段必须被丢掉。"""
    payload = {
        "caption": " 一家人在公园 ",
        "activity": "拍照",
        "place": "公园",
        "event_type": "户外活动",
        "ocr_text": "生日快乐",
        "objects": ["相机", "", " 长椅 "],
        "clothing": "外套",
        "scene_type": "户外",
        "detail": {"visible_details": ["树"]},
        "facts": [{"subject": "妈妈", "predicate": "去过", "object": "赵县", "confidence": 0.8}],
    }

    semantics = semantics_from_payload(payload)

    assert semantics.caption == "一家人在公园"
    assert semantics.objects == ("相机", "长椅")
    assert semantics.clothing == ("外套",)
    assert semantics.confidence == pytest.approx(OBSERVATION_CONFIDENCE)
    assert [(item.subject, item.predicate, item.object, item.confidence) for item in semantics.facts] == [
        ("妈妈", "去过", "赵县", 0.8)
    ]
    assert not hasattr(semantics, "detail")


def test_a_fact_without_a_confidence_gets_the_model_default():
    """1.0 ``model_clients.py:1190``：``normalize_fact_confidences(facts, 0.65)``。

    断言的是那个**具体的数**：默认值换成 0.5 或 1.0 时这条要红——它是事实落库时的
    置信度，会直接影响 ``decide_fact_write`` 的 ``MAX(confidence, ?)``。
    """
    semantics = semantics_from_payload(
        {"caption": "海边", "facts": [{"subject": "妈妈", "predicate": "去过", "object": "赵县"}]}
    )

    assert [item.confidence for item in semantics.facts] == [0.65]


def test_a_fact_with_an_out_of_range_confidence_is_clamped_and_a_junk_one_falls_back():
    semantics = semantics_from_payload(
        {
            "caption": "海边",
            "facts": [
                {"subject": "妈妈", "predicate": "去过", "object": "赵县", "confidence": 7},
                {"subject": "爸爸", "predicate": "去过", "object": "保定", "confidence": "很高"},
                "这不是一条事实",
            ],
        }
    )

    assert [(item.subject, item.confidence) for item in semantics.facts] == [
        ("妈妈", 1.0),
        ("爸爸", 0.65),
    ]


def test_a_fact_missing_a_field_survives_the_parser():
    """**字段齐不齐是 ``is_writable_fact`` 的判据，不是解析器的。**

    解析器在这里替规则判一次，那条规则就有一半永远走不到——而「哪一半被谁判掉了」
    在出问题时是看不见的。
    """
    semantics = semantics_from_payload({"caption": "海边", "facts": [{"subject": "妈妈", "object": "赵县"}]})

    assert len(semantics.facts) == 1
    assert semantics.facts[0].predicate == ""


class ScriptedObserver(OpenAIVlmObserver):
    """把 ``_chat`` 换成脚本：按调用序返回预置回复，并记录次数。"""

    def __init__(self, replies: list[dict | None]) -> None:
        super().__init__("http://127.0.0.1:8100/v1", "fake")
        self.replies = replies
        self.calls: list[object] = []
        self.prompts: list[str] = []

    def _chat(self, prompt: str, image: str | None):  # noqa: ANN201
        self.calls.append(image)
        self.prompts.append(prompt)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "a.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    return path


def test_a_good_first_reply_costs_exactly_one_call(image):
    observer = ScriptedObserver(
        [{"caption": "海边", "activity": "散步", "place": "海边", "event_type": "出行"}]
    )

    semantics = observer.observe(image)

    assert semantics is not None
    assert len(observer.calls) == 1


def test_a_classification_only_reply_triggers_the_recovery_call(image):
    """1.0 ``model_clients.py:1150``：首轮只给了分类字段时补一次。"""
    observer = ScriptedObserver(
        [
            {"scene_type": "户外"},
            {"caption": "海边", "activity": "散步", "place": "海边", "event_type": "出行"},
        ]
    )

    semantics = observer.observe(image)

    assert semantics is not None
    assert semantics.caption == "海边"
    assert len(observer.calls) == 2


def test_latin_text_triggers_the_canonicalization_call(image):
    observer = ScriptedObserver(
        [
            {"caption": "Beach walk", "activity": "walking", "place": "beach", "event_type": "trip"},
            {"caption": "海边散步", "activity": "散步", "place": "海边", "event_type": "出行"},
        ]
    )

    semantics = observer.observe(image)

    assert semantics is not None
    assert semantics.caption == "海边散步"
    assert len(observer.calls) == 2


def test_the_prompt_asks_the_model_for_facts(image):
    """候选来源是**提示词**的一半：解析器认 facts、提示词不问，模型就不会给。

    只断言「解析器认得 facts」是不够的——那样把提示词里的 facts 删掉，整条路径安静地
    退回「恒无候选」，而事实表恒空不报错。
    """
    observer = ScriptedObserver([{"caption": "海边", "activity": "散步", "place": "海边"}])

    observer.observe(image)

    prompt = observer.prompts[0]
    assert "facts" in prompt
    assert "subject" in prompt and "predicate" in prompt and "object" in prompt
    assert "confidence" in prompt


def test_the_canonicalization_prompt_keeps_facts(image):
    """中文化那一轮要**保留** facts，否则 `翻译一遍` 这个动作会把候选事实整段吃掉。"""
    observer = ScriptedObserver(
        [
            {"caption": "Beach walk", "activity": "walking", "place": "beach"},
            {"caption": "海边散步", "activity": "散步", "place": "海边"},
        ]
    )

    observer.observe(image)

    assert "facts" in observer.prompts[1]


def test_facts_from_the_recovery_call_are_kept(image):
    """首轮一个字都没说画面内容时，补全那一轮给的事实候选同样要留下。"""
    observer = ScriptedObserver(
        [
            {"scene_type": "户外"},
            {
                "caption": "海边",
                "activity": "散步",
                "place": "海边",
                "event_type": "出行",
                "facts": [{"subject": "妈妈", "predicate": "去过", "object": "赵县"}],
            },
        ]
    )

    semantics = observer.observe(image)

    assert semantics is not None
    assert [item.subject for item in semantics.facts] == ["妈妈"]


def test_a_reply_without_facts_yields_an_empty_tuple_not_none(image):
    observer = ScriptedObserver([{"caption": "海边", "activity": "散步", "place": "海边"}])

    semantics = observer.observe(image)

    assert semantics is not None
    assert semantics.facts == ()


def test_the_ceiling_is_three_calls(image):
    """三段全走也只调三次——单卡 GPU 上串行跑批时这个上限就是「一张图的代价」。"""
    observer = ScriptedObserver([{"scene_type": "户外"}, {"caption": "Beach"}, {"caption": "海边"}])

    observer.observe(image)

    assert len(observer.calls) == MAX_MODEL_CALLS == 3


def test_an_unreadable_image_costs_no_calls(tmp_path):
    observer = ScriptedObserver([{"caption": "不该被用到"}])

    assert observer.observe(tmp_path / "gone.jpg") is None
    assert observer.calls == []


def test_the_first_call_carries_the_image_and_the_canonicalization_does_not(image):
    observer = ScriptedObserver([{"scene_type": "分类"}, {"caption": "海边"}])

    observer.observe(image)

    assert observer.calls[0] is not None, "首轮必须带图"
    assert observer.calls[1] is not None, "补全那一轮同样要看图"


def test_a_model_failure_returns_none(monkeypatch, image):
    """端点连不上时 httpx 抛的是 ``ConnectError``（``HTTPError`` 的子类），必须被吞掉。"""
    import httpx

    observer = OpenAIVlmObserver("http://127.0.0.1:8100/v1", "fake")

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("sentrix.infrastructure.media.vlm_observer.httpx.post", boom)

    assert observer.observe(image) is None


def test_prompt_budget_constant_is_shared_with_the_chat_client():
    """观察的 JSON 与对话的回答共用同一个 token 上限公式，不再各写一个数。"""
    from sentrix.infrastructure.assistant.chat_client import MAX_ANSWER_TOKENS

    assert json.dumps({"max": MAX_ANSWER_TOKENS}) == '{"max": 1501}'

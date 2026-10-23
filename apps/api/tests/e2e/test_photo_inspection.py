"""端到端对照：**同一个端点、同一张图、两个视觉入口给出两种形状的答案**。

这一层打 **8100 上真的视觉端点**（1.0 的服务，进程生命周期不归 2.0 管），判据与
``test_assistant_turn.py`` 同一条纪律：**不判「模型说得好不好」，判结构性事实**。

## 钉住的三条

1. **这个端口的答复形状是它自己的**：``observation`` / ``certainty`` / ``target_face_id``。
   与 ``VlmObserver.observe`` 在**同端点、同图**上给出的七字段 ``ObservationSemantics``
   不是一个东西——后者是导入期落库的形状，它连问题都不接。这就是「先落 inspector 端口」
   这件事的对照臂（``test_the_two_vision_entry_points_on_the_same_photo...``）。
2. **图真的到了模型那里**：现画的图上一个红圆、一个蓝方，答出来的话里必须提到「圆」。
   这条判的是链路（图片编码 → 请求 → 解析落到 ``observation``），不是模型的水平。
3. **``certainty`` 落在提示词写死的那两档里**（1.0 ``:2255`` 的默认值是 ``supported``）。
   真模型也一样，不许溢出。

## 「问题真的到了图那里」这条判据为什么不在这里（一次假绿的记录）

第一版断言的是「两个不同的问题得到两句不同的话」——定点变异把问题在适配器里**写死**之后
它照样绿。两个原因叠在一起：

1. 8100 上的 llama-server 默认采样，**同一个提示词发两次回来的措辞本来就不一样**，
   所以「两句不同」量的是采样噪声；
2. 改成判内容（问颜色必须提到「红」）之后仍然绿——**这个 4B 模型会答出问题范围之外的
   东西**。实测：问「这张图里画的是什么形状？只回答形状名称。」，它回
   「这张照片里画的是一个红色圆和一个蓝色正方形」——形状和颜色一起说了。于是无论问哪一问，
   「红」都在答案里，判据依然没有区分力。

**模型答得比问的多，这件事本身就是 T124 的输入**：``read_photo_text`` 要的是「读出真的
写着什么」，而一个自发补充内容的模型正是那条路的风险所在（§4 Task 124 的那张对照表：
「会不会读出图上没有的字」）。这份实测证据记在这里，供 T124 定工具描述时引用。

**判据于是放在能判的地方**：问题有没有进提示词，由 ``tests/unit/test_photo_inspector.py``
的两个用例守（那里能逐字节断言发出去的提示词），定点变异在那里是红的。

## 环境守卫与实现坏掉是两件事

守卫直接探端点（不看本仓库的代码），所以适配器坏掉时它是绿的、下面的断言是红的——
不会替一个真 bug 打掩护（S5c T87 第 141 条那次教训）。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw

from sentrix.config import Settings
from sentrix.domain.media.inspection import CERTAINTY_SUPPORTED, CERTAINTY_UNCERTAIN
from sentrix.infrastructure.assistant.model_endpoint import ServedModel
from sentrix.infrastructure.media.photo_inspector import OpenAIPhotoInspector
from sentrix.infrastructure.media.vlm_observer import OpenAIVlmObserver

# Requires real album data, local weights or a running model; run via make test-real.
pytestmark = pytest.mark.real_environment

# 图上真有的东西：一个红圆、一个蓝方。问题与期望都从这里来，不另立一份标注。
SHAPE_QUESTION = "这张图里画的是什么形状？只回答形状名称。"
SHAPE_ANSWER_MUST_MENTION = "圆"


@pytest.fixture(scope="module", autouse=True)
def requires_the_real_endpoint() -> None:
    """8100 上的视觉端点得在跑。**探的是端点本身**，不是本仓库的适配器。"""
    base_url = Settings().vlm_base_url
    try:
        # 名字问端点要，与生产走同一条路（ServedModel）——写死名字的话，换过后端之后
        # 这个守卫会自信地打一个 404，然后把整层判成「环境不可用」跳过。
        model_name = ServedModel(base_url).current()
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": "好"}],
                "max_tokens": 4,
            },
            timeout=20.0,
        )
        response.raise_for_status()
    except Exception as error:  # noqa: BLE001 - 环境不可用时给出可执行的提示
        pytest.skip(f"8100（{base_url}）上的视觉端点不可用，inspector 走这个端点: {error}")


@pytest.fixture(scope="module")
def endpoints() -> tuple[OpenAIPhotoInspector, OpenAIVlmObserver]:
    settings = Settings()
    base_url = settings.vlm_base_url
    served = ServedModel(base_url)
    return (
        OpenAIPhotoInspector(base_url, served, timeout_s=settings.vlm_timeout_s),
        OpenAIVlmObserver(base_url, served, timeout_s=settings.vlm_timeout_s),
    )


@pytest.fixture
def inspected_photo(tmp_path: Path) -> Path:
    """一张**内容由构造决定**的图：白底、一个红圆、一个蓝方。

    用现画的图而不是相册里的真照片，是因为要断言的是「答出来的话与图上真有的东西一致」
    ——真照片没有可机读的标注（PhotoBench 那份 ``ocr-subset.jsonl`` 是
    ``review_status: machine_draft`` / ``human_verified: false``，拿它当判据等于让两份
    机器草稿互相放过）。合成图让这条判据是确定的。
    """
    path = tmp_path / "shapes.png"
    image = Image.new("RGB", (384, 384), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((40, 40, 200, 200), fill=(220, 20, 20))
    draw.rectangle((250, 60, 350, 200), fill=(20, 40, 220))
    image.save(path, format="PNG")
    return path


def test_a_real_inspection_returns_the_inspect_contract(
    inspected_photo: Path, endpoints: tuple[OpenAIPhotoInspector, OpenAIVlmObserver]
) -> None:
    """打真端点：回答解析成一个对象，``observation`` 非空且提到了图上真有的东西。

    「提到了圆」这一半是**证明图真的发过去了**——图片编码坏掉、发错文件、发了空串，
    答出来的话都不会提到图上的内容。定点变异（把发出去的图换成一张白图）能把它变红。
    """
    inspector, _ = endpoints

    result = inspector.inspect(str(inspected_photo), SHAPE_QUESTION)

    assert result is not None, "端点可用（守卫刚探过），这里 None 说明适配器自己坏了"
    observation = str(result.get("observation") or "").strip()
    assert observation, f"解析出来没有 observation：{result}"
    assert SHAPE_ANSWER_MUST_MENTION in observation, (
        f"答案里没有图上真有的「{SHAPE_ANSWER_MUST_MENTION}」——图可能没发对：{observation}"
    )


def test_certainty_stays_inside_the_two_levels_the_prompt_names(
    inspected_photo: Path, endpoints: tuple[OpenAIPhotoInspector, OpenAIVlmObserver]
) -> None:
    """提示词写死 ``supported|uncertain``——真模型也不许溢出这两档。

    ``supported`` 是 1.0 ``parsed.get("certainty") or "supported"`` 的默认值，所以
    「模型没给」与「模型说 supported」在这里是同一个结果（解析口径归 T123）。
    """
    inspector, _ = endpoints

    result = inspector.inspect(str(inspected_photo), SHAPE_QUESTION)

    assert result is not None
    assert result.get("certainty", CERTAINTY_SUPPORTED) in (CERTAINTY_SUPPORTED, CERTAINTY_UNCERTAIN)


def test_the_two_vision_entry_points_on_the_same_photo_answer_in_their_own_shapes(
    inspected_photo: Path, endpoints: tuple[OpenAIPhotoInspector, OpenAIVlmObserver]
) -> None:
    """**这就是这个端口存在的理由，在真端点上再证一次。**

    同一个端点、同一张图：

    - ``VlmObserver.observe`` 回的是 ``ObservationSemantics``——导入期要落库的**七个
      固定字段**，它连问题都不接（``observe`` 没有那个形参）；
    - ``PhotoInspector.inspect`` 回的是**问题驱动**的那一问的答复，形状是
      ``observation`` / ``certainty`` / ``target_face_id``。

    两者**都不是 None**，所以这条不是「一个能用一个不能用」，是「同一张图有两种问法，
    各自答各自的形状」——给 ``VlmObserver`` 加一个 ``question`` 形参长不出后者。
    """
    inspector, observer = endpoints

    asked = inspector.inspect(str(inspected_photo), SHAPE_QUESTION)
    observed = observer.observe(inspected_photo)

    assert asked is not None
    assert observed is not None, "观察器在这个端点上也该能看一眼这张图"
    assert "observation" in asked and "certainty" in asked
    # 两个形状的字段集**不相交**——这是「同一张图两种问法」在类型上的落点。
    observed_fields = {field.name for field in dataclasses.fields(observed)}
    assert observed_fields == {
        "caption",
        "place",
        "activity",
        "event_type",
        "ocr_text",
        "objects",
        "clothing",
        "facts",
        "confidence",
    }, f"观察器的字段集变了：{sorted(observed_fields)}"
    assert observed_fields & set(asked) == set(), "两个入口不该共用字段名"
    # 观察器真的在这张图上说了点什么——它走的是与 inspect 同一条 HTTP 路，
    # 所以这一条同时证明那条路对 PNG 也是通的。
    assert observed.caption.strip(), "观察器在这张图上一个字都没说"


def test_an_unreadable_photo_never_reaches_the_endpoint(
    tmp_path: Path, endpoints: tuple[OpenAIPhotoInspector, OpenAIVlmObserver]
) -> None:
    """读不了图就不是一次模型调用——真端点在场时也要守住这条（1.0 的「图片复核失败」）。"""
    inspector, _ = endpoints

    assert inspector.inspect(str(tmp_path / "missing.jpg"), SHAPE_QUESTION) is None

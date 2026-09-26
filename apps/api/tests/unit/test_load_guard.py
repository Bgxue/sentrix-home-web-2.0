"""加载失败的分流：**环境原因跳过，代码原因必须红**。

这条用例守的是**守卫本身**。没有它，``skip_or_fail_on_load_error`` 退化成
「一律跳过」也不会有任何用例变红——那正是它要防的东西，也正是本项目栽过十次的那个形状。

判断依据（2026-09-26 实测）：8100 的 vLLM 占住 24 GB 里的 22 GB 时，chinese-clip 在
``model.to(device)`` 处 OOM、RetinaFace 在建 CUDA provider 时 CUBLAS failure 3。
这些都是**这个部署跑不了**，不是代码坏了。而模型名写错、模块路径变了同样是
``available == False``——那必须红。
"""

from __future__ import annotations

import pytest
from conftest import ENVIRONMENTAL_LOAD_FAILURES, skip_or_fail_on_load_error

WHAT = "探针"

ENVIRONMENTAL = [
    "checkpoint missing: /home/asus/.cache/clip/clip_cn_vit-l-14.pt",
    "OutOfMemoryError: CUDA out of memory. Tried to allocate 20.00 MiB.",
    "onnxruntime.capi.onnxruntime_pybind11_state.Fail: CUBLAS failure 3: the resource allocation failed",
    "Failed to allocate memory for requested buffer of size 9437184 (bfc_arena.cc:360)",
    "No registered plugin EP device found for 'CUDAExecutionProvider' with device_id=0",
]

CODE_LEVEL = [
    "ValueError: unknown model name vit-l-99",
    "ModuleNotFoundError: No module named 'cn_clip'",
    "AttributeError: 'ChineseClipVisualEmbedder' object has no attribute 'embed_image'",
    "KeyError: 'image_resolution'",
]


@pytest.mark.parametrize("message", ENVIRONMENTAL)
def test_environmental_failures_skip(message: str) -> None:
    with pytest.raises(pytest.skip.Exception):
        skip_or_fail_on_load_error(message, what=WHAT)


@pytest.mark.parametrize("message", CODE_LEVEL)
def test_code_level_failures_do_not_skip(message: str) -> None:
    with pytest.raises(pytest.fail.Exception):
        skip_or_fail_on_load_error(message, what=WHAT)


def test_the_whitelist_is_a_whitelist_not_a_blacklist() -> None:
    """认不出的措辞一律**不跳过**。

    这条是「白名单」这三个字的字面验收：黑名单会随未知错因放行，白名单不会。
    """
    assert ENVIRONMENTAL_LOAD_FAILURES, "空名单等于一律 fail，这条用例的前提不成立"
    with pytest.raises(pytest.fail.Exception):
        skip_or_fail_on_load_error("some error nobody has seen before", what=WHAT)


def test_the_reason_names_the_cause() -> None:
    """skip 的说明文字必须带上原始错因——否则 CI 里只看到「跳过了」，看不出为什么。"""
    with pytest.raises(pytest.skip.Exception) as caught:
        skip_or_fail_on_load_error("OutOfMemoryError: CUDA out of memory", what=WHAT)
    assert "CUDA out of memory" in str(caught.value)

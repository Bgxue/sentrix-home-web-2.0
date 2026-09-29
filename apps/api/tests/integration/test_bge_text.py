"""``BgeM3TextEmbedder`` 的测试：正常路径 + 三条降级路径 + 熔断。

**重点不在「能拿到向量」，在「拿不到的时候会怎样」。** 文本嵌入是检索的增强通道，
不是前置条件；它失败时**必须降级成空表**，让上层按「这台机器没有文本能力」走。
抛出去会让每一次查询都挂在一个可选依赖上——而那个依赖是一个 HTTP sidecar，
它随时可能没起。

熔断那一条单独测：连续失败到阈值之后，后续调用**不再打网络**。
没有它，一个挂掉的 sidecar 会让每次查询都白等一个超时（10 秒 × 每条通道）。
"""

from __future__ import annotations

import httpx
import pytest

from sentrix.infrastructure.models.bge_text import (
    CIRCUIT_OPEN_S,
    DIMENSION,
    FAILURE_THRESHOLD,
    BgeM3TextEmbedder,
)

BASE = "http://127.0.0.1:8101"


def build(handler) -> tuple[BgeM3TextEmbedder, list[str]]:
    """返回 (适配器, 被请求过的路径表)——路径表用来断言「有没有真的打网络」。"""
    seen: list[str] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(wrapped))
    return BgeM3TextEmbedder(BASE, client=client), seen


def test_returns_the_vector(monkeypatch):
    embedder, seen = build(lambda request: httpx.Response(200, json={"vector": [0.5] * DIMENSION}))

    vector = embedder.embed_query("海边 蓝")

    assert len(vector) == DIMENSION
    assert seen == ["/embed"]
    assert embedder.load_error == ""


def test_empty_text_does_not_call_the_sidecar():
    embedder, seen = build(lambda request: httpx.Response(200, json={"vector": [1.0]}))

    assert embedder.embed_query("   ") == []
    assert seen == [], "空文本不该打网络——那是一次注定没用的往返"


def test_http_error_degrades_to_empty_vector():
    embedder, _ = build(lambda request: httpx.Response(500, json={"error": "boom"}))

    assert embedder.embed_query("海边") == []
    assert "HTTPStatusError" in embedder.load_error


def test_connection_error_degrades_to_empty_vector():
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sidecar 没起", request=request)

    embedder, _ = build(explode)

    assert embedder.embed_query("海边") == []
    assert "ConnectError" in embedder.load_error


def test_empty_vector_from_sidecar_counts_as_failure():
    """sidecar 回 200 但向量是空的——**这是失败，不是「这段文本没有语义」**。

    当成成功的话，熔断器永远不跳，而每一次查询都白跑一趟。
    """
    embedder, _ = build(lambda request: httpx.Response(200, json={"vector": []}))

    assert embedder.embed_query("海边") == []
    assert "空向量" in embedder.load_error


def test_circuit_opens_after_consecutive_failures():
    embedder, seen = build(lambda request: httpx.Response(500, json={}))

    for _ in range(FAILURE_THRESHOLD):
        embedder.embed_query("海边")
    calls_before = len(seen)
    assert calls_before == FAILURE_THRESHOLD

    # 断开期间：直接返回空，**不再打网络**
    assert embedder.embed_query("海边") == []
    assert len(seen) == calls_before, "熔断期间不该再打网络"


def test_success_resets_the_breaker():
    state = {"fail": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            return httpx.Response(500, json={})
        return httpx.Response(200, json={"vector": [0.5] * DIMENSION})

    embedder, seen = build(handler)
    for _ in range(FAILURE_THRESHOLD - 1):
        embedder.embed_query("海边")

    state["fail"] = False
    embedder.embed_query("海边")  # 这一次成功，计数器归零

    state["fail"] = True
    calls_before = len(seen)
    # 再连失败 4 次也不该断开（阈值是「连续」5 次）
    for _ in range(FAILURE_THRESHOLD - 1):
        embedder.embed_query("海边")
    assert len(seen) == calls_before + (FAILURE_THRESHOLD - 1)


def test_available_follows_health_endpoint():
    embedder, seen = build(lambda request: httpx.Response(200, json={"status": "ok"}))

    assert embedder.available is True
    assert seen == ["/health"]
    # 健康值有 TTL，第二次不该再打
    assert embedder.available is True
    assert seen == ["/health"]


def test_available_is_false_when_health_fails():
    embedder, _ = build(lambda request: httpx.Response(503, json={}))

    assert embedder.available is False


@pytest.mark.parametrize("seconds", [CIRCUIT_OPEN_S])
def test_circuit_constants_are_the_10s_window(seconds: float):
    """1.0 ``bge_text.py:47`` 的断开时长。改它是个决定，不是调参。"""
    assert seconds == 10.0

"""legacy 嵌入适配器：正常路径 + 降级 + 维度校验 + 熔断 + 健康探针。

重点仍是「拿不到的时候会怎样」：这两个侧车是可选依赖，失败必须返回空表让上层
标记 ``degraded``，不能把查询挂在一个 HTTP 超时上。
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from sentrix.domain.retrieval import scheme
from sentrix.infrastructure.models import legacy
from sentrix.infrastructure.models.legacy import (
    CIRCUIT_OPEN_S,
    FAILURE_THRESHOLD,
    BgeSidecarEmbedder,
    ChineseClipSidecarEmbedder,
    LegacyEmbeddingHealthProbe,
)

CLIP_BASE = "http://127.0.0.1:8103"
BGE_BASE = "http://127.0.0.1:8101"


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(wrapped)), seen


def clip_with(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[ChineseClipSidecarEmbedder, list[httpx.Request]]:
    client, seen = build(handler)
    return ChineseClipSidecarEmbedder(CLIP_BASE, client=client), seen


def bge_with(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[BgeSidecarEmbedder, list[httpx.Request]]:
    client, seen = build(handler)
    return BgeSidecarEmbedder(BGE_BASE, client=client), seen


def test_clip_shapes_and_endpoints():
    embedder, seen = clip_with(
        lambda request: httpx.Response(200, json={"vector": [0.1] * scheme.LEGACY_VISUAL_DIMENSION})
    )
    assert embedder.model_id == "chinese-clip-ViT-L-14"
    assert embedder.dimension == 768

    assert len(embedder.embed_image("/tmp/a.jpg")) == 768
    assert len(embedder.embed_query("毛绒睡衣")) == 768
    assert seen[0].url.path == "/embed_image"
    assert seen[1].url.path == "/embed_query"


def test_bge_uses_the_1_0_endpoint():
    embedder, seen = bge_with(
        lambda request: httpx.Response(200, json={"vector": [0.1] * scheme.LEGACY_TEXT_DIMENSION})
    )
    assert embedder.model_id == "BAAI/bge-m3"
    assert embedder.dimension == 1024
    assert len(embedder.embed_query("去年夏天去了哪里")) == 1024
    assert seen[0].url.path == "/embed"


def test_dimension_mismatch_is_a_failure_not_a_vector():
    """1.0 的 bge 是 1024、chinese-clip 是 768；放行错维度会静默毁掉检索。"""
    embedder, _ = clip_with(lambda request: httpx.Response(200, json={"vector": [0.1] * 1024}))
    assert embedder.embed_image("/tmp/a.jpg") == []
    assert "768" in embedder.load_error


def test_empty_input_does_not_hit_the_network_or_trip_the_breaker():
    embedder, seen = clip_with(lambda request: httpx.Response(500))
    assert embedder.embed_query("  ") == []
    assert seen == []


def test_failure_returns_empty_and_trips_the_breaker(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(legacy.time, "monotonic", clock.monotonic)
    embedder, seen = clip_with(lambda request: httpx.Response(500))

    for _ in range(FAILURE_THRESHOLD):
        assert embedder.embed_query("x") == []
    assert len(seen) == FAILURE_THRESHOLD

    # 熔断期内不再打网络。
    assert embedder.embed_query("x") == []
    assert len(seen) == FAILURE_THRESHOLD

    clock.advance(CIRCUIT_OPEN_S + 0.1)
    assert embedder.embed_query("x") == []
    assert len(seen) == FAILURE_THRESHOLD + 1


def test_health_probe_requires_both_halves():
    visual, _ = clip_with(lambda request: httpx.Response(200, json={"vector": [0.1] * 768}))
    text, _ = bge_with(lambda request: httpx.Response(200, json={"vector": [0.1] * 1024}))
    probe = LegacyEmbeddingHealthProbe(visual, text)
    assert probe.available is True
    assert probe.load_error == ""

    dead_text, _ = bge_with(lambda request: httpx.Response(503))
    probe = LegacyEmbeddingHealthProbe(visual, dead_text)
    assert probe.available is False
    assert "text:" in probe.load_error


def test_sidecar_health_shape_is_what_the_adapter_expects():
    """契约：侧车 /health 返回 2xx 即视为可用。"""
    embedder, seen = clip_with(lambda request: httpx.Response(200, json={"status": "ok"}))
    assert embedder.available is True
    assert seen[-1].url.path == "/health"

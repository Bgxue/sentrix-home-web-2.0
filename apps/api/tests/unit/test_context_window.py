"""``probe_context_tokens``：上下文窗口是**部署事实**，问端点要。

两条路各钉一条（vLLM 报在 ``/v1/models``、llama.cpp 报在 ``/props``），
外加「都读不到返回 ``None``」——那是调用方退回配置值的依据。

**这条为什么值得单独测**：那个窗口曾经是常量（6144），而部署改到 8192 之后代码没跟，
代价是 2026-10-10 那次 full 导入里家庭图谱推断崩了两次、三张 ``family_*`` 表一个字没写。
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from sentrix.infrastructure.assistant.model_endpoint import probe_context_tokens


class _Resp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def json(self) -> object:
        return self._payload


def _router(routes: Mapping[str, object]):
    """按 URL 后缀分派一个假的 ``httpx.get``；没配的路径当连不上。"""

    def fake_get(url: str, **_kwargs: object) -> _Resp:
        for suffix, payload in routes.items():
            if url.endswith(suffix):
                if isinstance(payload, Exception):
                    raise payload
                return _Resp(payload)
        raise httpx.ConnectError(f"no route for {url}")

    return fake_get


def test_probe_reads_max_model_len_from_a_vllm_endpoint(monkeypatch):
    """vLLM 的 ``max_model_len`` 本来就是**每请求**的上限，直接可用。"""
    monkeypatch.setattr(httpx, "get", _router({"/v1/models": {"data": [{"id": "m", "max_model_len": 8192}]}}))

    assert probe_context_tokens("http://host:8100/v1") == 8192


def test_probe_falls_back_to_llamacpp_props(monkeypatch):
    """llama.cpp 不报 ``max_model_len``，它报 ``/props`` 的 ``default_generation_settings.n_ctx``。

    **那已经是除过 ``--parallel`` 的单槽值**：2026-10-16 在 153 实测
    ``--ctx-size 32768 --parallel 4`` → ``n_ctx = 8192`` 且 ``total_slots = 4``。
    """
    monkeypatch.setattr(
        httpx,
        "get",
        _router(
            {
                "/v1/models": {"data": [{"id": "qwen35-q4km"}]},
                "/props": {"total_slots": 4, "default_generation_settings": {"n_ctx": 8192}},
            }
        ),
    )

    assert probe_context_tokens("http://host:8100/v1") == 8192


def test_probe_returns_none_when_nothing_reports_a_window(monkeypatch):
    """都读不到就返回 ``None``——调用方据此退回配置值，而不是把 0 当真。"""
    monkeypatch.setattr(httpx, "get", _router({"/v1/models": {"data": [{"id": "m"}]}}))

    assert probe_context_tokens("http://host:8100/v1") is None


def test_probe_survives_an_unreachable_endpoint(monkeypatch):
    """端点连不上不该抛——窗口读不到只是退回一个保守值，不是硬故障。"""
    monkeypatch.setattr(httpx, "get", _router({}))

    assert probe_context_tokens("http://host:8100/v1") is None

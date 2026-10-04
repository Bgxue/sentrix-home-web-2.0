"""``HttpxJsonClient`` 的用例：把 1.0 ``request_json`` / ``request_bytes`` 的行为钉住。

用 ``httpx.MockTransport`` 而不是真网络：状态码、错误体、超时都是从**这个** handler
摆出来的，用例不依赖任何服务的存活。
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from photobench.infrastructure.http.client import HttpxJsonClient

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> HttpxJsonClient:
    return HttpxJsonClient(client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True))


def test_get_returns_the_parsed_body():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["content_type"] = request.headers.get("content-type")
        seen["accepts"] = True
        return httpx.Response(200, json={"ok": True})

    assert _client(handler).request_json("http://sentrix/api/x") == {"ok": True}
    assert seen["method"] == "GET"
    assert seen["content_type"] == "application/json"


def test_post_sends_utf8_json_without_ascii_escaping():
    """1.0 ``:732`` 用的是 ``ensure_ascii=False``——中文原样进 body，不是 \\uXXXX。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={})

    _client(handler).request_json("http://sentrix/api/x", {"name": "相册"}, "POST")
    assert json.loads(seen["body"]) == {"name": "相册"}
    assert "相册".encode() in seen["body"]


def test_a_http_error_becomes_the_same_runtime_error_message_as_1_0():
    """1.0 ``:744-760``：非 2xx 转成 ``RuntimeError("HTTP {code} {reason}: {detail}")``。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "没有这个 scope"})

    with pytest.raises(RuntimeError) as excinfo:
        _client(handler).request_json("http://sentrix/api/x")
    message = str(excinfo.value)
    assert message.startswith("HTTP 404 ")
    assert message.endswith(": 没有这个 scope")


def test_a_non_json_error_body_is_used_verbatim():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="  boom  ")

    with pytest.raises(RuntimeError) as excinfo:
        _client(handler).request_json("http://sentrix/api/x")
    assert str(excinfo.value).endswith(": boom")


def test_an_error_body_falls_back_to_error_then_message_then_the_whole_object():
    cases = [
        ({"error": "e"}, "e"),
        ({"message": "m"}, "m"),
        ({"other": 1}, '{"other": 1}'),
    ]
    for payload, expected in cases:

        def handler(request: httpx.Request, payload: dict = payload) -> httpx.Response:
            return httpx.Response(400, json=payload)

        with pytest.raises(RuntimeError) as excinfo:
            _client(handler).request_json("http://sentrix/api/x")
        assert str(excinfo.value).endswith(f": {expected}")


def test_extra_headers_override_the_default_content_type():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("x-token")
        return httpx.Response(200, json={})

    _client(handler).request_json("http://sentrix/api/x", None, "GET", 5, {"x-token": "t"})
    assert seen["auth"] == "t"


def test_timeout_is_passed_through_per_call():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={})

    _client(handler).request_json("http://sentrix/api/x", None, "GET", 7)
    assert seen["timeout"]["read"] == 7


def test_request_bytes_returns_the_raw_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x00\x01JPEG")

    assert _client(handler).request_bytes("http://sentrix/api/assets/1/file") == b"\x00\x01JPEG"


def test_request_bytes_does_not_rewrite_the_error():
    """1.0 的 ``request_bytes``（``:799``）没有 except，``HTTPError`` 原样冒出。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).request_bytes("http://sentrix/api/assets/1/file")


def test_redirects_are_followed_like_urllib():
    """``urllib.request`` 默认跟随重定向，``httpx.Client`` 默认不跟；这里要跟。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/old"):
            return httpx.Response(302, headers={"location": "http://sentrix/api/new"})
        return httpx.Response(200, json={"followed": True})

    assert _client(handler).request_json("http://sentrix/api/old") == {"followed": True}

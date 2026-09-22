"""``GET /api/v1/assistant/result-sets/{id}/photo`` 的契约。

钉的是 1.0 ``app.py:2484`` 的四条判断与它们的**顺序**：结果集不存在 → 404、scope 不匹配
→ 403、handle 不在结果集里 → 404、结果集为空 → 404。搬错一条、或把顺序调换（先查
scope 再查存在性），返回码就变了。

这条路径的意义是「只交付它真的看过的图」：请求里从头到尾没有 asset_id，只有模型看过的
``photo_N``，映射由结果集自己做下标解析；越界的 handle 解析不出来。1.0 的前端
（``src/api.js:67``）与此完全同形。
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sentrix.domain.assistant.result_set import ResultSet

SCOPE = "album-rs-photo"


def photo_bytes(colour: tuple[int, int, int] = (12, 34, 56)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


def import_photo(client: TestClient, colour: tuple[int, int, int] = (12, 34, 56)) -> str:
    response = client.post(
        "/api/v1/media/import",
        files={"file": (f"{uuid.uuid4().hex}.jpg", photo_bytes(colour), "image/jpeg")},
        data={"scope_id": SCOPE},
    )
    assert response.status_code == 201, response.text
    assert response.json()["indexed"] is True, "导入必须写成视觉向量"
    return str(response.json()["asset_id"])


def make_result_set(client: TestClient, asset_ids: list[str], scope_id: str = SCOPE) -> str:
    result_set = ResultSet(
        id=f"rs_{uuid.uuid4().hex[:12]}",
        scope_id=scope_id,
        query="海边",
        asset_ids=tuple(asset_ids),
        created_at=datetime(2026, 9, 22, tzinfo=UTC),
    )
    client.app.state.container.result_set_repository.add(result_set)
    return result_set.id


@pytest.fixture
def asset_id(client: TestClient) -> str:
    return import_photo(client)


def test_a_handle_from_the_result_set_is_delivered(client: TestClient, asset_id: str):
    result_set_id = make_result_set(client, [asset_id])

    response = client.get(
        f"/api/v1/assistant/result-sets/{result_set_id}/photo",
        params={"handle": "photo_1", "scope_id": SCOPE, "original": 1},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content


def test_the_default_is_a_server_side_preview(client: TestClient, asset_id: str):
    """不给 ``original`` 时走服务端转码的预览——1.0 的默认也走归一化，只有 ``original=1``
    才给原图；2.0 这边对应 ``/assets/{id}/preview`` 那条既有映射。"""
    result_set_id = make_result_set(client, [asset_id])

    response = client.get(
        f"/api/v1/assistant/result-sets/{result_set_id}/photo",
        params={"handle": "photo_1", "scope_id": SCOPE},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")


def test_an_unknown_result_set_is_404(client: TestClient):
    response = client.get("/api/v1/assistant/result-sets/rs_nope/photo", params={"scope_id": SCOPE})

    assert response.status_code == 404
    assert response.json()["detail"] == "result set not found"


def test_a_scope_mismatch_is_403(client: TestClient, asset_id: str):
    result_set_id = make_result_set(client, [asset_id], scope_id="另一个相册")

    response = client.get(f"/api/v1/assistant/result-sets/{result_set_id}/photo", params={"scope_id": SCOPE})

    assert response.status_code == 403
    assert response.json()["detail"] == "scope mismatch"


def test_a_handle_outside_the_result_set_is_404(client: TestClient, asset_id: str):
    """``photo_9`` 不在这个结果集里——模型不能靠编一个 handle 把别的图取出来。"""
    result_set_id = make_result_set(client, [asset_id])

    response = client.get(
        f"/api/v1/assistant/result-sets/{result_set_id}/photo",
        params={"handle": "photo_9", "scope_id": SCOPE},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "handle not in result set"


@pytest.mark.parametrize("handle", ["asset_" + "a" * 8, "photo_0", "photo_", "photo_1x", ""])
def test_only_a_photo_n_handle_resolves(client: TestClient, asset_id: str, handle: str):
    """``resolve`` 只认 ``photo_<从 1 开始的下标>``，其余一律解析不出来。

    ``asset_xxx`` 直接传进来是最值得挡的一种：那正是这条路径存在的理由——把 asset_id
    挡在用户可见的 URL 之外。
    """
    result_set_id = make_result_set(client, [asset_id])

    response = client.get(
        f"/api/v1/assistant/result-sets/{result_set_id}/photo",
        params={"handle": handle, "scope_id": SCOPE},
    )

    if handle == "":
        # 不给 handle 时取结果集第一张，这是 1.0 的默认行为。
        assert response.status_code == 200
    else:
        assert response.status_code == 404


def test_an_empty_result_set_is_404(client: TestClient):
    result_set_id = make_result_set(client, [])

    response = client.get(f"/api/v1/assistant/result-sets/{result_set_id}/photo", params={"scope_id": SCOPE})

    assert response.status_code == 404
    assert response.json()["detail"] == "empty result set"


def test_the_second_asset_of_a_result_set_is_reachable_by_its_handle(client: TestClient, asset_id: str):
    """``handle`` 是**全局序号**：结果集里第 2 张就是 ``photo_2``。

    这条同时钉住「分页不会让同一个 handle 指向不同的图」——1.0 ``result_set.py`` 的
    那条不变量。
    """
    second = import_photo(client, colour=(200, 30, 30))
    assert second != asset_id, "两张不同颜色的图必须是两个资产（按内容去重不该合并它们）"
    result_set_id = make_result_set(client, [asset_id, second])

    first = client.get(
        f"/api/v1/assistant/result-sets/{result_set_id}/photo",
        params={"handle": "photo_1", "scope_id": SCOPE, "original": 1},
    )
    other = client.get(
        f"/api/v1/assistant/result-sets/{result_set_id}/photo",
        params={"handle": "photo_2", "scope_id": SCOPE, "original": 1},
    )

    assert first.status_code == 200
    assert other.status_code == 200
    assert first.content != other.content

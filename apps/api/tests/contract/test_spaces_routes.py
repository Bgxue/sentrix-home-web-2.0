"""``GET /api/v1/memory-spaces`` 的契约。

**这是 2.0 补的一个缺口**：1.0 有 ``/api/memory-spaces``（``src/api.js:17``，
侧边栏的相册切换器靠它），而 2.0 的 domain 端口与仓储都在、容器字段也在，
只差这一层 HTTP。

返回体的形状按 **1.0 前端的消费处** 定（``src/app.js:1161``
``spaceResult[0].value.spaces || []``）：``{"spaces": [...]}``，不是裸数组。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sentrix.config import Settings
from sentrix.main import create_app


def test_empty_database_is_an_empty_list_not_a_404(settings: Settings) -> None:
    """空库是合法状态（2.0 就是空库起步）。缺的是数据，不是端点。"""
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/memory-spaces")

    assert response.status_code == 200
    assert response.json() == {"spaces": []}


def test_every_space_carries_the_four_columns(settings: Settings) -> None:
    """有空间时字段齐全——``memory_spaces`` 的四列，一个不多一个不少。

    前端按 ``kind`` 决定回落行为（只有 ``benchmark`` 的旧选择被保留，见
    ``ScopeProvider.resolveStoredScope``），所以它必须出现。
    """
    app = create_app(settings)
    app.state.container.spaces.ensure("album1", "第一册", kind="benchmark")
    client = TestClient(app)

    body = client.get("/api/v1/memory-spaces").json()

    assert body == {"spaces": [{"id": "album1", "name": "第一册", "kind": "benchmark", "status": "active"}]}

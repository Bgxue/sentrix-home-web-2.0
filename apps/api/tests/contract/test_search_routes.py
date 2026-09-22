"""`GET /api/v1/search` 的契约测试。

嵌入器换成脚本化的假件（向量由测试直接指定），这样「哪张图该排第一」是构造出来的，
而不是碰运气撞上模型输出。真模型 + 真照片的验收在 tests/e2e（Task 33）。
"""

from __future__ import annotations

import dataclasses
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sentrix.config import Settings
from sentrix.domain.retrieval import scheme
from sentrix.main import create_app

RED = [1.0, 0.0, 0.0, 0.0]
GREEN = [0.0, 1.0, 0.0, 0.0]


class ScriptedEmbedder:
    """文本 -> 指定向量；图片 -> 由内容映射到指定向量。"""

    model_id = scheme.IMAGE_MODEL
    dimension = 4

    def __init__(self, images: dict[bytes, list[float]], texts: dict[str, list[float]]) -> None:
        self._images = images
        self._texts = texts

    def embed_image(self, path: Path) -> list[float]:
        return list(self._images.get(Path(path).read_bytes(), [0.0, 0.0, 0.0, 1.0]))

    def embed_query(self, text: str) -> list[float]:
        return list(self._texts.get(text.strip(), [0.0, 0.0, 0.0, 1.0]))


def jpeg_bytes(color: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def scripted_client(settings: Settings) -> TestClient:
    red, green = jpeg_bytes("red"), jpeg_bytes("green")
    app = create_app(settings)
    app.state.container = dataclasses.replace(
        app.state.container,
        visual_embedder=ScriptedEmbedder(images={red: RED, green: GREEN}, texts={"红色": RED, "绿色": GREEN}),
    )
    return TestClient(app)


def import_photo(client: TestClient, payload: bytes, scope_id: str = "album1") -> str:
    return client.post(
        "/api/v1/media/import",
        files={"file": ("a.jpg", payload, "image/jpeg")},
        data={"scope_id": scope_id},
    ).json()["asset_id"]


def test_search_ranks_the_matching_photo_first(scripted_client: TestClient):
    red, green = jpeg_bytes("red"), jpeg_bytes("green")
    red_id = import_photo(scripted_client, red)
    green_id = import_photo(scripted_client, green)

    response = scripted_client.get("/api/v1/search", params={"q": "红色", "scope_id": "album1"})

    assert response.status_code == 200
    body = response.json()
    # 召回不做阈值截断：低相似度的候选仍然在列表里，只是排在后面。要的是名次，
    # 不是「有没有命中」——S2 没有校准层，凭一个阈值判断相关性只会误伤。
    assert [item["asset"]["id"] for item in body["items"]] == [red_id, green_id]
    assert body["backend"] == "qdrant"
    assert body["degraded"] is False
    assert body["authoritative_vectors"] == 2


def test_the_other_word_ranks_the_other_photo_first(scripted_client: TestClient):
    red, green = jpeg_bytes("red"), jpeg_bytes("green")
    import_photo(scripted_client, red)
    green_id = import_photo(scripted_client, green)

    items = scripted_client.get("/api/v1/search", params={"q": "绿色", "scope_id": "album1"}).json()["items"]

    assert items[0]["asset"]["id"] == green_id


def test_search_hit_carries_everything_the_web_needs(scripted_client: TestClient):
    """命中必须带回可打开的原图信息——前端不该再发一次请求才知道图在哪。"""
    import_photo(scripted_client, jpeg_bytes("red"))

    item = scripted_client.get("/api/v1/search", params={"q": "红色", "scope_id": "album1"}).json()["items"][
        0
    ]

    assert item["asset"]["file_name"] == "a.jpg"
    assert item["asset"]["mime_type"] == "image/jpeg"
    assert item["asset"]["status"] == "processed"
    assert item["channels"] == {"visual_ann": 1}
    assert item["raw_scores"]["visual_ann"] == pytest.approx(1.0)
    # 融合分数是加权 RRF（visual 2.5 / k=60），不是原始余弦。
    assert item["score"] == pytest.approx(2.5 / 61)


def test_search_is_scoped(scripted_client: TestClient):
    red = jpeg_bytes("red")
    album1_id = import_photo(scripted_client, red, scope_id="album1")
    import_photo(scripted_client, red, scope_id="album2")

    items = scripted_client.get("/api/v1/search", params={"q": "红色", "scope_id": "album1"}).json()["items"]

    assert [item["asset"]["id"] for item in items] == [album1_id]


def test_search_on_an_empty_library_is_empty_and_not_degraded(scripted_client: TestClient):
    body = scripted_client.get("/api/v1/search", params={"q": "红色"}).json()

    assert body["items"] == []
    assert body["degraded"] is False
    assert body["authoritative_vectors"] == 0


def test_unrelated_word_gives_a_stable_ranking_without_a_relevance_claim(
    scripted_client: TestClient,
):
    """S2 **没有**相关性阈值：一个毫不相干的词照样返回排好序的候选。

    这条是已知边界，不是缺陷：1.0 的校准层（NeutralProbe / probe per-space 阈值）
    属于 S6。在它到位之前，宁可给出「按相似度排好的列表」，也不要凭一个拍脑袋的
    阈值假装判断了相关性。这里只钉住两件事：不报降级、两次结果一致。
    """
    import_photo(scripted_client, jpeg_bytes("red"))

    first = scripted_client.get("/api/v1/search", params={"q": "完全不相关的词"}).json()
    second = scripted_client.get("/api/v1/search", params={"q": "完全不相关的词"}).json()

    assert first["backend"] == "qdrant"
    assert first["degraded"] is False
    assert [item["asset"]["id"] for item in first["items"]] == [
        item["asset"]["id"] for item in second["items"]
    ]


def test_scores_are_non_increasing(scripted_client: TestClient):
    red, green = jpeg_bytes("red"), jpeg_bytes("green")
    import_photo(scripted_client, red)
    import_photo(scripted_client, green)

    items = scripted_client.get("/api/v1/search", params={"q": "红色", "scope_id": "album1"}).json()["items"]

    scores = [item["score"] for item in items]
    assert scores == sorted(scores, reverse=True)


def test_blank_query_is_rejected(scripted_client: TestClient):
    assert scripted_client.get("/api/v1/search", params={"q": ""}).status_code == 422
    assert scripted_client.get("/api/v1/search").status_code == 422


def test_limit_is_bounded(scripted_client: TestClient):
    assert scripted_client.get("/api/v1/search", params={"q": "红色", "limit": 0}).status_code == 422
    assert scripted_client.get("/api/v1/search", params={"q": "红色", "limit": 1000}).status_code == 422

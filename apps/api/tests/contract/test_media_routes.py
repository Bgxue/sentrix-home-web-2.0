import io

from fastapi.testclient import TestClient
from PIL import Image

from sentrix.domain.retrieval.scheme import IMAGE_MODEL


def jpeg_bytes(size=(64, 48), color="green") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_import_returns_201_and_asset_id(client: TestClient):
    response = client.post(
        "/api/v1/media/import",
        files={"file": ("IMG_0001.jpg", jpeg_bytes(), "image/jpeg")},
        data={"scope_id": "album1"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["asset_id"].startswith("asset_")
    assert body["media_type"] == "image"
    assert body["deduplicated"] is False


def test_reimport_same_bytes_is_deduplicated(client: TestClient):
    payload = jpeg_bytes()
    for _ in range(2):
        response = client.post(
            "/api/v1/media/import",
            files={"file": ("a.jpg", payload, "image/jpeg")},
            data={"scope_id": "album1"},
        )

    assert response.status_code == 201
    assert response.json()["deduplicated"] is True


def test_list_assets_for_scope(client: TestClient):
    client.post(
        "/api/v1/media/import",
        files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")},
        data={"scope_id": "album1"},
    )
    client.post(
        "/api/v1/media/import",
        files={"file": ("b.jpg", jpeg_bytes(color="red"), "image/jpeg")},
        data={"scope_id": "album2"},
    )

    response = client.get("/api/v1/assets", params={"scope_id": "album1"})

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


def test_asset_file_returns_original_bytes(client: TestClient):
    payload = jpeg_bytes()
    asset_id = client.post(
        "/api/v1/media/import", files={"file": ("a.jpg", payload, "image/jpeg")}, data={"scope_id": "album1"}
    ).json()["asset_id"]

    response = client.get(f"/api/v1/assets/{asset_id}/file")

    assert response.status_code == 200
    assert response.content == payload


def test_asset_preview_is_jpeg(client: TestClient):
    asset_id = client.post(
        "/api/v1/media/import",
        files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")},
        data={"scope_id": "album1"},
    ).json()["asset_id"]

    response = client.get(f"/api/v1/assets/{asset_id}/preview")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"


def test_missing_asset_is_404(client: TestClient):
    assert client.get("/api/v1/assets/asset_nope").status_code == 404
    assert client.get("/api/v1/assets/asset_nope/file").status_code == 404


def test_import_reports_and_writes_the_visual_vector(client: TestClient):
    """导入成功必须真的留下视觉向量，并且这件事在响应里看得见。

    1.0 的同类失败（223 张图没有任何视觉向量）在接口上完全没有痕迹，只能事后
    手工清点；``indexed`` 就是补上的那个痕迹。
    """
    body = client.post(
        "/api/v1/media/import",
        files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")},
        data={"scope_id": "album1"},
    ).json()

    assert body["indexed"] is True

    container = client.app.state.container
    assert (
        container.vector_repository.has_vector(
            space="visual",
            source_type="asset",
            source_id=body["asset_id"],
            model_name=IMAGE_MODEL,
        )
        is True
    )


def test_reimport_does_not_duplicate_the_visual_vector(client: TestClient):
    payload = jpeg_bytes()
    for _ in range(2):
        client.post(
            "/api/v1/media/import",
            files={"file": ("a.jpg", payload, "image/jpeg")},
            data={"scope_id": "album1"},
        )

    assert client.app.state.container.vector_repository.count(space="visual") == 1

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0", "database": "ok"}


def test_unknown_v1_route_is_404(client: TestClient):
    assert client.get("/api/v1/nope").status_code == 404


def test_no_unversioned_public_routes(client: TestClient):
    """公开 API 只有 /api/v1；/api/health 这种 1.0 风格路径必须不存在。"""
    assert client.get("/api/health").status_code == 404

"""Installing real face weights must not turn HTTP contracts into inference workloads."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sentrix.infrastructure.models.face_adapter import FaceAdapter


def test_http_fixture_does_not_call_real_face_inference(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("HTTP fixture invoked real face inference")

    monkeypatch.setattr(FaceAdapter, "detect", forbidden)
    monkeypatch.setattr(FaceAdapter, "detect_identity_photo", forbidden)
    album = tmp_path / "probe-album"
    album.mkdir()
    Image.new("RGB", (32, 32), "red").save(album / "probe.jpg")
    result = client.post("/api/v1/imports", json={"directory": str(album), "scope_id": "isolated"})
    assert result.status_code == 201, result.text
    assert result.json()["imported"] == 1
    # 身份入口同样隔离；需要脸的具体契约必须自行注入，而不是依赖本机权重。
    assert client.app.state.container.face_detector.detect_identity_photo(str(album / "probe.jpg")) == []

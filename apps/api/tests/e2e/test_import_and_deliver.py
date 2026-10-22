"""端到端：导入真实照片 → 列表可见 → 能取到与原文件逐字节相同的原图。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Requires real album data, local weights or a running model; run via make test-real.
pytestmark = pytest.mark.real_environment

FIXTURE_DIR = Path(__file__).resolve().parents[4] / "data/album3-v2-source/photos"


@pytest.fixture
def real_photo() -> Path:
    candidates = sorted(FIXTURE_DIR.glob("*.jpg")) + sorted(FIXTURE_DIR.glob("*.JPG"))
    if not candidates:
        pytest.skip(f"真实照片夹具不可用: {FIXTURE_DIR}")
    return candidates[0]


def test_real_photo_survives_full_roundtrip(client: TestClient, real_photo: Path):
    original = real_photo.read_bytes()

    imported = client.post(
        "/api/v1/media/import",
        files={"file": (real_photo.name, original, "image/jpeg")},
        data={"scope_id": "album1"},
    )
    assert imported.status_code == 201
    asset_id = imported.json()["asset_id"]

    listed = client.get("/api/v1/assets", params={"scope_id": "album1"}).json()["items"]
    assert any(item["id"] == asset_id for item in listed)

    served = client.get(f"/api/v1/assets/{asset_id}/file")
    assert served.content == original, "原图必须逐字节一致——这是产品硬约束第 5 条"

    preview = client.get(f"/api/v1/assets/{asset_id}/preview")
    assert preview.status_code == 200
    assert preview.content[:2] == b"\xff\xd8"


def test_captured_at_is_derived_when_exif_present(client: TestClient, real_photo: Path):
    imported = client.post(
        "/api/v1/media/import",
        files={"file": (real_photo.name, real_photo.read_bytes(), "image/jpeg")},
        data={"scope_id": "album1"},
    ).json()

    detail = client.get(f"/api/v1/assets/{imported['asset_id']}").json()

    assert detail["captured_at"] is not None

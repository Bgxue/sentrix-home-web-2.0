import pytest
from fastapi.testclient import TestClient

from sentrix.config import Settings
from sentrix.main import create_app


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    return Settings()


@pytest.fixture
def client(settings) -> TestClient:
    return TestClient(create_app(settings))

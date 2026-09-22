import dataclasses
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentrix.config import Settings
from sentrix.domain.retrieval import scheme
from sentrix.infrastructure.persistence.database import Database
from sentrix.main import create_app


class FakeVisualEmbedder:
    """契约测试用的确定性嵌入器。

    真模型是进程内 GPU 推理、权重 1.6GB：放进 make ci 会让契约测试依赖一台带显卡
    和权重的机器，而契约测试要验的是 HTTP 映射，不是模型。真链路的验收在
    tests/e2e（Task 33），那里用真模型 + 真 Qdrant。
    """

    model_id = scheme.IMAGE_MODEL
    dimension = 8

    @staticmethod
    def _vector(seed: str) -> list[float]:
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        return [digest[index] / 255.0 for index in range(8)]

    def embed_image(self, path) -> list[float]:
        return self._vector(Path(path).read_bytes().hex())

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    return Settings()


@pytest.fixture
def client(settings) -> TestClient:
    """装配好的应用，但把视觉嵌入器换成确定性假件。

    Container 是 frozen dataclass，用 dataclasses.replace 换掉一个适配器，
    其余装配路径（真 SQLite、真迁移、真 Qdrant）保持原样。
    """
    app = create_app(settings)
    app.state.container = dataclasses.replace(app.state.container, visual_embedder=FakeVisualEmbedder())
    return TestClient(app)


@pytest.fixture
def make_state():
    """构造一个回合状态。默认参数够大多数用例用，需要时逐字段改。"""
    from sentrix.domain.assistant.profile import DEFAULT_PROFILE
    from sentrix.domain.assistant.task_state import TaskState

    def _make(**overrides):
        fields = {
            "turn_id": "turn_1",
            "conversation_id": "conv_1",
            "scope_id": "album1",
            "question": "给我看看在海边的照片",
            "profile": DEFAULT_PROFILE,
        }
        fields.update(overrides)
        return TaskState(**fields)

    return _make


@pytest.fixture
def migrations_ready_db(tmp_path) -> Database:
    """真 SQLite + 真迁移文件，跑在临时目录里。"""
    migrations = Path(__file__).resolve().parents[1] / "src/sentrix/infrastructure/persistence/migrations"
    db = Database(tmp_path / "sentrix.db", migrations)
    db.migrate()
    return db

import dataclasses
import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentrix.config import Settings
from sentrix.domain.assistant.models import ChatMessage, ModelReply
from sentrix.domain.media.ports import ObservationSemantics
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


class FakeVlmObserver:
    """契约测试用的确定性观察器。

    真观察器会打 8100 上的 llama-server（本机确实在跑 1.0 的那一个）。契约测试要验的是
    HTTP 映射与导入链的接线，不是模型；真链路的验收在 tests/e2e。
    """

    model_id = "fake-vlm"

    def observe(self, path):
        return ObservationSemantics(
            caption="一张测试图",
            place="测试地点",
            activity="测试活动",
            event_type="测试事件",
            objects=("测试物体",),
            confidence=0.65,
        )


class FakeChatModel:
    """契约测试用的确定性文本模型。

    真的 ``container.chat_model`` 打的是 8100 的 llama-server——``/api/v1/imports``
    的批末收尾（``SummarizeEvent``）会调它，每批一次、每个事件一次。契约测试要验的是
    HTTP 映射，不该把「8100 在不在」和「它这次回了什么标题」变成断言的前置条件：
    模型不可用时 ``SummarizeEvent`` 会退回确定性摘要（这条降级路径在
    ``tests/unit/test_event_summary.py`` 里单独验），但退回之前要付一次连接失败。

    返回一份合法的事件投影，让模型那条路在契约测试里也走到且确定。
    """

    model_id = "fake-chat"

    def complete(self, messages: Sequence[ChatMessage], tools: Sequence[object]) -> ModelReply:
        return ModelReply(
            content=(
                '{"title":"测试事件标题","event_type":"家庭活动","activity":"测试活动",'
                '"summary":"测试摘要","confidence":0.8}'
            ),
            finish_reason="stop",
        )


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    return Settings()


@pytest.fixture
def client(settings) -> TestClient:
    """装配好的应用，但把三个模型适配器换成确定性假件。

    Container 是 frozen dataclass，用 dataclasses.replace 换掉适配器，
    其余装配路径（真 SQLite、真迁移、真 Qdrant）保持原样。
    ``chat_model`` 也是假件：``/api/v1/imports`` 的批末事件总结会调它
    （见 ``FakeChatModel``）。
    """
    app = create_app(settings)
    app.state.container = dataclasses.replace(
        app.state.container,
        visual_embedder=FakeVisualEmbedder(),
        vlm_observer=FakeVlmObserver(),
        chat_model=FakeChatModel(),
    )
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


@pytest.fixture
def confirmed_person_events():
    """造一个 ``ConfirmedPersonEvents``：确认之后的事件侧（参与者投影 + 重分段）。

    簇审阅 / 命名 / 事件参与者三个用例文件都要构造它，装配写在这里一份。**这不是
    「顺手抽个工具函数」**：``ReviewCluster`` 的构造参数里它是必填的，三个文件各自手写
    一遍装配，迟早会有一处漏掉某个仓储、然后那条用例去测一个与生产不同的组合。

    调用方给的是自己那个已经种好的 ``Database``——仓储都建在同一张库上。
    """
    from sentrix.application.memories.assign_events import (
        ConfirmedPersonEvents,
        EventVisualSimilarity,
    )
    from sentrix.infrastructure.persistence.repositories.assets import (
        SqliteAssetRepository,
        SqliteObservationRepository,
    )
    from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
    from sentrix.infrastructure.persistence.repositories.events import SqliteEventRepository
    from sentrix.infrastructure.persistence.repositories.participants import (
        SqliteEventParticipantRepository,
    )
    from sentrix.infrastructure.persistence.repositories.vectors import SqliteVectorRepository

    def _build(db: Database) -> ConfirmedPersonEvents:
        entities = SqliteEntityRepository(db)
        observations = SqliteObservationRepository(db)
        assets = SqliteAssetRepository(db)
        return ConfirmedPersonEvents(
            events=SqliteEventRepository(db),
            participants=SqliteEventParticipantRepository(db),
            observations=observations,
            assets=assets,
            entities=entities,
            # 视觉向量一路留空：这些用例验的是投影与阈值，不是嵌入模型。没有向量时
            # ``visual_boost`` 恒 0、``split_guard`` 走 ``not visual_available`` 那一支，
            # 都是 1.0 在生产上真实会走到的状态。
            visual_similarity=EventVisualSimilarity(
                vectors=SqliteVectorRepository(db), observations=observations, assets=assets
            ),
            confirmed_people=entities.confirmed_people_for_observation,
            confirmed_event_people=entities.confirmed_people_for_event,
        )

    return _build

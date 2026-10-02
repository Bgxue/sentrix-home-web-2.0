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

# ---- 加载失败：环境原因才跳过，代码原因必须红 --------------------------------------
#
# 守卫一旦对**任何**加载失败都 pytest.skip，模型加载的代码级损坏（模型名写错、import
# 路径变了、权重格式不对）就会被静默跳掉——`test_chinese_clip.py` 与两个 e2e 会一起
# 跳过，没有任何一条自动化用例会红。跳过只对「**这个部署跑不了**」成立。
#
# 这份名单是**白名单**：只认下面这些措辞。不在名单里的错因一律 pytest.fail，让用例红。
ENVIRONMENTAL_LOAD_FAILURES = (
    "checkpoint missing",  # chinese_clip._load 自己写的措辞
    "out of memory",  # torch / CUDA
    "outofmemoryerror",
    "cublas",  # onnxruntime CUDA provider 建不起来
    "bfc_arena",  # onnxruntime 分配器分配失败
    "no registered plugin ep device",  # CUDA EP 在这台机器上不可用
)


def skip_or_fail_on_load_error(error: str, *, what: str) -> None:
    """加载失败时的分流：环境原因 skip，其余 fail。"""
    lowered = error.lower()
    if any(marker in lowered for marker in ENVIRONMENTAL_LOAD_FAILURES):
        pytest.skip(f"{what} 在这个部署上加载不了（环境，不是代码）: {error}")
    pytest.fail(f"{what} 加载失败，且错因不像环境问题——按代码问题处理: {error}")


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
    from sentrix.infrastructure.persistence.repositories.relationships import (
        SqliteRelationshipRepository,
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
            relationships=SqliteRelationshipRepository(db),
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


@pytest.fixture
def observe_appearance():
    """造一个 ``ObserveAppearance``：确认之后的第三步（外观取样，Task 85d）。

    装配写在这里一份，理由与 ``confirmed_person_events`` 逐字相同：它是
    ``ReviewCluster`` 的必填构造参数，三个用例文件各写一遍，迟早有一处漏掉某个仓储，
    然后那条用例测的是一个与生产不同的组合。

    **模型一路是不通的**——``_UnavailablePersonVision`` 三个方法都返回 ``None``。
    簇审阅那些用例验的是身份与事件，不是外观；而「模型不可用」正是生产上没配模型时
    的真实状态（见 ``PersonVision`` 的 docstring：模型不可用一律返回 ``None``，
    外观是增强、不是确认的前置条件）。所以确认路径在这些用例里照样得走完，
    而不是被这个新钩子弄红。
    """

    from sentrix.application.identity.observe_appearance import ObserveAppearance
    from sentrix.infrastructure.media.person_vision import PillowPersonCropRenderer
    from sentrix.infrastructure.persistence.repositories.appearance import (
        SqliteAppearanceRepository,
    )
    from sentrix.infrastructure.persistence.repositories.assets import (
        SqliteObservationRepository,
    )
    from sentrix.infrastructure.persistence.repositories.entities import SqliteEntityRepository
    from sentrix.infrastructure.persistence.repositories.faces import SqliteFaceRepository

    class _UnavailablePersonVision:
        @property
        def model_id(self) -> str:
            return "unavailable"

        def write_person_portrait(self, pack):
            return None

        def analyze_person_moments(self, path, labels, context=None):
            return None

        def analyze_person_appearance(self, path, metadata=None):
            return None

    def _build(db: Database) -> ObserveAppearance:
        return ObserveAppearance(
            faces=SqliteFaceRepository(db),
            entities=SqliteEntityRepository(db),
            observations=SqliteObservationRepository(db),
            appearances=SqliteAppearanceRepository(db),
            crops=PillowPersonCropRenderer(),
            vision=_UnavailablePersonVision(),
        )

    return _build

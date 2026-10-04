"""PhotoBench 的装配点。

**字段按端口声明，不写具体类**——与 ``apps/api`` 的 ``sentrix/bootstrap/container.py``
同一条不变量（那里由 ``test_container_wiring.py`` 强制）。理由也一样：字段写成
``HttpxJsonClient`` 之类的具体类，可替换性就只存在于 ``build()`` 的那一行里，
调用方在类型上看不见，于是很容易顺手用上协议之外的方法——**结构上换得掉、
类型上看不出来，等于没换**。

运行时的三个提供者（推理 / 生命周期 / 遥测）在这里是**两个按端口声明的工厂**，
不是具体类：

- ``lifecycle_provider``：``manager_url -> LifecycleProvider``，服务 ``/api/profiles``
  与 ``/api/current-model`` 对 Manager 的查询；
- ``inference_provider``：``(model_base_url, manager_url) -> InferenceProvider``，
  服务「这个端点到底在跑哪个模型」的核对。

**为什么是工厂而不是一个 ``RuntimeProviders`` 字段**：1.0 的这两条查询是**按请求**构造
提供者的（``query_profiles`` ``:5069`` / ``query_current_model`` ``:5078`` 各自
``ManagerLifecycleProvider(vllm_api_url)`` / ``OpenAICompatibleInferenceProvider(...)``），
端点来自请求体而不是启动参数。``RuntimeProviders``（S9a 的聚合值对象）仍然是
``create_runtime_providers`` 的返回类型，只是不加进 Container ——本切片没有常驻的
「当前提供者」可用（那要等 S9b-2 的 run-execution 把选定的端点落下来）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from photobench.application.config.service import ConfigService
from photobench.application.model_profiles.service import ModelProfiles
from photobench.application.run_execution.service import RunExecution
from photobench.application.runs.index import RunIndex
from photobench.application.runs.read_runs import ReadRuns
from photobench.config import Settings
from photobench.domain.config.ports import ConfigStore
from photobench.domain.datasets.ports import AlbumSource, QaMetadataSource
from photobench.domain.http.ports import JsonHttpClient
from photobench.domain.results.ports import RunResultWriter, RunStore
from photobench.domain.runtime.ports import InferenceProvider, LifecycleProvider, TelemetryProvider
from photobench.domain.sentrix.ports import SentrixClient
from photobench.infrastructure.config.files import JsonConfigStore
from photobench.infrastructure.datasets.albums import JsonAlbumSource
from photobench.infrastructure.datasets.qa_metadata import JsonQaMetadataSource
from photobench.infrastructure.http.client import HttpxJsonClient
from photobench.infrastructure.results.store import FileResultWriter, FileRunStore
from photobench.infrastructure.runtime.providers import (
    create_runtime_providers,
    manager_lifecycle,
    manager_telemetry,
)
from photobench.infrastructure.sentrix.client import HttpxSentrixClient

#: 1.0 建 provider 时用的超时：``query_current_model`` 里是 15 秒（``:5095``）。
#: ``query_profiles`` 那一路由 provider 自己的默认值管（1.0 也没传）。
_CURRENT_MODEL_TIMEOUT = 15


@dataclass(frozen=True)
class Container:
    settings: Settings
    config_store: ConfigStore
    run_store: RunStore
    result_writer: RunResultWriter
    qa_metadata: QaMetadataSource
    albums: AlbumSource
    http: JsonHttpClient
    sentrix: SentrixClient
    lifecycle_provider: Callable[[str], LifecycleProvider]
    inference_provider: Callable[[str, str], InferenceProvider]
    telemetry_provider: Callable[[str], TelemetryProvider]
    config: ConfigService
    run_index: RunIndex
    runs: ReadRuns
    model_profiles: ModelProfiles
    run_execution: Callable[[str, str], RunExecution]

    @staticmethod
    def build(settings: Settings) -> Container:
        config_store: ConfigStore = JsonConfigStore(settings.config_root)
        config = ConfigService(config_store)
        run_store: RunStore = FileRunStore(settings.results_root)
        run_index = RunIndex()
        qa_metadata: QaMetadataSource = JsonQaMetadataSource(
            settings.data_root, settings.config_root / "qa_task_actions.json"
        )

        def lifecycle_provider(manager_url: str) -> LifecycleProvider:
            """有 Manager 就是 ``ManagerLifecycleProvider``，没有就是 ``Unavailable``。

            **不能直接 ``ManagerLifecycleProvider(manager_url)``**：那个类的构造器在
            空串上抛 ``ValueError("manager URL is required")``（``providers.py:576``）。
            S9b-1 的调用方（``ModelProfiles._manager_profiles``）先自判 ``if not manager_url``
            再调，所以没撞上；S9b-2 的 ``run_execution`` 是**无条件**构造三件套的
            （「没有管理器」本身就是一种合法的运行形态——1.0 的
            ``use_cloud_model`` / 外部端点两条路都不需要 Manager）。

            实现走 ``manager_lifecycle``（S9b-2 从 ``create_runtime_providers`` 里拆出来的
            那一支）：空 URL 判 ``Unavailable`` 的口径**只有那一处**，工厂与这里共用它，
            两份默认行为不会各自漂移。
            """
            return manager_lifecycle(manager_url)

        def inference_provider(model_base_url: str, manager_url: str) -> InferenceProvider:
            return create_runtime_providers(
                model_base_url,
                manager_url=manager_url,
                api_mode="generic",
                timeout=_CURRENT_MODEL_TIMEOUT,
            ).inference

        def telemetry_provider(manager_url: str) -> TelemetryProvider:
            """遥测也按端口工厂声明，理由与另外两个相同。

            **实现是 ``manager_telemetry``**：有 Manager 就用 ``ManagerTelemetryProvider``，
            没有就用 ``UnavailableTelemetryProvider``。**这不是 1.0 的完整选择逻辑**——
            1.0 的 ``select_runtime_providers``（``:176``）还会按主机形态选
            Apple / Jetson / Orin / 宿主 nvidia-smi 四路，那一份要连 660 行的
            ``jetson_telemetry.py`` 一起搬，是独立的一块（见 S9b-2 施工记录）。
            """
            return manager_telemetry(manager_url)

        album_source: AlbumSource = JsonAlbumSource(
            settings.data_root, settings.config_root / "qa_task_actions.json"
        )
        http: JsonHttpClient = HttpxJsonClient()
        sentrix: SentrixClient = HttpxSentrixClient(http, lambda: config.sentrix_url)
        result_writer: RunResultWriter = FileResultWriter(settings.results_root)

        def run_execution(manager_url: str, model_base_url: str) -> RunExecution:
            """一 run 一个 ``RunExecution``：它是**有状态**的（跑一轮带一份 item 表），
            所以是工厂不是常驻字段——与三个 provider 工厂同一个理由。

            端点为空时退回配置里的那个：1.0 的 ``self.vllm_model_base_url`` 本来就是
            ``_configured_url`` 读出来的，不由调用方每次传。**不退的话
            ``OpenAICompatibleInferenceProvider`` 会在空串上抛 ``ValueError``**
            （``providers.py:453``）——那是把「配置缺失」报成「参数缺失」。
            """
            resolved_base_url = model_base_url or config.model_base_url
            return RunExecution(
                client=sentrix,
                albums=album_source,
                writer=result_writer,
                lifecycle=lifecycle_provider(manager_url),
                inference=inference_provider(resolved_base_url, manager_url),
                telemetry=telemetry_provider(manager_url),
            )

        runs = ReadRuns(
            store=run_store,
            index=run_index,
            qa_metadata=qa_metadata,
            http=http,
            # 1.0 的 ``_hydrate_legacy_gt_images`` 读的是模块级 ``DEFAULT_SENTRIX_URL``，
            # 而 ``POST /api/config`` 会就地改那个全局量。传取值函数而不是当前值，
            # 才会在配置被写回之后用上新的地址——与 1.0 的 ``global`` 等价。
            sentrix_url=lambda: config.sentrix_url,
        )
        model_profiles = ModelProfiles(
            config=config,
            lifecycle_for=lifecycle_provider,
            inference_for=inference_provider,
        )
        return Container(
            settings=settings,
            config_store=config_store,
            run_store=run_store,
            result_writer=result_writer,
            qa_metadata=qa_metadata,
            albums=album_source,
            http=http,
            sentrix=sentrix,
            lifecycle_provider=lifecycle_provider,
            inference_provider=inference_provider,
            telemetry_provider=telemetry_provider,
            config=config,
            run_index=run_index,
            runs=runs,
            model_profiles=model_profiles,
            run_execution=run_execution,
        )

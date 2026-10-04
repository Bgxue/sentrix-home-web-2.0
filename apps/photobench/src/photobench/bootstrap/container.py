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
from photobench.application.runs.index import RunIndex
from photobench.application.runs.read_runs import ReadRuns
from photobench.config import Settings
from photobench.domain.config.ports import ConfigStore
from photobench.domain.datasets.ports import QaMetadataSource
from photobench.domain.http.ports import JsonHttpClient
from photobench.domain.results.ports import RunStore
from photobench.domain.runtime.ports import InferenceProvider, LifecycleProvider
from photobench.infrastructure.config.files import JsonConfigStore
from photobench.infrastructure.datasets.qa_metadata import JsonQaMetadataSource
from photobench.infrastructure.http.client import HttpxJsonClient
from photobench.infrastructure.results.store import FileRunStore
from photobench.infrastructure.runtime.providers import (
    ManagerLifecycleProvider,
    create_runtime_providers,
)

#: 1.0 建 provider 时用的超时：``query_current_model`` 里是 15 秒（``:5095``）。
#: ``query_profiles`` 那一路由 provider 自己的默认值管（1.0 也没传）。
_CURRENT_MODEL_TIMEOUT = 15


@dataclass(frozen=True)
class Container:
    settings: Settings
    config_store: ConfigStore
    run_store: RunStore
    qa_metadata: QaMetadataSource
    http: JsonHttpClient
    lifecycle_provider: Callable[[str], LifecycleProvider]
    inference_provider: Callable[[str, str], InferenceProvider]
    config: ConfigService
    run_index: RunIndex
    runs: ReadRuns
    model_profiles: ModelProfiles

    @staticmethod
    def build(settings: Settings) -> Container:
        config_store: ConfigStore = JsonConfigStore(settings.config_root)
        config = ConfigService(config_store)
        run_store: RunStore = FileRunStore(settings.results_root)
        run_index = RunIndex()
        http: JsonHttpClient = HttpxJsonClient()
        qa_metadata: QaMetadataSource = JsonQaMetadataSource(
            settings.data_root, settings.config_root / "qa_task_actions.json"
        )

        def lifecycle_provider(manager_url: str) -> LifecycleProvider:
            return ManagerLifecycleProvider(manager_url)

        def inference_provider(model_base_url: str, manager_url: str) -> InferenceProvider:
            return create_runtime_providers(
                model_base_url,
                manager_url=manager_url,
                api_mode="generic",
                timeout=_CURRENT_MODEL_TIMEOUT,
            ).inference

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
            qa_metadata=qa_metadata,
            http=http,
            lifecycle_provider=lifecycle_provider,
            inference_provider=inference_provider,
            config=config,
            run_index=run_index,
            runs=runs,
            model_profiles=model_profiles,
        )

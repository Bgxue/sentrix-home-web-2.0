"""模型 Manager 的 profile 列表与「当前模型」核对。

对应 1.0 ``OrchestratorRepository.query_profiles``（``:5069``）与 ``query_current_model``
（``:5078``），以及 ``OrchestratorHandler.do_POST`` 里 ``/api/profiles``（``:7826``）
与 ``/api/current-model``（``:7844``）两条分支的请求整形。

**它为什么落在 application 而不是 api。** 那两条分支的一半是「选哪个 target、参数怎么
归一化」——那是用例，不是 HTTP 映射。1.0 把它们写在 handler 里，于是同一段 target
解析在 ``/api/profiles`` 与 ``/api/current-model`` 里各写了一遍。

**这一层是本切片里唯一会打模型端点的东西，而且只读**：``profiles`` / ``state`` /
``list_models`` / ``capabilities`` 四个查询。**起停模型（``start`` / ``stop``）不在
这里**——1.0 的 ``_phase_model_deploy`` 才做那件事，属 S9b-2 的 run-execution 主链。

**提供者按端口注入，不写死具体类**：``photobench-infrastructure-purity`` 的反方向是
``photobench-application-purity``，application 不许 import infrastructure。装配点
（``bootstrap.container``）把 S9a 的工厂包成两个可调用对象传进来——这也正是
``docs/architecture/photobench-module-map.md`` 要求的那条不变量：**按端口声明，
不写具体类**。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from photobench.application.config.service import (
    ConfigService,
    normalize_model_base_url,
    normalize_service_url,
)
from photobench.domain.errors import InvalidRequestError

# 1.0 的 ``now_iso``（``:572``）：带时区偏移的本地时间。它落在 runs.analysis 是因为
# 1.0 里第一个用它的是 run 持久化；这里的 ``verified_at`` 用的是同一个口径，不能换一个
# 时间戳写法，否则「什么时候核对的模型」在两边会有两种格式。
from photobench.domain.runs.analysis import now_iso
from photobench.domain.runtime.ports import InferenceProvider, LifecycleProvider

LifecycleFactory = Callable[[str], LifecycleProvider]
InferenceFactory = Callable[[str, str], InferenceProvider]


class ModelProfiles:
    def __init__(
        self,
        config: ConfigService,
        lifecycle_for: LifecycleFactory,
        inference_for: InferenceFactory,
    ) -> None:
        self._config = config
        self._lifecycle_for = lifecycle_for
        self._inference_for = inference_for

    def query_profiles(self, payload: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/profiles``（1.0 ``:7826`` + ``OrchestratorRepository.query_profiles`` ``:5069``）。

        方法名跟着 1.0 的仓储名走（``query_profiles``），**刻意不叫 ``profiles``**：
        ``check_method_reachability`` 的判据是「端口方法名在全仓唯一」，一个同名的
        application 方法会把 ``LifecycleProvider.profiles`` 挤进那张「重名盲区」名单
        ——那是把一条本来查得到的不变量换成一条查不到的。
        """
        manager_url = normalize_service_url(payload.get("vllm_manager_url"))
        if manager_url:
            target_id = "external"
            target: dict[str, Any] = {
                "label": "自定义模型 Manager",
                "manager_url": manager_url,
                "model_base_url": normalize_model_base_url(payload.get("model_base_url")),
                "kind": "external",
            }
        else:
            target_id, target = self._config.resolve_vllm_target(payload.get("vllm_target_id"))
        result = self._manager_profiles(str(target["manager_url"]))
        result.update({"target_id": target_id, "target": target})
        return result

    def _manager_profiles(self, manager_url: str) -> dict[str, Any]:
        """1.0 ``:5069``。Manager 没配 / 打不通都**不是错误**，是 ``status``。"""
        if not manager_url:
            return {"profiles": [], "status": "not_applicable", "reason": "model_manager_not_configured"}
        try:
            return self._lifecycle_for(manager_url).profiles()
        except Exception as exc:  # noqa: BLE001 —— 1.0 如此：Manager 掉线是状态，不是异常
            return {"profiles": [], "status": "unavailable", "error": str(exc)}

    def current_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        """``POST /api/current-model``（1.0 ``:7844`` + ``query_current_model`` ``:5078``）。"""
        direct_base_url = normalize_model_base_url(payload.get("model_base_url"))
        if direct_base_url:
            manager_url = normalize_service_url(payload.get("vllm_manager_url"))
            result = self._query_current_model(
                manager_url,
                direct_base_url,
                str(payload.get("model") or ""),
            )
            target_id = "external"
            target: dict[str, Any] = {
                "label": "自定义模型服务",
                "model_base_url": direct_base_url,
                "manager_url": manager_url,
                "kind": "external",
            }
        else:
            target_id, target = self._config.resolve_vllm_target(payload.get("vllm_target_id"))
            result = self._query_current_model(
                str(target.get("manager_url") or ""),
                str(target.get("model_base_url") or ""),
                str(payload.get("model") or ""),
            )
        result.update({"target_id": target_id, "target": target})
        return result

    def _query_current_model(
        self, manager_url: str, model_base_url: str, requested_model: str = ""
    ) -> dict[str, Any]:
        """1.0 ``:5078``。"""
        manager_error = None
        state: dict[str, Any] = {}
        if manager_url:
            try:
                state = self._lifecycle_for(manager_url).state() or {}
            except Exception as exc:  # noqa: BLE001 —— Manager 掉线不阻止我们直接问模型端点
                manager_error = str(exc)
        model_base_url = normalize_model_base_url(model_base_url)
        if not model_base_url:
            raise InvalidRequestError("model endpoint is required; enter host:port or an OpenAI /v1 URL")
        inference = self._inference_for(model_base_url, manager_url)
        served_models = inference.list_models().get("models") or []
        if not served_models:
            raise InvalidRequestError("model endpoint exposes no models")
        requested_model = str(requested_model or "").strip()
        if requested_model and requested_model not in served_models:
            raise InvalidRequestError(
                f"selected model {requested_model!r} is not exposed by the endpoint; "
                f"available models: {served_models}"
            )
        served_name = str(state.get("served_model_name") or "").strip()
        if served_name and served_name not in served_models:
            raise InvalidRequestError(
                f"manager state is stale or mismatched: expected {served_name}, "
                f"live endpoint serves {served_models or 'nothing'}"
            )
        if served_name and requested_model and requested_model != served_name:
            raise InvalidRequestError(
                f"Manager reports {served_name!r} as the active model; "
                f"cannot reuse requested model {requested_model!r} without switching it through Manager"
            )
        if not served_name:
            served_name = requested_model or (served_models[0] if len(served_models) == 1 else "")
        profile = str(state.get("profile") or "").strip()
        model_id = profile or served_name or None
        return {
            "model_id": model_id,
            "served_model_name": served_name or None,
            "selection_required": not bool(served_name),
            "verified_at": now_iso(),
            "served_models": served_models,
            "state": state,
            "model_base_url": model_base_url,
            "manager_available": bool(state),
            "manager_error": manager_error,
            "capabilities": inference.capabilities(),
        }

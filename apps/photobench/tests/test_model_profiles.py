"""``ModelProfiles`` 的用例：两条**只查询**的路（1.0 ``:5069`` / ``:5078``）。

**这里也是接线证据**：``LifecycleProvider.profiles`` / ``.state`` 与
``InferenceProvider.list_models`` / ``.capabilities`` 的生产调用者就在这个模块里。
起停模型（``start`` / ``stop``）不在其中——那是 S9b-2。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes_read import FakeInferenceProvider, FakeLifecycleProvider

from photobench.application.config.service import ConfigService
from photobench.application.model_profiles.service import ModelProfiles
from photobench.domain.errors import InvalidRequestError
from photobench.infrastructure.config.files import JsonConfigStore

TARGETS = {
    "default_target_id": "local",
    "targets": {
        # 本机 target：没有 Manager、也没有端点——用来验「没配」那两条分支。
        "local": {"label": "本机", "manager_url": "", "model_base_url": ""},
        "managed": {"label": "153", "manager_url": "http://153:8500", "model_base_url": "http://153:8100/v1"},
    },
}
PROVIDERS = {"default_provider_id": "p", "providers": {"p": {"url": "http://j/v1", "model": "m"}}}


@pytest.fixture
def service(tmp_path: Path) -> ConfigService:
    (tmp_path / "judge_providers.json").write_text(json.dumps(PROVIDERS), encoding="utf-8")
    (tmp_path / "vllm_targets.json").write_text(json.dumps(TARGETS), encoding="utf-8")
    return ConfigService(JsonConfigStore(tmp_path))


def _profiles(
    service: ConfigService, lifecycle: FakeLifecycleProvider, inference: FakeInferenceProvider
) -> ModelProfiles:
    return ModelProfiles(
        config=service,
        lifecycle_for=lambda _url: lifecycle,
        inference_for=lambda _base, _manager: inference,
    )


def test_profiles_without_a_manager_is_not_applicable(service: ConfigService):
    lifecycle = FakeLifecycleProvider()
    result = _profiles(service, lifecycle, FakeInferenceProvider()).query_profiles(
        {"vllm_manager_url": "", "vllm_target_id": "local"}
    )
    assert result["status"] == "not_applicable"
    assert result["reason"] == "model_manager_not_configured"
    assert lifecycle.calls == []


def test_profiles_asks_the_injected_lifecycle_provider(service: ConfigService):
    lifecycle = FakeLifecycleProvider(profiles={"profiles": [{"id": "big_model"}]})
    result = _profiles(service, lifecycle, FakeInferenceProvider()).query_profiles(
        {"vllm_manager_url": "http://manager:8500"}
    )
    assert result["profiles"] == [{"id": "big_model"}]
    assert result["target_id"] == "external"
    assert lifecycle.calls == ["profiles"]


def test_a_dead_manager_is_a_status_not_an_exception(service: ConfigService):
    """1.0 ``:5074``：Manager 掉线回 ``unavailable`` + 原因，不抛。"""
    lifecycle = FakeLifecycleProvider(error=RuntimeError("connection refused"))
    result = _profiles(service, lifecycle, FakeInferenceProvider()).query_profiles(
        {"vllm_manager_url": "http://manager:8500"}
    )
    assert result["status"] == "unavailable"
    assert result["error"] == "connection refused"


def test_current_model_requires_an_endpoint(service: ConfigService):
    with pytest.raises(InvalidRequestError) as excinfo:
        _profiles(service, FakeLifecycleProvider(), FakeInferenceProvider()).current_model(
            {"vllm_target_id": "local"}
        )
    assert "model endpoint is required" in str(excinfo.value)


def test_current_model_reads_the_served_model_off_the_endpoint(service: ConfigService):
    lifecycle = FakeLifecycleProvider(state={"served_model_name": "gemma", "profile": "big_model"})
    inference = FakeInferenceProvider(models=["gemma"])
    result = _profiles(service, lifecycle, inference).current_model(
        {"model_base_url": "http://endpoint:8100", "vllm_manager_url": "http://manager:8500"}
    )
    assert result["served_model_name"] == "gemma"
    assert result["model_id"] == "big_model"
    assert result["selection_required"] is False
    assert result["manager_available"] is True
    assert result["capabilities"] == {"modes": ["generic"]}
    assert "verified_at" in result
    assert lifecycle.calls == ["state"] and inference.calls == ["list_models", "capabilities"]


def test_current_model_flags_a_manager_that_disagrees_with_the_endpoint(service: ConfigService):
    lifecycle = FakeLifecycleProvider(state={"served_model_name": "stale"})
    with pytest.raises(InvalidRequestError) as excinfo:
        _profiles(service, lifecycle, FakeInferenceProvider(models=["live"])).current_model(
            {"model_base_url": "http://endpoint:8100", "vllm_manager_url": "http://manager:8500"}
        )
    assert "manager state is stale" in str(excinfo.value)


def test_current_model_rejects_an_endpoint_the_model_is_not_on(service: ConfigService):
    with pytest.raises(InvalidRequestError) as excinfo:
        _profiles(service, FakeLifecycleProvider(), FakeInferenceProvider(models=["a", "b"])).current_model(
            {"model_base_url": "http://endpoint:8100", "model": "c"}
        )
    assert "is not exposed by the endpoint" in str(excinfo.value)


def test_current_model_without_a_requested_model_requires_a_choice(service: ConfigService):
    result = _profiles(
        service, FakeLifecycleProvider(), FakeInferenceProvider(models=["a", "b"])
    ).current_model({"model_base_url": "http://endpoint:8100"})
    assert result["selection_required"] is True
    assert result["served_model_name"] is None


def test_an_empty_endpoint_is_an_error(service: ConfigService):
    with pytest.raises(InvalidRequestError):
        _profiles(service, FakeLifecycleProvider(), FakeInferenceProvider(models=[])).current_model(
            {"model_base_url": "http://endpoint:8100"}
        )

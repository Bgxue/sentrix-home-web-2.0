"""部署 profile：同一份代码，server / mac 只差一组默认值。

profile 只提供默认值，显式 env 永远优先；数据根、端点地址、judge 配置不归它管。
"""

from __future__ import annotations

import pytest

from sentrix.config import Settings
from sentrix.infrastructure.models.onnx_runtime import FACE_PROVIDERS_ENV, provider_chain

_PROFILE_ENVS = (
    "SENTRIX_DEPLOYMENT_PROFILE",
    "SENTRIX_EMBEDDING_SCHEME",
    "SENTRIX_AGENT_WALL_TIME_S",
    "SENTRIX_CHAT_TIMEOUT_S",
    "FACE_PROVIDERS",
    "RETINAFACE_PROVIDERS",
)


def _clean(monkeypatch) -> None:
    for name in _PROFILE_ENVS:
        monkeypatch.delenv(name, raising=False)


def test_server_is_the_default_and_keeps_the_old_behaviour(monkeypatch):
    _clean(monkeypatch)
    settings = Settings()
    assert settings.deployment_profile == "server"
    assert settings.embedding_scheme == "wemm"
    assert settings.agent_wall_time_s == 180.0
    assert settings.chat_timeout_s == 120.0


def test_mac_profile_sets_the_three_product_defaults(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("SENTRIX_DEPLOYMENT_PROFILE", "mac")
    settings = Settings()
    assert settings.deployment_profile == "mac"
    assert settings.embedding_scheme == "legacy"
    assert settings.agent_wall_time_s == 600.0
    assert settings.chat_timeout_s == 180.0


def test_explicit_env_overrides_the_profile(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("SENTRIX_DEPLOYMENT_PROFILE", "mac")
    monkeypatch.setenv("SENTRIX_EMBEDDING_SCHEME", "wemm")
    monkeypatch.setenv("SENTRIX_AGENT_WALL_TIME_S", "240")
    monkeypatch.setenv("SENTRIX_CHAT_TIMEOUT_S", "90")
    settings = Settings()
    assert settings.embedding_scheme == "wemm"
    assert settings.agent_wall_time_s == 240.0
    assert settings.chat_timeout_s == 90.0


def test_unknown_profile_is_rejected(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("SENTRIX_DEPLOYMENT_PROFILE", "windows")
    with pytest.raises(ValueError):
        Settings()


def test_mac_profile_defaults_face_providers_to_cpu(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("SENTRIX_DEPLOYMENT_PROFILE", "mac")
    assert provider_chain(FACE_PROVIDERS_ENV) == ["CPUExecutionProvider"]


def test_explicit_face_providers_win_over_the_profile(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("SENTRIX_DEPLOYMENT_PROFILE", "mac")
    monkeypatch.setenv("FACE_PROVIDERS", "CUDAExecutionProvider,CPUExecutionProvider")
    assert provider_chain(FACE_PROVIDERS_ENV) == ["CUDAExecutionProvider", "CPUExecutionProvider"]

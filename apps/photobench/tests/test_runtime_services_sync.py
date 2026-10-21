from __future__ import annotations

import json
from pathlib import Path

from photobench.application.config.service import ConfigService
from photobench.infrastructure.config.files import JsonConfigStore


class _Secrets:
    def read_all(self) -> dict[str, str]:
        return {}

    def read(self, name: str) -> str | None:
        return None

    def write(self, name: str, value: str) -> None:
        return None


def test_save_preserves_service_topology_in_runtime_connection(tmp_path: Path) -> None:
    (tmp_path / "judge_providers.json").write_text(
        json.dumps(
            {
                "default_provider_id": "cloud",
                "providers": {"cloud": {"url": "https://judge.example/v1", "model": "judge"}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "vllm_targets.json").write_text(
        json.dumps(
            {
                "default_target_id": "local",
                "targets": {"local": {"model_base_url": "http://127.0.0.1:8100/v1"}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "runtime_connection.json").write_text(
        json.dumps(
            {
                "model_base_url": "http://127.0.0.1:8100/v1",
                "endpoint_model": "old",
                "embedding": {"mode": "split_text_image", "text_url": "http://127.0.0.1:8101"},
                "product": [{"id": "photobench", "ports": [8872]}],
            }
        ),
        encoding="utf-8",
    )
    service = ConfigService(JsonConfigStore(tmp_path), _Secrets())
    service.persist_runtime_connection(
        {
            "sentrix_url": "http://127.0.0.1:8291",
            "judge_url": "https://judge.example/v1",
            "judge_model": "judge",
            "model_base_url": "http://127.0.0.1:8103/v1",
            "endpoint_model": "qwen35-mixed-3p1",
            "judge_provider_id": "cloud",
        }
    )
    saved = json.loads((tmp_path / "runtime_connection.json").read_text(encoding="utf-8"))
    assert saved["model_base_url"] == "http://127.0.0.1:8103/v1"
    assert saved["endpoint_model"] == "qwen35-mixed-3p1"
    assert saved["embedding"]["text_url"] == "http://127.0.0.1:8101"
    assert saved["product"] == [{"id": "photobench", "ports": [8872]}]

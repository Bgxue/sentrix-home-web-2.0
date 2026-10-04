"""HTTP 契约：状态码与**响应体形状**都要与 1.0 一致（前端一行不动）。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from photobench.config import Settings
from photobench.main import create_app

PROVIDERS = {
    "default_provider_id": "cloud",
    "providers": {"cloud": {"url": "http://j/v1", "model": "m", "api_key": ""}},
}
# manager_url 留空：本切片的路由用例**不打模型端点**，profiles 会自己回 not_applicable。
TARGETS = {
    "default_target_id": "local",
    "targets": {"local": {"label": "本机", "manager_url": "", "model_base_url": "http://m:8100/v1"}},
}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    config_root = tmp_path / "config"
    config_root.mkdir()
    (config_root / "runtime_connection.json").write_text(
        json.dumps({"sentrix_url": "http://sentrix:8191", "judge_provider_id": "cloud"}), encoding="utf-8"
    )
    (config_root / "judge_providers.json").write_text(json.dumps(PROVIDERS), encoding="utf-8")
    (config_root / "vllm_targets.json").write_text(json.dumps(TARGETS), encoding="utf-8")

    results_root = tmp_path / "results"
    (results_root / "run-a").mkdir(parents=True)
    (results_root / "run-a" / "run.json").write_text(
        json.dumps(
            {
                "run_id": "run-a",
                "album_id": "album3",
                "status": "completed",
                "started_at": "2026-01-01T00:00:00+08:00",
                "qa_count": 1,
                "items": [{"qa_id": "q1", "question": "一加一", "judge": {"score": 2}}],
            }
        ),
        encoding="utf-8",
    )
    data_root = tmp_path / "datasets"
    data_root.mkdir()

    settings = Settings(config_root=config_root, results_root=results_root, data_root=data_root)
    app = create_app(settings, scan_in_background=False)
    container = app.state.container
    # 用例同步扫一次：断言不必与后台线程赛跑。
    container.run_index.refresh(container.run_store)
    with TestClient(app) as test_client:
        yield test_client


def test_get_runs_lists_the_scan_result(client: TestClient):
    response = client.get("/api/runs")
    assert response.status_code == 200
    body = response.json()
    assert [run["run_id"] for run in body["runs"]] == ["run-a"]
    assert body["total"] == 1
    assert body["scan"] == {"state": "ready", "scanned": 1, "total": 1, "error": None}


def test_a_bad_page_number_is_a_400_with_the_1_0_message(client: TestClient):
    """1.0 ``:7652`` 的 ``ValueError("page and page_size must be integers")`` → 400。"""
    response = client.get("/api/runs?page=x")
    assert response.status_code == 400
    assert response.json() == {"error": "page and page_size must be integers"}


def test_get_run_returns_the_state_without_items(client: TestClient):
    body = client.get("/api/runs/run-a").json()
    assert body["item_count"] == 1
    assert "items" not in body
    assert body["summary"]["answer_quality_mean"] == 2.0


def test_an_unknown_run_is_a_404_in_the_1_0_shape(client: TestClient):
    """1.0 ``:7757``：``{"error": str(KeyError)}`` —— ``str(KeyError("nope"))`` 带引号。"""
    response = client.get("/api/runs/nope")
    assert response.status_code == 404
    assert response.json() == {"error": "'nope'"}


def test_get_run_items_paginates(client: TestClient):
    body = client.get("/api/runs/run-a/items").json()
    assert body["total"] == 1
    assert body["items"][0]["qa_id"] == "q1"
    assert body["facets"]["task_types"] == []


def test_get_run_item_by_index(client: TestClient):
    assert client.get("/api/runs/run-a/items/0").json()["item"]["qa_id"] == "q1"
    assert client.get("/api/runs/run-a/items/9").status_code == 404


def test_reviews_route_is_read_only(client: TestClient):
    assert client.get("/api/runs/run-a/reviews").json() == {"reviews": {}}
    assert client.post("/api/runs/run-a/reviews", json={"reviews": {}}).status_code == 405


def test_no_write_routes_are_mounted_in_this_slice(client: TestClient):
    """写侧（建 run / 取消 / 重跑）属 S9b-2——本切片一条都不该有。"""
    assert client.post("/api/runs", json={}).status_code == 405
    assert client.post("/api/runs/run-a/cancel").status_code == 404
    assert client.delete("/api/runs/run-a").status_code == 405


def test_get_config_exposes_the_effective_values(client: TestClient):
    body = client.get("/api/config").json()
    assert body["default_sentrix_url"] == "http://sentrix:8191"
    assert body["default_vllm_target_id"] == "local"
    assert body["default_judge_provider_id"] == "cloud"
    assert body["judge_providers"][0]["id"] == "cloud"
    assert body["runtime_config_file"].endswith("runtime_connection.json")
    assert body["cloud_model_profile"]["id"] == "big_model"
    # 三项 judge 域字段刻意没搬（见路由模块的注释），这里钉住「没搬」这件事。
    assert "judge_prompt" not in body and "custom_judge_prompt" not in body


def test_post_config_persists_and_answers_in_the_1_0_shape(client: TestClient):
    response = client.post(
        "/api/config",
        json={
            "sentrix_url": "http://new:8191",
            "judge_url": "http://j2/v1",
            "judge_model": "m2",
            "judge_provider_id": "cloud",
            "model_base_url": "http://m2:8100",
        },
    )
    assert response.status_code == 200
    assert response.json()["saved"] is True
    assert response.json()["runtime_config"]["sentrix_url"] == "http://new:8191"
    assert client.get("/api/config").json()["default_sentrix_url"] == "http://new:8191"


def test_post_config_rejects_an_unknown_provider_with_a_400(client: TestClient):
    response = client.post("/api/config", json={"judge_model": "m", "judge_provider_id": "nope"})
    assert response.status_code == 400
    assert "unknown judge provider" in response.json()["error"]


def test_post_config_with_a_broken_body_is_treated_as_empty(client: TestClient):
    """1.0 ``_payload``（``:7523``）：坏 JSON 当空对象，然后落到字段校验上。"""
    response = client.post("/api/config", content=b"{ not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    # 空对象落到第一道校验上（1.0 的校验顺序：sentrix_url 先于 judge_model）。
    assert response.json()["error"] == "Sentrix URL must be a host:port or HTTP(S) URL"


def test_current_model_get_says_use_post(client: TestClient):
    """1.0 ``:7530``。"""
    response = client.get("/api/current-model")
    assert response.status_code == 405
    assert response.json() == {"error": "current-model requires POST"}


def test_profiles_without_a_manager_is_not_an_error(client: TestClient):
    """Manager 没配是 ``status: not_applicable``，不是 4xx——1.0 ``:5069``。"""
    response = client.post("/api/profiles", json={"vllm_target_id": "local"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_applicable"
    assert body["target_id"] == "local"

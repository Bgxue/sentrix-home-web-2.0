"""``/api/v1/timeline``、``/api/v1/events/{id}``、``/api/v1/imports`` 的契约测试。

只验 HTTP 映射、状态码与分页边界；链路的正确性在 integration（``test_read_timeline``
/ ``test_import_pipeline``），真照片 + 真模型的验收在 tests/e2e。
"""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image


def jpeg_bytes(size=(48, 32), color="blue") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def album(tmp_path: Path, *, count: int = 1) -> Path:
    root = tmp_path / "album"
    root.mkdir(exist_ok=True)
    for index in range(count):
        (root / f"IMG_{index:04d}.jpg").write_bytes(jpeg_bytes(color=(index * 40 % 255, 10, 10)))
    return root


def photos(client: TestClient, scope_id: str = "album1") -> list[dict]:
    body = client.get("/api/v1/timeline", params={"scope_id": scope_id}).json()
    return [photo for day in body["days"] for event in day["events"] for photo in event["photos"]]


def test_timeline_is_empty_before_any_import(client: TestClient):
    response = client.get("/api/v1/timeline", params={"scope_id": "album1"})

    assert response.status_code == 200
    body = response.json()
    assert body["scope_id"] == "album1"
    assert body["total"] == 0
    assert body["days"] == []


def test_import_batch_then_timeline_has_every_photo(client: TestClient, tmp_path: Path):
    imported = client.post(
        "/api/v1/imports",
        json={"directory": str(album(tmp_path, count=2)), "scope_id": "album1"},
    )

    assert imported.status_code == 201
    body = imported.json()
    assert body["imported"] == 2
    assert body["failures"] == []

    listed = photos(client)
    assert sorted(item["file_name"] for item in listed) == ["IMG_0000.jpg", "IMG_0001.jpg"]
    # 地点展示是后端算好的字符串：没有 GPS 时是空串，不是「未知」。
    assert {item["place"] for item in listed} == {""}


def test_import_batch_reports_each_file_once_across_reruns(client: TestClient, tmp_path: Path):
    root = str(album(tmp_path))
    client.post("/api/v1/imports", json={"directory": root, "scope_id": "album1"})

    again = client.post("/api/v1/imports", json={"directory": root, "scope_id": "album1"})

    assert again.json()["imported"] == 0
    assert again.json()["deduplicated"] == 1


def test_import_batch_rejects_a_missing_directory(client: TestClient, tmp_path: Path):
    response = client.post(
        "/api/v1/imports",
        json={"directory": str(tmp_path / "gone"), "scope_id": "album1"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "directory not found"


def test_import_batch_rejects_an_empty_directory_field(client: TestClient):
    response = client.post("/api/v1/imports", json={"directory": "", "scope_id": "album1"})

    assert response.status_code == 422


def test_import_batch_requires_a_directory_field(client: TestClient):
    assert client.post("/api/v1/imports", json={"scope_id": "album1"}).status_code == 422


def test_event_detail_404_for_an_unknown_event(client: TestClient):
    response = client.get("/api/v1/events/evt_missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "event not found"


def test_event_detail_returns_photos_after_an_import(client: TestClient, tmp_path: Path):
    client.post("/api/v1/imports", json={"directory": str(album(tmp_path)), "scope_id": "album1"})
    event_id = client.get("/api/v1/timeline", params={"scope_id": "album1"}).json()["days"][0]["events"][0][
        "id"
    ]

    response = client.get(f"/api/v1/events/{event_id}")

    assert response.status_code == 200
    assert response.json()["id"] == event_id
    assert len(response.json()["photos"]) == 1


def test_timeline_limit_and_offset_bounds(client: TestClient):
    assert client.get("/api/v1/timeline", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/timeline", params={"limit": 500}).status_code == 422
    assert client.get("/api/v1/timeline", params={"offset": -1}).status_code == 422


def test_place_filter_is_applied_by_the_backend(client: TestClient, tmp_path: Path):
    """地点筛选在后端：前端只把用户输入传过来，不做任何地名计算（计划 Task 61）。

    契约测试里的照片没有 GPS，所以这里先把事件的地点补上（走仓储，不走 HTTP），
    再断言筛选真的按 ``place`` / ``admin_district`` 过滤。
    """
    client.post("/api/v1/imports", json={"directory": str(album(tmp_path)), "scope_id": "album1"})
    container = client.app.state.container  # type: ignore[attr-defined]
    event = container.event_repository.list_by_scope("album1", limit=10)[0]
    container.event_repository.fold_observation(
        event.id,
        time_start=event.time_start,
        time_end=event.time_end,
        place="浙江省杭州市萧山区",
        admin_district="萧山区",
        activity="",
        summary="",
        confidence=event.confidence,
        aggregation_score=0.0,
        aggregation_breakdown={},
    )

    hit = client.get("/api/v1/timeline", params={"scope_id": "album1", "place": "萧山"})
    miss = client.get("/api/v1/timeline", params={"scope_id": "album1", "place": "清迈"})

    assert hit.status_code == 200 and miss.status_code == 200
    assert [item["id"] for day in hit.json()["days"] for item in day["events"]] == [event.id]
    assert miss.json()["days"] == []
    assert miss.json()["total"] == 0


def test_the_timeline_without_a_scope_covers_every_album(client: TestClient, tmp_path: Path) -> None:
    """**空 scope = 全部相册**，与资料库那条同一形状，但根因多一层。

    `read_timeline` 里原有一句 `target_scope = scope_id or DEFAULT_SCOPE_ID`，把空值
    **强行兜底**成 `home-default` 再去过滤——而那个空间在 2.0 的空库里根本不存在。
    表现是「时间线一个事件都没有」，而事件其实好好地在一个具体相册里。

    1.0 `db.py:1514` 的 `if scope_id:` 才是这个语义：不传 = 不按空间过滤。
    """
    client.post("/api/v1/imports", json={"directory": str(album(tmp_path, count=3)), "scope_id": "album1"})

    everything = client.get("/api/v1/timeline", params={"scope_id": ""}).json()
    one_album = client.get("/api/v1/timeline", params={"scope_id": "album1"}).json()

    assert everything["total"] == one_album["total"] > 0, "空 scope 必须覆盖到具体相册里的事件"
    assert everything["scope_id"] == "", "回给调用方的是用户请求的范围，不是内部的兜底值"


def test_import_progress_is_queryable_during_batch_finalization(client, tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from sentrix.api.routes import timeline

    entered, release = threading.Event(), threading.Event()

    def blocked_insights(*args):
        entered.set()
        assert release.wait(5)
        return None

    monkeypatch.setattr(timeline, "_maybe_run_insights", blocked_insights)
    payload = {"directory": str(album(tmp_path)), "scope_id": "album1", "operation_id": "tracked"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, "/api/v1/imports", json=payload)
        try:
            assert entered.wait(5)
            response = client.get("/api/v1/imports/tracked/progress")
            assert response.status_code == 200
            fact = response.json()
            assert (fact["completed"], fact["total"], fact["imported"]) == (1, 1, 1)
            assert fact["status"] == "running" and fact["stage"] == "person_insights"
            assert fact["observed_at"] >= fact["updated_at"]
            assert client.post("/api/v1/imports", json=payload).status_code == 409
        finally:
            release.set()
        assert future.result(timeout=5).status_code == 201
    final = client.get("/api/v1/imports/tracked/progress").json()
    assert final["status"] == "completed" and final["sequence"] > fact["sequence"]
    assert client.get("/api/v1/imports/unknown/progress").status_code == 404


def test_tracked_import_failure_keeps_a_truthful_terminal(client, tmp_path):
    response = client.post(
        "/api/v1/imports",
        json={
            "directory": str(tmp_path / "missing"),
            "operation_id": "bad-directory",
        },
    )
    assert response.status_code == 400
    fact = client.get("/api/v1/imports/bad-directory/progress").json()
    assert fact["status"] == "failed" and fact["total"] is None
    assert fact["completed"] == 0 and "400" in fact["error"]

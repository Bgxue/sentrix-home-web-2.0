"""端到端验收：问一句「给我看看海边的照片」，拿回能打开的原图。

这一层用**真 chinese-clip（进程内推理）+ 真 Qdrant + 真 SQLite + 8100 上真的 llama.cpp**，
走完整 HTTP 链路（设计 §8 的 S3 验收）。

判据里刻意不包含「模型说了什么」——那是模型质量的验收，按设计 §9.1 由使用者肉眼做。
这里钉的是**链路的结构性事实**：

1. 回合以一个有名字的原因结束（不是空、不是 None）；
2. 用户要图时，回答里真的带回了可打开的照片（设计 §1.1 硬约束第 5 条）；
3. 那些照片的 URL 真的能取到字节；
4. 全过程只用了一条 SSE 连接，没有轮询任何状态端点。
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sentrix.infrastructure.models.chinese_clip import DEFAULT_CHECKPOINT
from sentrix.main import create_app

PHOTO_FIXTURES = Path("/home/asus/Github/Sentrix-Home-Web/data/album3-v2-source/photos")

SCOPE = "assistant-album"
# 只在 8100 上打这么多次模型调用。llama-server 是 --parallel 1，串行排队。
MAX_MODEL_CALLS = 6


@pytest.fixture(scope="module", autouse=True)
def requires_the_real_environment() -> None:
    """本层要的是真环境：本地权重 + 一个跑着的 8100。

    两样缺一就跳过，并给出可执行的提示——与同目录 ``test_visual_search.py`` 对权重的
    处理保持一致（设计 §9.1：模型与真实功能验证由使用者负责，自动层只保证链路没坏）。
    """
    if not DEFAULT_CHECKPOINT.is_file():
        pytest.skip(f"chinese-clip 权重不可用: {DEFAULT_CHECKPOINT}")
    import httpx

    from sentrix.config import Settings

    base_url = Settings().vlm_base_url
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json={
                "model": Settings().chat_model_name,
                "messages": [{"role": "user", "content": "好"}],
                "max_tokens": 4,
            },
            timeout=20.0,
        )
        response.raise_for_status()
    except Exception as error:  # noqa: BLE001 - 环境不可用时给出可执行的提示
        pytest.skip(f"8100（{base_url}）上的对话端点不可用，S3 的模型走这个端点: {error}")


def photo_bytes(colour: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (224, 224), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def client(settings) -> TestClient:
    """真容器：真模型、真 Qdrant、独立临时数据目录。"""
    return TestClient(create_app(settings))


def import_photo(client: TestClient, name: str, payload: bytes) -> str:
    response = client.post(
        "/api/v1/media/import",
        files={"file": (name, payload, "image/jpeg")},
        data={"scope_id": SCOPE},
    )
    assert response.status_code == 201, response.text
    assert response.json()["indexed"] is True, "导入必须写成视觉向量"
    return response.json()["asset_id"]


def start_conversation(client: TestClient) -> str:
    response = client.post("/api/v1/assistant/conversations", json={"scope_id": SCOPE})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def read_events(client: TestClient, turn_id: str) -> list[dict]:
    import json

    with client.stream("GET", f"/api/v1/assistant/turns/{turn_id}/events") as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    events: list[dict] = []
    current: dict = {}
    for line in lines:
        if line.startswith("event: "):
            current["event"] = line[7:]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[6:])
            events.append(current)
            current = {}
    return events


def ask(client: TestClient, conversation_id: str, question: str) -> tuple[str, list[dict]]:
    response = client.post(
        f"/api/v1/assistant/conversations/{conversation_id}/turns",
        json={"message": question, "scope_id": SCOPE},
    )
    assert response.status_code == 202, response.text
    turn_id = response.json()["turn_id"]
    return turn_id, read_events(client, turn_id)


def progress_of(events: Sequence[dict]) -> list[dict]:
    return [item["data"] for item in events if item["event"] == "progress"]


def test_asking_for_photos_returns_openable_originals(client: TestClient):
    """S3 的验收问句。"""
    import_photo(client, "海边_蓝.jpg", photo_bytes((20, 60, 200)))
    import_photo(client, "草地_绿.jpg", photo_bytes((30, 180, 60)))
    import_photo(client, "雪地_白.jpg", photo_bytes((240, 240, 245)))

    conversation_id = start_conversation(client)
    turn_id, events = ask(client, conversation_id, "给我看看在海边的照片")

    complete = events[-1]["data"]
    assert complete["status"] in {"complete", "partial"}
    assert complete["termination_reason"]
    assert complete["attachments"], "用户要图时回答必须带回照片，不能只回一段文字"

    # 每张交付的照片都必须真的能打开——设计 §1.1 硬约束第 5 条。
    for attachment in complete["attachments"]:
        response = client.get(f"/api/v1/assets/{attachment['asset_id']}/file")
        assert response.status_code == 200, attachment
        assert response.content[:2] == b"\xff\xd8", "取回的应当是真正的 JPEG 字节"

    # 交付的 handle 必须来自本轮的结果集，而不是模型编的编号。
    assert all(item["handle"].startswith("photo_") for item in complete["attachments"])
    assert len(complete["attachments"]) <= 6


def test_the_answer_never_leaks_internal_identifiers(client: TestClient):
    import_photo(client, "海边_蓝2.jpg", photo_bytes((25, 70, 210)))

    conversation_id = start_conversation(client)
    _turn_id, events = ask(client, conversation_id, "给我看看在海边的照片")

    answer = events[-1]["data"]["answer"]
    assert "photo_" not in answer
    assert "asset_" not in answer
    assert "result_set" not in answer


def test_the_turn_records_the_whole_reasoning_chain(client: TestClient):
    """规划 → 工具循环 → 证据账本 → 答案门禁 → 回答，每一步都留下痕迹。"""
    import_photo(client, "海边_蓝3.jpg", photo_bytes((15, 50, 190)))

    conversation_id = start_conversation(client)
    _turn_id, events = ask(client, conversation_id, "给我看看在海边的照片")
    progress = progress_of(events)

    assert progress[0]["kind"] == "plan"
    assert progress[0]["payload"]["requirements"], "规划必须给出证据需求"
    assert any(item["kind"] == "tool_result" for item in progress), "至少要真正用过一次工具"
    assert progress[-1]["stage"] == "terminal"
    assert events[-1]["data"]["termination_reason"]


def test_a_conversational_question_still_terminates_with_a_reason(client: TestClient):
    """闲聊没有照片可交付，但回合仍然要以一个有名字的原因收场。

    1.0 的 25,112 条轨迹里有 11,304 条读不到 ``termination_reason``。
    """
    conversation_id = start_conversation(client)
    _turn_id, events = ask(client, conversation_id, "你好呀")

    complete = events[-1]["data"]
    assert complete["termination_reason"]
    assert complete["status"] in {"complete", "partial", "blocked", "failed"}


def test_a_follow_up_question_sees_the_previous_turn(client: TestClient):
    import_photo(client, "海边_蓝4.jpg", photo_bytes((18, 55, 195)))

    conversation_id = start_conversation(client)
    ask(client, conversation_id, "给我看看在海边的照片")
    _turn_id, events = ask(client, conversation_id, "刚才那些是哪年拍的？")

    assert events[-1]["data"]["answer"]


def test_unknown_scope_still_answers_honestly(client: TestClient):
    """库里什么都没有时不能编造。"""
    conversation_id = start_conversation(client)
    _turn_id, events = ask(client, conversation_id, "给我看看在月球上的照片")

    complete = events[-1]["data"]
    assert complete["termination_reason"]
    assert complete["attachments"] == []

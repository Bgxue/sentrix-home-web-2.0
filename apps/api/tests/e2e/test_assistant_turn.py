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
from conftest import skip_or_fail_on_load_error
from fastapi.testclient import TestClient
from PIL import Image

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

    视觉权重那一条**不在这里判**：本夹具拿不到 app 实例，而另建探针会让探针自己占住
    显存、app 再建第二个就 OOM（实测）。那一条由 ``require_visual_embedder`` 在真正
    用到嵌入器的地方判，判据取 app 自己的实例。
    """
    import httpx

    from sentrix.config import Settings
    from sentrix.infrastructure.assistant.model_endpoint import ServedModel

    base_url = Settings().vlm_base_url
    try:
        # 名字**问端点要**，不写常量：这个守卫的职责就是「这层能不能跑」，写死名字的话
        # 换过后端之后它会自信地打一个 404，然后把这层 e2e 判成「环境不可用」跳过——
        # 守卫与生产走同一条路（ServedModel），才验得到真东西。
        model_name = ServedModel(base_url).current()
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json={
                "model": model_name,
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


def require_visual_embedder(client: TestClient) -> None:
    """视觉嵌入器加载不到就跳过，而不是让断言去背这个锅。

    判据取 **app 自己的**那个嵌入器（``container.visual_embedder``）——既真实，又不
    额外占显存。两个坑都踩过，都记在这里：

    - 原先的守卫查的是**代理判据**（``DEFAULT_CHECKPOINT.is_file()``）：权重文件在、
      显存不够时不跳过，用例在运行期报「导入必须写成视觉向量」，读起来像实现坏了
      （2026-09-26 实测：8100 的 vLLM 占住 24 GB 里的 22 GB，CLIP 在 ``model.to(device)``
      处 OOM）；
    - 改成在这里另建一个 ``ChineseClipVisualEmbedder()`` 探针**反而更糟**：探针自己
      占住显存，app 再建第二个就 OOM，于是守卫不跳、测试照红（实测仍有 3 条红）。
    """
    embedder = client.app.state.container.visual_embedder
    if not embedder.available:
        skip_or_fail_on_load_error(embedder.load_error, what="chinese-clip")


def import_photo(client: TestClient, name: str, payload: bytes) -> str:
    # 守卫必须在 POST **之前**：``ChineseClipVisualEmbedder._load()`` 失败时**不缓存**
    # 失败，所以「import 那一刻失败、紧接着重试却成功」是可能的。放在后面查会读到真值，
    # 断言却拿着过期的 ``indexed: False`` 去红（实测 1 条）。放在前面还有第二个好处：
    # 这一次 ``available`` 会把模型真的加载并缓存住，随后这次 import 看到的是同一个状态。
    require_visual_embedder(client)
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
    # 刻意不传 scope：回合的 scope 必须来自会话（``start_conversation`` 建的那个 SCOPE）。
    response = client.post(
        f"/api/v1/assistant/conversations/{conversation_id}/turns",
        json={"message": question},
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

"""bge-m3 文本嵌入：HTTP 客户端（对应 1.0 ``backend/embeddings/bge_text.py``）。

**模型不在这个进程里。** bge-m3 跑在一个独立的 venv（``.venv-text``）后面，由一个小小的
HTTP sidecar 提供（1.0 仓库的 ``scripts/maintenance/text_embedder_sidecar.py``，
监听 8101）。这条与 8100 上的 VLM 是同一条口径：**2.0 不管理模型进程的生命周期**
（设计 §2.1），只连它。本进程因此不 import ``torch`` / ``sentence-transformers``。

**失败要降级，不能抛、不能挂。** 1.0 的做法是一个轻量熔断器：连续 5 次失败就断开
10 秒，期间 ``embed_query`` 直接返回空表；健康检查带 TTL，不是每次调用都打一次。
返回空表让上层按「这台机器没有文本能力」降级——**抛出去会让每一次查询都挂在这个
可选依赖上**，而文本通道是增强，不是检索的前置条件。

**``available`` 与 ``load_error`` 的分工照 ``ChineseClipVisualEmbedder``**：能不能用
是一个可问的问题，为什么不能用是另一个；合成一个布尔值，排查时只剩「不能用」三个字。
"""

from __future__ import annotations

import threading
import time

import httpx

from sentrix.domain.retrieval import scheme

# 维度与模型名从 ``scheme`` 取，不在这里再写一遍——那正是 ``scheme.py`` 存在的理由
# （它的模块注释：写入 / 查询 / 重建索引三处唯一的来源）。1.0 ``bge_text.py:43``
# 把 1024 写在适配器里，是 1.0 的做法，不是这里该照抄的部分：写在两处之后，
# 「换模型要改几处」就变成看运气，而**维度不一致在检索侧不报错**，只是永远算不出相似度。
DIMENSION = scheme.TEXT_DIMENSION
DEFAULT_MODEL_ID = scheme.TEXT_MODEL

# 1.0 ``bge_text.py:30`` 的超时；sidecar 是本地往返，10 秒足够。
DEFAULT_TIMEOUT_S = 10.0
# 1.0 ``bge_text.py:35`` 的健康检查 TTL。
HEALTH_TTL_S = 10.0
# 1.0 ``bge_text.py:84``：连续失败到这个数就断开。
FAILURE_THRESHOLD = 5
# 1.0 ``bge_text.py:47`` 的断开时长。
CIRCUIT_OPEN_S = 10.0


class BgeM3TextEmbedder:
    """实现 ``domain.retrieval.ports.TextEmbedder``。"""

    def __init__(
        self,
        base_url: str,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_id = model_id
        self._timeout_s = timeout_s
        self._client = client or httpx.Client(timeout=timeout_s)
        # 熔断器与健康缓存都是进程内可变状态，而 uvicorn 的同步端点在线程池里跑。
        self._lock = threading.Lock()
        self._failures = 0
        self._tripped_at = 0.0
        self._health_checked_at = 0.0
        self._health_value = False
        self._error = ""

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimension(self) -> int:
        return DIMENSION

    @property
    def load_error(self) -> str:
        """最近一次失败的原因。**与 ``available`` 分开**：合成一个布尔值，
        「sidecar 没起」与「sidecar 起了但模型没加载」就分不开了。"""
        return self._error

    @property
    def available(self) -> bool:
        with self._lock:
            if self._tripped_at and time.monotonic() - self._tripped_at < CIRCUIT_OPEN_S:
                return False
            now = time.monotonic()
            if now - self._health_checked_at < HEALTH_TTL_S:
                return self._health_value
        try:
            response = self._client.get(f"{self._base_url}/health")
            healthy = response.status_code == 200
        except Exception:  # noqa: BLE001 - 探活失败就是不可用，不该让调用方处理异常
            healthy = False
        with self._lock:
            self._health_checked_at = time.monotonic()
            self._health_value = healthy
        return healthy

    def embed_query(self, text: str) -> list[float]:
        """一段文本 → 1024 维向量；不可用时返回空表（不抛）。

        用 ``POST /embed {"text": ...}``——1.0 ``bge_text.py:100`` 的请求形状。
        写成批量接口会逼 sidecar 与客户端各自决定「一批几条」，而 1.0 逐条调，
        换掉这个形状等于改了两侧的行为。
        """
        phrase = str(text or "").strip()
        if not phrase:
            return []
        with self._lock:
            if self._tripped_at and time.monotonic() - self._tripped_at < CIRCUIT_OPEN_S:
                return []
        try:
            response = self._client.post(f"{self._base_url}/embed", json={"text": phrase})
            response.raise_for_status()
            vector = response.json().get("vector") or []
            values = [float(item) for item in vector]
        except Exception as error:  # noqa: BLE001 - 见模块注释：降级，不抛
            self._record_failure(f"{type(error).__name__}: {error}")
            return []
        if not values:
            self._record_failure("sidecar 返回了空向量")
            return []
        self._record_success()
        return values

    def _record_failure(self, reason: str) -> None:
        with self._lock:
            self._failures += 1
            self._error = reason
            self._health_value = False
            self._health_checked_at = 0.0
            if self._failures >= FAILURE_THRESHOLD:
                self._tripped_at = time.monotonic()
                self._failures = 0

    def _record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._tripped_at = 0.0
            self._health_value = True
            self._health_checked_at = time.monotonic()
            self._error = ""

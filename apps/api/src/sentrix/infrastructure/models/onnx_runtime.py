"""人脸 ONNX session 的 provider 链、显存预算与 CUDA 运行库预载。

1.0 ``backend/onnx_runtime.py`` 全文（92 行）的移植，去掉一处**在 2.0 里被证伪**的做法：
1.0 在 ``model_clients.py:1649`` 用 ``os.environ["LD_LIBRARY_PATH"] = ...`` 把 pip 装的
NVIDIA 库目录告诉动态链接器——**动态链接器只在进程启动时读这个变量**，运行期再设对同一个
进程里的 ``dlopen`` 没有任何作用，它只对之后 fork 出来的子进程有效。见
``s5-deviations.md`` 第 56 条的实测：不预载时 ``CUDAExecutionProvider`` **静默退化成 CPU**
（``InferenceSession`` 不报错，正常返回结果，只有读 ``sess.get_providers()`` 才看得出来）。

provider 链为什么默认不写死 ``CPUExecutionProvider``：1.0 的注释记着实测（视觉编码
0.72 秒 → 21~36 秒），而这条退化在日志里看不出来。链尾永远带 CPU，所以没有 CUDA 的机器
仍能跑，只是慢——**慢是可以观测的，静默降级不是**。
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Iterator
from typing import Any

# 两个 provider 链分开：RetinaFace 与 buffalo_l 的显存预算是各算各的（1.0 同此）。
RETINAFACE_PROVIDERS_ENV = "RETINAFACE_PROVIDERS"
FACE_PROVIDERS_ENV = "FACE_PROVIDERS"

DEFAULT_PROVIDERS = "CUDAExecutionProvider,CPUExecutionProvider"

# 每个 ONNX session 的显存上限。1.0 取 256 MiB 是有理由的：一个人脸适配器可能同时持有
# 好几个 session（RetinaFace 检测 + buffalo_l 检测 + buffalo_l 识别），预算是**逐个**
# session 算的，所以单个不能定大。
DEFAULT_SESSION_LIMIT_MIB = 256

_preload_lock = threading.Lock()
_preloaded = False


def preload_cuda_runtime() -> bool:
    """把 pip 装的 CUDA 运行库预载进本进程，返回是否真的载了。

    走 torch 自己的加载器，而不是逐个 ``ctypes.CDLL``：这些库之间有依赖顺序
    （``libcublasLt`` → ``libcublas`` → …），torch 已经把这份顺序维护好了；而 torch 本来
    就是本仓库的硬依赖（视觉编码在同一进程里用它），不是为这件事新引入的重量级依赖。

    调一次就够；失败返回 ``False`` 而不是抛错——没装 CUDA 运行库的机器上 provider 链会退到
    CPU，那是**允许的运行形态**，不是异常。调用方靠 ``session_providers()`` 读实际生效的
    provider 来观测它有没有退化。
    """
    global _preloaded
    if _preloaded:
        return True
    with _preload_lock:
        if _preloaded:
            return True
        try:
            import torch  # noqa: F401
        except Exception:
            return False
        _preloaded = True
        return True


def provider_chain(provider_env: str) -> list[str]:
    """环境变量给出的 provider 链；没设就用默认链。空项与空白项被清掉。"""
    raw = (os.getenv(provider_env) or "").strip() or DEFAULT_PROVIDERS
    return [item.strip() for item in raw.split(",") if item.strip()]


def _cuda_requested(provider_env: str) -> bool:
    return "CUDAExecutionProvider" in provider_chain(provider_env)


def _session_limit_mib(provider_env: str) -> int:
    limit_env = (
        "SENTRIX_RETINAFACE_GPU_SESSION_LIMIT_MIB"
        if provider_env == RETINAFACE_PROVIDERS_ENV
        else "SENTRIX_FACE_GPU_SESSION_LIMIT_MIB"
    )
    try:
        return max(128, int(os.getenv(limit_env, str(DEFAULT_SESSION_LIMIT_MIB))))
    except ValueError:
        return DEFAULT_SESSION_LIMIT_MIB


def face_onnx_providers(provider_env: str) -> list[Any]:
    """``onnxruntime.InferenceSession`` 的 ``providers`` 实参。

    CUDA 那一项带着 ``provider_options``（元组形态），其余是裸字符串——这是
    onnxruntime 的入参约定，不是本仓库的发明。
    """
    requested = provider_chain(provider_env)
    if not _cuda_requested(provider_env):
        return list(requested)
    preload_cuda_runtime()
    options = {
        "device_id": int(os.getenv("SENTRIX_FACE_GPU_DEVICE_ID", "0")),
        "gpu_mem_limit": _session_limit_mib(provider_env) * 1024 * 1024,
        "arena_extend_strategy": os.getenv("SENTRIX_FACE_GPU_ARENA_EXTEND_STRATEGY", "kSameAsRequested"),
        "cudnn_conv_algo_search": "HEURISTIC",
        "cudnn_conv_use_max_workspace": "0",
    }
    return [(item, dict(options)) if item == "CUDAExecutionProvider" else item for item in requested]


def face_onnx_provider_options(provider_env: str) -> list[dict[str, Any]]:
    """与 ``face_onnx_providers`` 同序的 options 列表。

    insightface 的 ``FaceAnalysis`` 收的是 ``providers`` + ``provider_options`` **两个平行
    列表**，不是元组——同一个预算要从同一个地方算出来，所以由上面那个函数派生。
    """
    options: list[dict[str, Any]] = []
    for provider in face_onnx_providers(provider_env):
        options.append(dict(provider[1]) if isinstance(provider, tuple) else {})
    return options


def session_providers(session: Any) -> list[str]:
    """一个已建 session **实际**拿到的 provider 链。

    这是唯一能证明 CUDA 没退化的观测点：``get_available_providers()`` 只说明 EP 被编译
    进去了，``InferenceSession`` 在 EP 加载失败时既不抛错也不换 provider 名。
    """
    return [str(item) for item in session.get_providers()]


_GATE = threading.BoundedSemaphore(max(1, int(os.getenv("SENTRIX_FACE_GPU_MAX_CONCURRENCY", "1"))))


@contextlib.contextmanager
def face_gpu_inference_gate() -> Iterator[None]:
    """把同时进行的人脸 GPU 推理限流。

    默认 1：8091 生产设 4（1.0 的部署实况），2.0 沿用同一个环境变量名，所以那一份配置
    不用改就能搬过来。限的是**同时**，不是总量——ASR 与 VLM 在别的线程里跑，人脸这一路
    不能把显存全占掉。
    """
    if _cuda_requested(RETINAFACE_PROVIDERS_ENV) or _cuda_requested(FACE_PROVIDERS_ENV):
        _GATE.acquire()
        try:
            yield
        finally:
            _GATE.release()
    else:
        yield

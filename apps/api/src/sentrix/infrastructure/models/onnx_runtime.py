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

**但「慢是可以观测的」这句话被实测推翻了三次**（第 56 条那次退化的表现是一句话都没有；
第 111 条那次是 ``make ci`` 从 21 分钟涨到 55 分钟、而外衣是「卡住」）。所以 2.0 不再只
靠观测：**每个 session 建完都要过一遍 ``require_requested_providers``——请求了 CUDA 而
实际拿到 CPU 的直接抛错拒启**。想在这台机器上纯 CPU 跑人脸的，唯一的口子是**显式**把
``FACE_PROVIDERS`` / ``RETINAFACE_PROVIDERS`` 设成 ``CPUExecutionProvider``：那时压根没有
「请求了 CUDA」这回事，防线不参与，而不是让 session 悄悄退化。
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

# 两个 provider 名只在常量里写一次。下面那道防线的判据是「请求的链里有 CUDA、实际链里没有」，
# 判据两侧各写一次字符串字面量，改一处漏一处就会让防线静默失效——正是它要防的那类失败。
CUDA_EXECUTION_PROVIDER = "CUDAExecutionProvider"
CPU_EXECUTION_PROVIDER = "CPUExecutionProvider"

DEFAULT_PROVIDERS = f"{CUDA_EXECUTION_PROVIDER},{CPU_EXECUTION_PROVIDER}"


def _default_providers() -> str:
    """没显式设 ``FACE_PROVIDERS`` / ``RETINAFACE_PROVIDERS`` 时的默认链。

    Mac 没有 CUDA；默认链里带 CUDA 会让人脸防线按设计拒启整套导入。profile=mac
    时默认 CPU；显式 env 仍然优先（例如一台有 eGPU 的 Mac 想用 CUDA）。
    """
    profile = os.environ.get("SENTRIX_DEPLOYMENT_PROFILE", "server").strip().lower()
    return CPU_EXECUTION_PROVIDER if profile == "mac" else DEFAULT_PROVIDERS

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

    调一次就够；失败返回 ``False`` 而不是抛错——**这里**不该抛：预载失败与「没有 CUDA」是
    同一件事，而「没有 CUDA」是不是允许的运行形态，由调用方请求哪条 provider 链决定。
    判据统一在 ``require_requested_providers``：请求了 CUDA 却退化成 CPU 才算错误，而
    「压根没请求 CUDA」的纯 CPU 部署是合法的。
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
    raw = (os.getenv(provider_env) or "").strip() or _default_providers()
    return [item.strip() for item in raw.split(",") if item.strip()]


def _cuda_requested(provider_env: str) -> bool:
    return CUDA_EXECUTION_PROVIDER in provider_chain(provider_env)


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
    return [(item, dict(options)) if item == CUDA_EXECUTION_PROVIDER else item for item in requested]


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


class CudaProviderMissingError(RuntimeError):
    """请求了 CUDA 而 session 实际落在 CPU 上——依赖装错了，不是「这台机器没 GPU」。

    单独一个类型而不是裸 ``RuntimeError``：人脸链路上有六处 ``except Exception``，它们存在
    的理由是「人脸是可选能力，不该让整条导入链失败」。**这一条不在那个理由里**——它说明
    装的东西不对，而装错的后果是整条链路慢 2.6 倍、外表像卡住（第 111 条）。那些裸
    ``except`` 必须显式放过这一个类型，否则这道防线会被它们吞掉，与没加一模一样。
    """


def require_requested_providers(session: Any, provider_env: str, *, label: str) -> list[str]:
    """请求了 CUDA 而 session 实际拿到 CPU → 抛错拒启。返回实际生效的 provider 链。

    **判据只能是 ``session.get_providers()``。** ``get_available_providers()`` 只说明 EP 被
    编译进来了，两者在退化时给出相反的答案——这一族坑已经咬过三次：① ``get_available_providers()``
    里照样有 CUDA；② insightface 的 ``providers`` 传元组被它降级成 CPU（一行警告，然后整条
    链路在 CPU 上跑）；③ 第 111 条那次 ``onnxruntime`` 被依赖安装覆盖成 CPU 版。

    **逃生阀就是 ``provider_env`` 自己**，没有第二个开关：把它显式设成
    ``CPUExecutionProvider`` 表示「这台机器就只在 CPU 上跑人脸」，那时没有「请求了 CUDA」
    这回事，这道防线不参与。**默认路径（不设或设成默认链）必须是报错**——静默退化正是要
    防的东西，所以不能给它留一条「打了日志继续跑」的岔路。
    """
    actual = session_providers(session)
    if CUDA_EXECUTION_PROVIDER in provider_chain(provider_env) and CUDA_EXECUTION_PROVIDER not in actual:
        raise CudaProviderMissingError(_degraded_message(label, provider_env, actual))
    return actual


def _degraded_message(label: str, provider_env: str, actual: list[str]) -> str:
    """退化时的报错正文。**必须能照着修**——只说「CUDA 没生效」的报错，下一个人还得再查一遍。"""
    return (
        f"{label} 请求了 {CUDA_EXECUTION_PROVIDER}，实际拿到的 provider 链是 {actual}。\n"
        f"onnxruntime 在 EP 加载失败时既不抛错也不换 provider 名，只是静静地在 CPU 上跑"
        f"（第 56 条实测），所以这里从 session 自己读实际链路并直接拒启。\n"
        f"怎么修：\n"
        f"  1. 装上 GPU 版，**按同一个版本、且最后装**——两个发行包共用同一个 ``onnxruntime``\n"
        f"     命名空间，先装的那个会被后装的整份覆盖：\n"
        f"       pip install --force-reinstall --no-deps onnxruntime-gpu=={_onnxruntime_version()}\n"
        f'  2. 回到这里重试。``python -c "import onnxruntime;'
        f' print(onnxruntime.get_available_providers())"`` 也可以先看一眼，但**它不足以证明\n'
        f"     修好了**：它只说明 EP 被编译进来了，本防线读的是 session 的实际链路。\n"
        f"  3. 如果这台机器本来就只在 CPU 上跑人脸，**显式**声明它，而不是让 session 悄悄退化：\n"
        f"       {provider_env}={CPU_EXECUTION_PROVIDER}\n"
        f"     RetinaFace 与 buffalo_l 各有自己的变量，两条链路各设一次。"
    )


def _onnxruntime_version() -> str:
    """报错里要给出的版本号：正在跑的这一份 onnxruntime 自己的版本。

    取 ``__version__`` 而不是按发行包名查 metadata：两个发行包共用命名空间，按名字查会查到
    与当前实际加载的那份无关的一个数，而修复命令要钉的正是**实际加载的那份**。
    """
    try:
        import onnxruntime

        return str(onnxruntime.__version__)
    except Exception:
        return "<版本>"


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

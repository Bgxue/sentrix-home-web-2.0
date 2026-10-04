"""运行时提供者的**选择**逻辑：1.0 ``select_runtime_providers``（``benchmark_orchestrator.py:176``）
连同它的三个前置函数（``local_lan_ip`` ``:121`` / ``_is_orin_telemetry_host`` ``:134`` /
``resolve_runtime_framework`` ``:147`` / ``detect_runtime_framework`` ``:160``）。

**为什么单独一个模块，而不是并进 ``providers.py``。** 它要同时 import
``providers``、``apple_telemetry``、``jetson_telemetry`` 三份实现——放进 ``providers.py``
会造出一个环（provider 定义 → 选择器 → provider 定义）。它本身也不提供任何 provider，
只是**按主机形态挑一个**。

**它是 S9b-2 那条「真机遥测没有可用来源」的修复点。** S9b-2 的 container 只按
「有没有 manager_url」装配遥测（``ManagerTelemetryProvider`` / ``Unavailable``），
于是 153 上跑着的 llama-server（8100，没有 vLLM Manager）那一路根本没有被选中的
路径，采样器拿不到样本。这一段把四路补齐：

====================  ==========================================================
主机形态                选中的遥测
====================  ==========================================================
有 Manager 且端点在     ``ManagerTelemetryProvider``（``vllm_manager``）
本机
有 Manager 但端点不在   ``UnavailableTelemetryProvider``（管理器只管本机 vLLM）
本机
本机 Jetson + llama.cpp ``LocalJetsonLlamaCppTelemetryProvider``（``jetson_local_pss``）
本机 Apple Silicon      ``LocalAppleUnifiedTelemetryProvider``（``apple_unified``）
其余本机端点            ``LlamaCpp`` / ``Vllm`` / ``Ollama`` / ``HostNvidia``
                        （``host_nvidia_smi``）
====================  ==========================================================

**一条 1.0 的实测发现，登记在这里免得被误读成漏搬**：``OrinLlamaCppTelemetryProvider``
在 1.0 的 ``select_runtime_providers`` 里**没有任何分支构造它**——110 行的
``OrinLlamaCppTelemetryProvider`` 是从 ``backend.runtime_providers`` 里 import 进来
却在编排侧一次都没用过的死重量（与 S9a 报的 F401 是同一处）。「Orin」这个形态在
1.0 的选择逻辑里**只通过 ``_is_orin_telemetry_host`` 参与框架探测**（8100 端口的远端
主机被判成 llama.cpp），遥测本身仍落到 ``Unavailable``。这一段照 1.0 的分支照搬，
**不替 1.0 补一条它没有的分支**——补了就会让一台端点在 118 的部署突然开始跑 ssh 采样，
而那是一个没人验证过的行为变化。

**它与 ``providers.py`` 的工厂（``manager_lifecycle`` / ``manager_telemetry`` /
``create_runtime_providers``）的分工**：那两个只回答「有没有 Manager」，服务的是
``/api/profiles`` 这类**按请求**构造的查询；这里回答「这台主机是什么形态」，服务的是
**按 run** 的评测主链。两者都保留，是因为前者的空 URL 判 Unavailable 是 1.0
``create_runtime_providers`` 的原样，后者是 1.0 ``select_runtime_providers`` 的原样。
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from photobench.domain.runtime.ports import LifecycleProvider, TelemetryProvider
from photobench.infrastructure.runtime.apple_telemetry import (
    LocalAppleUnifiedTelemetryProvider,
    is_apple_silicon,
)
from photobench.infrastructure.runtime.jetson_telemetry import (
    LocalJetsonLlamaCppTelemetryProvider,
    is_jetson_host,
)
from photobench.infrastructure.runtime.providers import (
    HostNvidiaTelemetryProvider,
    LlamaCppTelemetryProvider,
    ManagerLifecycleProvider,
    ManagerTelemetryProvider,
    OllamaTelemetryProvider,
    UnavailableLifecycleProvider,
    UnavailableTelemetryProvider,
    VllmTelemetryProvider,
    normalize_openai_base_url,
)


@dataclass(frozen=True, slots=True)
class SelectedRuntime:
    """选择结果。1.0 返回的是 ``(lifecycle, telemetry, source)`` 三元组，这里给它一个名字。

    ``source`` 会落进 run.json（采样行与 ``telemetry_source``），读侧靠它区分
    「本机 NVML」与「Mac 的统一内存」——两者的数字口径不同，混在一起没有可比性。
    """

    lifecycle: LifecycleProvider
    telemetry: TelemetryProvider
    source: str


def local_lan_ip() -> str:
    """本机首选 LAN 地址，**不发应用流量**：UDP connect 只让内核选一条路由。

    用途是判「端点是不是本机」——1.0 本地跑时 ``vlm_base_url`` 写的是 ``0.0.0.0`` 或
    局域网 IP 而不是 ``127.0.0.1``，只认环回会把本机端点误判成远端。
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.168.0.1", 9))
        address = probe.getsockname()[0]
        return address if not address.startswith("127.") else "127.0.0.1"
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def is_orin_telemetry_host(host: str | None) -> bool:
    """该主机是否被显式配置为远端 Orin 遥测目标（``SENTRIX_ORIN_TELEMETRY_HOST``，
    形如 ``orin@<host>``）。

    1.0 原先在这里硬比字面量 ``192.168.0.118``；把某一台机器的地址编进主干，换台
    机器就得改代码。本机是 Jetson 时由 ``is_jetson_host()`` 这个能力信号判定，
    跨机场景由环境变量显式声明。
    """
    if not host:
        return False
    remote = os.getenv("SENTRIX_ORIN_TELEMETRY_HOST", "").strip()
    return bool(remote) and host == remote.split("@", 1)[-1]


def resolve_runtime_framework(value: str, model_base_url: str) -> str:
    """1.0 ``:147``：从显式取值 / 端点端口推运行时框架，推不出就是 ``generic``。"""
    framework = str(value or "").strip().lower().replace("_", ".")
    if framework and framework != "generic":
        return framework
    endpoint = urlparse(model_base_url)
    if endpoint.port == 11434:
        return "ollama"
    if endpoint.port == 8100 and (is_jetson_host() or is_orin_telemetry_host(endpoint.hostname)):
        return "llama.cpp"
    # 8100 也是 vLLM 的常用端口，一个任意服务不该被当成 llama.cpp。
    return "generic"


def _request_text(url: str, timeout: float = 3) -> str:
    """1.0 ``request_text``（``:793``）在 2.0 的最小面：只给框架探测用。

    ``verify=False`` 与 ``HttpxJsonClient`` 同口径（内网自签证书的部署依赖它）。
    """
    response = httpx.get(url, timeout=timeout, verify=False)
    response.raise_for_status()
    return response.text


def detect_runtime_framework(
    value: str,
    model_base_url: str,
    *,
    request_text: Callable[[str, float], str] | None = None,
) -> str:
    """1.0 ``:160``：端口推不出来时，问一下 ``/metrics`` 里是谁的指标。

    探测失败**不抛**：一个没有 ``/metrics`` 的端点仍然是合法端点，退回 ``generic``。
    ``request_text`` 可注入只为用例不碰网络。
    """
    fetch = request_text or _request_text
    framework = resolve_runtime_framework(value, model_base_url)
    if framework != "generic" or not model_base_url:
        return framework
    metrics_url = normalize_openai_base_url(model_base_url).removesuffix("/v1") + "/metrics"
    try:
        metrics = fetch(metrics_url, 3).lower()
    except Exception:  # noqa: BLE001 —— 探不到就是 generic，1.0 如此
        return framework
    if "vllm:" in metrics:
        return "vllm"
    if "llamacpp:" in metrics or "llama_" in metrics:
        return "llama.cpp"
    return framework


def select_runtime_providers(
    manager_url: str, endpoint_url: str, framework: str, *, cloud: bool = False
) -> SelectedRuntime:
    """1.0 ``:176`` 逐分支照搬。

    ``cloud=True`` 是 1.0 的云端大模型形态（2.0 没有云端模型这个概念，但分支保留——
    调用方传 False 时行为与 1.0 相同；留着的理由见 ``application/runs/suites.py``
    对「云端模型」那条待定项的登记）。
    """
    endpoint = urlparse(endpoint_url)
    host = (endpoint.hostname or "").strip().lower()
    local_hosts = {"127.0.0.1", "localhost", "::1", local_lan_ip()}
    is_local_endpoint = host in local_hosts
    # Manager 只对**被管的 vLLM**有权威性。外部 OpenAI 兼容端点（llama.cpp / Ollama）
    # 的界面里可能还留着一条过期的 Manager 设置，**绝不能让那条设置盖过端点主机判据**。
    if manager_url and framework in {"generic", "vllm", "vllm_manager"}:
        if not is_local_endpoint:
            return SelectedRuntime(
                ManagerLifecycleProvider(manager_url), UnavailableTelemetryProvider(), "unavailable"
            )
        return SelectedRuntime(
            ManagerLifecycleProvider(manager_url), ManagerTelemetryProvider(manager_url), "vllm_manager"
        )
    lifecycle: LifecycleProvider = UnavailableLifecycleProvider()
    jetson = is_jetson_host()
    if not cloud and framework in {"llama.cpp", "llamacpp"} and jetson and host in local_hosts:
        return SelectedRuntime(
            lifecycle, LocalJetsonLlamaCppTelemetryProvider(endpoint_url=endpoint_url), "jetson_local_pss"
        )
    if not cloud and is_apple_silicon() and is_local_endpoint:
        return SelectedRuntime(
            lifecycle, LocalAppleUnifiedTelemetryProvider(endpoint_url=endpoint_url), "apple_unified"
        )
    if not cloud and not jetson and host in local_hosts:
        telemetry_class = {
            "llama.cpp": LlamaCppTelemetryProvider,
            "llamacpp": LlamaCppTelemetryProvider,
            "ollama": OllamaTelemetryProvider,
            "vllm": VllmTelemetryProvider,
        }.get(framework, HostNvidiaTelemetryProvider)
        return SelectedRuntime(lifecycle, telemetry_class(endpoint_url=endpoint_url), "host_nvidia_smi")
    return SelectedRuntime(lifecycle, UnavailableTelemetryProvider(), "unavailable")


__all__ = [
    "SelectedRuntime",
    "detect_runtime_framework",
    "is_orin_telemetry_host",
    "local_lan_ip",
    "resolve_runtime_framework",
    "select_runtime_providers",
]

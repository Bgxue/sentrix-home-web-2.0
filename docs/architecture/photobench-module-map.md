# PhotoBench 模块地图

**本文件由 `scripts/check_module_map.py --src apps/photobench/src --doc docs/architecture/photobench-module-map.md` 校验**
（在 `make ci` 的 `photobench-check` 那一段上），规则与 `docs/architecture/module-map.md` 逐条相同：

1. **文件集合双向一致**——`apps/photobench/src` 下每个 `.py` 必须在这里有一行，反过来也成立。
2. **依赖边一致**——脚本用 `ast` 解析每个文件的**包内** import 后与「依赖谁」列比对，
   「被谁依赖」是它的转置。少写、多写、与转置不符都会失败。

两列只记 `photobench` 包内的模块关系；stdlib 与第三方（httpx 等）不进这两列。

## 为什么不并进 `module-map.md`

PhotoBench 是**另一个发行包**（`apps/photobench`，见设计文档 §145 与用户 2026-10-04
裁决 §3.5），源码根是 `apps/photobench/src` 而不是 `apps/api/src`。而
`check_module_map.py` 的路径基准只有一份（`--src` 传 src 根、表格里的路径相对它书写），
一份文档对应一个根——把两个包塞进一张表会让路径前缀变成两套基准，那正是该脚本
注释里点名的那个坑。

**这份文档覆盖的是 PhotoBench 的全部 Python 代码，不只是 S9a 的部分。** S9b
（T911–T913）新建 `application/` 与 `api/` 两层时，按本表体例续写即可。

## 装配点按端口声明

`apps/api` 有一条配套的不变量（由 `apps/api/tests/unit/test_container_wiring.py`
强制）：**`Container` 的每个字段都按 domain 端口声明，不写 infrastructure 的具体类**。

**PhotoBench 现在还没有装配点**——S9a 只落了 domain 端口与 infrastructure 实现，
工厂 `create_runtime_providers` 是 1.0 原样的自由函数，不是容器。S9b 建 FastAPI 装配层
（T911–T913）时，这条不变量要一并立起来：那时 `RuntimeProviders` 的三个字段必须按
`photobench.domain.runtime.ports` 的协议声明，而不是写死
`OpenAICompatibleInferenceProvider` 之类的具体类——否则「提供者可替换」只剩一句口头承诺。

列含义：**层** = domain / application / infrastructure / api / bootstrap；`—` 表示这一列没有内容。

| 路径 | 职责 | 层 | 依赖谁 | 被谁依赖 |
|---|---|---|---|---|
| `photobench/__init__.py` | 包标记；写明 PhotoBench 与主产品的隔离约束（设计 §145）由 `photobench-isolation` 契约强制 | — | — | — |
| `photobench/domain/__init__.py` | 包标记 | domain | — | — |
| `photobench/domain/runtime/__init__.py` | 包标记 | domain | — | — |
| `photobench/domain/runtime/ports.py` | 模型运行时的三个端口（`InferenceProvider` / `LifecycleProvider` / `TelemetryProvider`）与 `RuntimeProviders` 值对象。对应 1.0 `backend/runtime_providers.py` 的三个 ABC 基类；1.0 把接口与实现写在同一份文件里，2.0 按分层契约拆开。`chat` / `chat_stream` 的返回类型**不泄漏 httpx**（domain-purity 禁止 httpx） | domain | — | `photobench/infrastructure/runtime/providers.py` |
| `photobench/infrastructure/__init__.py` | 包标记 | infrastructure | — | — |
| `photobench/infrastructure/runtime/__init__.py` | 包标记 | infrastructure | — | — |
| `photobench/infrastructure/runtime/hardware.py` | CPU 温度读数 `read_cpu_temperature_c`：读 `/sys/class/hwmon`，按 `k10temp`/`coretemp`/`cpu-thermal` 白名单挑芯片、按 `tctl`/`package id 0` 等标签挑传感器。**只搬 1.0 `backend/hardware.py` 里被运行时提供者用到的那一个函数**，其余四个（`collect_gpu`/`collect_cpu`/`collect_memory`/`collect_hardware`）服务 run meta，归 S9b | infrastructure | — | `photobench/infrastructure/runtime/providers.py` |
| `photobench/infrastructure/runtime/providers.py` | 1.0 `backend/runtime_providers.py`（869 行）的重建：`OpenAICompatibleInferenceProvider`（探活 / 列模型 / 对话 / 流式 / token 预算 / 自述能力）、`ManagerLifecycleProvider`（`/profiles`·`/start`·`/stop`·`/state`）、`ManagerTelemetryProvider`、`HostNvidiaTelemetryProvider` 与其三个子类（llama.cpp / vLLM / Ollama 的进程 hint）、`OrinLlamaCppTelemetryProvider`（118:8100 的 ssh PSS 与 llama.cpp metrics）、两个 `Unavailable*` 占位、宿主进程归属 `_augment_host_process_memory`，以及工厂 `create_runtime_providers` | infrastructure | `photobench/domain/runtime/ports.py`、`photobench/infrastructure/runtime/hardware.py` | — |
| `photobench/infrastructure/runtime/apple_telemetry.py` | 1.0 `backend/apple_telemetry.py`（418 行）的重建：`LocalAppleUnifiedTelemetryProvider`（`phys_footprint` 逐进程占用、vm_stat 口径的宿主已用内存、AGX/macmon 的 GPU 与温度功耗、KV cache 不适用）与其解析层（`parse_vm_stat` / `parse_macmon_sample` / `parse_agx_performance` / `_listening_ports`）。**常量刻意不与 providers 共用**：它多一个 `"mlx"` 且不读 `PHOTOBENCH_SYSTEM_PROCESS_*`（1.0 如此） | infrastructure | — | — |

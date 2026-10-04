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

**这份文档覆盖的是 PhotoBench 的全部 Python 代码，不只是 S9a 的部分。** S9a 落了
`domain/` 与 `infrastructure/`；S9b-1 补上 `application/`、`api/`、`bootstrap/` 与进程入口
`main.py`（FastAPI + 后台扫描，端口 8772）；`run-execution` 主链在 S9b-2，judge 与遥测采样
在 S9b-3。

## 装配点按端口声明

`apps/api` 有一条配套的不变量（由 `apps/api/tests/unit/test_container_wiring.py` 强制）：
**`Container` 的每个字段都按 domain 端口声明，不写 infrastructure 的具体类**。

**S9b-1 把这条不变量立起来了**：`photobench/bootstrap/container.py` 的字段全部是
`ConfigStore` / `RunStore` / `QaMetadataSource` / `JsonHttpClient` 四个协议加上两个
`Callable` 工厂，工厂的返回类型是 S9a 的 `LifecycleProvider` / `InferenceProvider`。
用例在 `apps/photobench/tests/test_photobench_container_wiring.py`——没有它，可替换性只是
`build()` 那一行里的一句口头承诺。

运行时三件套为什么是**两个按请求的工厂**而不是一个常驻的 `RuntimeProviders` 字段：
1.0 的 `/api/profiles` 与 `/api/current-model` 是按请求体里的端点构造提供者的
（`query_profiles` `:5069` / `query_current_model` `:5078`），本切片没有「当前端点」这个概念
——选定端点并常驻是 S9b-2 的 run-execution 才会做的事。

列含义：**层** = domain / application / infrastructure / api / bootstrap；`—` 表示这一列没有内容。

| 路径 | 职责 | 层 | 依赖谁 | 被谁依赖 |
|---|---|---|---|---|
| `photobench/__init__.py` | 包标记 + `__version__`；写明 PhotoBench 与主产品的隔离约束（设计 §145）由 `photobench-isolation` 契约强制 | — | — | `photobench/main.py` |
| `photobench/config.py` | 部署参数 `Settings`：配置根 / 结果根 / 数据集根。1.0 是写死的 `PROJECT_ROOT / ...` 常量（`benchmark_orchestrator.py:63-67`）；2.0 允许 `PHOTOBENCH_CONFIG_ROOT` / `PHOTOBENCH_RESULTS_ROOT` / `PHOTOBENCH_DATA_ROOT` 覆盖——迁移期必须能指到 1.0 的 `results/`（19 GB，不进仓库） | — | — | `photobench/bootstrap/container.py`、`photobench/main.py` |
| `photobench/main.py` | 进程入口 `create_app()`：装配 FastAPI（前缀 `/api`，端口 8772）并起**后台**扫描 `results/` 的线程。**没有模块级 `app`**——那会让任何 `import photobench.main` 带上建目录与起线程的副作用（与 `sentrix/main.py` 同一条理由） | — | `photobench/__init__.py`、`photobench/api/__init__.py`、`photobench/api/errors.py`、`photobench/api/routes/__init__.py`、`photobench/api/routes/config.py`、`photobench/api/routes/model_profiles.py`、`photobench/api/routes/runs.py`、`photobench/bootstrap/container.py`、`photobench/config.py` | — |
| `photobench/domain/__init__.py` | 包标记 | domain | — | — |
| `photobench/domain/errors.py` | 领域错误基类与两个子类（404 / 400）。1.0 里这两个码由 `OrchestratorHandler` 的两个 `except` 表达（`:7758` / `:7854`），2.0 把它变成领域层可断言的事实 | domain | — | `photobench/api/errors.py`、`photobench/api/routes/runs.py`、`photobench/application/config/service.py`、`photobench/application/model_profiles/service.py` |
| `photobench/domain/config/__init__.py` | 包标记 | domain | — | — |
| `photobench/domain/config/ports.py` | `ConfigStore`：读一个 JSON、原子写一个 JSON、问一个路径。取值规则（必填、默认值、provider 白名单）不在这里，在 application | domain | — | `photobench/application/config/service.py`、`photobench/bootstrap/container.py` |
| `photobench/domain/datasets/__init__.py` | 包标记 | domain | — | — |
| `photobench/domain/datasets/ports.py` | `QaMetadataSource`：`qa_id` → 数据集侧元信息。读题目要用它补齐老 run 没存下的字段（1.0 `_hydrate_qa_metadata` `:4919`） | domain | — | `photobench/application/runs/read_runs.py`、`photobench/bootstrap/container.py` |
| `photobench/domain/http/__init__.py` | 包标记 | domain | — | — |
| `photobench/domain/http/ports.py` | `JsonHttpClient`：1.0 `request_json`（`:731`）/ `request_bytes`（`:799`）的端口。返回形状与失败形状都与 1.0 一致；httpx 类型不泄漏进 domain | domain | — | `photobench/application/runs/read_runs.py`、`photobench/bootstrap/container.py` |
| `photobench/domain/results/__init__.py` | 包标记 | domain | — | — |
| `photobench/domain/results/ports.py` | `RunStore`：列表瘦条目（**流式产出**）、单个 run 的完整状态（按需）、评审、GPU 采样原始行。流式是为了让 1.0 那个「扫完 267 个 run 才绑端口」的 75 秒变成后台进度 | domain | — | `photobench/application/runs/index.py`、`photobench/application/runs/read_runs.py`、`photobench/bootstrap/container.py` |
| `photobench/domain/runs/__init__.py` | 包标记 | domain | — | `photobench/application/runs/read_runs.py` |
| `photobench/domain/runs/analysis.py` | run 读侧的**全部纯计算**，逐函数照搬 1.0：`list_summary` / `effective_summary` / `capability_summary`（`:4639`）/ `run_list_entry`（`:5309`）/ `item_summary`（`:6559`）/ `hydrate_qa_metadata`（`:4919`）/ 检索与排名指标 / 归因与轨迹对齐（`:3838`–`:4566`）/ 采样降采样（`:5364`–`:5488`）。它落在 domain 是因为 infrastructure 的扫描也要用它，而 infrastructure 不许 import application | domain | — | `photobench/application/model_profiles/service.py`、`photobench/application/runs/read_runs.py`、`photobench/infrastructure/results/store.py` |
| `photobench/domain/runtime/__init__.py` | 包标记 | domain | — | — |
| `photobench/domain/runtime/ports.py` | 模型运行时的三个端口（`InferenceProvider` / `LifecycleProvider` / `TelemetryProvider`）与 `RuntimeProviders` 值对象。对应 1.0 `backend/runtime_providers.py` 的三个 ABC 基类；1.0 把接口与实现写在同一份文件里，2.0 按分层契约拆开。`chat` / `chat_stream` 的返回类型**不泄漏 httpx**（domain-purity 禁止 httpx） | domain | — | `photobench/application/model_profiles/service.py`、`photobench/bootstrap/container.py`、`photobench/infrastructure/runtime/providers.py` |
| `photobench/application/__init__.py` | 包标记 | application | — | — |
| `photobench/application/config/__init__.py` | 包标记 | application | — | — |
| `photobench/application/config/service.py` | 配置用例：运行时连接 / vLLM target / judge provider 的**读写**（1.0 `:71`–`:455`）。取值优先级照搬（环境 > 文件 > 兜底，显式空串表示未配置）；1.0 把结果摊在模块级全局变量上、用 `global` 就地改，2.0 收进一个对象由装配点决定生命周期 | application | `photobench/domain/config/ports.py`、`photobench/domain/errors.py` | `photobench/application/model_profiles/service.py`、`photobench/bootstrap/container.py` |
| `photobench/application/model_profiles/__init__.py` | 包标记 | application | — | — |
| `photobench/application/model_profiles/service.py` | `/api/profiles` 与 `/api/current-model` 两条**只查询**的路（1.0 `query_profiles` `:5069` / `query_current_model` `:5078`）。它也是 `LifecycleProvider.profiles` / `.state` 与 `InferenceProvider.list_models` / `.capabilities` 四个端口方法的**生产调用者**；起停模型（`start` / `stop`）不在其中，那是 S9b-2。方法名用 1.0 仓储的 `query_profiles` 而不是 `profiles`——同名会把那个端口方法挤进 method-reachability 的重名盲区 | application | `photobench/application/config/service.py`、`photobench/domain/errors.py`、`photobench/domain/runtime/ports.py`、`photobench/domain/runs/analysis.py` | `photobench/bootstrap/container.py` |
| `photobench/application/runs/__init__.py` | 包标记 | application | — | — |
| `photobench/application/runs/index.py` | `RunIndex`：后台扫描的进度与瘦条目表。1.0 在 `OrchestratorRepository.__init__`（`:4873`）里同步解析完 270 个 run 才绑端口——实测 267 个 `run.json`、11.07 GB、75.0 秒 | application | `photobench/domain/results/ports.py` | `photobench/application/runs/read_runs.py`、`photobench/bootstrap/container.py` |
| `photobench/application/runs/read_runs.py` | 读侧用例：列表 / 详情 / 题目分页筛选 / 单题 / 评审（1.0 `:5343` / `:5354` / `:6441` / `:6528` / `:4976`），外加老 run 的 GT 图片补全（`:6851`，本切片里 `request_json` 的唯一调用者）。**写侧一条都没有** | application | `photobench/application/runs/index.py`、`photobench/domain/datasets/ports.py`、`photobench/domain/http/ports.py`、`photobench/domain/results/ports.py`、`photobench/domain/runs/__init__.py`、`photobench/domain/runs/analysis.py` | `photobench/bootstrap/container.py` |
| `photobench/infrastructure/__init__.py` | 包标记 | infrastructure | — | — |
| `photobench/infrastructure/config/__init__.py` | 包标记 | infrastructure | — | — |
| `photobench/infrastructure/config/files.py` | `JsonConfigStore`：`apps/photobench/config/*.json` 的读与**原子写**（写临时文件 + `os.replace`，1.0 `:445`）。配置根从 1.0 的 `services/photobench/config/` 换成 2.0 的 `apps/photobench/config/` | infrastructure | — | `photobench/bootstrap/container.py` |
| `photobench/infrastructure/datasets/__init__.py` | 包标记 | infrastructure | — | — |
| `photobench/infrastructure/datasets/qa_metadata.py` | `JsonQaMetadataSource`：扫 `datasets/*/manifest.json` 指向的 qa `.jsonl`，叠加 `qa_task_actions.json` 的 QA 集级默认值（1.0 `_load_qa_metadata` `:4885` + `load_jsonl` `:1600` + `load_task_action_policy` `:1609`） | infrastructure | — | `photobench/bootstrap/container.py` |
| `photobench/infrastructure/http/__init__.py` | 包标记 | infrastructure | — | — |
| `photobench/infrastructure/http/client.py` | `HttpxJsonClient`：httpx 实现。行为逐条对照 1.0——超时按调用传、非 2xx 转成同一条 `RuntimeError("HTTP {code} {reason}: {detail}")`、**无重试**、TLS 不校验证书、跟随重定向（urllib 默认跟，httpx 默认不跟） | infrastructure | — | `photobench/bootstrap/container.py` |
| `photobench/infrastructure/results/__init__.py` | 包标记 | infrastructure | — | — |
| `photobench/infrastructure/results/store.py` | `FileRunStore`：`results/<run_id>/{run.json,reviews.json,gpu_samples.jsonl}`。目录形状与 1.0 完全一致，所以 2.0 指到 1.0 的 results 目录就能直接读；坏掉的单个 `run.json` 只跳过它自己（1.0 `:5043`） | infrastructure | `photobench/domain/runs/analysis.py` | `photobench/bootstrap/container.py` |
| `photobench/infrastructure/runtime/__init__.py` | 包标记 | infrastructure | — | — |
| `photobench/infrastructure/runtime/hardware.py` | CPU 温度读数 `read_cpu_temperature_c`：读 `/sys/class/hwmon`，按 `k10temp`/`coretemp`/`cpu-thermal` 白名单挑芯片、按 `tctl`/`package id 0` 等标签挑传感器。**只搬 1.0 `backend/hardware.py` 里被运行时提供者用到的那一个函数**，其余四个（`collect_gpu`/`collect_cpu`/`collect_memory`/`collect_hardware`）服务 run meta，归 S9b-2 | infrastructure | — | `photobench/infrastructure/runtime/providers.py` |
| `photobench/infrastructure/runtime/providers.py` | 1.0 `backend/runtime_providers.py`（869 行）的重建：`OpenAICompatibleInferenceProvider`（探活 / 列模型 / 对话 / 流式 / token 预算 / 自述能力）、`ManagerLifecycleProvider`（`/profiles`·`/start`·`/stop`·`/state`）、`ManagerTelemetryProvider`、`HostNvidiaTelemetryProvider` 与其三个子类（llama.cpp / vLLM / Ollama 的进程 hint）、`OrinLlamaCppTelemetryProvider`（118:8100 的 ssh PSS 与 llama.cpp metrics）、两个 `Unavailable*` 占位、宿主进程归属 `_augment_host_process_memory`，以及工厂 `create_runtime_providers`。**`SYSTEM_PROCESS_PORTS` 的默认值在 S9b-1 改成 2.0 的 8772 / 8191** | infrastructure | `photobench/domain/runtime/ports.py`、`photobench/infrastructure/runtime/hardware.py` | `photobench/bootstrap/container.py` |
| `photobench/infrastructure/runtime/apple_telemetry.py` | 1.0 `backend/apple_telemetry.py`（418 行）的重建：`LocalAppleUnifiedTelemetryProvider`（`phys_footprint` 逐进程占用、vm_stat 口径的宿主已用内存、AGX/macmon 的 GPU 与温度功耗、KV cache 不适用）与其解析层（`parse_vm_stat` / `parse_macmon_sample` / `parse_agx_performance` / `_listening_ports`）。**常量刻意不与 providers 共用**：它多一个 `"mlx"` 且不读 `PHOTOBENCH_SYSTEM_PROCESS_*`（1.0 如此；端口号在 S9b-1 与 providers 一起换成 2.0 的 8772/8191）。**本切片没有调用者**——构造它的是 S9b-2 的 `select_runtime_providers` | infrastructure | — | — |
| `photobench/api/__init__.py` | 包标记 | api | — | `photobench/main.py` |
| `photobench/api/deps.py` | 路由取装配点（`app.state.container`，api 不许 import infrastructure）+ 1.0 `_payload`（`:7523`）的等价物：空体 / 坏 JSON / 非对象一律当 `{}` | api | `photobench/bootstrap/container.py` | `photobench/api/routes/config.py`、`photobench/api/routes/model_profiles.py`、`photobench/api/routes/runs.py` |
| `photobench/api/errors.py` | 领域错误 → HTTP 的**唯一**映射点：响应体固定 `{"error": ...}`（1.0 的形状，前端按它读），`DomainError` 按自己的码、`KeyError` → 404、其余 → 400（先 `logger.exception` 再回） | api | `photobench/domain/errors.py` | `photobench/main.py` |
| `photobench/api/routes/__init__.py` | 包标记 | api | — | `photobench/main.py` |
| `photobench/api/routes/config.py` | `GET` / `POST /api/config`（1.0 `:7536` / `:7822`）。**响应体比 1.0 少三项**（`judge_prompt` / `custom_judge_prompt` / `evidence_judge_enabled`），它们是 judge 域，归 S9b-3 | api | `photobench/api/deps.py` | `photobench/main.py` |
| `photobench/api/routes/model_profiles.py` | `POST /api/profiles`、`POST /api/current-model`，外加 1.0 `:7530` 那条「GET 要明说请用 POST」的 405 | api | `photobench/api/deps.py` | `photobench/main.py` |
| `photobench/api/routes/runs.py` | run 的**只读**路由：`GET /api/runs`、`/runs/{id}`、`/runs/{id}/items`、`/runs/{id}/items/{index}`、`/runs/{id}/reviews`（1.0 `:7648`–`:7740`）。分页参数声明成 `str` 再自己转，是为了让错的时候回 1.0 那条 400 文案而不是 FastAPI 的 422 | api | `photobench/api/deps.py`、`photobench/domain/errors.py` | `photobench/main.py` |
| `photobench/bootstrap/__init__.py` | 包标记 | bootstrap | — | — |
| `photobench/bootstrap/container.py` | 装配点：字段**全部按 domain 端口声明**（四个协议 + 两个按协议类型声明的 provider 工厂），只有 `build()` 里知道具体类是哪一个。运行时三件套按请求构造，所以是工厂不是常驻字段（理由见上） | bootstrap | `photobench/application/config/service.py`、`photobench/application/model_profiles/service.py`、`photobench/application/runs/index.py`、`photobench/application/runs/read_runs.py`、`photobench/config.py`、`photobench/domain/config/ports.py`、`photobench/domain/datasets/ports.py`、`photobench/domain/http/ports.py`、`photobench/domain/results/ports.py`、`photobench/domain/runtime/ports.py`、`photobench/infrastructure/config/files.py`、`photobench/infrastructure/datasets/qa_metadata.py`、`photobench/infrastructure/http/client.py`、`photobench/infrastructure/results/store.py`、`photobench/infrastructure/runtime/providers.py` | `photobench/api/deps.py`、`photobench/main.py` |

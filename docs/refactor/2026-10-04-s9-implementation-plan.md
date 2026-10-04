# S9 实施计划：PhotoBench 后端重构

> 路线图出处：设计文档 `docs/architecture/2026-09-21-sentrix-home-web-2-implementation-design.md:514`
> ——「**S9 | PhotoBench 后端重构 | 7,751 行 stdlib 单文件 → FastAPI 拆分；断 `sys.path` 硬耦合；
> 修死端点循环；改指 `/api/v1` | 能对 2.0 跑完一轮评测**」。它是 **M3（S8–S11）** 的第二段。
> §5.1 的端口表（`:365`）给 2.0 的 PhotoBench 定的是 **8772**。
>
> 前置：S0–S8 全部完成（S8 于 2026-10-04 收口）。W1（检索换 wemm）完成。

---

## 0. 动工前核查（2026-10-04）

### 0.1 **S9 是「在 2.0 新建一个服务」，不是「搬一个已存在的服务」**

- 2.0 的 `apps/photobench/` 只有 **6 个文件**：`DATASET.md` + 5 个 config 示例。
  **代码一行没有**；`services/` 目录在 2.0 不存在。
- 1.0 的实现在 `services/photobench/backend/benchmark_orchestrator.py`，
  **实测 7,997 行**（设计文档写 7,751——**文档比代码旧**；S10 那个 3,646 同样应为 3,797）。

设计文档把它叫「重构」是准确的：目标产物是按 FastAPI 分层重写，不是拷贝。

### 0.2 有一块设计文档**没单列**的隐藏工作量

`benchmark_orchestrator.py:42-61` 反向 `sys.path` 硬耦合：

```python
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
from backend.runtime_providers import (...)
from backend.apple_telemetry import LocalAppleUnifiedTelemetryProvider, is_apple_silicon
```

被依赖的两份在 **2.0 里都不存在**：

| 1.0 文件 | 行数 | 2.0 |
|---|---|---|
| `backend/runtime_providers.py` | 869 | **无** |
| `backend/apple_telemetry.py` | 418 | **无** |

**合计 1,287 行要在 2.0 重建**——设计文档没有把这块算进「7,751 行」里。

### 0.3 PhotoBench 现在**跑不通 2.0**

它调的端点与 2.0 的对照（`benchmark_orchestrator.py` 行号）：

| 方法 | 1.0 路径 | 2.0 状态 |
|---|---|---|
| GET | `/api/arbiter/status` | **死端点**（1.0/2.0 均无） |
| POST | `/api/ingest-batches/{id}/cancel` | **死端点**（1.0/2.0 均无，**本次新发现**） |
| POST | `/api/model-profiles/bind-{runtime,cloud-runtime,external-runtime}` | ❌ 2.0 完全没有 |
| POST | `/api/ingest-batches/{id}/complete` | ❌ 2.0 无（语义并入 imports） |
| POST | `/api/memory-spaces` | ❌ 只有 GET（2.0 改为导入时内部自动建） |
| POST | `/api/people/seed-batch` | ⚠️ 只有 `people/seed` |
| GET | `/api/assistant/turn/{id}` | 改名 → `/assistant/turns/{turn_id}` |
| POST | `/api/assistant/turn` | 改两段式（先建 conversation） |
| POST | `/api/import` | 改名 → `/imports` 或 `/media/import` |
| — | 其余（memory-spaces 读、assets、assets/{id}/file、relationships/batch） | ✅ 有，路径加 `/v1` |

### 0.4 两个死端点

1. **`/api/arbiter/status`**（`:762-774`）：设计文档 §10 点过。核对属实——1.0 主 API 里
   `arbiter` 零命中，该端点从不存在；PhotoBench 自己的前端轮询它，后端再去打主 API，
   恒 404 且被 `unsupported` 分支吞掉。
2. **`/api/ingest-batches/{id}/cancel`**（`:2265-2277`，**设计文档没提**）：1.0 的
   ingest-batches 只有 POST / GET / `complete` 三条，**没有 `/cancel`**；调用点被
   `except Exception` 吞掉，静默 404。

### 0.5 部署现状

- 1.0 的 PhotoBench 跑 **8771**，**不受 systemd 管**——裸进程 + `scripts/deploy/restart_photobench.sh`。
- 启动时要扫 `results/` 下 **270 个 run 目录**，**约 55 秒**才绑端口（脚本注释明说）。
- 结果体量大：单个 `run.json` 最大 **447 MB**、`results.jsonl` 337 MB。
- 2.0 的目标端口是 **8772**（设计文档 §5.1）。

---

## 1. 裁决

### 用户 2026-10-04 定的四条

| # | 问题 | 裁决 |
|---|---|---|
| ① | 要不要保留「模型部署编排」那半边 | **照搬全量**——runtime providers / 遥测 / 多模型编排都搬 |
| ② | 2.0 缺的那几个端点怎么办 | **改 PhotoBench 适配 2.0**，不给主 API 加端点 |
| ③ | 评测中途换模型怎么落地 | **PhotoBench 重启 2.0**（带上新的 `vlm_base_url`） |
| ④ | S9 怎么切 | **切三段**：S9a 遥测 → S9b 主链 → S9c 适配 |

### ③ 的由来（**这是本切片最要紧的一条**）

1.0 的多模型评测流程建立在「**不重启主 API 就能换模型端点**」之上：

```python
request_json(f"{sentrix_url}/api/model-profiles/bind-runtime",
             {"manager_url": self.vllm_api_url,
              "model_base_url": self.vllm_model_base_url}, "POST", 30)
# 上一行的注释：Sync .100 Sentrix gamma client to the new model (no restart)
```

而 2.0 的 `vlm_base_url` 是**启动时从 Settings 读死的**（`container.py:248` 的
`ServedModel(settings.vlm_base_url)`）。`ServedModel` 只有**模型名**带 TTL 重探与
`invalidate()`，**base_url 换不了**。

裁决②（不加端点）与裁决①（保留编排）放在一起，出路只有三条：重启 2.0、约束
PhotoBench 只用固定端点、或给 2.0 加 rebind 端点。**用户选第一条。**

**代价要在实现时如实承担**：PhotoBench 于是要管一个它本不该管的进程（起停 2.0），
那属于部署知识。**它与 S11（服务与部署）有重叠**——S11 要做「主 API / Web /
PhotoBench / model-manager 独立启停」，这里的重启逻辑到 S11 可能要收敛。
登记这一点，免得两边各写一套。

---

## 2. 三段

### S9a 运行时提供者与遥测（1,287 行）

**验收**：能在 2.0 里查到本机 GPU/模型进程的遥测，且 provider 可被替换成假件测试。

| # | 内容 |
|---|---|
| T901 | `runtime_providers.py`（869 行）在 2.0 重建。**先读它到底提供了哪些 provider**——1.0 支持 vLLM / llama.cpp / Ollama / Jetson / Apple / 各种 telemetry，其中一部分 2.0 未必用得上（2.0 的设计口径是「不管理模型进程生命周期」）。**但用户裁决是照搬全量**，所以默认全搬，只在某一条明显只服务 1.0 特有部署时才提出来单议 |
| T902 | `apple_telemetry.py`（418 行）重建 |
| T903 | 落位：这些是**基础设施**（子进程 / 系统调用 / HTTP 探活），按 import-linter 契约只能进 `infrastructure/`；端口进 `domain/` |
| T904 | 测试：1.0 那边**没有**这两个模块的测试（它们是被 benchmark 间接覆盖的）。2.0 要为它们补，用假件 |

### S9b orchestrator 主链拆分（7,997 行）

**验收**：能对一个跑着的 2.0 跑完**一轮**评测并产出指标。

| # | 内容 |
|---|---|
| T911 | 拆 `benchmark_orchestrator.py`。建议按域拆：**runs**（建/查/取消/重跑/评审）/ **config**（profiles / targets / judge providers）/ **judge**（打分与重判）/ **telemetry**（GPU 采样、功耗）/ **run-execution**（那条主链：建空间 → 种身份 → 导入 → 处理 → 问答 → 打分 → 聚合）/ **http-client**（`request_json` / `request_bytes` 的替代，2.0 用 `httpx`） |
| T912 | HTTP 栈从 `http.server` 换成 FastAPI + uvicorn（端口 8772）。**40 多条路由**逐条对照搬 |
| T913 | **断 `sys.path` 硬耦合**（裁决①的连带）：不再反向 import 1.0 的 `backend/`，改为用 T901/T902 在 2.0 里的实现 |
| T914 | 它自己的 `results/` 读写（270 个 run、单个 JSON 最大 447 MB）：**开服务时要扫目录**，1.0 因此要 55 秒才就绪。**这是一个真实的启动性能问题**，值得在迁移时一并想（懒加载？索引文件？）——但**先量再改**，不要凭感觉 |

### S9c 结果读侧 + 对 2.0 适配 + 死端点

**验收**：`能对 2.0 跑完一轮评测`（设计文档 S9 的验收原话）。

| # | 内容 |
|---|---|
| T921 | **端点对照表落地**（§0.3 那张）：每一处调用点改成 2.0 的等价接口，或按裁决②删掉 |
| T922 | **修两个死端点**：arbiter 那条**删掉**（它对应的能力在 2.0 也不存在）；`ingest-batches/{id}/cancel` 同样处理——**先确认 2.0 的导入取消语义落在哪**，没有就删 |
| T923 | **换模型 = 重启 2.0**（裁决③）：实现这条路径，并**登记它与 S11 的重叠** |
| T924 | 全局前缀 `/api` → `/api/v1` |
| T925 | 收口：capability-map / module-map / behaviour-baseline / 偏离登记 / openapi |

---

## 3. 门禁与交付物

- 新 `.py` 登 `docs/architecture/module-map.md`；新能力登 `capability-map.jsonl`；
  新常量登 `behaviour-baseline.md`。
- **PhotoBench 是 2.0 仓库里的新 app**（`apps/photobench/`），**它与 `apps/api` 是两套东西**——
  先确认：它要不要受 `make ci` 的同一套 import-linter 契约管？还是有自己的门禁？
  **这是动工前要定的第一个问题**（见 §4）。
- 2.0 仓库根 `Makefile` 的 `ci` 链要不要加 photobench 的测试？同样要先定。

---

## 3.5 工程标准（用户 2026-10-04 裁决）

**PhotoBench 受与 `apps/api` 同一套门禁**：`ruff` + `mypy strict` + 同样的
`domain / application / infrastructure / api` 分层契约，并在 `Makefile` 的 `ci` 链上
加一段 photobench 检查。

理由：**它不会是仓库里唯一无人看守的代码**。仓库全部价值都压在这几道门上，
一个 9,300 行的服务豁免于它们，等于在仓库里开一块飞地。而 FastAPI 拆分本来
就该有分层——设计文档把它叫「重构」也是这个意思。

**代价如实承担**：~9,300 行都要过 strict 类型检查，且分层契约会约束 provider
那批代码的落位（它们碰子进程/系统调用/HTTP，只能进 `infrastructure`）。

---

## 3.6 动工前还要定的两件

1. **1.0 那 8 个测试文件（约 71 KB）怎么处理**：重写、适配、还是先不搬。
2. **`results/` 的 270 个 run 要不要迁进 2.0**：它们按 `DATASET.md` 声明是「本地数据，
   不入 Git」。2.0 的 `apps/photobench/datasets/` 已有 1.4 GB 数据集，`results/` 是另一回事。

---

## 5. 施工记录

### S9a 运行时提供者与遥测（T901–T904，2026-10-04）

**落位结论（§4 那个「动工前要定的第一个问题」）**：PhotoBench 的 Python 代码放在
**`apps/photobench/src/photobench/`**，与 `apps/api/src/sentrix` 同构（
`domain / infrastructure`，S9b 再补 `application / api`），并**新起一个发行包**
`apps/photobench/pyproject.toml`。依据三条：实施设计 §145 定死「PhotoBench 不得读取
主产品 Python 内部模块」——同一个包里就没有隔墙；用户 2026-10-04 裁决 §3.5 要求它与
`apps/api` 同一套门禁，而门禁（mypy `files` / import-linter `root_packages` /
三个 arch 脚本的 `--src`）都按「包」为单位配置；`apps/api` 自己就是
`apps/api/pyproject.toml` + `src/` 布局，照它的体例不需要发明新东西。

**新增**

| 文件 | 一句话 |
|---|---|
| `apps/photobench/pyproject.toml` | 第二个发行包；依赖只有 httpx（1.0 原本就用它） |
| `apps/photobench/src/photobench/__init__.py` | 包标记 + 隔离约束的说明 |
| `apps/photobench/src/photobench/domain/runtime/ports.py` | `InferenceProvider` / `LifecycleProvider` / `TelemetryProvider` 三个 Protocol + `RuntimeProviders` |
| `apps/photobench/src/photobench/infrastructure/runtime/providers.py` | 1.0 `backend/runtime_providers.py` 的重建（全部 provider + 工厂） |
| `apps/photobench/src/photobench/infrastructure/runtime/apple_telemetry.py` | 1.0 `backend/apple_telemetry.py` 的重建 |
| `apps/photobench/src/photobench/infrastructure/runtime/hardware.py` | 只搬 1.0 `backend/hardware.py` 的 `read_cpu_temperature_c` |
| `apps/photobench/tests/{fakes,test_runtime_providers,test_apple_telemetry}.py` | 假件 + 66 条用例（HTTP / 子进程 / `/proc` / `sysfs` 全假） |
| `docs/architecture/photobench-module-map.md` | PhotoBench 自己的模块地图（与 `module-map.md` 同一个校验脚本、另一个 `--src`） |

**修改**：`pyproject.toml`（ruff isort / mypy files / pytest testpaths / coverage source /
import-linter 加 `photobench` 四条契约）、`Makefile`（install 加第二个 editable 包、
lint 与 format 加 photobench 路径、新 `photobench-check` 段并挂上 ci 链）、
`scripts/check_reachability.py` 的 `PENDING_WIRING`（4 条）、
`scripts/check_method_reachability.py` 的 `PENDING_METHODS`（9 条）、
`docs/refactor/capability-map.jsonl`（4 条）、`docs/refactor/behaviour-baseline.md`
（新增一节）、`apps/api/tests/unit/test_architecture_rules.py`（它把契约**条数**
写死成 5，加契约后必须改成 8——见下第 12 条）。

**「行为一致」的证据方式**：除用例之外，另跑了一遍**差分对照**——把 1.0 的
`backend.runtime_providers` / `backend.apple_telemetry` 当预言机，用同一批假件
（子进程、`/proc`、HTTP 全不打）同时喂两个版本，比对返回的字典：纯函数
（`parse_vm_stat` / `parse_macmon_sample` / `parse_agx_performance` /
`_is_related` / `_is_model_process` / 两个 URL 归一化 / 四组常量）、
`_augment_host_process_memory` 的四种情形、Apple 提供者的四个方法，**逐字段一致**。
脚本是一次性的（依赖 1.0 仓库在盘上），**没有进仓库**——进仓库会在没有 1.0 检出的
机器上红。

**偏离与待裁决**

1. **1.0 这两个模块其实有测试**（`backend/tests/test_runtime_providers.py` /
   `test_runtime_provider_system_memory.py` / `test_apple_telemetry.py`），§2 的
   「它们没有测试」不准确。2.0 的用例是**重写**（改用假件对象而不是 `unittest.mock`
   的 patch 目标），不是照搬。
2. **`backend/jetson_telemetry.py`（660 行）不在 S9a**。它是第三个模块：1.0 的
   `runtime_providers.py` **import 了它的两个名字却从未使用**（ruff F401），真正
   构造 Jetson 那路提供者的是编排侧的 `select_runtime_providers`。按「照搬全量」
   的口径它该搬，但它属于 S9b 的 provider 选择逻辑；**本切片只搬了被真正用到的两条**
   （`runtime_providers` + `apple_telemetry`）。**请裁决**：Jetson 那 660 行是进 S9b，
   还是单独切片。
3. **`backend/hardware.py` 只搬了一个函数**（`read_cpu_temperature_c`）。另外四个
   （`collect_gpu` / `collect_cpu` / `collect_memory` / `collect_hardware`）服务
   `run_qa_benchmark` 写 run meta，归 S9b。
4. **只服务 1.0 部署形态的两处，照搬但登记**：(a) `OrinLlamaCppTelemetryProvider`
   把远端写死成 `orin@192.168.0.118` + 固定 PID 文件路径——它只对 153 上跑、
   模型在 118 上这一种部署成立；(b) `LocalAppleUnifiedTelemetryProvider` 只对
   Mac mini M4 成立（`macmon` / `ioreg` / `vm_stat` / `libproc`）。两者都是 1.0 的
   真实部署，按裁决①照搬；**若 2.0 只跑 153，这两条是候选的裁剪对象**。
5. **`chat` / `chat_stream` 的返回类型改了**：1.0 返回 `httpx.Response` 与 httpx 的
   stream 上下文管理器，端口不能把 httpx 摆到 domain 上，改成「解析后的 JSON」与
   「逐行的迭代器」。**失败行为不变**（非 2xx 仍抛异常，只是由适配器抛）。
6. **1.0 的 ABC 基类不搬**（换成 Protocol，实现类不继承），基类上的
   `raise NotImplementedError` 与 `token_count` 的默认实现随之消失——后者落在唯一的
   实现 `OpenAICompatibleInferenceProvider` 上。
7. **`_process_smaps_field_mib` 结尾那行不可达的 `return None` 没搬**（1.0
   `runtime_providers.py:244`）。行为等价。
8. **门禁登记**：`photobench-*` 四个模块进 `PENDING_WIRING`、九个端口方法进
   `PENDING_METHODS`，接线任务写的是 S9b 的 T911–T913。另记一条**本门禁的已知盲区**：
   `start` / `stop` / `list_models` / `gpu_stats` / `process_memory` 五个方法名因为
   photobench 自己的实现里就有 `self.*()` 调用，被判成「已接线」而**不在登记表里**——
   它们是漏报，不是接线。
9. **import-linter 只加了 `photobench` 的四条契约**（`domain-purity` /
   `infrastructure-purity` / `isolation` / 加进 `root_packages`）：`forbidden` 契约
   引用不存在的源模块会加载失败，而 `photobench.application` / `api` / `main` 要 S9b
   才建。S9b 建层时补 `photobench-application-purity` 与 `photobench-layers`。
10. **`check_reachability.py` 的 ROOTS 还没扩到 `photobench.main`**：S9a 没有入口，
    四个模块本来就该登记；**S9b 建出 `photobench.main` 时必须把 ROOTS 扩过去**，
    否则那张 PENDING 表会因为「根找不到」而永远合法。这条不变量现在无人守。
11. **`check_module_map.py` 的依赖边只统计包内 import**，所以 `photobench-isolation`
    契约（禁止 `sentrix.*`）是这条隔离的**唯一**机械保障——它不在模块地图里体现。
12. **`apps/api/tests/unit/test_architecture_rules.py` 的 `CONTRACT_COUNT` 从 5 改成 8**。
    这是 S9a 唯一改动的 sentrix 侧文件，理由是那个常量写死的是**仓库全部契约**的条数，
    而 `lint-imports` 一次跑两个包。**它同时暴露了一个反过来的缺口**：以后任何切片加
    契约都会红在一个与契约本身无关的地方——这次是 S9a 撞上的。
13. **`make ci` 的结果**：`1 failed, 2147 passed`，唯一失败是已知的模型侧 flaky
    `apps/api/tests/e2e/test_assistant_turn.py::test_the_answer_never_leaks_internal_identifiers`
    （两次全量跑都在同一个位置红）。因为 pytest 非零退出，`coverage` 那一步之后的
    目标没有跑；另外单独跑了一遍「去掉这条 flaky」的同一条链确认覆盖率和前端门禁。

### S9a 带出来的待定项与处置（2026-10-04）

实施者做完后提了五个「只服务 1.0 特有部署形态」的疑点。逐条处置：

| # | 疑点 | 处置 |
|---|---|---|
| 1 | `OrinLlamaCppTelemetryProvider`（端点/ssh 用户/PID 路径全写死成「模型在 118、PhotoBench 在 153」） | **保留**（用户裁决①「照搬全量」）。2.0 若两者同机它确实是死重量，但那是**部署决定**，不是迁移时该替用户做的决定 |
| 2 | `LocalAppleUnifiedTelemetryProvider`（只对 Mac M4 成立） | **保留**，同上。2.0 的目标机是 153，但 provider 可被替换——这正是把它做成端口的意义 |
| 3 | **`SYSTEM_PROCESS_PORTS` 默认值是 1.0 的部署**（8771 / 8091 / 8500） | **S9b 必须改**（2.0 是 8772 / 8191，model-manager 归 S11）。不改的话 PhotoBench 自己的端口判据是过期的——进程名关键词还能兜住，端口那条已经失效。可由 env 覆盖，所以是「改默认值」不是「能不能改」 |
| 4 | Apple 那份的 `SYSTEM_PROCESS_KEYWORDS` 是**独立一份**（多 `mlx`、不读 env） | **不合并**。1.0 如此；合并会悄悄改变 Apple 主机上的归属结果 |
| 5 | `backend/jetson_telemetry.py`（660 行）：1.0 的 `runtime_providers.py` **import 了它两个名字却从未使用**（F401），真正构造 Jetson provider 的是编排侧的 `select_runtime_providers` | **归 S9b**，与 `select_runtime_providers` 一起搬。它属于「provider 选择逻辑」那一块，不是 provider 本体 |

**另外两条实施者报上来的、我确认为需要跟进的：**

1. **`check_reachability.py` 的 ROOTS 还没扩到 `photobench.main`**——S9a 没有入口，四个模块
   本来就该登记（已在 `PENDING_WIRING` 里注明接线任务是 S9b 的 T911–T913）。
   **但 S9b 建出入口时必须把 ROOTS 也扩过去**，否则那张表会因为「根找不到」而永远合法。
   **这条不变量现在无人守**，已登记在此。
2. **一个门禁盲区（实施者如实报的）**：`LifecycleProvider.start/.stop`、
   `InferenceProvider.list_models`、`TelemetryProvider.gpu_stats/.process_memory`
   五个方法名，因为 photobench 自己的实现里就有 `self.*()` 调用，
   被 method-reachability 判成「已接线」而**不在登记表里**。**那是漏报，不是接线。**
   S9b 接上真调用者后要复查这五个是否真的可达。
3. **`test_architecture_rules.py` 的 `CONTRACT_COUNT` 5→8** 暴露一个反向缺口：
   它写死的其实是**全仓契约条数**，所以**以后任何切片加契约都会红在一个与契约无关的地方**。
   本轮照实改了；要不要把它做成动态的（读 `pyproject.toml`）留待后续。

### S9b-1 服务骨架与 HTTP 层（2026-10-04）

**交付点达成**：8772 起得来、能列出 run。建起 `application / api / bootstrap` 三层与
入口 `photobench.main`；契约从 8 条增至 **11 条**。

#### 一条反直觉的实测，推翻了「懒加载」的设计直觉（T914）

计划书 T914 说「先量再改，不要凭感觉」。量出来的结果**否定了一个很自然的直觉**：

| 步骤（463 MB 的 `run.json`） | 耗时 |
|---|---|
| 只读字节，不解析 | 2.9 s |
| `json.loads` 全部解析 | **2.3 s** |
| **纯 Python 逐字节扫一遍**（想跳过 `items` 只读其余键） | **18.3 s** |

**扫描比直接解析慢 8 倍**——纯 Python 的括号配对比 C 解析器慢一个数量级，
「跳过 items」是**净亏**。（实施者测到 24.8 s / 12×，我用更粗糙的扫描器复测得 18.3 s / 8×，
**结论一致**。）顺带修正一个文档数字：1.0 脚本注释写「约 55 秒」就绪，实测**要 75 秒**。

**所以选的方案是「照常解析、丢掉 items」，不是「懒解析」**，另加两条：
- **启动时后台扫描**（daemon 线程），**端口立刻绑**——实测 `uvicorn` 起来后 **t+3 s**
  就能拿到 200 与数据，扫描 **t+50~60 s** 完成（`total` 从 169 涨到 267）；
- **只留瘦条目**（每个几百字节），进程里任何时刻最多压着一个 run 的 items
  ——1.0 是 11 GB 全常驻；单 run 完整状态按需解析 + 2 条 LRU。

#### 验证

- **差分对照**：把 1.0 当预言机，对 3 个真实 run（0.4 MB/10 题、32.9 MB/100 题、
  **448.9 MB/1167 题**）比对 6 个纯函数，**逐字段一致**。
- **定点变异 12/12 被杀**。头一轮有 2 条存活，查明**是用例太弱**（见下）。

#### 又一条「夹具必须让两种口径给出不同答案」

两条存活变异都是**夹具不具区分力**：

1. **路径穿越**：原来只断言「抛 `KeyError`」——**不拦也一样抛**（文件根本不存在）。
   改成在 `results/` 的**兄弟目录**里真放一份 `run.json`，拦截器才成为唯一让它失败的东西。
2. **`scope_id` 早退**：原来断言「没抛异常」，可那段外面裹着 `except Exception`（1.0 `:6862`），
   真打网络失败也会被吞。改成断言假件的**调用记录为空**。

**这是本项目第五次同一个教训。**

#### 一处「把查得到的不变量换成查不到的」被挡住了

实施者原想给 `ModelProfiles` 的方法起名 `profiles`（跟 1.0 仓储名）。但
`check_method_reachability` 判「端口方法名**全仓唯一**」，一个同名 application 方法会把
`LifecycleProvider.profiles` 挤进**重名盲区**。改名 `query_profiles` 后**盲区名单归 0**。
**这是拿一条查得到的不变量换了查不到的，幸好门禁把它顶回来了。**

#### 五盲区复查（S9a 登记的那条）

| 方法 | 复查结论 |
|---|---|
| `InferenceProvider.list_models` | **现在真接线了**（本切片的 `/api/current-model` 调它，假件钉住） |
| `LifecycleProvider.start` / `.stop` | **仍是盲区**，一个真调用者都没有（属 S9b-2） |
| `TelemetryProvider.gpu_stats` / `.process_memory` | 同上 |

**改坏这几个方法名不会有任何用例变红**——如实登记，**别读成「已接线」**。

#### 门禁

`make ci`：`1 failed / 2234 passed / 6 skipped`，唯一失败是那条已知的模型侧 flaky；
coverage 94.35%；`web-check` 单独补跑绿；photobench 自己的 160 条用例 0.39 s。

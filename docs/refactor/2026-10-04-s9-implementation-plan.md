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

（施工时逐条填）

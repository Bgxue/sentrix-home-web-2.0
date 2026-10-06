# S11 偏离与收敛记录（T1104：四条收敛）

> 依据：`docs/refactor/2026-10-06-s11-implementation-plan.md` §3 四条；用户 2026-10-06 裁决「四条全做」。
> 本文只记 **T1104**。T1101–T1103 / T1105 / T1106 是并行切片，不在本文范围。
> 核查时间：2026-10-06，机器 asus@192.168.0.153。

---

## 0. 事实更正（相对 S11 计划书与设计文档的转录）

| # | 计划书/设计中写的 | 实测 |
|---|---|---|
| 0.1 | registry 第一处是「**2.0 仓库**的 `configs/…_192_168_0_153.json`」 | **错**。2.0 仓库（`sentrix-home-web2.0/`）**没有 `configs/` 目录**，也没有任何 registry 文件。那份 `configs/…_192_168_0_153.json` 在 **1.0 仓库** `/home/asus/Github/Sentrix-Home-Web/configs/` |
| 0.2 | 「启动脚本却指向 `…_local_100.json`」 | 对，但**只是其中一个脚本**：`1.0 repo/scripts/runtime/start_sentrix_api_8091.sh:62`。1.0 仓库里另有 `scripts/runtime/start_sentrix_api.sh:66` 指 **153 版**，而 8091 的 systemd ExecStart（`/home/asus/sentrix-8091-systemd.sh`）指**部署版**。当前活着的 8091 进程（pid 1765805，裸 uvicorn，**不带任何 `SENTRIX_VLLM_*`**）走的是 `backend/app.py:76` 的默认值 `/home/realmagic/sentrix-vllm/registry.json`（**100 机器的路径**）。所以实际上是**四处**口径，不是三处 |

---

## 1. A：`services/model-manager/` —— 结论「**不建**」

**处置：改文档，不补目录。** 已就地更正设计文档 §2.2（原 `:193`，现该行已删，代之以脚注）。

**依据（设计文档 §2.1，行号未动）**：
- `:139`「services/model-manager → managed model profiles（**2.0 只通过 HTTP 使用**）」
- `:147`「**2.0 不管理模型进程生命周期**。模型进程、锁、PID、显存采样、LoRA 加载逻辑都不进 2.0 的 API 进程」

**为什么补目录是错的**：`services/model-manager/` 若存在，它承载的只能是 profile 注册表、模型进程起停/锁/PID、显存采样、LoRA 加载——**逐条都在 `:147` 的禁止名单里**。2.0 里放不下任何「属于它」的内容，补目录等于把 1.0 的 8500 角色搬进 2.0，直接违反系统边界。因此按不建处置，model-manager 留在 1.0（8500）。

---

## 2. B：registry 三处不一致 —— 核实结果与收敛结论

### 2.1 三处各是什么、差在哪、谁在读

| 处 | 路径 | profiles | 谁在读 |
|---|---|---|---|
| ① 1.0 仓库（**不是 2.0**） | `/home/asus/Github/Sentrix-Home-Web/configs/sentrix_vllm_registry_192_168_0_153.json`（7453 B，2026-09-08） | 13 | `services/vllm_manager/manager.py:24-26` 与 `services/vllm_manager/app.py:33` 的**默认值**；`scripts/runtime/start_sentrix_api.sh:66`。**活的 8500 / 8091 都不读它** |
| ② 部署 | `/home/asus/sentrix-vllm/registry.json`（10036 B，2026-09-25） | **15** | **活的 8500**（`sentrix_vllm_api.py:26` 默认值，进程 pid 1268 无 `SENTRIX_VLLM_REGISTRY`）；`/home/asus/sentrix-8091-systemd.sh` 显式指它（该 unit 现为 dead） |
| ③ 100 机器版 | `/home/asus/Github/Sentrix-Home-Web/configs/sentrix_vllm_registry_local_100.json`（8170 B，2026-09-16） | 13 | `scripts/runtime/start_sentrix_api_8091.sh:62`。**该脚本不是当前 8091 的启动者** |

**内容差异（① vs ②）**：② 是 ① 的**超集/演进版**——② 多 `qwen3.5-4b-bnb4`、`qwen3.5-4b-lora`，且两者共有的 **12 个 profile 内容全都不同**（模型路径/参数已各自演进）。③ 是**另一台机器**（100，realmagic）的 profile 集（`gemma4-12b-it-bnb4-tp2`、`qwen3.5-0.8b-agent-closed-loop-v18` 之类），与 153 不是同一套。

### 2.2 谁权威、其余怎么处理

- **权威 = ② `/home/asus/sentrix-vllm/registry.json`**。理由：它是**唯一被活着的 model-manager 8500 读取**的那份（manager 的 `/registry`、`/switch` 都基于它），模型切换的事实来源就是它。
- **① / ③ 是 1.0 仓库里的陈旧快照，不是权威**。③ 更是**串了机器**（100 的 profile 挂在 153 的启动脚本上）——那是 1.0 的一个 bug。
- **处置：仓库外文件一律不动**（`/home/asus/sentrix-vllm/`、1.0 仓库均只读）。收敛产物落在 **2.0 可读处**：
  1. 本文件（权威判定 + 三处清单）；
  2. 设计文档 §10 的 registry 行已就地更正（注明「1.0 仓库」、标注 ② 权威、指到本文）；
  3. 2.0 **不需要**在仓库里放 registry 的副本——那只会制造第四处。2.0 要用模型 profile 时，**经 HTTP 向 8500 取**（`GET /registry`）。
- **留给 1.0 的待办（不在 2.0 范围，只登记）**：修 `start_sentrix_api_8091.sh:62` 的 `_local_100` 指向；决定 ① 是删除还是由运维从 ② 回灌。2.0 不复刻这些问题（设计 §10「修复，不迁移」）。

---

## 3. C：8500 在 2.0 的定位 —— **不需要自建，只需可选 HTTP 消费**

**明文一句话**（已写入设计文档 §2.1，紧随 `:147`）：
「**2.0 不建 model-manager**……2.0 只在需要时**经 HTTP 消费**它。」

**代码注释**：`apps/photobench/src/photobench/application/config/service.py` 的 `vllm_manager_url` 定义处，指向上面那句话。

**结论细节**：
- `apps/api`（2.0 主 API）全仓 grep `8500` / `model_manager` / `vllm_manager` **零命中**——主产品不碰 model-manager。
- PhotoBench 侧确实还带 `manager_url`：`config/service.py`（`vllm_manager_url`，来源 `runtime_connection.json` / `BENCH_VLLM_API_URL`）与 `config/vllm_targets.json`（每个 target 一个 `manager_url`）。它喂 `infrastructure/runtime/selection.py` 的 `ManagerLifecycleProvider` / `ManagerTelemetryProvider`（跑测时可选地经 HTTP 起停/采样模型）。
- **保留这条通路**。理由：它正是设计 §2.1「2.0 只通过 HTTP 使用 model-manager」的实现；删掉它等于砍掉「通过 HTTP 使用」这条被明文允许的能力。2.0 不自建 8500（不启停），但可选消费它。

---

## 4. D：PhotoBench 侧的 8091 残留 —— 收敛到 8191

改了 4 个文件（1 个是**新增的第 4 处**，见下）：

| 文件 | 改动 | 性质 |
|---|---|---|
| `apps/photobench/src/photobench/application/config/service.py:102` | fallback `http://127.0.0.1:8091` → `8191` | 代码，**受版本管理** |
| `apps/photobench/config/runtime_connection.example.json` | `8091` → `8191` | 模板，受版本管理（任务书未点名，但同属残留） |
| `apps/photobench/config/runtime_connection.json` | `http://192.168.0.153:8091` → `8191` | **部署者配置，`.gitignore` 忽略**（`config/runtime_connection.json`） |
| `apps/photobench/tests/test_config_service.py:131` | 断言兜底 `8091` → `8191` | 测试，跟随代码默认值 |

**关于 `runtime_connection.json` 的取舍（已先读其现有内容）**：它现有 6 个键（`sentrix_url` / `judge_url` / `judge_model` / `model_base_url` / `endpoint_model` / `judge_provider_id`），是 2.0 PhotoBench（8772）在本机**实际生效**的连接配置（`settings.config_root` 默认 = `apps/photobench/config`，`config.py:66`）。**决定：就地改**，因为 153 上这套部署就是 2.0 自己，它该指向 8191；但它是 **gitignore 的部署文件**，改动**不进 git**、也不会随仓库分发到别的实例。所以：
- **可分发的那半**是代码兜底 + `*.example.json`（已在 git 里改）；
- **每个已部署实例必须手工改**自己的 `runtime_connection.json`，否则它继续读旧值（文件值优先于兜底）。本机已改；**其他实例（若有）需运维照做**——这条记在这里，T1105 的 runbook 应带上。

**未动、且经核实属于「刻意保留」而非残留**：`infrastructure/runtime/jetson_telemetry.py:48` 的 `("8771","8091",...)`——S9a 已裁决 Jetson 那份端口集与 `providers.py` **独立**（注释在案），照搬 1.0，不改。

---

## 5. 门禁

见交付报告正文（原文输出）。

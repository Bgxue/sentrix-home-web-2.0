# S10 实施计划：PhotoBench 前端重构

> 路线图出处：设计文档 `docs/architecture/2026-09-21-sentrix-home-web-2-implementation-design.md:515`
> ——「**S10 | PhotoBench 前端重构 | 3,646 行 Vue 单文件 → React/TS | 评测控制台可用**」。
> **实测是 3,797 行**（文档陈旧，与 S9 的 7,751→7,997 同一类偏差）。
>
> 前置：S9 已完成（PhotoBench 后端在 2.0 跑起来了，8772，有自己的 openapi 契约）。
>
> **用户 2026-10-05 裁决：这一轮只做核查与计划，动工前停。**

---

## 0. 核查结论（2026-10-05）

### 0.1 1.0 那边的形状

| 项 | 值 |
|---|---|
| 位置 | `services/photobench/frontend/` |
| 主文件 | `src/App.vue` **3,797 行**（脚本 `:1-2836` 74.7% + 模板 `:2838-3797` 25.3%，**无 `<style>` 块**） |
| 样式 | `src/styles.css` 881 行 |
| 栈 | Vue 3.5 + **echarts 6**，**纯 JS 无 TypeScript** |
| 工程 | **无 lint、无测试、无 test 脚本** |
| 构建 | Vite 8；dev 端口 5173，`/api` 代理到 `${BENCH_PORT\|8771}` |
| 杂物 | 6 个 `App.vue.bak-*` 备份文件（未分析） |

**难度分布**（按 grep 区块估）：

| 类别 | 占比 | 说明 |
|---|---|---|
| 纯格式化/展示 | ~55% | 可机械翻译 |
| **trace 解析与图数据构造** | ~20% | `:1340-1809`（~470 行）最重、最易出错，**需测试护航** |
| 网络与状态编排 | ~25% | `:2233-2835`（~600 行），重写核心 |

### 0.2 **7 个后端端点 2.0 没有**（这是 S10 的前置缺口）

| 端点 | 用途 | 2.0 |
|---|---|---|
| `GET /api/runs/{id}/keyframe-analysis` | 关键帧分析面板 | ❌ 全仓无实现 |
| `GET /api/runs/{id}/memory-effectiveness` | 记忆有效性 / 错误链 | ❌ |
| `GET /api/memory-spaces` | 复用相册基座列表（带 `sentrix_url`） | ❌ |
| `GET /api/runs/{id}/export-trace` | 导出 trace（`window.open` 下载） | ❌ |
| `POST /api/test-model` | 「测试模型端点」按钮 | ❌ |
| `GET /api/manifests` | 相册 manifest 列表（配置区相册下拉依赖） | ❌ |
| `GET /api/qa-dataset` | QA 数据集浏览页的数据 | ❌ |
| `GET /api/assets/{id}/file`、`GET /api/albums/{album}/{coll}/{file}` | 媒体展示 | ❌ |

**其中 4 条是「评测控制台可用」的硬依赖**（manifests / qa-dataset / 媒体服务 / memory-spaces）——
少了它们，配置区选不了相册、QA 浏览页没数据、结果里的图看不了、复用基座选不了。

### 0.3 一处**纠正**：前端没有用 `model-profiles/*`

grep `App.vue` 全文，`model-profiles` **零命中**。前端换模型走的是
`POST /api/current-model`（`:2618/:2622`），**2.0 有这个端点**。

所以那不是缺口。**但交互语义要重写**：1.0 的 popover 是「不重启切端点」，
2.0 换成了「重启 2.0」（S9 的裁决③）。这块 UI 要按新语义重新设计，
不是把 1.0 的搬过来。

### 0.4 一处**死面板**：arbiter 状态区

模板 `:2845-2856` 是一个 8 格的「实时调度状态（VLMArbiter）」区，
`loadArbiterStatus()`（`:2711-2723`）每 **3 秒**轮询 `GET /api/arbiter-status`——
**该端点在 S9 已被删除**（1.0/2.0 主 API 都不存在这个能力）。
**S10 应删掉这块面板**，不是把它搬过来。

`ingest-batches/cancel` 前端本来就没用（grep 空），无需处理。

### 0.5 与 `apps/web` 的复用判断（有依据，非偏好）

| 判断 | 依据 |
|---|---|
| **只复用工程约定与配置模板** | `tsconfig` 的 project references 结构、`.oxlintrc.json`、vitest+jsdom、`vite.config.ts` 的写法、以及 `make web-check` 的四步顺序（类型 → lint → 测试 → build）。`Makefile:93-113` 对这些约定写了明确理由（如 `tsc -b --force` 防增量漏检）——照做就继承这些**踩过的坑** |
| **不复用 `shared/api/client.ts`** | 它 `BASE = "/api/v1"` 且按 sentrix 的 `{code,message,detail}` 错误体解析；PhotoBench 是独立包、前缀 `/api`、有自己的 `api/errors.py`。改它等于改坏主产品客户端 |
| **不复用 `shared/ui/*` 与 `styles/tokens.css`** | 前者只有 5 个小展示组件；后者文件头自述是**逐行移植 1.0 主产品样式**，而 PhotoBench 有自己那 881 行 `styles.css`。搬过去会改变观感，**违背「迁移非重设计」** |
| **必须新增 `echarts`** | `apps/web` 里没有任何图表库（grep 空），而 PhotoBench 的遥测图全靠它 |

---

## 1. 动工前要定的（**用户裁决后再动**）

### ① 7 个后端缺口怎么处理（**这一段最大的问题**）

`manifests` / `qa-dataset` / 媒体服务 / `memory-spaces` 是控制台可用的硬依赖，
但 2.0 后端目前**没有任何 HTTP 层**提供数据集、媒体、manifest 与 memory-spaces。

三条出路：

| 方案 | 含义 | 代价 |
|---|---|---|
| **A. S10 一并补后端** | 前端 + 7 个端点一起做 | S10 从「前端重写」变成「前端重写 + 半个后端切片」，规模显著变大 |
| **B. 前端先做能做的，缺口面板留空** | 只做已有 8 条端点的部分；缺口面板挂「尚未开放」空状态 | 与设计文档「评测控制台可用」有距离；但**与主产品前端 F7 的处置同一条口径**（后端未实现的能力 → 空状态） |
| **C. 先补后端，S10 拆成 S10a/S10b** | 先把 7 个端点补上，再做前端 | 段数增加，但每段都能单独验收 |

**用户 2026-10-05 裁决：选 C —— 拆两段，先补后端（S10a）再重写前端（S10b）。**

理由与我的倾向一致：这 7 个端点里 `manifests`/`qa-dataset`/媒体服务**都不是评测能力**，
是「把本地数据集与结果图端出来」的展示层——它们与 S9 做完的评测链是两回事，
所以值得单独成段、单独验收，而不是混进前端重写里。

### ② 前端端口

后端在 **8772**（设计文档 §5.1）。前端 dev 端口建议 **4275**（`apps/web` 用 4274），
`/api` 代理到 `127.0.0.1:8772`。**待与设计文档端口表核对后确认。**

### ③ 要不要引 `echarts`

PhotoBench 的遥测图、记忆层级图全靠它。`apps/web` 没有图表库，
所以这是**给 PhotoBench 前端单独加一个依赖**——与「生产依赖只有 3 个」那条主产品原则不冲突
（那是 `apps/web` 的原则，PhotoBench 是另一个 app）。但**要你确认**。

---

## 1.5 S10a：补齐 7 个后端端点（**先做这一段**）

**验收**：`apps/photobench` 的 openapi 契约从 16 条路径增到约 24 条，
且前端要用的每一条都有真实响应可读。

**范围是 1.0 已有的那 7 条 + 2 条媒体服务**（§0.2 那张表）。
**1.0 是唯一权威**：`services/photobench/backend/benchmark_orchestrator.py`
里对应的 handler 要逐条读、照搬响应形状。

| # | 内容 |
|---|---|
| T1001 | **数据集展示层**：`GET /api/manifests`（相册 manifest 列表）、`GET /api/qa-dataset`（QA 数据集浏览）。数据源在 `apps/photobench/datasets/`（1.4 GB，按 `DATASET.md` 声明不入 Git）——**先核实目录形状与 DATASET.md 的约定** |
| T1002 | **媒体服务**：`GET /api/assets/{id}/file`（从 run 结果里取图）、`GET /api/albums/{album}/{coll}/{file}`（相册本地媒体）。**注意路径穿越**——1.0 那条直接拼路径，2.0 要拦（S9b-1 已有先例与用例） |
| T1003 | **`GET /api/memory-spaces`**：1.0 是编排器**转发** Sentrix 核心的接口（带 `sentrix_url` 参数）。2.0 要转发到 `apps/api` 的 `/api/v1/memory-spaces`——**这是 PhotoBench 唯一一处要调主 API 的读侧**，注意别破 `photobench-isolation` 契约（那是禁止 import，不是禁止 HTTP） |
| T1004 | **`POST /api/test-model`**：测模型端点是否可用。**2.0 的语义可能与 1.0 不同**（换模型已改成「重启 2.0」）——先读 1.0 那条做什么，再决定 2.0 版测的是什么 |
| T1005 | **结果读侧三条**：`GET /api/runs/{id}/keyframe-analysis`、`/memory-effectiveness`、`/export-trace`。前两条要从 run 的 `results.jsonl` 里算（S9b-1 的 `domain/runs/analysis.py` 已有那批纯函数可扩），第三条是导出下载 |
| T1006 | 收口：photobench 的 openapi 重跑、`capability-map` / `photobench-module-map` / `behaviour-baseline`、真机验收（**对 1.0 的真实 run 结果跑一遍**，比对 1.0 的响应） |

**S10a 的风险点**：`keyframe-analysis` 与 `memory-effectiveness` 是**两条最重的计算**
（1.0 的前端里对应 `:1810-2055` 那 245 行图数据构造）。**先读 1.0 的 handler 到底返回什么**，
再决定是「后端算好给前端」还是「原样透出原始数据让前端算」——1.0 是哪一种就照哪种。

---

## 1.6 裁决

| # | 问题 | 裁决 |
|---|---|---|
| ① | 7 个后端缺口怎么处理 | **拆两段**：S10a 补后端 → S10b 重写前端 |
| ② | 前端端口 | 待核（建议 4275，`apps/web` 用 4274；后端在 8772） |
| ③ | 要不要引 `echarts` | **引**。1.0 的遥测图、记忆层级图全靠它；主产品那条「生产依赖只有 3 个」是 `apps/web` 的约束，PhotoBench 是另一个 app |

---

## 2. 建议的文件结构（待裁决后细化）

```
apps/photobench/web/            ← 与 apps/photobench/src/ 平级，照 apps/api + apps/web 的共存体例
├── package.json                React 19 + react-dom + react-router + echarts
├── index.html / vite.config.ts / tsconfig*.json / .oxlintrc.json / .gitignore
└── src/
    ├── main.tsx / App.tsx / router.tsx     两个视图：评测运行 / QA 数据集浏览
    ├── api/client.ts                       新写（BASE=/api，对齐 photobench 的 errors.py）
    ├── features/runs/                      列表 + 详情 + 发起配置
    ├── features/qa/                        逐题结果
    ├── features/qa-browser/
    ├── features/telemetry/                 echarts
    └── styles/                             移植 1.0 styles.css 881 行
```

**规模估计**：3,797 行 Vue → 拆成约 20–35 个文件，**净增（含类型与测试）预计 4,500–6,000 行**。

**门禁**：加 `photobench-web-check`（同 `web-check` 四步，`cd apps/photobench/web`），
`install` 里加 `npm ci`，`ci` 链尾追加——**不要混进 S9 那个 `photobench-check`**（它只覆盖后端 src）。

---

## 3. 施工记录

（动工后填）

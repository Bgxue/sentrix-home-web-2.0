# S11 实施计划：服务与部署

> 路线图出处：设计文档 `docs/architecture/2026-09-21-sentrix-home-web-2-implementation-design.md:516`
> ——「**S11 | 服务与部署 | model-manager 收敛、端口/进程方案、runbook | 主 API / Web /
> PhotoBench / model-manager 独立启停**」。
> **S11 是切片表最后一条，没有 S12**（全仓 grep `S12` 零命中；`M3 = S8–S11`，`:522`）。
>
> **用户 2026-10-06 裁决**：四条收敛全做 / `systemd --user` / dist 挂到各自 FastAPI。

---

## 0. 核查结论（2026-10-06）

### 0.1 现状：2.0 四个进程全是裸进程

| 端口 | 进程 | 启动方式 | Makefile |
|---|---|---|---|
| 8191 | 主 API | 前台裸进程 | `dev` |
| 8772 | PhotoBench 后端 | 前台裸进程 | `photobench-dev` |
| 4274 | Web | vite dev | `web-dev` |
| 4276 | PhotoBench 前端 | vite dev | `photobench-web-dev` |

**没有 systemd unit、没有 `start`/`stop`/`restart` 目标、没有 `scripts/deploy/`、没有 runbook。**
机器重启即全丢——与 1.0 当初 8091 那个坑同型（`docs/runbooks/153-services.md` 记着
1.0 的 8091 曾是一个 `setsid` 脱离的裸进程，systemd 显示 dead 而端口被占）。

**唯一的 pid 记录**是 `apps/photobench/logs/sentrix-process.json`——它由 PhotoBench 在
「换模型重启 2.0」时写，**只服务那一条路径**，不是通用的权威记录。

### 0.2 1.0 的对照

1.0 **只有 8091 与 8500 在 systemd 下**（前者 system、后者 `--user`），其余裸进程 + 脚本。
`scripts/deploy/` 7 个、`scripts/runtime/` 13 个。

### 0.3 设计文档的四处**与现状矛盾**（照实指出，不迁就文档）

| # | 文档说 | 现状 |
|---|---|---|
| 1 | §2.2（`:193`）列了 `services/model-manager/` | **2.0 仓库里没有 `services/`** |
| 2 | §5.1 端口表（`:364-369`） | **缺 4276（S10b 新增）与 8102（W1 引入）** |
| 3 | §5.1 把「2.0 前端」写作 4274 | 4274 是**主产品 Web**；PhotoBench 前端是 4276，**不在任何设计条文里** |
| 4 | §10（`:571`）点名 registry 三处不一致 | 仍然不一致 |

---

## 1. 裁决（用户 2026-10-06）

| # | 问题 | 裁决 |
|---|---|---|
| ① | 「model-manager 收敛」收什么 | **四条全做**（见 §3） |
| ② | 进程托管形态 | **`systemd --user`**（不需 sudo；1.0 的 8500 就是 user unit） |
| ③ | 前端 dist 怎么交付 | **挂到各自的 FastAPI**（8191 顺带 serve Web、8772 顺带 serve PhotoBench 前端） |

### ① 的一个推论，登记备查

验收口径写的是「主 API / **Web** / PhotoBench / model-manager 独立启停」。
**裁决③之后，"Web" 与主 API 是同一个进程**（dist 由它 serve），PhotoBench 前端与
PhotoBench 后端同理。所以交付形态是 **2 个 2.0 单位**（8191、8772）+ 1 个**不由 2.0 管**的
model-manager。

这不是缩水：**设计 §2.1（`:147`）明写「2.0 不管理模型进程生命周期」**，所以
「model-manager 独立启停」的含义是**它独立于 2.0**——2.0 起停不碰它，它起停不影响 2.0。
这一条是**要验证的性质**，不是要写的功能。

---

## 2. 端口与进程方案（定稿）

**这份表取代设计文档 §5.1**（那份落后于 W1 与 S10b）：

| 端口 | 归属 | 2.0 的处理 | 谁托管 |
|---|---|---|---|
| 8091 / 4174 / 8771 | 1.0 API / Web / PhotoBench | **保持运行**，M3 之前是权威 | 1.0 自己 |
| **8191** | **2.0 主 API**（生产时同时 serve Web dist） | 本仓库 | `sentrix-api.service`（--user） |
| **8772** | **2.0 PhotoBench 后端**（同时 serve 它的前端 dist） | 本仓库 | `sentrix-photobench.service`（--user） |
| 4274 / 4276 | 两个前端的 **vite dev** | **仅开发用**；生产走各自的 FastAPI | 手动 |
| 8100 | llama-server（OpenAI 兼容 `/v1`） | **直接复用** | 1.0 的脚本 |
| 8101 | bge-m3 sidecar | **直接复用** | 由 8091 启动器按需拉起 |
| 8102 | WeMM sidecar（**W1 引入**） | **直接复用** | 手动 |
| 8500 | model-manager API | **通过 HTTP 使用**；2.0 不启停它 | 1.0 的 `sentrix-vllm-api.service`（--user） |
| 11434 | ollama（**别人的项目**） | 不碰 | — |

---

## 3. 四条收敛（裁决①）

| # | 收什么 | 依据 |
|---|---|---|
| **A** | `services/model-manager/` 的文档-仓库矛盾 | 文档 §2.2（`:193`）承诺、仓库没有。要么补目录，要么改文档承认不建 |
| **B** | **registry 三处不一致** | 文档 §10（`:571`）自己点名：仓库一份、部署 `/home/asus/sentrix-vllm/registry.json`、启动脚本指向 100 机器的 `_local_100.json` |
| **C** | 定清楚 8500 在 2.0 的定位 | 2.0 主 API 全仓 grep `8500` 零命中；只有 PhotoBench 侧的 run 配置还带 `manager_url` |
| **D** | PhotoBench 侧的 **8091 残留** | `application/config/service.py:102` 的 fallback 与 `config/runtime_connection.json` 仍指 1.0；S9 改了 `SYSTEM_PROCESS_PORTS` 却漏了这两处 |

---

## 4. 切片

| # | 内容 | 验收 |
|---|---|---|
| T1101 | **前端交付**：两个 `main.py` 挂 StaticFiles（`apps/web` 有 react-router，需 SPA fallback；PhotoBench 无 router，直接挂） | 不起 vite，8191/8772 各自能出界面 |
| T1102 | **systemd --user 两份 unit + 启停入口** | `systemctl --user start/stop sentrix-api` 真的起停 8191 |
| T1103 | **deploy 模块接管**：`SubprocessSentrixProcess` 的 `restart` 改走 `systemctl --user restart`，运行端点真相来源改成权威记录 | 换模型那条链仍通（真机） |
| T1104 | **四条收敛** | 见 §3 |
| T1105 | **runbook**：`docs/runbooks/2.0-services.md`（启停、迁移、重建索引、故障处理） | 文档 |
| T1106 | **收口验收**：独立启停的真机验证 | 见 §5 |

---

## 5. 验收

**验收口径是「独立启停」**，所以要验的是**独立性**，不是「起得来」：

1. `systemctl --user stop sentrix-api` → 8191 消失、**8772 不受影响**；
2. 反向同理；
3. 两个都停 → **8500 与 8100/8101/8102 不受影响**（2.0 不碰它们）；
4. 8500 停掉 → **2.0 仍能启动**（设计 §2.1：2.0 不管理模型生命周期）；
5. 重启机器（**若你能安排**）→ 两个 unit 自启。

**真机验收的两个坑**（S8b/S10a 踩过）：**先重启服务再验**，否则测的是旧代码；
**每次换内容**，否则命中内容哈希去重。

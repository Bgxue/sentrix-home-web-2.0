# S3 偏离审计

> 判据：**1.0 的大部分功能已经过验证；要做的是「找出 1.0 里有用的部分搬过来」**。
> 只有**有明确证据**说明 1.0 的做法是错的时，才允许偏离——证据必须能摆出来。
> 这一轮把 S3 的 9 处偏离逐条对回 1.0：**7 处回退成忠实移植，2 处保留并附证据。**

1.0 只读仓库：`/home/asus/Github/Sentrix-Home-Web`。
数据来源：`data/sentrix.db` 的 `agent_trajectories`（25,112 条轨迹）、
`services/photobench/results/*/run.json`、`logs/sentrix-api-8091*.log`。

---

## 1. 审计表

| # | 1.0 怎么做 | 1.0 为什么 | S3 改成了什么（当初的理由） | 证据强度 | 结论 |
|---|---|---|---|---|---|
| 1 | 规划是**一次模型调用**（`goal_planner.py` 的 `GoalPlanner.declare`），最多 3 次带错误回灌重试，失败记 `planner_call_error` → `planner_blocked` | 要产出的是「这道题需要哪几类证据」，是语义判断，规则判不了 | 纯函数规则（`domain/assistant/intent.py`）；理由「`planner_call_error` 出现 1,974 次」 | **无**（见 §2，那 1,974 次全在端点不可用的黑障窗口里） | **回退** |
| 2 | 模型在**正文里写 JSON**（`{"action":"tool_call",...}`），配一套 150 行解析修复器；`model_clients.py` 里**没有任何** `tools` 支持 | 为兼容不支持原生工具协议的端点（1.0 的注释点名 DeepSeek 会因为孤立的 `role=tool` 报 400） | 标准 JSON Schema + 原生 `tool_calls` | **强**（见 §3） | **保留** |
| 3 | 需求状态机 11 个状态 + `_TRANSITIONS` + 9 条 `UNMET_REASONS` | — | 3 个状态（open / satisfied / blocked） | **强**（1.0 自己说那 11 个是 legacy，见 §3） | **保留** |
| 4 | SSE 能跑，且轮询端点 `GET /api/assistant/turn/{id}` 是声明过的 **B3.4 契约**，前端 `src/api.js:64` 与评测机 `wait_for_assistant_turn` 都在用 | 非浏览器调用方没有 `EventSource` | 只留 SSE，轮询端点做成 404；理由「1.0 前端一 onerror 就降级轮询，轮询成了主路径」 | **无**（66 万次轮询的调用方是评测机，见 §4） | **回退** |
| 5 | `judge.py` 的 L2 语义评审：每次回答前一次模型调用，判编造/矛盾/漏报/过度声称 | 「回答是否合格由模型判断，代码不用词语/数字正则做等价匹配」 | 推迟到 S6；理由「每次评审是一次模型调用，S3 的模型预算要留给主循环」 | **无**（1.0 生产库有 1,669 条 `l2_soft_release`，见 §3） | **回退** |
| 6 | `final_guard.py` 的规则表：安全硬拦 5 条 + 占位符 + 未检索却否认 + 存在性矛盾 4 条 + `all_requested_but_has_more` + `delivery_contradiction` | 「代码永远不做语义等价判定」（1.0 为此删掉了一批正则） | 自加了一条「证据门」（需求还开着就拦），并删掉 `_ANY_DENIAL` 整句否定门槛 | **无** | **回退**（并补回漏掉的 4 条规则） |
| 7 | `/api/assistant/result-set/{id}/photo`：handle 必须属于该结果集、scope 必须匹配 | 让前端能打开图而 URL 里不出现 `asset_id` | 不搬；理由「2.0 的 web 侧本来就看得到 `asset_id`」 | **无**（那是「现在不需要」，不是「1.0 做错了」） | **回退** |
| 8 | `_detect_policy_refusal`：9 条正则 + 4 条固定文案，在任何模型调用**之前**返回 | 注入 / 人脸特征向量外泄 / 越权写 / DAN 越狱的确定性边界 | 不搬；理由「那是 benchmark 时代的产物」 | **无**（生产库 212 次命中，0.84%，见 §3） | **回退** |
| 9 | 没有「空答案」这个终止原因；模型输出不可用归进 `parse_failure` | — | 新增 `empty_answer` 终止原因 + 一次提醒 | **无**（1.0 从没以 `complete` 交付过空答案，见 §3） | **回退**（并回 `parse_failure`） |

---

## 2. 第 1 项的相关性分析：`planner_call_error` 是评测机的操作方式造成的

**问题**：25,112 条轨迹里 `telemetry.reason == "planner_call_error"` 出现 1,974 次
（7.86%）。这看起来像规划器本身有问题。

**结论：不是。它是一次也没被证实过的「规划器设计缺陷」——那 1,974 次全部落在模型端点
不可用的黑障窗口里。**

### 2.1 这条路径只在模型调用**抛异常**时产生

1.0 `goal_planner.py:135`：

```python
            except Exception:
                # 裸 except 会让 planner 失败原因彻底消失（外层只看到 planner_call_error，
                # 分不清是超时、连接被拒还是 400）。必须留下真实异常，否则只能靠猜。
                log.exception("planner call failed: ...")
                return PlannerDeclarationResult(fallback_reason="planner_call_error", ...)
```

`planner_call_error` ⟺ `chat_fn` 抛异常。解析失败走的是**另外两个**原因：
`invalid_planner_action`（141 条）与 `scope_mismatch`（11 条）——它们全在正常窗口里
（见 2.5）。

### 2.2 时间分布：1,974 次挤在 15 分钟里

```
分钟                 该分钟 planner_call_error / 该分钟全部回合
2026-08-25T10:25      1 / 1
2026-08-26T08:53      5 / 5
2026-08-26T16:34     48 / 49
2026-08-26T16:53     53 / 57
2026-08-26T17:44     63 / 67
2026-09-08T09:44    251 / 266
2026-09-08T13:44    206 / 209
2026-09-08T13:50      2 / 2
2026-09-08T13:51      2 / 2
2026-09-16T16:17     96 / 96
2026-09-16T16:21     53 / 53
2026-09-16T16:22     58 / 58
2026-09-17T10:14    257 / 261
2026-09-17T10:15    393 / 396
2026-09-17T10:16    439 / 439
```

**这 15 分钟覆盖 1,927 / 1,974 = 97.6% 的失败。** 剩下的 47 条散在整整 25,112 条语料里
——占全部回合的 **0.20%**。

设计缺陷产生的是**稳定比率**；总黑障产生的是「某一分钟 100% 失败、前一分钟 0% 失败」。
这里是后者：09-16T16:17 是 96/96，09-17T10:16 是 439/439。

### 2.3 同一分钟里，延迟分布是「连接被拒」的形状

| 延迟区间 | 条数 |
|---|---|
| ≤ 0.3s（其中 0.12s 有 1,672 条） | 1,884 |
| 0.3–2.5s | 73 |
| 60s | 7 |
| 182–186s | 3 |

中位数 **0.13 秒**。模型「给出坏输出」不会用 0.13 秒——那是端口没人听的形状。

### 2.4 同一窗口里，别的模型调用记的就是连接失败

在 `planner_call_error` 的黑障窗口里，**其他**模型调用（工具循环那些）留下的原始错误：

```
album_cbfbfce14789  2026-09-17T10:00–10:25
  planner_call_error / planner_blocked        1089
  model_call_error: [Errno 111] Connection refused   1
album_35b4e0e1d3cb  2026-09-08T09:40–09:55
  planner_call_error / planner_blocked         253
  model_call_error: model request failed: timed out   4
  model_call_error: [Errno 111] Connection refused    1
album_ca0cc0ddda3a  2026-08-26T16:30–16:40
  planner_call_error / planner_blocked          48
  model_call_error: ... timed out                1
```

`planner_call_error` 只是**没有把异常原文记下来**的那一个（1.0 的裸 `except` 吞掉了它，
只在日志层面 `log.exception`）。它和同窗口里的 `[Errno 111] Connection refused`
是同一件事。

### 2.5 与模型切换事件对齐

PhotoBench 的 `benchmark_orchestrator.execute()` 对**每一轮**都执行：

```python
lifecycle.stop({"timeout": 60}, timeout=90)      # 把模型服务停掉
time.sleep(5)
lifecycle.start({"profile": profile, "wait_ready": True, "ready_timeout": 600}, ...)
request_json(f"{sentrix_url}/api/model-profiles/bind-runtime", ...)
```

（`services/photobench/backend/benchmark_orchestrator.py:5060-5071`；`bind-*` 在 8091
访问日志里出现 41 次。）

把 1,974 个失败点与 `run.json` 里的模型切换边界对齐：

| 黑障窗口 | 最近的换模型事件 | 相差 |
|---|---|---|
| 08-26T16:34 | `20260826-162748` 起（qwen3.5-4b） | 391s |
| 08-26T16:53 | `20260826-165328` 起（qwen3.5-0.8-lora-v2） | **19s** |
| 08-26T17:44 | `20260826-174317` 起（gemma4-12b-it） | **86s** |
| 09-17T10:14–10:16 | `20260917-090633` 起（qwen3.5-0.8b-it → 0.8-lora-v2） | **298s** |
| 09-08T09:44 | `20260827-223513` 之后 / `20260908-095918` 之前 | 877s |
| 09-16T16:17 | `20260916-151252` 结束（16:10:04）之后 | 6320s |

**六个窗口与换模型边界重合的四个在 900 秒以内，其中三个在 100 秒以内。**
剩下两个（09-08T09:44、09-16T16:17）落在换模型/停服的窗口里，但 `run.json` 里没有
对应的一轮——它们是同一类事件，只是触发者在结果目录里没留下记录（手工换模型、
服务重启、或结果被清理）。**它们的形状与其余窗口完全一致**（同 scope、100% 失败、
0.1 秒级延迟），所以归为同一类。

### 2.6 非 PhotoBench 口径是 0

| scope | 回合数 | `planner_call_error` |
|---|---|---|
| `album3-v2`（手工驱动的老口径） | 763 | **0（0.0%）** |
| `album_9a2480247e5b` | 1,337 | 0 |
| `album_8771f4b50b54` | 1,337 | 0 |
| `album_a0050080d1f0` | 1,314 | 0 |
| `album_aa3f780834c2` | 1,063 | 0 |
| `album_7f529960ee78`（横跨 08-26 → 09-16） | 1,315 | 1（0.1%） |

同一个 scope 内部也一样：`album_35b4e0e1d3cb` 有 1,366 条、时间跨度 08-27 → 09-14，
**253 条失败全部集中在 09-08 的 180 秒里**，其余时间 0%。

### 2.7 结论

> 失败集中在模型切换前后 → **是 benchmark 操作方式造成的，不是设计缺陷**。

按判据，第 1 项**必须回退**。规划回到 1.0 的 `GoalPlanner`：一次模型调用、最多三次带
错误回灌重试、失败以 `planner_blocked` 收场。见 `docs/refactor/capability-map.jsonl`
的「规划：模型声明证据需求」与 commit `回退纯函数规划`。

**唯一一处收紧**（写在代码里）：`MIN_REQUIREMENTS = 1`。1.0 允许零需求，JIT 提示词
据此告诉模型「当前没有待确认的证据需求……可直接输出 final」——1.0 实测 29.3% 的回合
一次工具都没调。这条收紧的依据是那个失败率，不是偏好。

---

## 3. 保留清单（证据）

### 3.1 第 2 项：原生 `tools` / `tool_calls`

- 1.0 `backend/model_clients.py` 里**没有任何** `tools` / `tool_calls` / `tool_choice`
  的代码（`grep -n "tools\|tool_calls\|tool_choice" backend/model_clients.py` 只命中一条
  无关注释）。1.0 的正文 JSON 协议是**唯一**的实现，不是「在多方案里选了它」。
- 它的代价是可测量的：正文 JSON 协议生产了 239 次解析类失败，**全部在正常窗口里**
  （不是黑障）：

  | `telemetry.reason` | 条数 | 是否在黑障窗口 |
  |---|---|---|
  | `invalid_planner_action` | 141 | 全部否 |
  | `unparseable_action` | 87 | 全部否 |
  | `scope_mismatch` | 11 | 全部否 |

- 1.0 为此写了 150 行解析修复器（`runtime.py:1586-1661`：剥围栏、补花括号、补引号、
  删尾随逗号）与 `_action_candidates` 的六轮候选生成。2.0 只用 8100 一个端点，
  它的 llama.cpp 原生支持 `tools`（`finish_reason="tool_calls"`），这套修复器连同它的
  失败模式一起消失。
- 反方向的证据也在：1.0 库里有 21 条 `400 Bad Request ... Messages with role 'tool'
  must be a response to a preceding message with 'tool_calls'`（来自 `api.deepseek.com`）
  ——那说明 1.0 试过原生协议，但它的多后端定位要求一条对所有端点都成立的路径。
  **2.0 只连 8100**（设计里写明「2.0 只连它，不管理它的进程」），那个约束不成立。

### 3.2 第 3 项：需求状态机 11 → 3

- **1.0 自己的代码说那 11 个状态是 legacy**（`backend/agent_runtime/task_state.py`）：

  ```python
      def recompute_status(self, *, has_available_tools: bool = True) -> str:
          """Derive the single task status from requirement evidence.

          Legacy per-requirement states are retained for replay compatibility, but
          this status is the only completion decision used by the production path.
          """
  ```

  生产路径只用那个派生的单一状态（`complete` / `in_progress` / `insufficient_evidence`）。
  11 个 per-requirement 状态留着是为了**回放旧数据**，而 2.0 没有旧数据要回放。
- 第二份独立证据：25,112 条轨迹里 `steps_json` 含 `requirement_status_after` 的
  **0 条**——那 11 个状态在生产里从来没有被记下来过。
- 1.0 的 `blocked_budget` / `unavailable` / `unsupported` 三种「拿不到」在 2.0 归到
  `RequirementStatus.BLOCKED` + `unmet_reason`，语义没丢。

### 3.3 第 5 项的另一半：L2 的**调用条件**保留 1.0 的收窄

移植时保留了 1.0 的三个收窄条件，没有放宽：每问只评审一次、评审计一个模型步、
没有工具结果就不评审。见 `docs/refactor/capability-map.jsonl` 的「答案门禁 L2（语义评审）」。

---

## 4. 第 4 项的三问

### 问 1：1.0 的 SSE 端点（`backend/app.py:2969`）能用吗？

**能用，没有已知缺陷逼着前端去轮询。** 它是一个正常的 `StreamingResponse`：

- 事件契约 `progress` / `complete` 与 2.0 现用的那份**逐字段相同**（2.0 的
  `ProgressEventPayload` 就是照它抄的）；
- 每 0.3 秒发一条 `: keep-alive` 注释帧，连接不会闲着被中间层掐掉；
- 它的 docstring 自己写着「前端断开/失败可回退轮询（`/api/assistant/turn/{turn_id}`
  仍保留快照）」——**轮询是 1.0 有意保留的快照接口，不是事故**。

它确实有局限（2.0 修掉了）：`sent` 是闭包局部变量，重连从头重发；`_TURN_JOBS` 在
进程内存里，重启即丢。这两条都只影响「断线之后」，不影响「连得上时能不能用」。

### 问 2：轮询端点是公开 API 契约的一部分吗？

**是。** 三条独立证据：

1. 1.0 的 docstring 把它标成 **B3.4**（`backend/app.py:2938`：
   「B3.4：轮询异步 turn 的实时进度与最终结果」）——它是有编号的对外契约。
2. 前端 `src/api.js:64` 把它绑定成 `assistantTurnPoll`，`src/app.js:1526` 用它。
3. SSE 端点自己的 docstring 把它写成「仍保留快照」——**两条传输是同一份设计的两个面**。

### 问 3：668,490 次轮询是前端主动轮询，还是 SSE 失败后的回退？

**都不是——那是评测机的轮询循环。**

前端的顺序确实是 SSE 优先（`src/app.js:1482`：「优先 SSE 实时事件；EventSource 不可用
时回退 700ms 轮询」，`if (!done) done = await pollTurnEvents(...)`），这一点 2.0 抄对了。

但那 66 万次的调用方不是这个前端：

| 证据 | 数值 |
|---|---|
| `logs/sentrix-api-8091.log` 里 `GET /api/assistant/turn/{id}` | **668,552** |
| 同一份日志里 `GET /api/assistant/turn/{id}/events`（SSE） | **62** |
| 单条访问日志片段（`turn_901fed5fda7e` 首次出现处） | 十几个不同 `turn_id` 交替紧密轮询，中间只有 `GET /api/arbiter/status`，**没有任何浏览器 UI 流量** |

- 比值约 **1 : 10,782**。如果轮询是「SSE 断了才走」的回退，SSE 至少该被打开过同样的
  次数级；62 次说明浏览器几乎没用过这条链路。
- 「十几个回合交替、紧密、无 UI 流量」是**机器**的形状。浏览器不会同时开着十几个
  助手回合轮流打状态端点。
- 调用方在代码里找得到：`services/photobench/backend/benchmark_orchestrator.py:881`
  的 `wait_for_assistant_turn`，`time.sleep(0.5)` 轮询到回合结束：

  ```python
      poll_url = f"{base_url.rstrip('/')}/api/assistant/turn/{quote(str(turn_id))}"
      while time.monotonic() < deadline:
          state = request_json(poll_url, timeout=...)
          ...
          time.sleep(0.5)
  ```

  `EventSource` 是浏览器 API，评测机用不了。**对非浏览器调用方，轮询不是回退，是唯一的路。**

### 结论

「SSE 本来就能用、只是前端没用」这个前提**不成立**——前端用了（顺序还对），真正在用
轮询的是一个**不可能用 SSE** 的调用方。所以正确的做法是**两条都留**：搬 1.0 的 SSE 让
前端用它，**同时把轮询端点搬回来**。S3 删掉它，只会让所有非浏览器调用方没法用。

已执行：`GET /api/v1/assistant/turns/{id}` 恢复，载荷由与 SSE 同一对构造函数产出
（保证两条传输给出逐字段相同的答案）；前端恢复 1.0 的「先 SSE、onerror 才按 700ms /
最多 150 次转轮询」。

---

## 5. 回退清单

| # | commit | 改了什么 | 钉住 1.0 行为的测试 |
|---|---|---|---|
| 1 | `回退纯函数规划` | 新增 `application/assistant/goal_planner.py`；`domain/assistant/intent.py` 从规则改成解析层；`run_turn` 接线 | `tests/unit/test_assistant_intent.py`（31 条）、`tests/integration/test_run_turn.py` 的 3 条规划用例 |
| 4 | `回退轮询端点的删除` | `api/routes/assistant.py` 加 `GET /assistant/turns/{id}`；schemas 加 `TurnPollResponse`；`useTurnStream.ts` / `client.ts` 恢复回退 | `tests/contract/test_assistant_events.py` 的 4 条轮询用例 |
| 5 | `回退 L2 语义评审的推迟` | 新增 `domain/assistant/judge.py` + `application/assistant/judge.py`；`run_turn` 接线 | `tests/unit/test_assistant_judge.py`（21 条）、`test_run_turn.py` 的 4 条 L2 用例 |
| 6 | `回退自造的「证据门」` | `domain/assistant/final_guard.py` 改回 1.0 规则表；`run_turn` 的硬拦不重试 | `tests/unit/test_assistant_guard.py`（32 条） |
| 7 | `回退结果集交付 URL 的删除` | `api/routes/assistant.py` 加 `GET /assistant/result-sets/{id}/photo`；`media.py` 抽出 `deliver_asset`；`client.ts` 加 `resultSetPhotoUrl` | `tests/contract/test_result_set_photo.py`（12 条） |
| 8 | `回退政策拒答的删除` | 新增 `domain/assistant/policy.py`（9 条正则 + 4 条文案）；`models.py` 加 `POLICY_REFUSAL`；`run_turn` 在任何模型调用之前判 | `tests/unit/test_assistant_policy_refusal.py`（18 条）、`test_run_turn.py` 的 2 条 |
| 9 | （并入 #8） | `empty_answer` → `parse_failure` | `test_run_turn.py::test_a_persistently_empty_answer_ends_as_parse_failure` |

`docs/refactor/capability-map.jsonl` 里对应 5 条新增/改写的来源登记（文件 + 符号）。
每次回退都同步了 `docs/architecture/module-map.md` 与 `packages/contracts/openapi/sentrix-v1.yaml`。

---

## 6. 回退时发现的结构缺口（如实记录，不削足适履）

三处 1.0 的规则**照搬了，但 S3 暂时喂不进输入**。它们不是「装不下」，是「还没长出来」，
所以按原样留着并注明，而不是改成 S3 现有形状：

| 规则 | 缺什么 | 记在哪 |
|---|---|---|
| `final_guard.all_requested_but_has_more` | 1.0 的 `result_mode` 来自 `search_memories(mode=...)`；S3 的工具契约只有 `query` / `limit` | `final_guard.py` 的 `GuardFacts.result_mode` 注释 |
| `final_guard.fact_exists_contradiction` | 1.0 的 `query_memory_facts` 工具（S3 没有，留给 S6） | `final_guard.py` 的模块 docstring |
| `final_guard.delivery_contradiction` | 1.0 要求 `delivered_count` 是**整数 0**；S3 的 `get_original_photos` 交不出去时返回失败（`bad_handle`）而不是 `delivered: 0`，于是那个字段保持 `None` | `test_run_turn.py::test_a_delivery_that_delivers_nothing_ends_with_a_gap` 的 docstring |

**一处证据类型表被收窄**：规划器提示词里的 `evidence_type` 只有 S3 四个工具能产出的
三类（`photo_set` / `delivered_photo` / `person_profile`），而 1.0 有九类。1.0 有九类
是因为它有十来个工具；S3 声明一个没有工具能满足的类型，会让那一轮必然以缺口收场——
那不是 1.0 的行为。**收窄的是词表，机制（模型声明 → 校验 → 带错误回灌重试）原样**，
理由写在 `domain/assistant/intent.py` 的模块 docstring 里。

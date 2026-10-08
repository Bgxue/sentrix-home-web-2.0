# 助手失败模式：修因不修题

日期：2026-10-08
依据：`20261007-233339-album3-max-video10-qwen35-q4km-e1b604` 全量轨迹（487 题）
     + `20260930-113307-...-reuse-f7148c`（1.0 基准，487 题）对照

## 这份文档的纪律

**每一条都必须同时满足三条**，否则不做：

1. **判据是机制层的，不是某道题。** 「某题答错了」不是证据；「这个函数在任何题目上
   都会这么算」才是。下面每条都写了判据，没有一条是「因为 `album3v4-0xx` 答错了」。
2. **修的是系统，不是题。** 本次没有出现任何与具体问题文本、具体 asset、具体相册相关
   的分支。
3. **改完要有能封住它的门禁。** 每条修复都配了一条机制层的断言，且断言的是**关系**
   （文案里的数字 == profile 的值）而不是抄一遍常量。

## 观测到的事实（全部实测，非推断）

| # | 事实 | 数字 |
|---|---|---|
| J1 | `assistant/tools.py` 的 `_preview()` 只回 `handle`/`file_name`/`captured_at` | 3 个；1.0 的 `_preview_entry` 是 **14 个**，含 `place`/`people`/`evidence_summary` |
| J2 | `Observation` 上 `place`/`caption`/`people` 都是正式字段 | 数据在手上，只是没进 preview |
| J3 | `profile.max_inspections = 3`，而拒绝文案与工具描述都写「一轮最多 1 次」 | `tool_policy.py`、`domain/assistant/tools.py` |
| J4 | `decide()` 里**预算检查排在「重复失败」之前** | 额度用尽后 `repeated_failure` **永不触发**：309 次 `inspection_budget_exhausted` vs 24 次 `repeated_failure` |
| J5 | 被拒的调用不计工具预算，却**照常消耗一个模型步** | 762 次被拒；**426 步「整步全被拒」**，占 2667 步的 16% |
| J6 | 被拒之后模型有 **51%** 继续试同一个工具 | 386/762 |
| J7 | `read_photo_text` 是 `CHEAP`，不受精读预算约束 | 1.0 是 `EXPENSIVE`；`album3v4-026` 连调 6 次 |
| J8 | `final_answer.selected_image_handles` 不是必填 | 1.0 的 final 动作 **239/487（49%）** 不带这个字段 |
| J9 | 未满足需求的 `unmet_reason` 全是空串 | 375 条，取值只有 `''` |
| J10 | 硬拦分支不发 `guard` 事件；兜底文案把 `GUARD_BLOCKED` 说成「未通过事实校验」 | 实际四条硬拦规则全是安全泄漏 |
| J11 | `PhotoBench` 的 `retrieval_media_refs`/`retrieval_image_ids` 直接赋成 `gt_refs` | `run_execution/service.py:1354-1355` |

**后果面**：`step_budget_exhausted` 123 题里 **102 道判 0、2 分一道没有**——占全部
279 道错题的 **36.6%**。地点类题错率 38.7% → 75.3%，平均工具调用 1.83 → 6.63 次。

## 本次做的修改

### W1 · 照片级元数据进 preview

`_preview()` 读该资产**最新一条观察**，补 `place` / `people` / `evidence_summary`。
同时把 1.0 那句原话加回 `search_memories` 的描述：「单张照片的地点、时间、人物直接在
preview 里读，**不要为此再调用其他工具**」——不加这句，字段加了也白加。

**根因**：`_preview` 是 S3（Task 40）写的，那时这三样在 2.0 还不存在；S4 加了地点、
S5 加了人物与语义，**没有一个切片回头把它加宽**。

**门禁**：`test_the_preview_carries_where_who_and_what`（有观察时三个字段有值）+
`test_the_preview_keys_are_always_present`（没观察时键仍在、值取空——空值表示
「这张图没记录到」，缺键会让模型以为链路坏了）。

### W2 · 精读额度：数字从 profile 现渲染

拒绝文案改成 `state.profile.max_inspections` 现渲染；**工具描述里删掉具体数字**
（schema 是静态的，额度是运行时事实，写在 schema 里注定要漂）。

**门禁**：`test_the_inspection_denial_states_the_real_limit` —— 断言的是**关系**
（文案里的数字 == profile 的值），并且换一个 profile 文案跟着换；另断言那句陈旧的
「一轮最多精读一张图」不再出现。

### W3 · 「卡住」的检查提到预算之前

`decide()` 里新增的 `repeated_failure` 检查排在所有预算检查**之前**，判据换成
`TaskState.trailing_failures`（队尾连续没成功，**不问原因**）。原来的
`consecutive_failures`（要求同一失败原因）因此成了死代码，**已删**。

**门禁**：`test_a_stuck_tool_is_refused_before_the_budget_code` +
`test_a_denied_call_does_not_reset_the_stuck_counter`（反向：被拒不能算成功）。

### W4 · 循环级的活性护栏

`_converse` 数「连续几轮工具调用一条有用的结果都没拿到」，到 2 轮时用既有的
`block_guard_code` 给**一次**提醒让它收口。`_run_tool` 的返回值多一格
「有没有拿到东西」。

**为什么必须在循环这一层**：被拒的调用不推进任务、不占工具额度，**只烧模型步**；
工具层的每一条判据都拦不住它，因为**每一次被拒在它自己看来都是对的**。

**门禁**：`test_a_model_stuck_on_failing_tools_is_told_to_answer` +
`test_the_stall_reminder_is_given_only_once`。

### W5 · `read_photo_text` 回到受预算约束

回到 `EXPENSIVE`，与 `inspect_photo` 共用「每轮 N 次照片级取证」。

S6/T124 降它为 `CHEAP` 的理由是「别让一次免费的读库挤掉 `inspect_photo` 的精读预算」
——**那条理由的前提是 `max_inspections = 1`**。现在是 3，而代价翻到了另一面：`CHEAP`
的工具不受任何单轮次数约束，连读 6 张就把工具预算与模型步一起用光。

**门禁**：`test_read_photo_text_costs_a_photo_slot_like_1_0` +
`test_reading_a_fourth_photo_is_denied`。

### W6 · 交付字段必填（1.0 做不到的收紧）

`final_answer.selected_image_handles` 进 `required`，并同时修掉「缺」的判据：
原来 `not arguments.get(name)` 把**空数组也当缺**——那会把必填变成「必须至少交一张」，
而产品口径是「模型没选 → 交付为空合法」。

**门禁**：`test_the_delivery_field_is_required_in_the_schema_the_model_sees` +
`test_an_empty_delivery_list_is_not_a_missing_argument`。

### W7 · `unmet_reason` 写出真实原因

收尾时按「有没有对应工具被调用过」写 `not_attempted` / `attempt_failed`。
实测 `visible_text` 有 155 道开着，其中 **147 道（95%）没调用过 `read_photo_text`**
——那不是检索失败，是**规划与执行脱节**，两者修法完全不同。

**门禁**：`test_an_unattempted_requirement_says_so` +
`test_an_attempted_but_unmet_requirement_says_attempt_failed`。

### W8 · 硬拦的可见性与文案

硬拦也发一条 `kind="guard"` 事件（带规则码）；`emergency` 的 `GUARD_BLOCKED` 文案从
「回答未通过事实校验」改成「回答触及了安全边界」——前者对失败原因的表述是错的。

**门禁**：`test_a_hard_block_is_visible_in_the_trace`。

## 记一笔但不修（附理由）

- **J11 的度量口径**：`retrieval_*` 的分母是 GT、分子是**交付集**，所以零交付必然
  `recall=0`（424 道里 394 道零交付，一道不差）。而 2.0 的 item 里**没有候选集**，
  无法区分「检索没找到」与「没交付」。修它要先把候选集从助手契约里暴露出来
  （观测性缺口），**属另一个切片**。在那之前，**任何用 `retrieval_*` 指责检索后端的
  结论都不成立**。
- **规划器多声明**：212 道声明 `delivered_photo` 而只有 3 道真要图。这是规划质量
  问题；通用修法（收尾前提醒一次）要花一个模型步，**先看 W1–W5 的效果再定**。
  W7 已经把这件事变成可观测的。
- **8100 端点上下文 6144/slot**（`--ctx-size 24576 --parallel 4`）：1.0 与 2.0 两轮
  都撞了（各 3 次 `exceed_context_size_error`）。这是部署参数，不是代码。

## 验证

- `ruff` / `ruff format` / `mypy --strict`（293 files）/ `import-linter`（11 kept）/
  `reachability` / `method-reachability` / `capability-map` / `module-map` /
  两份 openapi —— 全绿。
- `apps/api/tests/unit` + `apps/api/tests/contract` —— 全绿（exit 0）。
- 助手相关集成测试 —— 全绿（exit 0）。
- **全量回归与真机评测由使用者跑**；本文件不声称成绩已恢复。

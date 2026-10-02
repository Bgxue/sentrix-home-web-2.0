# S7c 实施计划：助手引用家庭关系

> S7 的最后一段。前置 S7a / S7b1 / S7b2 已交付（`family_*` 六表 + 仓储 + 读写路由 +
> 自动推断），F7 前端第一版已交付并暂缓改版。
> **本段只做后端**：把家庭图谱已经算好的「成员归属 + 关系」接到助手可见面上。

---

## 0. 1.0 实况（动工前核查，2026-10-02）

### 0.1 唯一的装配点是 `person_profile_digest`

`1.0 backend/db.py:2840-2889`。它读的正是**家庭图谱三表**
（`family_person_memberships` / `family_relationships` / `family_person_portraits`），
**不是**通用 `relationships` 表。返回 12 个键：

```
person, membership, membership_source, status,
summary_zh, preference_summary_zh, activity_summary_zh,
place_summary_zh, appearance_summary_zh,
relationships[], patterns[], recent_events[], claims[]
```

**注意：它不返回 `family_role`。** 2.0 的工具现在返回 `family_role`，那是 S3 的占位
（`domain/assistant/models.py:239` 的 docstring 自己写着「S3 只有家庭角色这一格，
其余在 S5 落地」）。

### 0.2 助手链路（两条）

**路径 A —— 工具结果**：`get_person_profile` 工具（`tools.py:2798`）→ `_get_person_profile`
（`:2658`）→ digest。返回体带 `membership` / `relationships` 等，`relationships`
在 `tool_policy.py:121-126` 的模型可见白名单里，**会真实进入模型上下文**。

**路径 B —— 直接注入系统提示词**：`runtime.py:1869-1874` 在 `task.active_person`
存在时，把 `person_profile_summary`（`tools.py:2633`）的一行中文摘要塞进 system
message，那行里含「家庭归属：…；关系：A（父亲）、B（母亲）」。

**2.0 没有 `active_person` 这个概念**（那是 1.0 agent runtime 的），路径 B 不搬。

### 0.3 1.0 自己的两个缺陷（**2.0 不受影响，但要理解为什么**）

1. **只收出边**：`db.py:2852-2858` 的 `if rel["subject_entity_id"] != person_id: continue`
   ——关系只按（父→女）存一条时，查**女儿**的 digest 拿不到父亲。
   测试恰好只测了父为 subject 的方向，把它盖住了。
2. **没有端到端测试**：全 1.0 仓库只有 `tests/test_family_profile_digest.py` 一条，
   测的是 **store 层 digest**，不经助手、不经工具、不经提示词；Gold Set 里那条
   人物 case 引用的还是 `agent_runtime` 里**已不存在**的 `get_person_memory`。

**2.0 天然没有第 1 条**：`list_effective_relationships` 的 SQL 是
`subject_entity_id = ? OR object_entity_id = ?`（双向匹配），而 `set_relationship`
**一次写正向 + 反向两行**（F7 实测确认）。所以「按 subject 过滤」在 2.0 拿到的是
**完整**的关系集，而 1.0 拿到的是残缺的。这一条要在代码注释里写明。

### 0.4 还有哪些 1.0 的东西 **不搬**

| 1.0 有 | 为什么不搬 |
|---|---|
| `preference_summary_zh` / `activity_summary_zh` / `place_summary_zh` / `appearance_summary_zh` / `patterns` / `claims` | 来自 person memory / 语义画像 / claims 表，**属 S5**，2.0 还没有 |
| `recent_events` | 来自 `person_event_memory`，同上 |
| `person_profile_summary` 的提示词注入（路径 B） | 2.0 没有 `active_person` |
| `get_core_memory` / `search_conversation_history` | 2.0 助手没有这两个工具 |

**所以 S7c 的交付面就是**：`membership` + `membership_source` + `summary`（画像文本）
+ `relationships[]`。

---

## 1. 裁决

| # | 问题 | 裁决 | 依据 |
|---|---|---|---|
| ① | `family_role`（实体列）与 `membership`（家庭图谱）并存还是替换 | **替换**——工具改返回 `membership`，`family_role` 不再出现在工具结果里 | 1.0 的 digest **根本不返回 `family_role`**；2.0 真实库里 9 个 person 实体的 `family_role` **全是 NULL**；两套口径在 1.0 的 298 对配对里**一致率 0**，是两条不同的轴。留着只会给模型一个恒为 null 的字段。**注意**：`entities.family_role` 列本身保留，`GET /api/v1/people/{id}` 照旧返回它，只动助手工具 |
| ② | `relationships[]` 取哪个方向 | **按 1.0 取 `subject_entity_id == person` 的出边** | `predicate` 才是「此人是对方的什么」。2.0 双向存两行，不过滤会拿到**同一段关系两次**（`父亲→儿子` 与 `儿子→父亲`）；过滤后因为双向存储，**覆盖是完整的**（1.0 的缺陷在 2.0 不成立） |
| ③ | 富化逻辑放哪 | **`EntityPersonDirectory`**（`application/identity/person_directory.py`） | application 层只能依赖 domain 端口（`pyproject.toml:83-96` 的 `application-purity` 契约禁止 import `sentrix.infrastructure`）。目录就是「我们知道这个人的什么」的归属地，助手用例不该自己拼 |
| ④ | 关系为空的语义 | **`relationships: []` 且照常 `found: true`** | 1.0 也是空数组。**没有关系不等于查不到人**——实测这份数据就是 8 个人 0 条关系，返回 `found:false` 会让助手说「查不到这个人」 |
| ⑤ | 工具描述 | **必须改写**，删掉「人物关系…尚未建立」 | 现在是「2.0 目前只有这一项人物数据；人物关系、常去地点、性格等尚未建立」——S7c 之后前半句是假的。仓库反复强调工具描述不能承诺拿不到的数据，也不能否认已有的数据 |

---

## 2. 交付物

| 文件 | 改动 |
|---|---|
| `domain/assistant/models.py` | `PersonProfile` 加 `membership` / `membership_source` / `summary` / `relationships`；`family_role` 下线 |
| `domain/assistant/ports.py` | `PersonDirectory` 的 docstring 更新（签名不变） |
| `application/identity/person_directory.py` | `EntityPersonDirectory` 接 `FamilyGraphRepository`，命中后补三块 |
| `application/assistant/tools.py` | `_person` 把新字段放进 `observation` 与 `model_visible` |
| `domain/assistant/tools.py` | `GET_PERSON_PROFILE` 的 `description` 改写 |
| `api/routes/assistant.py` | `_run_turn` 一处：把 `family_graph_repository` 传给目录 |
| `bootstrap/container.py` | `person_directory` 装配处补一个参数 |

**不动**：`family_graph_repository` 本身、`GET /api/v1/people/{id}`、前端。

---

## 3. 契约

### `PersonProfile`（domain/assistant/models.py）

```python
@dataclass(frozen=True, slots=True)
class PersonRelationship:
    """此人是对方的什么。1.0 ``db.py:2855-2859`` 的三个键。"""
    predicate: str
    other_name: str
    source: str


@dataclass(frozen=True, slots=True)
class PersonProfile:
    name: str
    confirmed: bool
    # 1.0 digest 的 membership / membership_source，缺省是 "unknown" / ""（`db.py:2875-2876`）
    membership: str = "unknown"
    membership_source: str = ""
    # 1.0 的 summary_zh，2.0 这一格来自 family_person_portraits
    summary: str = ""
    relationships: tuple[PersonRelationship, ...] = ()
```

### `_person` 的 `model_visible`

命中：

```python
{"person": ..., "membership": ..., "membership_source": ..., "summary": ...,
 "relationships": [{"predicate": ..., "other_name": ..., "source": ...}, ...]}
```

未命中：**保持现状**（`{person, found: False, note}` + `blocks=PERSON_PROFILE`）——
那条封口逻辑是 S5 的，S7c 不动。

---

## 4. 验收

- `make ci` 静态门禁全绿；`reachability` / `method-reachability` / `capability-map` /
  `module-map` / openapi 契约一致
- **`test_assistant_toolbox.py:318-343` 那 3 条断言必改**（现在写死
  `model_visible == {"person":"妈妈","family_role":"母亲"}`）
- 新增用例（每条都要能被定点变异杀掉）：
  1. 命中的人带 membership + relationships；
  2. **关系只出一次**——夹具放「正向 + 反向两行」，结果必须是 1 条（这条钉裁决②，
     且夹具必须让「不过滤」与「过滤」给出**不同答案**）；
  3. **`other_name` 要解析成名字**，不是 `entity_xxx` id；
  4. **只有出边**：夹具放一条「此人是 object」的边，它**不该**出现；
  5. **0 条关系时 `found` 仍为 True**（钉裁决④，用真实数据形状：8 人 0 关系）；
  6. 没有 membership 行时 `membership == "unknown"`；
  7. 工具描述里不再出现「尚未建立」这类否认已实现能力的措辞。
- 在 153 真实数据上实跑一次：问助手「成员一和成员二是什么关系」，
  预期如实回答「没有已记录的关系」——**因为实测这份数据就是 0 条关系**。

---

## 5. 施工记录（2026-10-02 完成）

### 5.1 交付

| 提交 | 内容 |
|---|---|
| `513c9bf` | 计划书 |
| 本提交 | 实现 + 测试 + 收口 |

改动 6 个源文件：`domain/assistant/models.py`（`PersonProfile` 换字段 + 新增
`PersonRelationship`）、`domain/assistant/ports.py`（端口 docstring）、
`application/identity/person_directory.py`（接 `FamilyGraphRepository` 富化）、
`application/assistant/tools.py`（`_person` 两份投影）、
`domain/assistant/tools.py`（工具描述）、`bootstrap/container.py`（装配）。

**`api/routes/assistant.py` 没动**——它传的是 `container.person_directory`，
目录的装配在容器里，所以接线只改容器那一行。

### 5.2 定点变异 9/9 被杀

M1 关系不过滤方向 · M2 方向判反 · M3 关系里给 id 而不是名字 ·
M4 没有归属行时报空串 · M5 画像不取 · M6 没有关系就当作查不到 ·
M7 关系条数也发给模型 · M8 画像文本不给模型 · M9 工具描述改回旧版。

**M6 我第一次打错了地方**：改的是 `observation["found"]`，而那条断言看的是
`model_visible`——所以它「存活」了。真实的变异是改 `_person` 的 gap 分支条件
（`or not profile.relationships`）。**变异存活的第一种成因又出现一次：打错了靶子。**

### 5.3 真实数据实测（153）

**目录层**（只读）：

```
成员一: membership='unknown' src='model' 画像=「家庭归属：unknown；近期记忆：…」关系数=0
成员二: membership='unknown' src='model' 画像=「…」关系数=0
隔壁老王: None（查不到）
```

**助手回合**（真跑了一次完整问答，问「成员一和成员二是什么关系？他们算家人吗？」）：

事件流里 `get_person_profile` **被调用两次**（成员一、成员二各一次，
`agent_turn_events` 第 3、5 步），回答是：

> 在一张照片（photo_2）中，同时出现了"成员一"和"成员二"，但他们的家庭归属
> （membership）均显示为"unknown"（未知），且他们的关系图谱（relationships）为空。
> 因此，仅凭现有数据，无法确认他们是否算作家人……

`membership` 与 `relationships` 两个词都来自这个工具的新投影，而且它**如实说了没有**
而不是编造——这正是 S7b2 实测里那份「53 张照片零亲属信号」的数据该得到的答案。

`termination_reason` 是 `answered_with_gap`（与 2026-09-25 那条「没找到人物照片」的
历史回合同一个值），属正常收尾。

### 5.4 未做

- **没有端到端的行为测试**。1.0 同样没有（全仓只有一条 store 层用例，Gold Set 里
  那条人物 case 引用的还是 `agent_runtime` 里已不存在的 `get_person_memory`）。
  2.0 这一段补到契约/工具层为止，上面那次助手回合是**人工实跑**，没有变成回归网。
  要补的话得先有一个能稳定复现的模型桩，那是另一件事。
- `preference_summary` / `patterns` / `recent_events` / `claims` 仍缺，属 S5。

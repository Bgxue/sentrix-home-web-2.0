# 2.0 冻结能力

冻结 = 不实现、不建表、不迁移代码。依据见设计文档 §1.3-D。

| 能力 | 冻结理由 |
|---|---|
| 行程 trips | 表 0 行；`db.py:3235 derive_trip_candidates` 无生产调用者，只在 `test_entities.py` 跑 |
| 故事 stories | 表 0 行；`POST/PATCH/DELETE /api/stories` 零调用 |
| 反馈 memory_feedback | 表 0 行 |
| 关系假设 relationship_hypotheses | 表 0 行；decision 端点零调用 |
| 人物角色假设 person_role_hypotheses | 表 0 行 |
| 实体合并候选 entity_merge_candidates | 表 0 行；confirm/reject 零调用 |
| 洞察运行 person_insight_runs | 表 0 行 |
| 家庭范围组 family_scope_groups | 表 0 行 |
| 关系阻断 family_relationship_blocks | 表 0 行 |
| 邀请 invites | 表 0 行；而 8091 是 0.0.0.0 无鉴权 |
| 主动建议 | agent_proactivity_preferences / agent_impressions / agent_scene_cooldowns 全 0 |
| maintenance 运维接口 | `maintenance/*`（recheck / reindex-entities / summarize-events / entity-merge-candidates）零调用 |
| agent_contracts 校验 | `agent_contracts.py` 392 行，静态不可达 |
| **`adjacency` 召回通道** | **在 1.0 就是死代码**（2026-09-29 核查，S6 计划 §2）：`configs/retrieval/defaults.json:10` 写着 `"adjacency": false`，而 `build_default_retrievers`（`retrieval/__init__.py:80`）对关掉的通道**直接 `continue`、连实例都不建**；`AdjacencyRetriever` 只被自己的模块、那个工厂和 `test_adjacency_retriever.py` 引用；`.expand(` 唯一的生产调用点在 Kernel 里，而 Kernel 只有在它被构建时才跑。**实现它等于加一个 1.0 从没跑过的东西**——按 `agent_contracts` 的先例不实现。<br>**判据用的是 §3 那条方法论**：「判一个能力在不在，要看它有没有注册、有没有生产调用者」。同族的另外三次：`query_memory_facts`（注释说已删、实际注册着且被调 1,603 次）、`read_photo_text` 的 VLM 回退（注释承诺了、代码里根本没有）、L2 评审的 `trusted_facts`（算了但生产路径上从不执行）。<br>**注意 `fusion.py` 的权重表与 `EXPANDER_CHANNELS` 里还留着 `adjacency` 的位置**——那是照抄 1.0 的权重表，留着无妨，但不要因为「表里有」就去建召回器。|
| E2B LoRA 兼容服务 | 进程不存在，8101 已被 bge-m3 sidecar 占用 |
| 硬编码地点别名表里的**已硬编码泄漏条目**：`"赵州桥": ["赵县"]`、`"三峡坝址": ["夷陵区"]` | **benchmark 泄漏**：PhotoBench QA 里有题「…是在河北哪个景点拍的呀？」，答案「河北赵县的赵州桥景区」，而 1.0 数据库里 "赵州桥" 出现 **0 次**、"三峡坝址" 0 次，系统只可能从 GPS 反编码得到 "赵县"。这两条唯一作用就是让该评测题能匹配。代码注释自称"这是通用双语地名知识，不是测评答案"，辩解本身即佐证。<br>**冻结的是这两条，不是整张表。** 2026-09-22 裁决：表里另外 **30 条**是真正通用的中英地名桥接（清迈 ↔ Chiang Mai、行政区后缀归一等），S4 按设计 §3.4 搬进数据文件 `apps/api/src/sentrix/infrastructure/places/data/aliases.json`——可审查、可替换、可测试，且不得被 agent 提示词或评判逻辑引用。**这条冻结的判据是「值是不是具体景点名」**，不是「表在不在代码里」：把整张表连同 30 条通用条目一起冻掉，代价是国外地名召回当场归零，而收益是零。见 `docs/refactor/s4-deviations.md` §1 第 5 项 |

**2026-09-23 解冻两条**：`person_moments`（人物瞬间）与 `person_portrait_revisions`
（画像修订）按用户裁决移出冻结清单，表随迁移 `0006_identity.sql` 落地，
写入者在 S5c（Task 84/85）。移出的判据是本表自己的定义——「冻结 = 不实现、不建表」，
而这两张表现在建了；把它们继续留在冻结清单里会让「冻结」这个词同时指两件事。
`person_insight_runs`（洞察运行）**仍在冻结**：1.0 的六阶段编排把它当状态机，
2.0 的 S5c 改成同步跑、不落运行记录，所以它连表都不建（`person_moments.run_id`
这一列也随之取消——它本来是指向那张表的外键）。

**重新启用的条件**：出现真实使用需求时，按新切片单独设计，不沿用 1.0 实现。

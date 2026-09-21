# 2.0 冻结能力

冻结 = 不实现、不建表、不迁移代码。依据见设计文档 §1.3-D。

| 能力 | 冻结理由 |
|---|---|
| 行程 trips | 表 0 行；`db.py:3253 derive_trip_candidates` 无生产调用者，只在 `test_entities.py` 跑 |
| 故事 stories | 表 0 行；`POST/PATCH/DELETE /api/stories` 零调用 |
| 反馈 memory_feedback | 表 0 行 |
| 关系假设 relationship_hypotheses | 表 0 行；decision 端点零调用 |
| 人物角色假设 person_role_hypotheses | 表 0 行 |
| 实体合并候选 entity_merge_candidates | 表 0 行；confirm/reject 零调用 |
| 人物瞬间 person_moments | 表 0 行 |
| 洞察运行 person_insight_runs | 表 0 行 |
| 画像修订 person_portrait_revisions | 表 0 行 |
| 家庭范围组 family_scope_groups | 表 0 行 |
| 关系阻断 family_relationship_blocks | 表 0 行 |
| 邀请 invites | 表 0 行；而 8091 是 0.0.0.0 无鉴权 |
| 主动建议 | agent_proactivity_preferences / agent_impressions / agent_scene_cooldowns 全 0 |
| maintenance 运维接口 | `maintenance/*`（recheck / reindex-entities / summarize-events / entity-merge-candidates）零调用 |
| agent_contracts 校验 | `agent_contracts.py` 392 行，静态不可达 |
| E2B LoRA 兼容服务 | 进程不存在，8101 已被 bge-m3 sidecar 占用 |
| 硬编码地点别名表 `_PLACE_ALIASES` | **benchmark 泄漏**：含 `"赵州桥": ["赵县"]`、`"三峡坝址": ["夷陵区"]` —— PhotoBench QA 里有题「…是在河北哪个景点拍的呀？」，答案「河北赵县的赵州桥景区」，而 1.0 数据库里 "赵州桥" 出现 **0 次**、"三峡坝址" 0 次，系统只可能从 GPS 反编码得到 "赵县"。这张表唯一作用就是让该评测题能匹配。代码注释自称"这是通用双语地名知识，不是测评答案"，辩解本身即佐证。**2.0 不迁移该表**；S4 实现地点时只保留真正通用的中英地名桥接，放独立数据文件 `data/places/aliases.json`，可审查、可替换、可测试，且不得被 agent 提示词或评判逻辑引用 |

**重新启用的条件**：出现真实使用需求时，按新切片单独设计，不沿用 1.0 实现。

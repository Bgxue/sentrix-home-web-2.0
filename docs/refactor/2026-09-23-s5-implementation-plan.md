# Sentrix Home Web 2.0 — S5 实施计划（人脸与身份层 · 收尾与画像）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通离线链路的最后一块——**人脸与身份层**（人脸聚类 → 簇审阅 → 身份确认 → 写实体与人名，
实体与照片建「提及」关系作为身份证据桥梁）与**收尾与画像**（人物外观证据、人物画像）。
它同时解锁三样卡着的东西：事件八分量的 `person_score`（权重 0.20）、`facts` 层、`MemorySpace` 的层次错位。

**Architecture:** 沿用四层（domain / application / infrastructure / api），依赖方向由 `import-linter`
的 5 条契约强制。本切片新增域 `domain/identity`（人脸样本 / 多视角原型 / 贪心聚类 / 身份实体 /
提及关系，纯规则）；人脸检测与向量是 infrastructure 适配器（ONNX + InsightFace，**S5b 才需要**）；
簇审阅与命名是 application 用例；HTTP 在 api 层。

**Tech Stack:** Python 3.11 / FastAPI / 标准库 `sqlite3` · pytest / ruff / mypy --strict / import-linter
· **S5b 新增**：`onnxruntime-gpu`、`insightface`、`opencv-python-headless`（2.0 venv 实测一个都没有）

**依据文档:** `docs/architecture/2026-09-21-sentrix-home-web-2-implementation-design.md`、
`docs/refactor/behaviour-baseline.md`（人脸一节）、`docs/refactor/frozen.md`、
`docs/refactor/s4-deviations.md`（§4 第 287/609 行把身份层记在 S5）、
`docs/refactor/2026-09-22-s4-implementation-plan.md:308,309,313`（三处明确「留给 S5」的能力）

---

## 本文档是怎么写出来的

做法与 S2/S3/S4 相同，**不是照着意图写的**：

1. 在 153 上建了隔离副本 `/home/asus/s5verify/repo`（真实仓库一个字节都没动），
   跑通 `make install` 与基线 `make ci`；
2. **只读**精读 1.0：`backend/face_clustering.py`（182 行，全文）、`backend/face_embeddings.py`
   （245 行，全文）、`backend/face_detector.py`（181 行，全文）、`backend/model_clients.py` 的
   `FaceAdapter` 全段（`:1616-1938`）、`backend/db.py` 的人脸与身份段（`:400-1400` 的 DDL、
   `:4400-5600` 的实体/人脸/关系函数、`:1494` 的 facts 门禁）、`backend/app.py` 的簇审阅四端点
   （`:1969-2100`）、people 路由（`:1498-2420`）、`_seed_face_detect`（`:2253`）、
   `_refresh_confirmed_person` / `_analyze_confirmed_person_appearance`（`:1997/2022`）、
   `person_insights.py` / `person_portraits.py` / `person_moments.py` / `person_graph.py` /
   `person_appearance.py` 五个模块全文、`scripts/runtime/start_sentrix_api_8091.sh`；
3. 把 **Task 69**（人脸聚类纯规则）在副本里真的写出来：先红灯、再绿灯、再 `make ci`；
4. **按约定在第 18 个任务处停下**（「超过约 15 个任务就停下报告，提议拆分」），
   提议拆成 S5a / S5b / S5c，见「任务拆分」一节。

由此有三条硬保证与两条如实说明：

- 移植清单里每一条的 **1.0 位置（文件 + 行号 + 符号）都是实际读过的**；
- 每条「必须钉住的行为」都是 1.0 源码里的**常量、分支或边界**，不是转述；
- Task 69 的红灯 / 绿灯 / `make ci` 是**实际观察到的输出**（见「验证记录」）。

**必须写明的两条边界**：

| 项 | 状态 |
|---|---|
| **Task 69**（domain/identity 纯规则） | **已实现、已跑通、`make ci` 全绿**（隔离副本分支 `s5-tdd`） |
| **Task 70–86** | **只是计划**。本文档**不写推测的 2.0 代码块**——S2 的教训是「13–14% 的错误率全部发生在计划生成这一步」，所以没跑通过的代码一律不写，只写「从 1.0 的哪一段搬什么、必须钉住哪些常量」 |
| **阶段 3–5（逐任务红/绿、跨任务回归、每 commit `make ci`）** | **未执行**。原因见「需要用户裁决的点」第 1 条：本切片 18 个任务，按约定先报告拆分 |

---

## 与 S0–S1 / S2 / S3 / S4 的关系

### 任务编号

S0–S1 用了 Task 1–24，S2 用了 25–33，S3 用了 34–45，S4 用了 46–68，全局连续。
S5 顺着往下排：**Task 69–86**（提议 S5a = 69–76，S5b = 77–83，S5c = 84–86）。

### S4 明确留给 S5 的三处（本切片必须收口）

| 出处 | 原文 | S5 怎么收 |
|---|---|---|
| `s4-implementation-plan.md:308` | `event_participants` / `event_entities` / `select_event_cover`「强依赖 `entities`」→ **S5** | Task 81 |
| `s4-implementation-plan.md:309` | `person_bridge` 词法字段「没有 `entity_mentions` 可桥」→ **S5** | Task 83 |
| `s4-implementation-plan.md:313` | `facts` 的 `purge_unanchored_facts`「按 `entities` 里 confirmed person 的名字过滤」→ **S5** | Task 82 |
| `s4-implementation-plan.md:761-766` | `AssignEvents.__init__` 的 `confirmed_people` 默认恒空集，注释写明「这是 S5 的输入」 | Task 80 |
| `application/memories/maintain_facts.py:7-27` | `MaintainFacts` 的 `confirmed_people` 默认恒空集 + `candidates` 在生产装配下也空 | Task 82 |
| `infrastructure/assistant/identity.py:11` | 「S5 落地时替换的是这个适配器（实现同一个 `PersonDirectory` 端口）」 | Task 75 |
| `0005_timeline.sql:49-51` | 「参与者与实体都依赖 S5 的 `entities`，那两张表随 S5 一起建，不在这里留空壳」 | Task 70 |

---

## 前置条件

远端仓库 `/home/asus/Github/sentrix-home-web2.0`，分支 `psh2.0`，起点提交 `f663522`
（S4d 收尾，工作树干净）。隔离副本 `/home/asus/s5verify/repo`（`git clone --local` + `make install`）。

### 外部条件（规划时实测）

| 项 | 实测值 | 怎么来的 |
|---|---|---|
| RetinaFace 权重 | `~/benchmarks/retinaface/retinaface_r50.onnx`（按 `$HOME` 推导，不写死用户名） | `face_detector.py:30-32` |
| buffalo_l 权重 | `~/.insightface/models/buffalo_l/w600k_r50.onnx` | `model_clients.py:1690-1697` |
| AdaFace 权重 | `/home/asus/models/AdaFace/pretrained/adaface_ir50_ms1mv2.ckpt`（700,286,703 B，`ls -la` 实见） | 见「不移植」第 1 条 |
| GPU | 1 张 3090，`nvidia-smi` 实测 **13124 MiB / 24576 MiB 已用** | `nvidia-smi` 实测 |
| 2.0 venv 人脸依赖 | **`onnxruntime` / `insightface` / `opencv` 一个都没有**；只有 `torch 2.14.0` / `cn-clip 1.6.0` / `numpy 2.4.6` | `.venv/bin/pip list` 实测 |
| 1.0 venv 人脸依赖 | `insightface 1.0.1` / `onnxruntime-gpu 1.23.2` / `opencv-contrib-python 4.10.0.84` | `.venv/bin/pip list` 实测 |
| `memory_spaces.include_in_people` | **列已存在**（`0001_core.sql:12`，`MemorySpace` 数据类没映射它） | 读迁移原文 |
| `observations.people_json` | **2.0 没有**（1.0 有） | `grep` 迁移目录无命中 |
| `event_participants` / `event_entities` / `event_revisions` | **2.0 没有**（0005 注释明确留给 S5） | `0005_timeline.sql:49-51` |

### 2.0 侧现成的落点（**不用新建的部分**）

| 落点 | 位置 | 现在的样子 |
|---|---|---|
| `person_score` 打分 | `domain/memories/events.py:79,280,306` | `WEIGHTS["person"]=0.20`、`PERSON_MATCH=0.8`、`_person_score(people, event_people)`、`ObservationAnchor.people` / `EventAnchorSet.people` **都已就位**，只缺生产者 |
| 事件归属的注入点 | `application/memories/assign_events.py:123,188` | `confirmed_people: Callable[[str], frozenset[str]] = lambda _: frozenset()`；`:188` 已经把它喂进 `ObservationAnchor`（`people=self._confirmed_people(observation.id)`）——**接线只剩「给一个真的实现」** |
| facts 门禁的注入点 | `application/memories/maintain_facts.py:65,78` | `confirmed_people: Callable[[str], frozenset[str]]`（**scope_id → 名字集合，注意是名字不是 id**） |
| 人物目录端口 | `domain/assistant/ports.py:98` + `models.PersonProfile` | `PersonDirectory.lookup(name, scope_id)`；空实现 `NoIdentityDirectory`（`infrastructure/assistant/identity.py`） |
| 容器装配点 | `bootstrap/container.py:84-87,134` | `spaces: SqliteSpaceRepository` 是全仓**唯一**「字段类型落在 infrastructure」的例外，由 `tests/unit/test_container_wiring.py` 的 `EXCEPTIONS` 表强制 |
| 迁移执行器 | `infrastructure/persistence/database.py` | `0006_*.sql` 直接放进 `migrations/`；**注意 `_statements()` 用朴素 `split(";")`，脚本里不能有含分号的字符串常量** |
| 词法投影 | `application/retrieval/retrieve_lexical.py` + `repositories/search_terms.py` | `person_bridge` 字段类型**故意没建**，注释写明属于 S5 |

---

## 移植清单：1.0 有什么、S5 要哪些

### A. 移植（S5 需要）

| # | 能力 | 1.0 的确切位置 | 2.0 落点 | 移植方式 |
|---|---|---|---|---|
| 1 | 向量归一化 / 余弦 | `face_embeddings.py:19-24` `normalize_embedding`、`face_clustering.py:12-15` `cosine` | `domain/identity/faces.py` | **原样**：空或长度不等 → `0.0`；范数 0 → `[]` |
| 2 | 人脸质量 | `face_embeddings.py:233-244` `compute_face_quality` | 同上 | **原样**：`0.40·conf + 0.20·clamp(area/0.08) + 0.20·clamp(sharp) + 0.20·max(0.15, 1−min(90,|yaw|)/90)`，末了再 clamp |
| 3 | 姿态桶 | `face_embeddings.py:224-230` `pose_bucket` | 同上 | **原样**：取 `pose[1]`，`|yaw| <= 20.0` → `frontal`，正负分左右；`pose` 为 `None`/不足 → `yaw=0` → frontal |
| 4 | 贪心多视角聚类 | `face_clustering.py:56-147` `FaceClusterer` | 同上 | **原样**：默认 `0.30/0.30/6/"all"/3/0.1`；`fit` 按 `(-quality, id)` 排序；`noise = quality < minimum_quality`；簇 id `cluster-{n+1}` |
| 5 | 原型维护 | `face_clustering.py:129-147` `_update_prototypes` | 同上 | **原样**：每姿态桶一条、同桶只在新样本 quality 更高时替换、按 `(-quality, pose_bucket, sample_id)` 排序后截断到 `prototype_limit` |
| 6 | 跨模型保护 | `face_clustering.py:95-96`（簇内签名不一致 → 跳过）；`db.py:4876-4884`（原型签名比对） | 同上 + Task 72 | **原样**：`(embedding_model, embedding_version)` 必须与簇内原型之一完全相等，否则整个簇跳过 |
| 7 | 人脸检测（高召回） | `face_detector.py:1-181` `RetinaFaceTiledDetector` | `infrastructure/models/face_detector.py` | **原样**：BGR + mean 104/117/123、priors `STEPS=[8,16,32]`/`MIN_SIZES=[[16,32],[64,128],[256,512]]`、`VARIANCES=(0.1,0.2)`、NMS `0.4`、置信筛 `0.05`；**两趟**：粗 1024/128 过，空了才细 640/256 且阈值降到 `0.1` |
| 8 | SCRFD 复核闸门 | `model_clients.py:1718-1840` `_detect_retina_tiled` | `infrastructure/models/face_adapter.py` | **原样**：`FACE_MIN_SIZE=64`、`FACE_VERIFIED_SCRFD_SCORE=0.7`、`FACE_VERIFIED_MIN_AREA=0.003`、`FACE_IDENTITY_MIN_QUALITY=0.55`；`_bbox_agreement(iou>=0.05)` + `_landmark_sanity`；**只有 verified 才准建簇，uncertain 只做证据** |
| 9 | 人脸向量 | `model_clients.py:1690-1710` `_load_buffalo_recognition` / `:1711-1716` `_recognition_embed` | `infrastructure/models/face_adapter.py` | **原样**：`w600k_r50.onnx`、`((x−127.5)/128.0)`、112×112、512 维 |
| 10 | 在线匹配 | `db.py:4815-4911` `add_face_instance` | `application/identity/enroll_face.py` + 仓储 | **原样**：资格闸门 → 同 scope 且 `status IN ('pending','confirmed')` 的簇 → 原型签名过滤 → 代表向量 max 余弦 → `< 0.30` 建新簇；`quality >= 0.30` 才去匹配已有簇 |
| 11 | 原型刷新 | `db.py:4922-4955` `_refresh_face_prototypes` | 仓储 | **原样**：先删后插；每 `pose_bucket` 一条（`quality DESC, created_at ASC` 取首）；回写 `member_count = 该簇实例总数` |
| 12 | 提及桥 | `db.py:4912-4921` `_link_confirmed_entity_mention` + `:5435` `_add_entity_to_observation` | 仓储 | **原样**：**只有 `status='confirmed'` 的实体才写 mention**；`observations.people_json` 追加 `{entity_id, name, status}` |
| 13 | 簇审阅四件 | `db.py:5152` `confirm_face_cluster` / `:5579` `reject_face_cluster` / `:4776` `merge_face_clusters` / `:4797` `split_face_instance`；HTTP `app.py:1981/2061/2072/2087` | `application/identity/review_cluster.py` + 路由 | **原样**：confirm 写 `entities(status='confirmed', confidence=MAX(…,1))` + 簇 `confirmed` + 逐实例写 mention + `family_role` 非空时写事实 `("家庭角色", role, 1.0)`；**confirmed 簇拒绝 → 409；两个 confirmed 人物合并 → 409；跨 scope 合并 → 409**；merge 只改 `face_instances.cluster_id`；split 用该实例的 embedding 建新簇 |
| 14 | 同名合并 | `db.py:5194-5245` `_merge_cluster_into_person` + `:4407` `find_confirmed_person_by_name` | 同上 | **原样**：同名 → 并入已确认人物，源簇 `rejected` 且 `member_count=0`；`find_confirmed_person_by_name` **先精确名、再遍历 aliases** |
| 15 | 命名 | `db.py:4436-4466` `rename_person` + `:4399` `person_aliases` + `:4428` `set_person_aliases` | `application/identity/name_person.py` + 路由 | **原样**：改名时**把旧名折进 aliases**、`name_state='confirmed'`、写 `entity_revisions(source='user_rename')` |
| 16 | 候选拒绝 | `db.py:5290-5341` `delete_person_candidate` | 仓储 | **原样**：confirmed 抛错；**删实体与全部派生关联，但 `face_instances.cluster_id = NULL` 保留人脸证据** |
| 17 | 预置身份 | `app.py:2253-2310` `_seed_face_detect` + `db.py:5343-5396` `seed_person_identity` | `application/identity/seed_identity.py` | **原样**：三级降级（检测 → 2× 放大重试 → 整图 embedding 兜底）；建 confirmed entity + confirmed cluster + 实例 + 原型；**只在 `/api/people/seed*` 上用，不在相册导入链路上** |
| 18 | `person_score` 输入 | `db.py:2241-2260` `_confirmed_entity_ids_for_observation` / `_for_event`；`db.py:2093-2095` | `AssignEvents` 的生产方（Task 80） | **原样**：确认实体的交集非空 → `0.8`；喂进已有的 `ObservationAnchor.people` / `EventAnchorSet.people` |
| 19 | 事件参与者投影 | `db.py:5549-5578` `_refresh_event_participants` + `:2486` `upsert_event_participant` | `application/memories/…` + 仓储（Task 81） | **原样**：`visible_subject` 置信度 `0.75`、`captured_by` 取 asset 的 `source_confidence`（缺省 `0.5`）；每个受影响事件 `revision+1` 并重算摘要 |
| 20 | 确认后重分段 | `db.py:5450-5481` `resegment_events_for_confirmed_entity` | 同上（Task 81） | **原样**：只在新确认人物**桥起的两场事件**之间合并；6 小时硬窗 + 重跑八分量 + `total >= 0.50` |
| 21 | facts 门禁 | `db.py:1494-1503` `is_confirmed_person_name` + `pipeline.py:348` | `MaintainFacts.confirmed_people`（Task 82） | **原样**：`entities` 里 `entity_type='person' AND status='confirmed' AND canonical_name = ?`；**只校验 subject** |
| 22 | facts 清理 | `db.py:3433-3447` `purge_unanchored_facts` | 仓储（Task 82） | **原样**：删掉 subject 不在已确认人物名集合里的事实（限本 scope） |
| 23 | `person_bridge` 投影 | `retrieval_indexes.py:20,180` | `domain/retrieval/lexical.py` + 写侧（Task 83） | **原样**：字段类型 `person_bridge`，值源是观察的 `people` |
| 24 | 实体检索通道 | `retrieval/entity.py:1-60` | `application/retrieval/retrieve_entity.py`（Task 83） | **原样**：人条件是**硬条件**；优先读 `person_bridge` 投影；**从不宣布身份**，只给候选 |
| 25 | 外观证据裁剪 | `person_appearance.py:4-25` `expanded_person_crop` | `domain/identity/appearance.py`（Task 84） | **原样**：`head_scale=1.35` / `body_height_scale=4.5` / 上扩 `face_height*0.35`；框非法或结果 `< 24×48` → **抛错，不回退整图** |
| 26 | 外观观察 | `app.py:2022-2058` `_analyze_confirmed_person_appearance` + `db.py:4482` | 用例 + `VlmObserver`（Task 84） | **原样**：每人每事件只取 `quality DESC, detection_confidence DESC LIMIT 1` 一张；写 `person_appearance_evidence`，唯一键 `(person, face_instance, model)`；**确认人脸簇时同步触发** |
| 27 | 人物画像证据包 | `person_portraits.py:43-118` `compile_portrait_evidence` | `domain/identity/portrait.py`（Task 85） | **原样**：`max_moments=12`、`per_event=2`、每主题最多 2 条引用、高频动作 `most_common(3)` |
| 28 | 画像强制校验 | `person_portraits.py:119-153` `_evidence_ref_valid` / `validate_portrait` | 同上 | **见「偏离」第 2 条**：三条规则原样搬（80 ≤ 汉字 ≤ 500、themes 2–6、每个 theme 的 `evidence_refs` 非空），但 `_evidence_ref_valid` 的白名单后门**要修** |
| 29 | 确定性兜底画像 | `person_portraits.py:156-181` `deterministic_portrait` | 同上 | **见「偏离」第 3 条**：1.0 的兜底**必然通不过自己的校验**（实测 41 字 < 80），是缺陷 |
| 30 | 核心人物排序 / 代表照 | `person_insights.py:31-111` `core_person_score` / `rank_core_people` / `select_representatives` | `domain/identity/insights.py`（Task 85） | **原样**：六项权重 `0.30/0.20/0.15/0.10/0.10/0.15`、归一化除数 `8.0/6.0/20.0/4.0/4.0`、confirmed 加成 `0.20`、单日惩罚 `0.35`、`rank_core` 硬上限 10、core 门槛 `date_count >= 2 或 confirmed` |
| 31 | 六阶段编排 | `person_insights.py:11-18,159-253` | `application/identity/run_insights.py`（Task 85） | **改**：见「需裁决」第 1 条（三张表被冻结） |
| 32 | 簇列表过滤 | `db.py:5133-5151` `list_face_clusters` | 仓储 | **原样**：强制 `memory_spaces.include_in_people = 1`；`pending` 簇的名字对外显示成「待命名成员」 |
| 33 | 人脸裁剪端点 | `app.py:2508-2533` | 路由（Task 75） | **原样**：`thumbnail((256,256))`、JPEG `quality=88`、`Cache-Control: private, max-age=86400`、bbox 非法 → 422 |

### B. 明确不移植（附证据）

| 能力 | 1.0 位置 | 理由（可摆出来的证据） |
|---|---|---|
| **AdaFace / MagFace 适配器** | `face_embeddings.py:72-221`、`platform_profile.py:257-274` | **它在 1.0 已经是死代码。** `FaceAdapter.__init__` 把 `identity_adapter = None`（`model_clients.py:1629`），全仓**再无第二处赋值**（`grep -rn "\.identity_adapter\s*="` 只命中 `model_clients.py:1629` 与 `tests/test_face_clustering.py` 的 6 处），而唯一的调用点 `app.py:2299` 是 `pipeline.face.identity_adapter.embed(_crop)` → 必抛 `AttributeError` → 被它上面那行裸 `except Exception: return []` 吞掉。1.0 的测试自己写了 `@unittest.skip("legacy identity_adapter contract removed; identity is fixed to buffalo_l")`（`tests/test_face_clustering.py:126`）。**实际生效的身份向量是 buffalo_l 的 w600k_r50（512 维）**（`_detect_retina_tiled_unlocked` 两个分支都把 `embedding_model` 写成 `"buffalo_l"`）。启动脚本里那串 `FACE_EMBEDDING_MODE=adaface ADAFACE_* ` 没有任何读取者（`face_embedding_is_adaface()` 全仓零调用）。 |
| **全局重聚类** `recluster_faces` + `_attach_uncertain_faces` + `_sync_face_entity_statuses` | `db.py:4956` / `:5048` / `:5118` | **调用点全在 benchmark 脚本里**：`scripts/benchmarks/ingest_album3_v2.py:67`、`scripts/benchmarks/ingest_household_face_benchmark.py:57`；生产导入路径只走在线匹配。与 S4 不移植 `_event_pair_score` 的判据同形（那个的两个调用点也都在 benchmark）。**注意 `_attach_uncertain_faces` 与 `_sync_face_entity_statuses` 只被 `recluster_faces` 调**（`db.py:5038-5039`），所以它们也一起不移植。 |
| **benchmark 空间名黑名单** | `db.py:1313-1324` `_migrate_evaluation_spaces` | 它把 `'PhotoBench%' / 'photobench%' / '%_e2b'` 硬编码进生产代码（原文：`WHERE name LIKE 'PhotoBench%' OR name LIKE 'photobench%' OR id LIKE '%_e2b' OR id LIKE 'photobench%'`）。**同一判据已写进 `frozen.md`**（冻结硬编码泄漏条目）。要移植的是 `include_in_people` 这个**机制**（2.0 的列已存在），不是名字黑名单——谁建测试空间谁自己置 0。 |
| **`tools.py` 绕过 include_in_people 的查人物** | `agent_runtime/tools.py:688-698` | 注释自陈「绕过 `list_entities` 的 `include_in_people` 过滤，benchmark scope 也能用」——为评测开的旁路。 |
| **`persons` 遗留表与 `/api/persons`** | `db.py:515`（DDL）/ `:1327` `_migrate_legacy_persons` / `:3461` `update_person` / `:3499` `upsert_person`；`app.py:1498,1505` | 无生产写入者（`upsert_person` 全仓零调用）；真数据全在 `entities`。1.0 自己把 legacy person 迁进 native entity。 |
| **`pairwise_metrics` 及聚类评估脚本** | `face_clustering.py:149-182`、`scripts/benchmarks/evaluate_lfw_clusters.py`、`scan_cluster_params.py` | 只在 benchmark / 测试里用。 |
| **`apply_relationship_threshold`** | `person_graph.py:104-116` | **死代码**：`normalize_person_graph` 里没调它，全仓只有测试调。 |
| **`find_graph_violations` 的结果** | `person_graph.py:187` 返回，`person_insights.py:327/346` 从不读 | 算完即弃。随 S7 一起处理。 |
| **根目录的 `pipeline.py` / `tools.py` / `qdrant_memory.py` 等** | 仓库根 | **过期副本**，无任何模块 import（`app.py:36` 是 `from .pipeline import IngestionPipeline`）。不要照它写。 |
| **人物瞬间 / 关系推断 / 洞察运行 / 画像修订四张表** | `person_moments.py`、`person_graph.py`、`db.py:1127/1165/1184/1204` | **在 `frozen.md` 冻结清单里（表 0 行）**。见「需裁决」第 1 条。 |

### C. 需要用户裁决（1.0 没有对应实现的，先报不自作主张）

见文末「需要用户裁决的点」，共 7 条。

---

## 文件结构

本切片新增 / 改动的文件（其余保持 S4d 终态的原样）：

```text
sentrix-home-web2.0/
├── apps/api/
│   ├── pyproject.toml                      【S5b】+onnxruntime-gpu / insightface / opencv-python-headless
│   └── src/sentrix/
│       ├── domain/
│       │   ├── identity/                   【新包】
│       │   │   ├── __init__.py             包标记（空）
│       │   │   ├── faces.py                归一化 / 余弦 / 质量 / 姿态桶 / 贪心多视角聚类   ← Task 69 已落地
│       │   │   ├── models.py               身份实体 / 提及 / 簇 / 实例 / 原型的领域模型       ← Task 70
│       │   │   ├── ports.py                FaceRepository / EntityRepository / FaceDetector / FaceEmbedder  ← Task 71
│       │   │   ├── appearance.py           expanded_person_crop（纯几何）                  ← Task 84
│       │   │   ├── portrait.py             证据包 / 强制引用校验 / 确定性兜底                ← Task 85
│       │   │   └── insights.py             核心人物排序 / 代表照选择（纯规则）               ← Task 85
│       │   ├── spaces/                     【新包】MemorySpace 归位                        ← Task 76
│       │   │   ├── __init__.py
│       │   │   ├── models.py               MemorySpace（从 infrastructure 迁来）
│       │   │   └── ports.py                SpaceRepository
│       │   ├── memories/ports.py           +EventParticipantRepository（Task 81）
│       │   ├── retrieval/lexical.py        +person_bridge 字段类型（Task 83）
│       │   └── retrieval/ports.py          +EntitySearch（Task 83）
│       ├── application/
│       │   ├── identity/                   【新包】
│       │   │   ├── enroll_face.py          在线匹配（db.py:4815 的等价物）                 ← Task 72
│       │   │   ├── review_cluster.py       confirm / reject / merge / split                ← Task 73
│       │   │   ├── name_person.py          rename / aliases / 同名合并                     ← Task 74
│       │   │   ├── seed_identity.py        _seed_face_detect 三级降级                      ← Task 79
│       │   │   ├── person_directory.py     PersonDirectory 实现（替掉 NoIdentityDirectory）← Task 75
│       │   │   └── run_insights.py         六阶段编排                                      ← Task 85
│       │   ├── memories/assign_events.py   +confirmed_people 的真实来源                     ← Task 80
│       │   ├── memories/maintain_facts.py  +confirmed_people / candidates 的真实来源        ← Task 82
│       │   └── retrieval/retrieve_entity.py 实体通道                                        ← Task 83
│       ├── infrastructure/
│       │   ├── models/face_detector.py     RetinaFaceTiledDetector（照搬 face_detector.py）  ← Task 77
│       │   ├── models/face_adapter.py      SCRFD 复核 + w600k_r50 向量 + quality            ← Task 77/78
│       │   ├── models/onnx_runtime.py      provider 链与显存上限（照搬）                     ← Task 77
│       │   └── persistence/
│       │       ├── migrations/0006_identity.sql                                             ← Task 70
│       │       └── repositories/
│       │           ├── faces.py            face_clusters / face_instances / face_prototypes  ← Task 71
│       │           ├── entities.py         entities / entity_* / relationships               ← Task 71
│       │           ├── participants.py     event_participants / event_entities（Task 81）
│       │           ├── appearance.py       person_appearance_evidence（Task 84）
│       │           ├── spaces.py           改：SQL 保留，模型/端口搬走（Task 76）
│       │           └── search_terms.py    +person_bridge 投影（Task 83）
│       ├── api/routes/identity.py          face-clusters / people / face-instances crop 路由 ← Task 75
│       ├── api/routes/insights.py          画像与人物洞察路由（视裁决）                       ← Task 85
│       └── bootstrap/container.py          +identity 端口装配；**删掉 spaces 例外**（Task 76）
└── docs/{architecture/module-map.md, refactor/{behaviour-baseline,frozen,capability-map.jsonl,s5-deviations.md}}
```

---

## 依赖图与执行顺序

**先画图再排任务。** S0/S1 各踩过一次循环依赖（单独看都对、顺序错了），所以下面这张图里
每一条箭头都要求「上面先落地」。

```text
Task 69  domain/identity 纯规则（归一化 / 余弦 / 质量 / 姿态 / 贪心多视角聚类）
   │                                                        ← 已落地，隔离副本 s5-tdd
   ├── Task 70  迁移 0006：identity 表族 + observations.people_json + events 参与者投影表
   │      │
   │      └── Task 71  身份仓储与端口（faces / entities / PersonDirectory 的实现）
   │             │
   │             ├── Task 72  在线匹配用例（资格闸门 → 匹配 → 建簇 → 原型 → 向量 → mention）
   │             │      │
   │             │      └── Task 73  簇审阅用例（confirm / reject / merge / split）
   │             │             │
   │             │             ├── Task 74  命名与同名合并
   │             │             │      │
   │             │             │      └── Task 75  HTTP 路由（face-clusters / people / crop）+ PersonDirectory 实现
   │             │             │
   │             │             └── Task 80  person_score 接线（entity_mentions → AssignEvents）
   │             │                    │
   │             │                    ├── Task 81  事件参与者 / 实体投影 + 确认后重分段
   │             │                    └── Task 82  facts 门禁开通 + purge_unanchored_facts
   │             │
   │             └── Task 76  MemorySpace 迁进 domain + SpaceRepository 端口（可与 72–75 并行）
   │
   ├── Task 77  人脸检测适配器（RetinaFace tiled + SCRFD 复核）
   │      │
   │      └── Task 78  人脸向量适配器（buffalo_l w600k_r50）+ 质量/姿态归属
   │             │
   │             └── Task 79  导入路径接线（人脸先于事件；容器装配；seed 三级降级）
   │                    │
   │                    └── Task 83  person_bridge 词法投影 + 实体检索通道
   │
   ├── Task 84  外观证据（裁剪 + 观察者 + 确认时触发）        ← 依赖 73 / 75
   └── Task 85  人物画像（证据包 + 强制校验 + 增量触发）      ← 依赖 75 / 81 / 82 / 84

Task 86  e2e 验收（真照片 → 人脸簇 → 确认 → 事件 / 事实 / 画像）  ← 依赖全部
```

### 为什么不能换序（三条，都是「单独看都对、顺序错了就出错」）

1. **Task 73 不写 `_refresh_event_participants`，它属于 Task 81。**
   1.0 的 `confirm_face_cluster`（`db.py:5152`）在同一个函数里既写身份（entities / mentions）
   又刷事件投影（`_refresh_event_participants` → `resegment_events_for_confirmed_entity` → `rebuild_person_memory`）。
   2.0 的两半落在不同任务里，**如果 73 直接调用 81 的模块，就是一个循环**：
   81 的参与者投影要读 mentions，而 mentions 由 73 写。
   **处置**：73 落地时 `confirm` 只做身份层四件事（实体 / 簇状态 / mentions / 原型）；
   **81 落地时回头改 `confirm` 那一处**（同一个文件的后继编辑）。
   **不要**为它留一个空的回调钩子——那正是 S4 栽过的「零调用的纯规则」。
2. **Task 70 的迁移必须先于 71/72。** `observations.people_json`、`event_participants`、
   `event_entities` 三处的列/表在 0006 里一次建齐；1.0 是 `CREATE TABLE IF NOT EXISTS`
   + 运行期 `ALTER`（`db.py:1236-1249`），2.0 是编号迁移，加列只能改 0006 或新开 0007。
3. **Task 79 的人脸必须先于事件归并。** 1.0 在 `pipeline.py:316-320` 用注释钉过这件事：
   「A matching confirmed face writes an EntityMention above, so it is available as a real
   identity bridge during this event decision.」顺序写反**不报错**——`_confirmed_entity_ids_for_observation`
   返回空集，`person_score` 恒 0、`corroborated` 更容易为假，
   事件照分照建，只是**分错了**。2.0 用一条对照测试钉（同两张照片，人脸在前 / 在后）。

### 另一条不是顺序、但同样会静默出错的约束

**`quality >= 0.30`（`db.py:4868`）与 `threshold=0.30`（`db.py:4815`）是两个不同的 0.30。**
前者是「低质量人脸不去匹配已有簇」，后者是余弦阈值。数值相同、语义无关，
改一个不影响另一个。移植时**保持两个独立的常量**，并在测试里各钉一条。

---

## 任务拆分

**本切片 18 个任务，超过「约 15 个」的上限，按约定停下来报告。提议拆成三个子切片：**

| 子切片 | 任务 | 内容 | 是否需要 GPU / LLM | 能不能独立验收 |
|---|---|---|---|---|
| **S5a** | 69–76（8 个） | 身份层地基：纯规则、表、仓储、在线匹配、簇审阅、命名、HTTP、MemorySpace 归位 | **不需要**（端口用假件，模型适配器在 S5b） | 能：簇的生命周期（建 → 审 → 命名）可以端到端跑，只是人脸来自假检测器 |
| **S5b** | 77–83（7 个） | 模型适配与接线：检测 / 向量 / 导入接线 / person_score / 事件投影 / facts 门禁 / 实体通道 | **需要**（ONNX + InsightFace + 一张 3090） | 能：真照片导入 → 自动建簇 → 确认 → 事件与事实变化 |
| **S5c** | 84–86（3 个） | 收尾与画像：外观证据 / 人物画像 / e2e | **需要**（VLM） | 能：确认人物后画像落库且引用可核 |

**S5a 不含模型适配器，所以它交付时「人脸从哪来」是端口注入的假件。**
这一点必须在 S5a 的验收里写明：**S5a 结束时产品上还不能用**，它是 S5b 的地基。
（S4 的教训：Task 66/67 交付了两块零生产调用的能力，被用户点名为两次栽跟头的其中一次。
S5a 不重复这个形状——它的每个模块都会被 S5b 的导入路径调用，只是 S5b 才接线。
如果用户不接受「S5a 单独不可用」，建议直接把 S5a + S5b 合成一个切片，代价是一次要过 GPU。）

---

## 逐任务定义

> 本节的每一项都写成「**移植 1.0 `backend/X.py` 的 `Y`**」。
> **没有一个 2.0 侧的代码块**——它们没跑过，写出来就是 S2 那种「错误率全部发生在计划生成阶段」的重演。
> 每个任务的验收由「必须钉住的行为」那一列给定的**可断言量**构成，执行时先写测试、看它红、再实现。

### Task 69（S5a）· 人脸聚类纯规则 ✅ 已落地

- **移植**：`backend/face_clustering.py` 全文（除 `pairwise_metrics`）+ `backend/face_embeddings.py`
  的 `normalize_embedding` / `_clamp` / `pose_bucket` / `compute_face_quality` 四个纯函数。
- **2.0 落点**：`apps/api/src/sentrix/domain/identity/faces.py`（+ 空包标记 `__init__.py`）。
- **必须钉住的行为**：见「验证记录」的 Task 69 一节（那是实际跑过的清单）。
- **门禁**：`apps/api/tests/unit/test_identity_faces.py`。
- **capability-map**：`{"capability": "人脸多视角贪心聚类", "origin_kind": "migrated",
  "source_commit": "2d2cee7c", "source_path": "backend/face_clustering.py",
  "source_symbol": "FaceClusterer", ...}` 等 5 条（归一化 / 质量 / 姿态 / 聚类 / 原型）。

### Task 70（S5a）· 迁移 0006：identity 表族

- **移植**：`db.py` 的 DDL 段——
  `entities`（`:560-570`，含 `:1240-1243` 追加的 `identity_state` / `role_state` / `name_state`）、
  `entity_revisions`（`:572-580`）、`entity_properties`（`:582-594`）、
  `face_clusters`（`:612-622`）、`face_instances`（`:624-641`，含 `:1238` 追加的 `validity`）、
  `face_prototypes`（`:643-654`，**`UNIQUE(cluster_id, pose_bucket)`**）、
  `person_appearance_evidence`（`:656-669`，**`UNIQUE(person_id, face_instance_id, model_name)`**）、
  `entity_mentions`（`:671-678`，**`UNIQUE(entity_id, observation_id, face_instance_id)`**）、
  `entity_observations`（`:680-686`，复合主键）、`relationships`（`:688-699`，含 `:1244-1246` 的 `inverse_predicate`），
  以及索引 `:1095-1102` / `:1117-1121`。
- **2.0 另加**（1.0 没有独立列）：`observations.people_json`（1.0 在 `observations` 表上，
  2.0 的 0005 没建）、`event_participants`（`db.py:503-511`）、`event_entities`（`db.py:487-501`）。
- **不移植**：`persons`（遗留）、`entity_merge_candidates`（冻结）、`person_moments` /
  `person_role_hypotheses` / `relationship_hypotheses` / `person_insight_runs` /
  `person_portrait_revisions`（冻结，见「需裁决」第 1 条）、`family_*`（S7）。
- **必须钉住的行为**：三条 `UNIQUE` 约束真的生效（插重复要么报错要么 `INSERT OR IGNORE`）；
  `face_instances.cluster_id` 可空（1.0 为此外加重建表，`db.py:1257-1311`——2.0 一次建对）。
- **门禁**：`apps/api/tests/integration/test_identity_schema.py`（对齐 S4 的 `test_timeline_schema.py`：
  **不要**只验迁移执行器，那在 0006 写出来之前就是绿的，是恒真门禁）。
- **注意**：`database._statements()` 用朴素 `split(";")`，**迁移脚本里不能有含分号的字符串常量**。

### Task 71（S5a）· 身份仓储与端口

- **移植**：`db.py:4407-4545`（`find_confirmed_person_by_name` / `person_aliases` /
  `set_person_aliases` / `rename_person` / `get_face_instance` / `list_face_prototypes` /
  `record_person_appearance_evidence` / `list_person_appearance_evidence` / `entity_event_ids`）、
  `:4644` `_record_entity_revision`、`:4666-4750` 的实体属性层、`:5133` `list_face_clusters`、
  `:5402` `get_person_evidence`、`:5590` `list_relationships`。
- **2.0 落点**：`infrastructure/persistence/repositories/faces.py` + `entities.py`；
  端口在 `domain/identity/ports.py`（`typing.Protocol`，属性用 `@property`——见
  `domain/retrieval/ports.py:188-196` 的注解）。
- **必须钉住的行为**：`list_face_clusters` 强制 `include_in_people = 1`；
  `pending` 簇对外名字是「待命名成员」；`entity_properties` 里 `source='user'` 的当前值
  **永不被 derived 覆盖**（`db.py:4695-4727`）；derived 新值不同则落 `status='pending'`。
- **门禁**：`apps/api/tests/integration/test_identity_repositories.py`。

### Task 72（S5a）· 在线匹配用例

- **移植**：`db.py:4815-4911` `add_face_instance`，逐分支照搬：
  1. `scope_id` **由 asset 决定，调用方传不进来**；
  2. 资格闸门 `identity_eligible = identity_eligible and validity == "verified"`，
     不合格 → `cluster_id = NULL` 落库并返回（**不建簇**）；
  3. `quality >= 0.30` 才进候选簇循环；
  4. 候选 = 同 scope 且 `status IN ('pending','confirmed')`；
  5. 原型 `(model_name, model_version)` 签名不匹配 → 跳过该簇；
  6. 分数 = 与所有原型余弦的 **max**；无原型时退回 `representative_embedding_json`；
  7. `< 0.30` → 建新簇（**新簇同时建一个 pending person 实体**，`db.py:4629-4663`，
     summary 文案「由 buffalo_l embedding 生成，等待用户确认」）；
  8. 落实例 → 刷原型 → 写 `memory_vectors(space='visual', source_type='face_instance')`；
  9. **只有 `status='confirmed'` 的实体才写 mention**。
- **2.0 落点**：`application/identity/enroll_face.py`（用例）+ 仓储里的三个写方法。
- **必须钉住的行为**：上面 9 条各一条用例；另加「两个 0.30 互不影响」的对照用例。
- **门禁**：`apps/api/tests/integration/test_enroll_face.py`。

### Task 73（S5a）· 簇审阅用例

- **移植**：`db.py:5152-5193` `confirm_face_cluster`、`:5579-5589` `reject_face_cluster`、
  `:4776-4796` `merge_face_clusters`、`:4797-4814` `split_face_instance`、
  `:5290-5341` `delete_person_candidate`、`:5194-5245` `_merge_cluster_into_person`。
- **必须钉住的行为**：
  - confirm：空名字 → 400；写 `entities.status='confirmed'` + `confidence = MAX(…,1)`；
    簇 `status='confirmed'` + `revision+1`；**簇内每个实例都写 mention**；
    `family_role` 非空 → 写事实 `("家庭角色", role, 1.0)`；同名已确认人物存在 → 走合并分支并回 `merged_into`；
  - reject：confirmed 簇 → **409**；否则删候选实体，**人脸实例保留且 `cluster_id = NULL`**；
  - merge：跨 scope → **409**；两个 confirmed → **409**；只改 `face_instances.cluster_id`，
    源簇 `rejected` + `member_count=0`，刷目标原型，写 `entity_revisions(face_cluster_merge)`；
  - split：`face_instance_id` 不属于该簇 → 404；写 `entity_revisions(face_cluster_split)`；
  - **全部四件都写 `entity_revisions`**（审计要求，产品硬约束）。
- **不写**：`_refresh_event_participants` / `resegment_events_for_confirmed_entity` /
  `rebuild_person_memory`（Task 81/85）。
- **门禁**：`apps/api/tests/integration/test_review_cluster.py`。

### Task 74（S5a）· 命名与同名合并

- **移植**：`db.py:4407-4435`（`find_confirmed_person_by_name` / `person_aliases` /
  `set_person_aliases`）、`:4436-4466` `rename_person`。
- **必须钉住的行为**：改名后**旧名进 aliases**；`name_state='confirmed'`；
  写 `entity_revisions(source='user_rename')`；`find_confirmed_person_by_name` 先精确名再扫 aliases；
  **同名只在同 scope 内合并**（1.0 的 `test_cross_scope_same_name_does_not_merge` 钉的就是这条）。
- **门禁**：`apps/api/tests/integration/test_name_person.py`。

### Task 75（S5a）· HTTP 路由 + PersonDirectory 实现

- **移植**：`app.py:1969-2100`（簇列表与四审阅端点）、`:1512-1800`（`/api/people` 系列）、
  `:2205`（batch-confirm）、`:2237-2250`（rename）、`:2508-2533`（人脸裁剪）。
- **2.0 约定**：路由文件放 `api/routes/identity.py`，在 `main.py` 的 `v1` 上 `include_router`；
  **改了路由必须重跑 `scripts/export_openapi.py`**（`make ci` 的 `--check` 会红）。
- **同时**：把 `NoIdentityDirectory` 换成真实实现（`PersonDirectory.lookup`），
  工具契约 / 账本 / 门禁一行都不动（`infrastructure/assistant/identity.py:11` 的约定）。
- **必须钉住的行为**：四审阅端点的状态码（400 / 404 / 409）；裁剪端点的
  `thumbnail((256,256))` + `quality=88` + `Cache-Control`；bbox 非法 → 422。
- **门禁**：`apps/api/tests/contract/test_identity_routes.py`。

### Task 76（S5a）· MemorySpace 归位

- **现状**：`MemorySpace` 定义在 `infrastructure/persistence/repositories/spaces.py:11-18`，
  `Container.spaces` 是**全仓唯一**字段类型落在 infrastructure 的例外，
  由 `tests/unit/test_container_wiring.py` 的 `EXCEPTIONS` 字典强制。
- **要做**：新建 `domain/spaces/models.py`（`MemorySpace`）与 `domain/spaces/ports.py`
  （`SpaceRepository` Protocol）；`spaces.py` 只留 SQL；`container.py` 字段类型改成端口并
  **删掉例外**；`module-map.md` 里「装配点按端口声明」一节的两个例外改成**一个**（`database`）。
- **这一条 1.0 没有对应实现**（1.0 没有分层），属于设计 §2.3 的既定目标，**不是新设计**。
  用户 brief 把它列进 S5（「顺便看看是否该一起把它迁进 domain」），本计划**建议做**，
  理由是它让 `test_container_wiring.py` 的例外表少一条，而不是多一条。
- **风险**：`EXCEPTIONS` 字典是**两张表**（`container.py` 注释 + `test_container_wiring.py` 的字典
  + `module-map.md` 的说明文），三处要同时改，漏一处 `make ci` 红。
- **门禁**：`apps/api/tests/unit/test_container_wiring.py`（改完后仍绿）+ `test_spaces_ports.py`。

### Task 77（S5b）· 人脸检测适配器

- **移植**：`backend/face_detector.py:1-181` 全文 + `model_clients.py:1718-1840`
  `_detect_retina_tiled_unlocked` 的 SCRFD 复核段 + `backend/onnx_runtime.py` 全文。
- **新增依赖**：`onnxruntime-gpu`、`insightface`、`opencv-python-headless`（2.0 venv 实测都没有）。
- **必须钉住的行为**：priors / decode / NMS `0.4` / 两趟 tiling 参数；
  `_bbox_agreement` 的 `center_ratio=1.0` + `iou_threshold=0.05`；
  `_landmark_sanity` 的 `min_eye_ratio=0.1` + 「框小于 64 时直接放行」；
  `_expand_crop` 的 `margin=0.75` / `min_side=256`；
  provider 显存上限默认 `256 MiB`、`SENTRIX_FACE_GPU_MAX_CONCURRENCY` 默认 `1`（8091 生产设 4）。
- **门禁**：`apps/api/tests/unit/test_face_geometry.py`（priors/decode/NMS 可纯 CPU 验）
  + `apps/api/tests/integration/test_face_detector.py`（需要真权重，缺失时 `skip`）。

### Task 78（S5b）· 人脸向量适配器

- **移植**：`model_clients.py:1690-1716`（`_load_buffalo_recognition` / `_recognition_embed`）。
- **必须钉住的行为**：112×112、`(x-127.5)/128.0`、512 维、`embedding_model="buffalo_l"`、
  `embedding_version = FACE_MODEL_NAME`（**1.0 的变量名与语义不符但有真实后果**：
  它参与签名比对，换模型包时旧簇会被整体跳过——照搬，不改名）。
- **门禁**：`apps/api/tests/integration/test_face_embedding.py`（权重缺席时 skip）。

### Task 79（S5b）· 导入路径接线

- **移植**：`pipeline.py:316-320`（人脸先于事件）与 `pipeline.py:375`（fast-evidence 路径）；
  `app.py:2253-2310` `_seed_face_detect` 的三级降级。
- **必须钉住的行为**：人脸写入必须早于事件归并（对照测试）；三级降级的三个触发条件
  （`max(w,h) > 600`、`scale = min(2.0, 1280/max_dim) > 1.05`、整图兜底）；
  **1.0 的整图兜底是死路径**（见「偏离」第 4 条）。
- **门禁**：`apps/api/tests/integration/test_import_identity.py`。

### Task 80（S5b）· `person_score` 接线

- **移植**：`db.py:2241-2260` `_confirmed_entity_ids_for_observation` / `_for_event` → 喂进
  **已经存在的** `AssignEvents.confirmed_people`。
- **必须钉住的行为**：交集非空 → `0.8`（`PERSON_MATCH`）；`person_score` 参与
  `corroborated`（`domain/memories/events.py:317`），因此会改变 `split_guard` 的结果——
  **一条「有共同确认人物时不再拆事件」的对照用例**（这条比分数本身更接近产品行为）。
- **门禁**：`apps/api/tests/integration/test_assign_events.py` 加用例（文件已存在）。

### Task 81（S5b）· 事件参与者 / 实体投影 + 确认后重分段

- **移植**：`db.py:5549-5578` `_refresh_event_participants`、`:2486` `upsert_event_participant`、
  `:3287` `upsert_event_entity`、`:5450-5481` `resegment_events_for_confirmed_entity`。
- **同时改** Task 73 的 `confirm`：接上这三件事（顺序见「为什么不能换序」第 1 条）。
- **必须钉住的行为**：`visible_subject` 置信度 `0.75`；`captured_by` 取 `source_confidence`（缺省 `0.5`）；
  每个受影响事件 `revision+1`；`video_scene` 事件**不重算摘要**（`db.py:5575`）；
  重分段只在 **6 小时窗内 + 八分量 `total >= 0.50`** 时合并。
- **门禁**：`apps/api/tests/integration/test_event_participants.py`。

### Task 82（S5b）· facts 门禁开通

- **移植**：`db.py:1494-1503` `is_confirmed_person_name`（**只校验 subject，精确名，不查 alias**）
  → `MaintainFacts.confirmed_people`；`db.py:3433-3447` `purge_unanchored_facts`。
- **同时**：候选事实的来源（`MaintainFacts.candidates`）——1.0 来自模型自报的 `facts`
  （`model_clients.py:1130-1198` 让 `analyze_image` 回 `facts` 数组），2.0 的观察器**明确不搬**
  （`s4-deviations.md` 第 30 条）。**这是「需裁决」第 6 条**：门禁开了但候选来源仍空，
  事实表还是空的（只放门禁不够，S4 已经在注释里写明这一点）。
- **门禁**：`apps/api/tests/integration/test_maintain_facts.py` 加用例（文件已存在）。

### Task 83（S5b）· `person_bridge` 投影 + 实体检索通道

- **移植**：`retrieval_indexes.py:20,180`（字段类型 `person_bridge`，值源是观察的 `people`）
  + `retrieval/entity.py:1-60` 全文。
- **必须钉住的行为**：人条件是**硬条件**（不是加分）；检索器**从不宣布身份**，
  只返回候选（`entity.py:1-8` 的 docstring 原文）；缺 `person_bridge` 投影时退回直接读观察的 people。
- **门禁**：`apps/api/tests/integration/test_retrieve_entity.py`。

### Task 84（S5c）· 外观证据

- **移植**：`person_appearance.py:4-25` `expanded_person_crop`（纯几何，无 IO）+
  `app.py:2022-2058` `_analyze_confirmed_person_appearance` +
  `db.py:4482-4534` `record_person_appearance_evidence`。
- **必须钉住的行为**：`1.35` / `4.5` / `0.35` 三个系数；框非法或 `< 24×48` → **抛错不回退整图**；
  每人每事件只取一张（`quality DESC, detection_confidence DESC LIMIT 1`）；
  写 `person_appearance_evidence` 时 person 必须已 confirmed 且实例已 link，否则抛错；
  触发时机 = **确认人脸簇时同步**。
- **门禁**：`apps/api/tests/unit/test_person_appearance.py` + `integration/test_appearance_evidence.py`。
- **注**：`expanded_person_crop` 是 S5 里**唯一一处 1.0 有第二个生产调用者**的纯函数
  （另一个是 `agent_runtime/tools.py:2184` 的 agent 工具，属 S6）。

### Task 85（S5c）· 人物画像

- **移植**：`person_portraits.py` 全文（`compile_portrait_evidence` / `_evidence_ref_valid` /
  `validate_portrait` / `deterministic_portrait` / `normalize_writer_output` +
  `PERSON_PORTRAIT_PROMPT` 原文）、`person_insights.py:31-111`（排序与代表照，纯规则）、
  `person_insights.py:159-253`（六阶段编排的 1/2/5/6 阶段）。
- **必须钉住的行为**：80 ≤ 汉字 ≤ 500；themes 2–6；每 theme `evidence_refs` 非空；
  敏感词黑名单；未确认角色必须带模糊措辞（`似乎/可能/从照片看/看起来/像是`）；
  三级降级 writer → repair → 确定性兜底。
- **三处 1.0 缺陷必须先报告再决定**（见「偏离」第 2/3 条）：`_evidence_ref_valid` 的
  `relationship/semantic_claim/appearance` 白名单后门、`deterministic_portrait` 通不过自己的校验、
  校验失败完全静默（`errors` 从不落日志/落库）。
- **门禁**：`apps/api/tests/unit/test_portrait.py` + `integration/test_run_insights.py`。

### Task 86（S5c）· e2e 验收

- **移植**：无（这是 2.0 的验收）。
- **要断言内容，不只断言数量**（S4 栽在 `events: 53` 上）：导入一册真照片后，
  断言「至少一个簇的 `member_count >= 2`」且「该簇确认后，`entity_mentions` 的行数等于该簇实例数」、
  「确认前后同一对照片的事件归属从两个变成一个」、「`facts` 里出现该人物的家庭角色事实且
  subject 等于确认的名字」。
- **门禁**：`apps/api/tests/e2e/test_identity_album.py`。

---

## 验证记录

### 基线（隔离副本 `/home/asus/s5verify/repo`，起点提交 `f663522`）

```bash
$ cd /home/asus/s5verify/repo && make ci
.venv/bin/ruff check apps/api/src apps/api/tests scripts
All checks passed!
.venv/bin/ruff format --check apps/api/src apps/api/tests scripts
178 files already formatted
.venv/bin/mypy
Success: no issues found in 111 source files
.venv/bin/lint-imports
Analyzed 143 files, 575 dependencies.
domain-purity KEPT
application-purity KEPT
api-transport-only KEPT
infrastructure-purity KEPT
layers KEPT
Contracts: 5 kept, 0 broken.
.venv/bin/python scripts/check_capability_map.py
capability-map: 0 个问题
.venv/bin/python scripts/check_module_map.py
module-map: 0 个问题
.venv/bin/python scripts/export_openapi.py --check
openapi 契约一致
.venv/bin/pytest --cov --cov-report=term-missing
........................................................................ [ 10%]
......sssssssssssss.F......sss.......................................... [ 20%]
....................make: *** [Makefile:51: coverage] 已杀死
EXIT=2
```

**这个基线不是全绿，如实记录**：`coverage` 那一步被**信号 9（SIGKILL）**杀掉
（`make: *** [Makefile:51: coverage] 已杀死`），进程在打印自己的条数之前就死了，
**所以基线那一跑的「多少 passed」没有拿到**。
不带 `--cov` 的重跑拿到过 0 失败的一轮；但另一次全量重跑里
`apps/api/tests/e2e/test_assistant_turn.py` 的
`test_asking_for_photos_returns_openable_originals` 与
`test_the_turn_records_the_whole_reasoning_chain` **各失败一次**，
而**单独重跑该文件是 `6 passed`**。
基线的确切条数在 Task 69 落地后按「终态条数 − Task 69 新增条数」反推（见下）。

- 环境事实：`free -g` 显示 60 GB 内存、已用 39 GB、可用 19 GB；
  同机在跑 1.0 的 8091（RSS 11.7 GB）、vLLM 8500、photobench runner 等。
- 结论：**这是资源竞争下的抖动，不是代码缺陷**。S5 执行时「每个 commit 跑一次完整 `make ci`」
  这条要求在本机需要**避开 GPU/内存高峰**，否则会拿到假红灯。
  **建议执行前先确认机器空闲**，并把 `make ci` 的原始输出（含被杀的次数）如实留在实施记录里。

### Task 69（S5a）· 人脸聚类纯规则（**已实施**）

隔离副本分支 `s5-tdd`，commit `2824a5f`。三份原始日志在 `/home/asus/s5verify/logs/`。

**红灯**（只落 `apps/api/tests/unit/test_identity_faces.py`，实现文件不存在）：

```bash
$ .venv/bin/pytest apps/api/tests/unit/test_identity_faces.py -q
==================================== ERRORS ====================================
_________ ERROR collecting apps/api/tests/unit/test_identity_faces.py __________
ImportError while importing test module '.../apps/api/tests/unit/test_identity_faces.py'.
apps/api/tests/unit/test_identity_faces.py:12: in <module>
    from sentrix.domain.identity.faces import (
E   ModuleNotFoundError: No module named 'sentrix.domain.identity'
=========================== short test summary info ============================
ERROR apps/api/tests/unit/test_identity_faces.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

**绿灯**：

```bash
$ .venv/bin/pytest apps/api/tests/unit/test_identity_faces.py -q
62 passed, 2 warnings in 0.06s
```

**完整 `make ci`**（仓库根）：

```bash
$ make ci
.venv/bin/ruff check apps/api/src apps/api/tests scripts
All checks passed!
.venv/bin/ruff format --check apps/api/src apps/api/tests scripts
181 files already formatted
.venv/bin/mypy
Success: no issues found in 113 source files
.venv/bin/lint-imports
Analyzed 145 files, 579 dependencies.
domain-purity KEPT / application-purity KEPT / api-transport-only KEPT /
infrastructure-purity KEPT / layers KEPT
Contracts: 5 kept, 0 broken.
capability-map: 0 个问题
module-map: 0 个问题
openapi 契约一致
TOTAL                                                                 2844     68    98%
762 passed, 16 skipped, 2 warnings in 112.82s (0:01:52)
cd apps/web && npx tsc -b
cd apps/web && npx oxlint
Found 0 warnings and 0 errors.
cd apps/web && npx vite build
✓ built in 76ms
ci: 全部通过
```

**这一跑同时给出基线的确切条数**：`762 passed − 62`（Task 69 新增）= **700 passed**；
`mypy` 从 **111** 个文件长到 **113**（`identity/__init__.py` + `identity/faces.py`）；
import-linter 从 143 files / 575 deps 长到 145 / 579。
`16 skipped` 是**本机环境决定的**（缺 GeoNames / Qdrant / 真权重的用例），不是新增的。
`make ci` 这一次**一次性通过，没有触发 OOM 或 e2e 抖动**。

> **一处如实说明**：这次的红灯是「模块不存在导致的收集错误」，
> 它证明的是「测试文件先于实现落地」，**不证明任何一条行为被钉住**。
> 真正证明行为被钉住的是绿灯的 62 条用例——每一条都对应 1.0 源码里的一个常量或分支。
> **S5 后续任务的红灯层要求更高**：实现先落地、再删掉那一处，让红灯指向具体断言。

#### Task 69 实测出来的 1.0 行为（behaviour-baseline 的人脸一节缺的正是这些）

`behaviour-baseline.md` 的人脸表只记了三个阈值。实施时实测到的、**必须补进那张表**的：

1. **`continue` 与 `break` 等价**：候选簇已按 `(-score, cluster.id)` 降序排过，
   第一个低于阈值的后面必然都低于阈值。1.0 用的是 `continue`，照抄。
   **这条写不成测试**（变异体杀不掉），只能靠读代码——如实登记为「未被门禁覆盖的一处」。
2. **「同姿态桶为空 → 直接命中」不是无门槛的**：阈值检查（`best_score` 对**原型**取最大）
   发生在 `_same_view_members` **之前**；空桶分支绕过的只是 `_matches`（逐成员比对）。
3. 承 2，**后果比想象的强**：一个 `profile_left` 新样本只要与簇里任一**原型**够像，
   就能无条件并入，**哪怕它与簇里某些成员的余弦是 −0.64**。
   1.0 自带的 `test_frontal_and_profile_views_share_multi_view_cluster` 正是靠这条。
   这也划定了「unknown 必须匹配全部成员」这条防线的真实适用范围：
   **它只对 `pose_bucket == "unknown"` 的样本生效**（`pose_bucket` 确实存在但不等于
   新样本姿态的样本，走的是另一条路）。
4. **`noise=True` ⟺ 单例 + 低于质量门**：低于质量门的样本进不了匹配，noise 簇又被排除出候选。
   它仍然会进 `result.clusters` 与 `labels`，**不是被丢掉**。
5. `_update_prototypes` 的排序/截断**只在追加新桶时跑**；同桶原地替换直接返回、不重排
   （替换只在 quality 变大时发生，排序键首项就是 `-quality`，行为等价）。
6. **`p10` 的退化条件是 `len(sims) <= 2`**（两个成员也退化），不是 `< 2`。
7. 模型身份与 `protected_cluster` 都是**整簇否决**，不是按成员过滤。
8. **1.0 的四个纯函数里一行 `os` 都没有**——`os.getenv("ADAFACE_*")` 全在被判死的适配器里。
   所以本次移植**没有产生任何 env 派生常量**（与「读 env 的落成模块级常量」这个预设不同，
   这一类读取在 Task 69 的范围里根本不存在）。
9. `compute_face_quality` 最后那次 `_clamp` 是**恒等**（四项非负、权重和为 1，上界恰好 1.0）——
   照抄保留，**不可达**。
10. **`recluster_faces(threshold=0.30, minimum_quality=0.55)` 的 `minimum_quality`
    与 `FaceClusterer` 自身默认的 `0.30` 不一致**。1.0 的两个 0.30 之外的第二个 0.55
    只出现在那个 benchmark-only 的函数里。
11. **唯一一处字节级偏离**：`cosine` 里的 `zip(...)` 加了 `strict=True`
    （ruff 的 B905 要求；长度已在上一步判断过，行为不变）。
12. **变异检查**：对实现做了 32 处变异，**31 处被测试杀掉**，存活的那一处就是第 1 条。

**另外**：`behaviour-baseline.md` 的人脸表需要按上面 1–10 补全（本轮**没有**改那份文档，
因为那份文档的更新应当与代码同一次提交落地；Task 69 进真实仓库时一并改）。

### 环境事实（规划时实测，供执行时对齐）

| 项 | 实测值 | 命令 |
|---|---|---|
| GPU 显存 | 13124 MiB / 24576 MiB 已用 | `nvidia-smi --query-gpu=memory.used,memory.total --format=csv` |
| 内存 | 60 GB 总 / 19 GB 可用 | `free -g` |
| 2.0 venv 人脸依赖 | **全缺**（无 onnxruntime / insightface / opencv） | `.venv/bin/pip list \| grep -iE "onnx\|insight\|opencv"` |
| 1.0 venv 人脸依赖 | `insightface 1.0.1` / `onnxruntime-gpu 1.23.2` / `opencv-contrib-python 4.10.0.84` | 同上 |
| AdaFace 权重 | 700,286,703 B，在 `/home/asus/models/AdaFace/pretrained/` | `ls -la` |
| 隔离副本大小 | 18 MB（不含 venv 与 node_modules） | `du -sh` |
| 磁盘 | `/home/asus` 可用 1.1 T | `df -h` |

---

## 需要用户裁决的点（7 条，先报不做）

1. **【必须先裁决】拆分与起点。** 本切片 18 个任务 > 约定的 15。建议按
   S5a（69–76，无 GPU）/ S5b（77–83，需 GPU）/ S5c（84–86，需 VLM）拆。
   **请裁决：先做 S5a，还是 S5a+S5b 一起？** 这决定了接下来「阶段 3–5」在哪个范围里执行。

2. **人物画像的落点与冻结清单冲突。** 用户 2026-09-22 解冻「人物画像」，但
   `frozen.md` 同时冻结了 `person_insight_runs`（洞察运行）/ `person_portrait_revisions`（画像修订）/
   `person_moments`（人物瞬间）三张表（都是 0 行），而 1.0 的画像**恰好全靠这三张表**：
   六阶段的状态写 `person_insight_runs`（`person_insights.py:170-187`）、
   画像成品写 `person_portrait_revisions`（`:246`）、画像的证据层来自 `person_moments`（`:209-211`）。
   **另外**，`person_moments.py` 与 `person_graph.py` 里的提示词与代码是 S7（家庭关系）的前置。
   **请裁决最小解冻集**。候选：
   (a) 只解冻 `person_portrait_revisions`（画像有落点），仍冻结 run / moments
   → 代价：画像证据包只剩 facts + relationships，`person_moment` 类引用恒空，
   而 1.0 对另外三种引用（`relationship` / `semantic_claim` / `appearance`）**不做存在性校验**；
   (b) 解冻 revisions + moments，仍冻结 run（编排不落库，改成同步跑）
   → 代价：失去断点续跑与并发保护；
   (c) 三张全解冻（等于把 S5c 做成「1.0 六阶段原样搬」）→ 代价：把 S7 的角色/关系假设一起拖进来。
   **我的建议是 (b)**，但这是你的裁决。

3. **AdaFace 要不要。** 你 brief 里把它写成 S5 的硬约束（「AdaFace 走 CPU、权重在
   `/home/asus/models/AdaFace/…`」），但**实测 1.0 当前的 AdaFace 路径是死代码**：
   `identity_adapter` 恒为 `None`（唯一赋值 `model_clients.py:1629`），唯一调用点
   `app.py:2299` 必抛 `AttributeError` 并被裸 `except` 吞掉；1.0 自己的测试把它标成
   `@unittest.skip("legacy identity_adapter contract removed; identity is fixed to buffalo_l")`。
   真正生效的身份向量是 **buffalo_l 的 w600k_r50（512 维）**。
   **请裁决：按实测走 buffalo_l（我的建议，理由是「证据强可以按证据走」），还是照搬 AdaFace
   并把那条断掉的接线接上？** 后者要额外背 700 MB PyTorch checkpoint + `PYTHONNOUSERSITE`
   与 NVIDIA 库路径的坑（`scripts/runtime/start_sentrix_api_8091.sh` 里为此写了两段注释）。

4. **全局重聚类要不要。** 你的 brief 把它当成 1.0 的聚类机制之一。**实测它没有任何生产调用者**：
   `recluster_faces` 的两个调用点都在 `scripts/benchmarks/`；`_attach_uncertain_faces` 与
   `_sync_face_entity_statuses` 只被 `recluster_faces` 调。
   按 S4 不移植 `_event_pair_score` 的同一判据，**建议不移植**；但它有一个真实的产品后果：
   一册照片导完后，本该合并的簇只能靠用户手点合并。
   **请裁决：不移植（我的建议）还是要一个「批末全局重聚类」？**

5. **`entity` 检索通道属不属于 S5。** S4 的计划与 deviations 把它记在 S5
   （`s4-implementation-plan.md:1632`「`entity` / `text_ann` 两路（S5 / S6）」），
   但你的 S5 职责描述里只有离线侧的「人脸与身份层 + 收尾与画像」。
   本计划按 S4 的记录**把 Task 83 放进 S5b**（因为 `person_bridge` 的写侧依赖 S5 的 mentions，
   没有身份层它无从填）。**请裁决：留在 S5b，还是挪到 S6 与 `text_ann` 一起？**

6. **facts 的候选来源。** 门禁（Task 82）开了之后，事实表**仍然是空的**——
   1.0 的候选来自模型自报的 `facts` 数组（`model_clients.py:1130-1198`），2.0 明确不搬
   （`s4-deviations.md` 第 30 条，「属于 S5」）。**请裁决**：
   (a) S5 把「观察 → 事实候选」的规则搬过来（需要先定义候选从哪来）；
   (b) 维持现状，事实层留到 S6；
   (c) 只开通门禁 + `purge_unanchored_facts`，候选来源单独立项。

7. **「关系确认」的边界。** 你把「关系确认」列为 S5 的核心（与簇审阅、命名并列），
   但家庭关系是 S7。1.0 里这两件事分处两地：确认人脸簇时写的是一条**家庭角色事实**
   （`confirm_face_cluster` 里的 `("家庭角色", role, 1.0)`），
   而人物关系是 `relationships` 表 + `confirm_relationship`（`db.py:6014`）+ 家族图谱专线
   （`db.py:5620-5950`）。**请裁决 S5 的边界**：
   (a) S5 只做「确认簇时写家庭角色事实」（我的建议）；
   (b) S5 另加 `relationships` 表的确认/拒绝端点（不含图谱推理）；
   (c) 更多。

---

## 顾虑与阻塞

1. **`make ci` 在当前机器上不稳定。** 基线就有一次被 SIGKILL、两次 e2e 抖动
   （详见「验证记录 · 基线」）。「每个 commit 跑一次完整 `make ci`」这条要求在 153 上
   需要挑时间窗，否则会拿到假红灯并**据此改代码**——这正是 S2/S3 反复栽过的那类错误。
2. **S5b 会新增三个重型依赖**（onnxruntime-gpu / insightface / opencv-python-headless），
   而 153 上 1.0 的 8091 还在跑、GPU 只剩 11 GB。**S5b 的验收必须与 1.0 的生产错峰**，
   或者走 46/118 那台。这一条不在本计划能解决的范围里，先报。
3. **2.0 不管理模型进程生命周期**（用户 brief），而人脸模型是**进程内**加载
   （1.0 的 `FaceAdapter` 是模块级单例，`app.py:61` 在 import 期构造、首次 `detect()` 才加载）。
   2.0 的装配点是 `Container.build()`——人脸适配器会变成**每个进程一份**，
   而 2.0 有 `SENTRIX_PIPELINE_MAX_WORKERS` 这类并发设置的历史。**这一条需要设计确认**，
   本计划按「容器内单例、进程内共享」写。
4. **`_seed_face_detect` 的两处 1.0 缺陷**（先报不改）：
   (a) 第三级「整图 embedding 兜底」依赖 `identity_adapter`，那个永远是 `None`
   → 该级**永远返回 `[]`**（`app.py:2296-2310`）；
   (b) `seed_person_identity` 的返回语句用了未定义的 `m_role`（`app.py:2349`；
   `m_role` 只在 `seed_persons_batch` 的 `:2373` 定义）→ `POST /api/people/seed` 一旦走到
   return 就是 `NameError`。**两条都照实记录，移植时按证据走（不搬死路径、把变量改对），
   并在 `s5-deviations.md` 里登记。**
5. **`person_insights` 的生产触发默认关闭**（`SENTRIX_PERSON_INSIGHT_SCOPES` 默认空串 →
   任何 scope 都不匹配 → 永不自动跑，`backend/pipeline.py:764-769`），
   只有 HTTP 显式请求或 benchmark 脚本会跑六阶段。
   **所以「增量触发」这条用户描述，在 1.0 的实际默认装配下并不生效**——
   画像不会因为导入相册而更新。这一点会直接影响 Task 85 的验收定义，**先报**。
6. **`person_insights` 的三处内部缺陷**（先报不改）：阶段 2 把 `vector_by_asset` 传成空 dict
   （`person_insights.py:271`）→ `duplicate_threshold=0.94` 的向量去重**从未生效**；
   阶段 4 的 `cooccurrence` / `moments` / `devices` 恒为空，而提示词宣称输入包含它们；
   `graph_violations` 算完即弃。**这三条要么修（偏离）、要么照搬（保留缺陷）——请裁决。**

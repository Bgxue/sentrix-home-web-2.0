# W1 实施计划：检索方案换成 wemm 统一嵌入

> **命名**：S8 已被「音频与视频」占用（`2026-09-29-s6-implementation-plan.md:15`），
> 而这一段不是迁移切片、是后端完成后的能力替换，所以另起 `W` 系列，不动 S 的序号。
>
> 前置：S0–S7 全部交付。**本段只做后端检索层，不碰前端。**

---

## 0. 依据与一个必须先说清的前提

**用户 2026-10-03 的裁决**：已在 1.0 上验证 wemm 的检索效果优于原方案，2.0 改用 wemm。

### 0.1 但 1.0 的 wemm 是**半接线**——那个结论有前提

1.0 的 `backend/embeddings/`（未提交改动 + 两个未跟踪新文件）里：

| 侧 | 状态 |
|---|---|
| **查询侧** `router.py` | ✅ 已支持 wemm（两槽共用同一实例 + 同一 query 只编码一次） |
| **写入侧** | ❌ **只有 `field_desc` 一条通道跟随方案**（`pipeline.py:109-131`） |

三处写入路径**没有**跟随方案，而且其中两处会**标错模型名**：

- `pipeline._text_embed`（`pipeline.py:64-79`）：仍算 bge/8101，却 `return vec, scheme.TEXT_MODEL`
  ——方案为 wemm 时会写出「**bge 算的 1024 维，标着 WeMM 的名字**」；
- `ClipAdapter`（`model_clients.py:1520`）硬编码 `chinese-clip-ViT-L-14` → 新导入的图片仍写 768 维；
- `scope_finalize.ensure_chinese_clip_asset_vectors`（`scope_finalize.py:25-68`）硬编码
  `ChineseClipVisualEmbedder` 却 import `scheme.IMAGE_MODEL` → 同样标错。

**所以「wemm 优于多路」成立于「先用重建脚本把库里向量重嵌过」这个前提**，
不是导入链路自然产出的向量。**2.0 要搬就得把写入侧一并接上**，否则原样重演
`scheme.py` 开头那段事故注释讲的事：多套模型并存、检索时好时坏**且不报错**。

### 0.2 「多路检索」与「embedding 方案」是两个正交的开关

1.0 里：

```
embedding 方案  SENTRIX_EMBEDDING_SCHEME = legacy | wemm_visual | wemm   （默认 legacy）
多路检索        config.multi_retriever   = true     （configs/retrieval/defaults.json）
```

`multi_retriever` 开着时走 **metadata / entity / lexical / visual / text 五路**加权 RRF。
**wemm 只改其中 `visual` 与 `text` 两路用哪个 embedder**——1.0 开着 wemm 时另外三路照跑。

**用户验证的那个组合，就是「五路都在、其中两路换成 wemm」。**

---

## 1. 裁决

### 用户 2026-10-03 定的两条

| # | 问题 | 裁决 |
|---|---|---|
| ① | 「不再用老方案」的范围 | **只换 embedding**（chinese_clip + bge → wemm），**保留 lexical / metadata / entity 三路** |
| ② | 要不要保留方案开关 | **钉死 wemm，不加 env 开关** |

②的直接好处：2.0 既有的那条不变量——`tests/unit/test_scheme.py` 的
`test_settings_expose_no_embedder_switch`（settings 不得暴露 embedder 开关）——**原样保住**。
它正是为防当年那种「两个槽各切各的」事故而立的，只用 wemm 就不需要开关。

### 我按 1.0 的事实定的三条（有依据，不另问）

| # | 决定 | 依据 |
|---|---|---|
| ③ | **保留三个 space**（`visual` / `semantic` / `episodic`），不合并成一个 | 1.0 `consistency.SPACE_ROLE` 在 wemm 下**仍分三个**（visual→image_model，episodic/semantic→text_model）。而且 2.0 的 `visual` 空间里**还住着人脸向量**（`buffalo_l` 512d / `source_type=face_instance`，实测 117 行），合并会把它们卷进文本检索 |
| ④ | **维度钉 2048，不做 MRL 截断** | 1.0 的 `SENTRIX_WEMM_TRUNCATE_DIM` **默认不截断**（只有 ≠2048 才带该字段），用户验证的就是 2048 |
| ⑤ | **熔断照搬**：连续 5 次失败熔断 30s，失败返回 `[]` 不抛错 | 1.0 `wemm_sidecar.py:110-112,72-73`；2.0 的 `BgeM3TextEmbedder` 已有同样机制，照它的形状写 |

---

## 2. 2.0 的有利条件：application 层几乎不用动

1.0 的写入侧有三处**绕过方案直连具体类**（§0.1），2.0 **没有这个问题**：
所有检索通道与写入用例都只依赖 domain 端口
（`VisualEmbedder` / `TextEmbedder`，`domain/retrieval/ports.py:207-247`）。

所以 W1 要改的其实是**「谁满足这两个端口」**：

```
现在：ChineseClipVisualEmbedder(768d, 进程内 GPU)  → VisualEmbedder
      BgeM3TextEmbedder(1024d, HTTP 8101, 熔断)   → TextEmbedder

之后：WeMMSidecarEmbedder(2048d, HTTP 8102, 熔断)  → 同时满足两个端口（同一个实例）
```

`retrieve_visual.py` / `retrieve_text_ann.py` / `index_visual.py` / `index_observation_text.py`
**预期一行不改**——它们从端口拿 `dimension` 与 `model_id`。**这是本段最大的风险点，
也是第一个要验证的假设**（见 §4 的 T401）。

---

## 3. 任务表

| # | 内容 |
|---|---|
| **T401** | **依赖核查**：把「application 层是否真的一行不改」验证掉。逐文件确认 `retrieve_visual` / `retrieve_text_ann` / `index_visual` / `index_observation_text` 里没有硬编码的 768 / 1024 / `chinese-clip` / `bge`；把结果写进施工记录。**核出硬编码就先修硬编码再往下走。** |
| **T402** | `domain/retrieval/scheme.py` 换常量：`WEMM_MODEL = "tencent/WeMM-Embedding-2B"`、`WEMM_DIMENSION = 2048`；三个 space 常量保留；`IMAGE_*` / `TEXT_*` 下线 |
| **T403** | `infrastructure/models/wemm.py` 新增 `WeMMSidecarEmbedder`：`/health` `/embed_text` `/embed_image`、60s 超时、熔断、`model_id`/`dimension` property、失败返回 `[]`。`config.py` 加 `wemm_url`（默认 `http://127.0.0.1:8102`） |
| **T404** | 容器装配：`visual_embedder` / `text_embedder` 两个字段指向**同一个 wemm 实例**（1.0 `router.py:57-63` 的理由：两个实例会让显存翻倍且状态可能不一致） |
| **T405** | 重嵌路径：`scripts/backfill_text_vectors.py` 已是幂等且从 scheme 取模型 → 换常量后自动变成「用 wemm 重嵌」。**视觉侧缺一个对应的**（53 张图），补 `scripts/backfill_visual_vectors.py`，照前者的体例 |
| **T406** | 测试改造 + 收口：`test_scheme.py`（冻结常量那条要改）、`test_chinese_clip.py` / `test_bge_text.py`（换成 wemm 适配器测试）、`test_vector_repository` / `test_qdrant_index`（若依赖 model 名/维度）、检索集成与契约；`behaviour-baseline.md` 同步 |

**删除待定**：`infrastructure/models/chinese_clip.py`、`bge_text.py`——
T401 的核查里一并确认没有别的消费方（人脸走 `buffalo_l`，与这两个无关）。

---

## 4. 不做 / 明确排除

| 项 | 为什么不 |
|---|---|
| 多路检索的通道集合 | 裁决①：五路保留，权重表 `DEFAULT_CHANNEL_WEIGHTS` 与加权 RRF 不动 |
| `SENTRIX_EMBEDDING_SCHEME` 式的开关 | 裁决② |
| MRL 截断 | 裁决④ |
| 视频嵌入 | WeMM 支持视频，但 2.0 的 `Observation.transcript` 属 **S8**（`index_observation_text.py:33` 明说）。本段不引入视频向量 |
| 1.0 的一次性保真度脚本（`fidelity*.py` / `residual.py` / `probe_text.py` / `verify_rebuild.py`） | 那些是 1.0 验证「重建拼法会不会失真」的临时工具，2.0 不必照搬。**但「重建拼法必须与当初嵌入时逐字一致」这条纪律要留**——2.0 的 `compose_text` 是同一个函数，天然满足 |
| 1.0 的 `clip_text.py` / `clip_visual.py`（open_clip ViT-B-32） | 1.0 自己的遗留，2.0 从来没有这套 |

---

## 5. 验收

- `make ci` 静态门禁全绿（5 契约 + 两条可达性 + capability-map + module-map + openapi）
- **定点变异**：每条新断言都要能被杀掉（尤其：模型名/维度取错、熔断不生效、
  两个槽用了两个实例、失败不返回空而是抛错）
- **真实数据实跑**（153）：换方案 → 跑重嵌 → 检索一次，确认
  `memory_vectors` 里出现 2048 维的 wemm 行、Qdrant 建出对应的 collection、
  检索**不是空的**（这是最容易出的错：查询向量与库里向量不同源时是**静默返回空**）
- **反面验证**：故意不重嵌就检索，确认**能看出不对**——要么空、要么有明确的
  degraded/告警，**不能是「看起来正常但结果变差」**。这是 `consistency.py` 存在的
  全部理由（1.0 写了却**没接进运行时**，只在 CLI 里）

---

## 6. 施工记录

### T402–T404 实现（2026-10-03）

| 文件 | 改动 |
|---|---|
| `domain/retrieval/scheme.py` | `WEMM_MODEL = "tencent/WeMM-Embedding-2B"` / `WEMM_DIMENSION = 2048`；`IMAGE_MODEL`/`TEXT_MODEL` 都指向它，`VISUAL_DIMENSION`/`TEXT_DIMENSION` 都是 2048；三个 space 名不变 |
| `infrastructure/models/wemm.py` | 新增 `WeMMSidecarEmbedder`，一个类同时满足 `VisualEmbedder` 与 `TextEmbedder` |
| `config.py` | 加 `wemm_url = "http://127.0.0.1:8102"` |
| `bootstrap/container.py` | 两个槽指向**同一个实例** |

**保留 `IMAGE_MODEL` / `TEXT_MODEL` 两个名字**（都指向同一个字符串），不是偷懒：它们标注的是
两个**不同的 space**（`visual` 对阵 `semantic`/`episodic`），这个区分在 wemm 下仍成立；
改名要让 20 多处按槽位引用的消费点全改一遍，换不来任何语义上的准确。
**这正兑现了 T401 核查出的那条性质——application 层一行未改。**

### 门禁当场抓出两个孤儿模块

改完 `make arch` 报 `reachability: 2 个问题`：

```
sentrix.infrastructure.models.bge_text: 没有任何生产调用者
sentrix.infrastructure.models.chinese_clip: 没有任何生产调用者
```

新方案一接上，两个旧适配器立刻变成孤儿。按仓库的反向规则孤儿要么接线要么删——
W1 之后不会有任何东西接它们（用户裁决：只用 wemm），所以删掉四个文件
（两个适配器 + 各自测试）。它们承载的知识没丢：熔断、降级、`available`/`load_error`
分工那几条理由，`wemm.py` 的注释逐条接过来了。删完 `reachability: 0`。

**同一轮里文档防腐链也抓了一次**：`capability-map.jsonl` 有一条
`chinese-clip 视觉嵌入: verification 文件不存在 -> test_chinese_clip.py`。

### T405 重嵌（真实数据实测）

文本侧不用新写：`scripts/backfill_text_vectors.py` **是从 `scheme` 取模型的**，
换常量后自动变成「用 wemm 重嵌」。视觉侧补了 `scripts/backfill_visual_vectors.py`。

```
文本：观察 53 条，写 92 行，覆盖率 46/46（7 条文本为空，跳过）
视觉：图片 53 张，覆盖率 53/53
Qdrant：新建 sentrix_home_visual_2048_tencent_wemm_embedding_2
```

`memory_vectors` 现状（旧模型行按设计保留，切回旧方案仍可用）：

```
visual     tencent/WeMM-Embedding-2B   2048  asset           53   ← W1
visual     chinese-clip-ViT-L-14        768  asset           53   （旧，不动）
visual     buffalo_l                    512  face_instance  117   （人脸，不受影响）
episodic   BAAI/bge-m3                 1024  observation     46   （旧，不动）
semantic   BAAI/bge-m3                 1024  observation     46   （旧，不动）
```

### 补做：把方案漂移接进查询侧（用户 2026-10-03 裁决）

上面那节「反面验证未达标」之后，用户裁决**补上**。做法与依据：

**判据只有一条，不查资产数**：某个 space 里**有**向量，却**没有一条属于当前方案**。
两个数都从现成的 `VectorRepository.count` 拿——`model_name=None` 是不按模型过滤，
所以两个数一比就知道是漂移。

```
count(space=X, model_name=<当前方案>)  == 0
count(space=X)                          >  0     ← 有别人的向量
```

**为什么不用「本该有多少」**（查资产数/观察数再比对）：那要多两次查询，还得区分
「这个 scope 本来就没资产」与「有资产但没嵌」；而上面这条判据在两个数都是 0 时
**自然不报**，正好把「刚建、还没导入过东西的 scope」放过去——不需要额外的特例。

| 文件 | 改动 |
|---|---|
| `application/retrieval/scheme_drift.py` | **新增**：`scheme_drift_reason(vectors, *, space, model_name) -> str`，没问题时返回空串 |
| `application/retrieval/retrieve_visual.py` | 漂移时**覆盖** `degraded` / `reason`（它比索引健康度更根本：索引再健康，库里没有当前方案的向量，召回就是错的） |
| `application/retrieval/retrieve_text_ann.py` | 两个 space 各查一次，任一漂移即报。**这条通道原先写着「没有降级档」**——那句话仍然对（它不依赖索引），但不再等于「它永远不会降级」，注释已改 |

**没有新增端口**：`VectorRepository.count` 本来就接受可选的 `model_name`，
这个接口当初就是为「同一个口径去和 Qdrant 的 collection 计数比对」而设计的，
这里只是把同一个口径用在另一个方向。

**测试**：`test_retrieve_visual.py` +2、`test_retrieve_text_ann.py` +3。
两边的假仓储都加了可控的「外来向量」入口（**默认空，既有用例语义不变**）。

**夹具刻意造了两种状态**，因为只造一种的话两种坏实现都能过：

- **空 space**（两个数都是 0）→ 不许报。防的是「恒定报警」；
- **漂移**（不带过滤数得到、带过滤数不到）→ 必须报。防的是「永远不报」。

**定点变异 5/5 被杀**：视觉不报 · 去掉 `stored == 0` 的判据 · 文本不报 ·
文本只查 semantic 漏掉 episodic · 判据写反。

**一处值得记的写法问题**：空 store 那条我一开始断言的是 `result.degraded is False`，
红了——因为那个夹具没配索引，`_recall` 先置了 `no_index_configured`。
**`degraded` 是多个来源共用的一个字段，拿它做断言等于把别的原因也算进来。**
改成断言「**报的不是漂移**」（`"scheme_out_of_sync" not in reason`），那才是两个夹具
真正的差别所在。

### 真实数据复验（153）——从「看不出」到「说清楚」

同一个反面场景重跑（删掉 145 行当前方案的向量，检索，再恢复）：

```
修之前
  命中 2 | degraded False | reason '' | auth 0
  visual_ann ok 0 · text_ann ok 0 · lexical ok 2

修之后
  命中 2 | degraded True | auth 0
  visual_ann degraded 0 | scheme_out_of_sync:visual 里有 170 条向量，没有一条属于
                          当前方案 tencent/WeMM-Embedding-2B；先重建再检索（scripts/…）
  text_ann   degraded 0 | scheme_out_of_sync:semantic 里有 46 条向量，…同上
  lexical    ok         2

恢复后
  命中 3 | degraded False | auth 53
```

**通道状态从 `ok` 变成 `degraded`，响应级 `degraded` 置起来，消息里带着补救动作**
——不是只说「不匹配」，是告诉你跑哪个脚本。这正是 1.0 `consistency.py` 的
`ConsistencyError` 想做而没接上的事。

**一处已知的不精确**（不修，记在这里）：`visual` 那句报的是 **170** 条，而其中 117 条是
`buffalo_l` 的人脸向量——它们与嵌入方案无关，永远不会变成 wemm。数字因此比「53 张图的
旧向量」大。判据本身不受影响（`authoritative == 0 && stored > 0` 仍然只在真漂移时成立），
补救动作也不受影响；要让数字精确得给 `count` 加 `source_type` 过滤，那是接口变更，
与这条判据的价值不成比例。

### 顺带修掉一条前端门禁的漏洞（F7 留下的，W1 期间发现）

**W1 的测试 agent 报告 `make web-check` 是红的**，报在 F7 加的
`apps/web/src/source-hygiene.test.ts`：

```
src/source-hygiene.test.ts(16,53): error TS2591: Cannot find name 'node:fs'
src/source-hygiene.test.ts(17,22): error TS2591: Cannot find name 'node:path'
src/source-hygiene.test.ts(22,18): error TS2591: Cannot find name 'process'
```

**但 F7 交付时 `make web-check` 是绿的。** 追下去发现不是「当时绿、后来变红」，
是**那条门禁当时就没检查这个文件**：

`make web-check` 跑的是 `npx tsc -b`（不带 `--force`），而 **`tsc -b` 的增量检查只在
「它已经知道的文件被改动」时触发——新加一个匹配 `include` 的源文件不会让它重建**，
那个文件于是根本不被类型检查。定点复现（2026-10-03）：

```
1. 干净构建（造出 tsbuildinfo）                  exit=0
2. 新加一个用 node:fs 的 .ts 文件（应报 TS2591）
3. npx tsc -b（不带 --force）                    exit=0   ← 没抓到
   同一状态加 --force                            exit=2   ← 抓到了
```

**这不是理论风险，它已经发生过一次**：F7 那个 NUL 门禁文件就是这么带着三个类型错误
进仓库的，而当时所有的门禁都是绿的。

**修法（两处）**：

1. `Makefile` 的 `web-check` 把那一步改成 **`npx tsc -b --force`**，并在注释里写清为什么
   ——`--force` 在这里不是保险丝，是**必须的**：不加就会漏掉所有新增的前端源文件。
2. `source-hygiene.test.ts` 加 `/// <reference types="node" />`。

**第 2 条我一开始的理由是错的，一并记下来**：我以为文件级 reference 的作用域只有那一个
文件，实测不是——**它把 node 类型加进整个 program**（加上之后，同工程另一个新文件的
`node:fs` 也不再报错）。所以它与「往 `tsconfig.app.json` 的 `types` 里加 `"node"`」等效。
留着它只是因为意图离使用点最近，**不是因为它作用域小**。注释已按实测改写。

### 反面验证：**未达标**（2026-10-03）

§5 写的验收是「故意不重嵌就检索，确认**能看出不对**——要么空、要么有明确的
degraded/告警，**不能是「看起来正常但结果变差」**」。实测**是后一种**。

做法：停服 → 删掉全部 `tencent/WeMM-Embedding-2B` 的向量行（145 行）→ 起服 →
同一个查询。结果：

```
基线（有 wemm 向量）
  命中 3 | backend qdrant | degraded False | auth 53
  metadata 0 · entity 0 · lexical 2 · visual_ann 12 · text_ann 12

删掉 wemm 向量后
  命中 2 | backend qdrant | degraded False | reason '' | auth 0
  metadata 0 · entity 0 · lexical 2 · visual_ann 0 · text_ann 0
```

**两条向量通道都报 `status: ok, candidate_count: 0`**——不是 `degraded`，没有 `reason`，
响应里 `degraded` 是 `False`、`degraded_reason` 是空串。检索**返回 2 条而不是 3 条**，
少掉的正是视觉命中，而**用户看不出任何差别**。

唯一还留在响应里的线索是 `authoritative_vectors: 0`——一个没有任何解释的裸数字。

**这正是要避免的那一类，而且它正是 1.0 `scheme.py` 开头那段事故注释描述的事**
（「检索时好时坏，而且**没有任何一处会报错**」）。1.0 为此写了
`backend/embeddings/consistency.py` 的 `assert_ready`（不匹配就抛 `ConsistencyError`，
注释明说「刻意做成异常而不是日志——检索静默返回空比报错难查得多」），
**但它全仓没有任何运行时调用点**，只有两个重建脚本在结尾打印一行提示。
所以 1.0 是「写了工具、没接上」；**2.0 现在连工具都还没有**。

**为什么没有当场修**：这不是「补个 if」。判定「0 条向量」是不是错的，需要区分
「这个 scope 本来就没有资产」与「有资产但当前方案一条向量都没有」——后者才是配置错。
这个判据要落在哪一层（通道内 / 编排层 / 查询侧的统一一致性检查）、
以及按 scope 逐次查资产数的代价，都是设计选择，不是 W1 的范围。
**但它挡着 W1 的验收，所以如实记在这里，不当作已通过。**

可选的方向（留待决策，都不在本次改动里）：
1. 让两条向量通道在「权威存储里当前方案的向量数为 0」时把自己的 trace 标成
   `degraded` 并给出 reason；
2. 把 1.0 `consistency.py` 那个 `assert_ready` 的思路接进查询侧（1.0 没做的那一步）；
3. 什么都不做，但至少让 `authoritative_vectors` 这个字段在响应里有解释。

**恢复**：重跑两个回填脚本，覆盖率回到 46/46 与 53/53，同一查询恢复成命中 3 条。

### T405 撞出来的两处发现（**都不在 W1 范围内，报告不修**）

**① 回填必须先停 API。** Qdrant 是嵌入式本地实例，同一份存储目录同时只能一个进程打开。
API 在跑时回填，权威存储照写，但每张图的镜像都以
`Storage folder ... already accessed by another instance` 失败——**日志里看得见**，
但**重跑补不回来**：

```python
if self._vectors.has_vector(...):
    # 幂等：重复导入同一张图不该再跑一次推理。
    return VisualIndexOutcome(indexed=True, mirrored=True, reason="already_indexed")
```

**② 那行 `mirrored=True` 是假的**——这条早返回根本没做镜像，却报告镜像成功。
连带后果是**派生索引漂移后没有修复路径**：权威存储里有行就早返回，重跑也补不上镜像。
本次补救靠「停服 → 删掉那 53 行 → 重跑」。

**为什么不在 W1 里修**：它与「换嵌入方案」无关，是索引状态上报的问题；而且真要修得先定清楚
「`already_indexed` 时该报什么」——诚实的答案需要一次镜像存活检查，那是独立的设计问题。
`outcome.mirrored` 目前**在生产代码里没有任何读者**（只有测试桩设置它），所以这是潜伏的谎，
不是正在发生的故障。**已写进 `backfill_visual_vectors.py` 的模块注释**，免得下一次有人
照着踩。修与不修留给后续切片定。

### 真实数据验收（153，album1）

```
GET /api/v1/search?q=旋转木马&scope_id=album1&limit=3
  backend: qdrant     degraded: False     authoritative_vectors: 53

channel_trace —— 五路全 ok：
  metadata   0 条
  entity     0 条
  lexical    2 条      ← 不依赖向量的那路照常工作，正是裁决①要的
  visual_ann 12 条  backend=qdrant        24.6ms
  text_ann   12 条  backend=sqlite_exact  52.2ms
```

另外两个查询（「山顶合影」「水上游乐设施」）各命中 3 条。**wemm 的视觉与文本两路都通，
词法那路保留并贡献了候选，没有降级。**

### T401 依赖核查（2026-10-02 动工前）

**结论：§2 那个假设成立，application 层确实一行不用改。** 核查逐条：

| 查什么 | 结果 |
|---|---|
| `application/` 里有没有硬编码 `768` / `1024` | **零命中**（`grep -rn "\b768\b\|\b1024\b" apps/api/src/sentrix/application/`） |
| 全 `src/` 里硬编码模型名的地方 | 只有三处**该改的**：`scheme.py`（常量本身）、两个旧适配器（要删）、`container.py`（装配 import）。其余是注释与 docstring |
| `ChineseClipVisualEmbedder` / `BgeM3TextEmbedder` 的消费方 | **只有 `container.py:240,243` 与各自的测试**。没有第二个生产消费方，删得干净 |

**所以 2.0 与 1.0 的关键差别坐实了**：1.0 有三处写入路径绕过方案直连具体类
（`pipeline._text_embed` / `ClipAdapter` / `scope_finalize`），2.0 一处都没有——
所有通道与写入用例都只认 domain 端口。

**但核查带出一条计划里没写的东西：融合权重有实测出处，而那个出处是 chinese-clip 时代的。**

`domain/retrieval/fusion.py:10` 的原文：

> 权重不是拍脑袋：**R7 消融里 chinese-clip 视觉 r10=0.887**、词法 0.373、文本 0.158，
> 平权 RRF 会让弱通道把强通道稀释掉，混合反而低于纯视觉。**改任何一个数字前先改基线。**

也就是说 `visual_ann: 2.5 / lexical: 1.0 / text_ann: 0.5` 这组数**是针对 chinese-clip
的视觉通道消融出来的**。换成 wemm 后视觉通道的强度变了，词法通道的相对位置（0.373 那个数）
也就跟着失效了。

**处置：照搬，并登记这一点。** 依据是——**1.0 自己也没重跑消融**：
`backend/retrieval/fusion.py:33-42` 在开了 wemm 之后权重仍是同一组
（`visual_ann: 2.5 / lexical: 1.0 / text_ann: 0.5 / metadata: 1.0 / entity: 1.0 / adjacency: 0.5`，
连那句 "R7 ablation: Chinese-CLIP visual r10=0.887" 的注释都原样留着）。
所以「照搬权重」= 1.0 的现状 = **用户验证过的那个配置**。

**但这是一条继承来的未验证假设，不是被验证过的结论。** 真要让权重与 wemm 匹配，
得重跑一次消融——那是独立的一件事，不在 W1 范围内。登记在 `behaviour-baseline.md`
的同一段旁边，免得后来的人以为这组数在 wemm 下重新验过。


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

（施工时逐条填）

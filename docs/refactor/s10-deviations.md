# S10 违背 / 偏离审计（PhotoBench 前端重构）

审计对象：`docs/refactor/2026-10-05-s10-implementation-plan.md`。
基线：设计文档 `docs/architecture/2026-09-21-sentrix-home-web-2-implementation-design.md:515`
对 S10 的定义，与 1.0 的 `services/photobench/`（**只读，唯一权威**）。

**这份文档现在的形态是「施工中的偏离登记」**（S3/S4/S5 那三份是收尾审计）。
S10b（前端重写）动工后若有新偏离，追加到 §5。

---

## 0. 一句话摘要

S10 按用户 2026-10-05 的裁决**拆成两段**：S10a（补齐后端端点）→ S10b（前端重写）。

S10a 施工中**范围扩到了 `apps/api`**：计划假定那些端点是纯新增，核查发现其中两条
（`keyframe-analysis` / `memory-effectiveness`）要用的数据 2.0 根本没暴露。
根因是**同一个缺陷形态出现了三次**——1.0 的读侧是 `SELECT *`，2.0 把它换成窄投影时
丢掉的列里，有几列**有真实的读者**（见 §2.2）。

另外**修正了一处 S8 的错误判断**：`raw` 被当成「没有读侧」去掉了，实际有一个读者（§2.1）。

端点数是 **8 条不是 7 条**：计划漏了 `export-sft`（§4.3）。

**没有一条偏离是凭喜好的**——每条都指到 1.0 的具体行，或指到本仓库的一条既有契约。

---

## 1. 与计划 / 设计文档的偏离

### 1.1 S10a 的范围扩到 `apps/api`（用户 2026-10-05 裁决）

计划 §1.5 把 S10a 定义成「补齐 7 个 PhotoBench 后端端点」。施工前核查发现，
那些端点里有 2 条依赖 **2.0 主 API 没有暴露、甚至没有存储**的数据。

| # | 缺什么 | 1.0 从哪读 | 2.0 的状况 | 处置 |
|---|---|---|---|---|
| 1 | 关键帧血缘：`derived_kind` / `parent_asset_id` / `source_timestamp_sec` | `GET /api/assets`（`SELECT *`，`backend/db.py:1505`） | `AssetSummary` 是窄投影，三个字段都没了 | 补进 `AssetSummary` |
| 2 | `nb_frames`（原片总帧数） | `metadata_json.video_metadata.raw.streams[].nb_frames`（`:5721`） | **没存**——S8 主动去掉了整包 `raw` | 补回，只存这一个整数 |
| 3 | `video_processing_seconds` | `metadata_json.video_processing_seconds`（`:5729`） | **全仓无** | 补回，视频流水线加计时 |
| 4 | 记忆空间的 `created_at` | `GET /api/memory-spaces`（`SELECT *`，`backend/db.py:1410`） | spaces 投影只挑四列 | 补回（§4.4） |

**第 4 条是施工中才发现的**（第 1–3 条在计划阶段就报给了用户并获裁决）。
它和第 1 条是同一个形态，处置口径一致，所以**没有再单独问一次**——见 §4.4 的说明。

**为什么值得扩范围，而不是「PhotoBench 自己绕开」。** 绕开那条路核过：

- 逐视频打 `/videos/{id}/scenes` → `/video-scenes/{id}` 只能拼出血缘，**第 2、3、4 条
  2.0 根本没存/没暴露，绕不过去**；而且每次分析要多 ~40 次 HTTP。
- 补齐的四处**都是恢复 1.0 有过的东西**，不是给 2.0 加新能力。

**代价**：S10a 从「PhotoBench 后端」变成「PhotoBench 后端 + `apps/api` 四处小补」。
**好处**：PhotoBench 的分析函数与 1.0 逐行同形，日后并排比对不必先解释差异。

### 1.2 设计文档 `:515` 的行数是陈旧的

文档写「3,646 行 Vue 单文件」，**实测 3,797 行**（脚本 `:1-2836` + 模板 `:2838-3797`，
无 `<style>` 块）。与 S9 的 7,751→7,997 同一类偏差，不影响范围。

---

## 2. 修 2.0 的缺陷（不是偏离，是**不把缺陷留在 2.0**）

### 2.1 S8 判定 `raw`「没有读侧」——**这个判断是错的**

`apps/api/src/sentrix/domain/media/models.py:110` 原话：

> 只有 `raw` 不搬——那是整包 ffprobe JSON，1.0 写进 `metadata_json.video_metadata`
> 之后**没有任何读侧用过**

实测 1.0 全仓（排除 `.bak-*` 与 `__pycache__`），两个字段**各恰好有一个读者**，
都在 `services/photobench/backend/benchmark_orchestrator.py` 的 `get_keyframe_analysis`：

| 字段 | 1.0 的唯一读点 |
|---|---|
| `video_metadata.raw.streams[].nb_frames` | `:5721` |
| `metadata_json.video_processing_seconds` | `:5729` |

**S8 查的是主产品读侧，漏了 PhotoBench——它在另一个服务里。** 那句判断对主产品成立、
对整个 1.0 不成立。

**改法**：`VideoMetadata` 补 `frame_count`（**只补这一个整数，不存整包 raw**——
「别为每个视频塞一份完整流/标签树」的顾虑仍然成立），视频流水线补
`video_processing_seconds`。注释里那句被证伪的话一并改对。

**一处附带确认**：2.0 的 `video_processing_seconds` **探测失败也写**。1.0 同样如此——
成功路径写 `elapsed`（`processor.py:205`），失败路径写
`round(time.perf_counter() - started, 3)`（`:223`，那条带 `retryable` 标记，是 `except`
分支）。所以这不是偏离。

### 2.2 窄投影丢掉「有读者的列」——同一个形态出现三次

1.0 的 `list_assets` / `list_memory_spaces` 都是 `SELECT *`（`db.py:1505` / `:1410`），
2.0 把它们换成显式列投影。**投影本身是对的**（少回几十列），问题在于**没人核对
「被丢掉的那些列里有没有读者」**：

| # | 丢掉的东西 | 读者 | 少了会怎样 |
|---|---|---|---|
| 1 | `AssetSummary` 的 `derived_kind` / `parent_asset_id` / `source_timestamp_sec` | PhotoBench 关键帧分析 | 认不出派生帧，`retrieval` 与 `temporal` 两块归零/为空 |
| 2 | `VideoPayload` 的 `nb_frames`（现 `frame_count`）与 `processing_seconds` | 同上 | `processing` 整块空 |
| 3 | spaces 的 `created_at` | PhotoBench 复用基座 | 三处退化成**空串比较**，不报错、结论默默地不对 |

第 1 条顺带修好一条 S8 登记过的缺陷：`s8-deviations.md` §2 第 3 条「1.0 前端关键帧
计数恒 0」——主产品前端要按关键帧计数，就必须能认出关键帧，依据正是这三列。

**这三处的共同点：少一个字段不会抛异常，只会让下游算出一个看起来正常的错数。**
这是本仓库反复出现的失败形态（W1 的方案漂移、S9b 的门禁盲区都是同一类）。

---

## 3. 1.0 的历史包袱，**不移植**

| # | 包袱 | 为什么不 |
|---|---|---|
| 1 | **arbiter 状态面板**：模板 `:2845-2856` 的 8 格「实时调度状态（VLMArbiter）」，`:2711-2723` 每 3 秒轮询 `GET /api/arbiter-status` | **该端点不存在**（1.0/2.0 主 API 都没有这个能力）。搬一块永远拿不到数据、每 3 秒打一次 404 的面板过去，不如不搬。S10b 删掉 |
| 2 | `video_keyframe_webp` 这第二个 `derived_kind` 取值 | 随 `s8-deviations.md` §3 第 1 条一起去掉。读侧保留同时认两个的形状，实际只命中一个 |
| 3 | `worldmm_*` 那套挑选口径（`worldmm_selection_reason` / `worldmm_score`） | 随 `s8-deviations.md` §3 第 2 条一起去掉。读侧沿用 1.0 的 `or` 回退链（缺了就是「未记录」/ `None`），不报错 |

---

## 4. 施工中的新增偏离

### 4.1 视频元数据的来路：资产清单 → 逐视频端点（形状偏离，非口径偏离）

1.0 一次 `GET /api/assets` 就把视频时长/帧数/处理耗时全拿到了（`SELECT *` 里的
`metadata_json`）。2.0 即使补上血缘列仍是投影，所以 `RunReports` 会**逐视频再取一次**
`GET /videos/{id}`。

**代价**：每次分析多 N 次 HTTP（N = scope 里的视频数）。benchmark 相册实测最大
`album3-max-video10` 有 10 个视频，`album3-kling` 有 3 个。取失败就跳过那个视频，
与 1.0 遇到字段缺失时的行为一致（它有一整套 `isinstance(...)` 判断）。

**为什么不索性塞进 `AssetSummary`**：那会让通用的资产列表接口为一个视频专属面板
背三个视频专属字段，还要在 `list_by_scope` 里为每个视频补一次 probe——把 N+1 从
应用层挪到仓储层，问题没消失、只是更难看见。

### 4.2 资产清单的 `limit`：1.0 的 2000/5000 在 2.0 只能是 1000

1.0 关键帧分析写 `limit=2000`（`:5611`）、记忆有效性写 `limit=5000`（`:5648`）。
2.0 的 `GET /api/v1/assets` 在路由里做 `min(limit, 1000)`（`api/routes/media.py:301`）
——给更大的数**不报错，只静默截断**。

benchmark 相册实测最大约 400 条，1000 够用。**已知代价**：相册超过 1000 条时这两个
面板会悄悄少算。「静默截断」这个形状本身也是 `infrastructure/sentrix/client.py`
已经登记过的差异。

### 4.3 补了 1.0 有、前端没用的 `export-sft`

计划列的 7 条里没有它。实现时一并补了：它是 1.0 的真实能力（SFT 训练数据导出），
少了它「同一个界面换后端」时那条能力会静默消失。_前端确实零命中_（`App.vue` grep 空）。

### 4.4 spaces 的 `created_at`：同一形态的第四处，**没有再单独问**

`GET /api/v1/memory-spaces` 原先只回 `{id, name, kind, status}`。1.0 回的是
`SELECT *`，`created_at` 在里面，而它有**三个真实读者**（全在 PhotoBench）：

1. `GET /api/memory-spaces` 的「新创建的在前」排序；
2. `_build_reuse_bases` 挑最新 scope 做复用基座；
3. `_qa_scope_id_for_album` 的次级排序键。

少了它，这三处一起退化成 `str(None or "")` 之间的比较——**不报错**。

**为什么没有再问用户一次**：用户 2026-10-05 已就同一形态裁决过两次（§1.1 的第 1–3 条），
处置口径一致，且这是一列的暴露、不是新端点。按「计划已定时直接做完再报」执行，
在这里登记备查。

### 4.5 `sentrix_url` 查询参数的语义收窄

1.0 的 `/api/memory-spaces` 收 `sentrix_url` 参数，可以指向**任意** Sentrix
（1.0 的前缀是 `/api`）。2.0 的前缀写死 `/api/v1`，所以把它指向 1.0 的 Sentrix
会失败。默认值仍是配置里的 `sentrix_url`（线上是 `192.168.0.153:8091`，实测回 404 →
`sentrix_unavailable`，不崩）。

**没有为它加「兼容两种前缀」的分支**：那是给一个迁移期的临时便利加永久复杂度。

### 4.6 starlette 的 `FileResponse` 与 1.0 手写 Range 的两处行为差

1.0 的 `_serve_file`（`:8020+`）自己实现了分段。2.0 用 starlette 1.6.0 的
`FileResponse`（原生支持 Range），但两处行为不同，已在路由 docstring 写明：

| 情形 | 1.0 | 2.0（starlette） |
|---|---|---|
| 格式错误的 Range（`bytes=abc`） | 416 + `Content-Range: bytes */<size>` | **400**，无 `Content-Range` |
| 越界的 Range | 416 + `Content-Range` | 416 + `Content-Range`（一致） |

另外 1.0 发的 `Cache-Control: no-cache` / `Vary` 两条头没有照搬。

### 4.7 几处照 1.0 而非照我的任务描述的地方（**我的描述错了**）

施工中核对出来的，记在这里免得后人以为实现写歪了：

| 处 | 我写的 | 1.0 实际 |
|---|---|---|
| `list_manifests` 的计数 | `len(m.get("faces", []))` | **`len(m.get("faces", []))` 没有 `or []`**——`"faces": null` 会抛 `TypeError`，而那个 `except` 只接 `(OSError, KeyError, JSONDecodeError)`，于是**整个列表端点回 400**。照 1.0 |
| `test-model` 的报错文案 | `no complete choices` | `model endpoint returned no **completion** choices` |
| 1.0 行号（`raw` / `processing_seconds`） | `:5718` / `:5726` | `:5721` / `:5729` |

### 4.8 PhotoBench 侧的其它适配（2.0 与 1.0 的接口事实）

| 项 | 1.0 | 2.0 |
|---|---|---|
| 前缀 | `/api` | `/api/v1`（`API_PREFIX` 单点定义在 `domain/sentrix/ports.py`） |
| assets 信封 | `{"assets": […]}` | `{"items": […]}` |
| 资产路由 | — | 用 `{asset_id:path}`：否则 `/api/assets/a/b/file` 在 2.0 会变成 FastAPI 的 404 `{"detail": …}`，而 1.0 是 400 `invalid asset id` |
| `media_file` 穿越拦截 | 只比对 `file_name` 一段（`album_id="../../etc"` 能穿出去） | 三段都拦 |
| `reuse_bases` 的输入 | `repo.list_runs()`（全部） | `RunIndex.entries()`（全部）——**不能用 `ReadRuns.list_runs`**，它分页 20 条，会让第 21 条之后的基座整批消失 |
| `qa-dataset` 的 jsonl | 裸读 | 裸读（**不用** `AlbumSource.qa_rows`，它会叠 `expected_action`，形状就变了） |

---

## 5. 未决 / 待办

- ~~老视频的 `processing` 面板~~（§2.1 残留）：**已核实不适用**。2.0 的库里现有
  53 个资产、**全是图片、零视频**（`GET /api/v1/assets` 实测），不存在「S10a 之前导入
  的视频」这批数据。将来导入的视频会由新代码当场写上这两个数，不需要一次性重探脚本。
- **前端端口**：计划 §1.6 裁决②待核（建议 4275，`apps/web` 用 4274；后端 8772）。
- **主产品前端的 TS 类型**：`apps/web/src/shared/api/client.ts` 的 `AssetSummary`
  接口没有跟着加那三个血缘字段。字段是**增量**的，不影响 `tsc`；但主产品前端要用
  它们做关键帧计数（§2.2 第 1 条）时得补。
- **S10b 动工前要扫的**：`App.vue.bak-*` 那 6 个备份文件（可能含已从当前 `App.vue`
  移除的功能）。

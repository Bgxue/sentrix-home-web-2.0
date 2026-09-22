# 模块地图

**本文件由 `scripts/check_module_map.py` 校验**，脚本实际查两件事：

1. **文件集合双向一致**——源码里每个 `.py` 必须在下面有一行，文档里有但代码里不存在的
   条目会让 CI 失败。
2. **依赖边一致**——脚本用 `ast` 解析每个文件的包内 import，映射成文件路径后与「依赖谁」
   列比对；「被谁依赖」是同一次解析的转置，由脚本推导后比对。少写、多写、与转置不符
   都会失败。

两列只记 `sentrix` 包内的模块关系；stdlib 与第三方（fastapi / sqlite3 / pydantic 等）
不进这两列。

## 装配点按端口声明

上面两条管的是「谁不许 import 谁」；还有一条配套的不变量管「装配时必须按接口拿」：
**`Container` 的每个字段都按 domain 端口声明，不写 infrastructure 的具体类。**
写在类型上的端口才是可替换性的凭证——只写在 `build()` 里的替换能力，调用方看不见，
很容易顺手用上协议之外的方法，于是「可插拔」只剩一句口头承诺。

这条由 `apps/api/tests/unit/test_container_wiring.py` 在 `make ci` 里强制。
当前例外两条，理由同时写在 `container.py` 的字段旁与测试的例外表里：
`database`（共享连接工厂，不是端口的实现，且 `database_healthy()` 直接需要它）、
`spaces`（尚无 application / api 消费者，补端口属投机抽象；等记忆空间的用例落地时，
把 `MemorySpace` 一并迁进 domain 再补 `SpaceRepository`）。

列含义：**层** = domain / application / infrastructure / api / bootstrap ·
**依赖谁** 与 **被谁依赖** 写 `src/` 下的相对路径，空集写 `—`，多项用 `、` 分隔。

| 路径 | 职责 | 层 | 依赖谁 | 被谁依赖 |
|---|---|---|---|---|
| `sentrix/__init__.py` | 包版本号 | — | — | `sentrix/api/routes/health.py`、`sentrix/main.py` |
| `sentrix/api/__init__.py` | 包标记 | api | — | — |
| `sentrix/api/deps.py` | `get_container`：路由从 `app.state` 取装配好的容器，不自己构造 | api | `sentrix/bootstrap/container.py` | `sentrix/api/routes/media.py`、`sentrix/api/routes/search.py` |
| `sentrix/api/errors.py` | 领域错误基类与 `DomainError` → HTTP 的映射 | api | — | `sentrix/main.py` |
| `sentrix/api/routes/__init__.py` | 包标记 | api | — | `sentrix/main.py` |
| `sentrix/api/routes/health.py` | `GET /api/v1/health`，只做 HTTP 映射 | api | `sentrix/__init__.py` | `sentrix/main.py` |
| `sentrix/api/routes/media.py` | 媒体导入与交付端点（import / assets / file / preview），只做 HTTP 映射 | api | `sentrix/api/deps.py`、`sentrix/api/schemas/media.py`、`sentrix/application/media/import_assets.py`、`sentrix/application/retrieval/index_visual.py`、`sentrix/bootstrap/container.py`、`sentrix/domain/media/models.py` | `sentrix/api/routes/search.py`、`sentrix/main.py` |
| `sentrix/api/routes/search.py` | `GET /api/v1/search`：按内容词检索，只做 HTTP 映射 | api | `sentrix/api/deps.py`、`sentrix/api/routes/media.py`、`sentrix/api/schemas/search.py`、`sentrix/application/retrieval/retrieve_visual.py`、`sentrix/bootstrap/container.py` | `sentrix/main.py` |
| `sentrix/api/schemas/__init__.py` | 包标记 | api | — | — |
| `sentrix/api/schemas/media.py` | v1 媒体端点的响应模型（ImportResponse / AssetSummary / AssetListResponse） | api | — | `sentrix/api/routes/media.py`、`sentrix/api/schemas/search.py` |
| `sentrix/api/schemas/search.py` | v1 检索端点的响应模型（SearchHit / SearchResponse） | api | `sentrix/api/schemas/media.py` | `sentrix/api/routes/search.py` |
| `sentrix/application/__init__.py` | 包标记 | application | — | — |
| `sentrix/application/media/__init__.py` | 包标记 | application | — | — |
| `sentrix/application/media/import_assets.py` | 导入用例 `ImportAssets` / `ImportRequest` / `ImportResult`；按 scope 的 SHA-256 去重 | application | `sentrix/domain/media/models.py`、`sentrix/domain/media/ports.py`、`sentrix/domain/retrieval/ports.py` | `sentrix/api/routes/media.py` |
| `sentrix/application/retrieval/__init__.py` | 包标记 | application | — | — |
| `sentrix/application/retrieval/index_visual.py` | 导入即向量化：嵌入 → 写权威存储 → 镜像派生索引；空向量不写、重复导入不重嵌 | application | `sentrix/domain/media/models.py`、`sentrix/domain/retrieval/__init__.py`、`sentrix/domain/retrieval/ports.py`、`sentrix/domain/retrieval/scheme.py` | `sentrix/api/routes/media.py` |
| `sentrix/application/retrieval/retrieve_visual.py` | 视觉召回用例：内容词 → 视觉通道候选 → 加权 RRF；索引被占用/为空/落后时回退权威存储并置 `degraded` | application | `sentrix/domain/retrieval/__init__.py`、`sentrix/domain/retrieval/fusion.py`、`sentrix/domain/retrieval/models.py`、`sentrix/domain/retrieval/ports.py`、`sentrix/domain/retrieval/scheme.py` | `sentrix/api/routes/search.py` |
| `sentrix/bootstrap/__init__.py` | 包标记 | bootstrap | — | — |
| `sentrix/bootstrap/container.py` | 唯一依赖装配点；只有这里构造适配器 | bootstrap | `sentrix/config.py`、`sentrix/domain/media/ports.py`、`sentrix/domain/retrieval/ports.py`、`sentrix/infrastructure/media/image_io.py`、`sentrix/infrastructure/media/metadata.py`、`sentrix/infrastructure/media/storage.py`、`sentrix/infrastructure/models/chinese_clip.py`、`sentrix/infrastructure/persistence/database.py`、`sentrix/infrastructure/persistence/repositories/assets.py`、`sentrix/infrastructure/persistence/repositories/spaces.py`、`sentrix/infrastructure/persistence/repositories/vectors.py`、`sentrix/infrastructure/vector/qdrant_index.py` | `sentrix/api/deps.py`、`sentrix/api/routes/media.py`、`sentrix/api/routes/search.py`、`sentrix/main.py` |
| `sentrix/config.py` | 类型化配置；`data_dir` 是唯一可配根，其余路径派生 | — | — | `sentrix/bootstrap/container.py`、`sentrix/main.py` |
| `sentrix/domain/__init__.py` | 包标记 | domain | — | — |
| `sentrix/domain/assistant/__init__.py` | 包标记 | domain | — | — |
| `sentrix/domain/assistant/evidence_ledger.py` | 证据账本：需求状态机与显式证据绑定 | domain | — | `sentrix/domain/assistant/tools.py` |
| `sentrix/domain/assistant/models.py` | 助手域的通用模型：ChatMessage / ToolCall / ModelReply / ProgressEvent / TurnRecord | domain | — | `sentrix/domain/assistant/task_state.py`、`sentrix/domain/assistant/tool_policy.py` |
| `sentrix/domain/assistant/profile.py` | 回合预算常量（8 步 / 6 调用 / 180s / max_search_calls=1），来自行为基线 | domain | — | `sentrix/domain/assistant/task_state.py` |
| `sentrix/domain/assistant/result_set.py` | 结果集与分页规则；handle 到 asset_id 的映射 | domain | — | — |
| `sentrix/domain/assistant/task_state.py` | 回合状态：计数器、进度流、终止原因与门禁标志位 | domain | `sentrix/domain/assistant/models.py`、`sentrix/domain/assistant/profile.py` | `sentrix/domain/assistant/tool_policy.py` |
| `sentrix/domain/assistant/tool_policy.py` | 工具收敛策略：执行 / 回放 / 拒绝的唯一决定点 | domain | `sentrix/domain/assistant/models.py`、`sentrix/domain/assistant/task_state.py`、`sentrix/domain/assistant/tools.py` | — |
| `sentrix/domain/assistant/tools.py` | 四个工具的契约：标准 JSON Schema + 声明能满足哪类证据 | domain | `sentrix/domain/assistant/evidence_ledger.py` | `sentrix/domain/assistant/tool_policy.py` |
| `sentrix/domain/media/__init__.py` | 包标记 | domain | — | — |
| `sentrix/domain/media/models.py` | 媒体领域模型 Asset / Observation / MediaType / GpsPoint；纯数据与规则，零 IO | domain | — | `sentrix/api/routes/media.py`、`sentrix/application/media/import_assets.py`、`sentrix/application/retrieval/index_visual.py`、`sentrix/domain/media/ports.py`、`sentrix/domain/retrieval/ports.py`、`sentrix/infrastructure/media/metadata.py`、`sentrix/infrastructure/media/storage.py`、`sentrix/infrastructure/persistence/repositories/assets.py` |
| `sentrix/domain/media/ports.py` | 领域端口（AssetRepository / MediaStorage / MetadataReader / PreviewEncoder）与浏览器转码规则 | domain | `sentrix/domain/media/models.py` | `sentrix/application/media/import_assets.py`、`sentrix/bootstrap/container.py` |
| `sentrix/domain/retrieval/__init__.py` | 包标记与检索域导出 | domain | `sentrix/domain/retrieval/fusion.py`、`sentrix/domain/retrieval/models.py`、`sentrix/domain/retrieval/ports.py`、`sentrix/domain/retrieval/similarity.py` | `sentrix/application/retrieval/index_visual.py`、`sentrix/application/retrieval/retrieve_visual.py`、`sentrix/infrastructure/models/chinese_clip.py` |
| `sentrix/domain/retrieval/fusion.py` | 加权 RRF 融合与证据分级；权重/RRF_K/anchor 加成来自行为基线 | domain | `sentrix/domain/retrieval/models.py` | `sentrix/application/retrieval/retrieve_visual.py`、`sentrix/domain/retrieval/__init__.py` |
| `sentrix/domain/retrieval/models.py` | 候选命中 `CandidateHit` 与融合结果 `FusedCandidate`；分值不是裸 float | domain | — | `sentrix/application/retrieval/retrieve_visual.py`、`sentrix/domain/retrieval/__init__.py`、`sentrix/domain/retrieval/fusion.py` |
| `sentrix/domain/retrieval/ports.py` | 向量端口：`VectorRepository`（权威）/ `VectorIndex`（派生镜像）/ `VisualEmbedder`（跨模态），以及 `VectorRecord` / `VectorHit` / `VectorIndexStatus` | domain | `sentrix/domain/media/models.py` | `sentrix/application/media/import_assets.py`、`sentrix/application/retrieval/index_visual.py`、`sentrix/application/retrieval/retrieve_visual.py`、`sentrix/bootstrap/container.py`、`sentrix/domain/retrieval/__init__.py`、`sentrix/infrastructure/persistence/repositories/vectors.py`、`sentrix/infrastructure/vector/qdrant_index.py` |
| `sentrix/domain/retrieval/scheme.py` | 钉死的嵌入方案常量（模型名/维度/space）；不读 env，2.0 不提供切换开关 | domain | — | `sentrix/application/retrieval/index_visual.py`、`sentrix/application/retrieval/retrieve_visual.py`、`sentrix/infrastructure/models/chinese_clip.py` |
| `sentrix/domain/retrieval/similarity.py` | 余弦相似度与维度一致性判定；1.0 把它埋在 `MemoryStore._cosine` 里，精确回退与主路径各算一套 | domain | — | `sentrix/domain/retrieval/__init__.py`、`sentrix/infrastructure/persistence/repositories/vectors.py` |
| `sentrix/infrastructure/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/media/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/media/image_io.py` | HEIC 注册与 JPEG 预览编码（`ensure_heif_support` / `encode_jpeg_preview` / `PillowPreviewEncoder`） | infrastructure | — | `sentrix/bootstrap/container.py`、`sentrix/infrastructure/media/metadata.py`、`sentrix/infrastructure/models/chinese_clip.py` |
| `sentrix/infrastructure/media/metadata.py` | EXIF / GPS / 设备抽取（`PillowMetadataReader`），失败降级为 None 并记日志 | infrastructure | `sentrix/domain/media/models.py`、`sentrix/infrastructure/media/image_io.py` | `sentrix/bootstrap/container.py` |
| `sentrix/infrastructure/media/storage.py` | 文件系统媒体存储（`FileSystemMediaStorage` / `sanitise`），实现 domain 的 MediaStorage 端口 | infrastructure | `sentrix/domain/media/models.py` | `sentrix/bootstrap/container.py` |
| `sentrix/infrastructure/models/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/models/chinese_clip.py` | `ChineseClipVisualEmbedder`：进程内 Chinese-CLIP 视觉嵌入，GPU 优先、可退 CPU | infrastructure | `sentrix/domain/retrieval/__init__.py`、`sentrix/domain/retrieval/scheme.py`、`sentrix/infrastructure/media/image_io.py` | `sentrix/bootstrap/container.py` |
| `sentrix/infrastructure/persistence/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/persistence/database.py` | SQLite 连接工厂与版本化迁移执行器 | infrastructure | — | `sentrix/bootstrap/container.py`、`sentrix/infrastructure/persistence/repositories/assets.py`、`sentrix/infrastructure/persistence/repositories/spaces.py`、`sentrix/infrastructure/persistence/repositories/vectors.py` |
| `sentrix/infrastructure/persistence/repositories/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/persistence/repositories/assets.py` | Asset / Observation 的 SQLite 仓储（`SqliteAssetRepository` / `SqliteObservationRepository`）；SQL 只出现在这一层 | infrastructure | `sentrix/domain/media/models.py`、`sentrix/infrastructure/persistence/database.py` | `sentrix/bootstrap/container.py` |
| `sentrix/infrastructure/persistence/repositories/spaces.py` | 记忆空间的 SQLite 仓储（`MemorySpace` / `SqliteSpaceRepository`） | infrastructure | `sentrix/infrastructure/persistence/database.py` | `sentrix/bootstrap/container.py` |
| `sentrix/infrastructure/persistence/repositories/vectors.py` | `SqliteVectorRepository`：memory_vectors 的权威存储与精确回退检索 | infrastructure | `sentrix/domain/retrieval/ports.py`、`sentrix/domain/retrieval/similarity.py`、`sentrix/infrastructure/persistence/database.py` | `sentrix/bootstrap/container.py` |
| `sentrix/infrastructure/vector/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/vector/qdrant_index.py` | `QdrantVectorIndex`：嵌入式 Qdrant 派生索引；目录被占用时抛 `VectorIndexUnavailable` 而不是静默降级 | infrastructure | `sentrix/domain/retrieval/ports.py` | `sentrix/bootstrap/container.py` |
| `sentrix/main.py` | FastAPI 装配与 v1 路由注册；进程入口 | — | `sentrix/__init__.py`、`sentrix/api/errors.py`、`sentrix/api/routes/__init__.py`、`sentrix/api/routes/health.py`、`sentrix/api/routes/media.py`、`sentrix/api/routes/search.py`、`sentrix/bootstrap/container.py`、`sentrix/config.py` | — |

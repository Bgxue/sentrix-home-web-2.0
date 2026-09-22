# 模块地图

**本文件由 `scripts/check_module_map.py` 校验**，脚本实际查两件事：

1. **文件集合双向一致**——源码里每个 `.py` 必须在下面有一行，文档里有但代码里不存在的
   条目会让 CI 失败。
2. **依赖边一致**——脚本用 `ast` 解析每个文件的包内 import，映射成文件路径后与「依赖谁」
   列比对；「被谁依赖」是同一次解析的转置，由脚本推导后比对。少写、多写、与转置不符
   都会失败。

两列只记 `sentrix` 包内的模块关系；stdlib 与第三方（fastapi / sqlite3 / pydantic 等）
不进这两列。

列含义：**层** = domain / application / infrastructure / api / bootstrap ·
**依赖谁** 与 **被谁依赖** 写 `src/` 下的相对路径，空集写 `—`，多项用 `、` 分隔。

| 路径 | 职责 | 层 | 依赖谁 | 被谁依赖 |
|---|---|---|---|---|
| `sentrix/__init__.py` | 包版本号 | — | — | `sentrix/api/routes/health.py`、`sentrix/main.py` |
| `sentrix/config.py` | 类型化配置；`data_dir` 是唯一可配根，其余路径派生 | — | — | `sentrix/bootstrap/container.py`、`sentrix/main.py` |
| `sentrix/main.py` | FastAPI 装配与 v1 路由注册；进程入口 | — | `sentrix/__init__.py`、`sentrix/api/errors.py`、`sentrix/api/routes/__init__.py`、`sentrix/api/routes/health.py`、`sentrix/api/routes/media.py`、`sentrix/bootstrap/container.py`、`sentrix/config.py` | — |
| `sentrix/bootstrap/__init__.py` | 包标记 | bootstrap | — | — |
| `sentrix/bootstrap/container.py` | 唯一依赖装配点；只有这里构造适配器 | bootstrap | `sentrix/config.py`、`sentrix/domain/media/ports.py`、`sentrix/infrastructure/media/image_io.py`、`sentrix/infrastructure/media/metadata.py`、`sentrix/infrastructure/media/storage.py`、`sentrix/infrastructure/persistence/database.py`、`sentrix/infrastructure/persistence/repositories/assets.py`、`sentrix/infrastructure/persistence/repositories/spaces.py` | `sentrix/api/deps.py`、`sentrix/api/routes/media.py`、`sentrix/main.py` |
| `sentrix/api/__init__.py` | 包标记 | api | — | — |
| `sentrix/api/deps.py` | `get_container`：路由从 `app.state` 取装配好的容器，不自己构造 | api | `sentrix/bootstrap/container.py` | `sentrix/api/routes/media.py` |
| `sentrix/api/errors.py` | 领域错误基类与 `DomainError` → HTTP 的映射 | api | — | `sentrix/main.py` |
| `sentrix/api/routes/__init__.py` | 包标记 | api | — | `sentrix/main.py` |
| `sentrix/api/routes/health.py` | `GET /api/v1/health`，只做 HTTP 映射 | api | `sentrix/__init__.py` | `sentrix/main.py` |
| `sentrix/api/routes/media.py` | 媒体导入与交付端点（import / assets / file / preview），只做 HTTP 映射 | api | `sentrix/api/deps.py`、`sentrix/api/schemas/media.py`、`sentrix/application/media/import_assets.py`、`sentrix/bootstrap/container.py`、`sentrix/domain/media/models.py` | `sentrix/main.py` |
| `sentrix/api/schemas/__init__.py` | 包标记 | api | — | — |
| `sentrix/api/schemas/media.py` | v1 媒体端点的响应模型（ImportResponse / AssetSummary / AssetListResponse） | api | — | `sentrix/api/routes/media.py` |
| `sentrix/domain/__init__.py` | 包标记 | domain | — | — |
| `sentrix/domain/media/__init__.py` | 包标记 | domain | — | — |
| `sentrix/domain/media/models.py` | 媒体领域模型 Asset / Observation / MediaType / GpsPoint；纯数据与规则，零 IO | domain | — | `sentrix/api/routes/media.py`、`sentrix/application/media/import_assets.py`、`sentrix/domain/media/ports.py`、`sentrix/infrastructure/media/metadata.py`、`sentrix/infrastructure/media/storage.py`、`sentrix/infrastructure/persistence/repositories/assets.py` |
| `sentrix/domain/media/ports.py` | 领域端口（AssetRepository / MediaStorage / MetadataReader / PreviewEncoder）与浏览器转码规则 | domain | `sentrix/domain/media/models.py` | `sentrix/application/media/import_assets.py`、`sentrix/bootstrap/container.py` |
| `sentrix/domain/retrieval/__init__.py` | 包标记与检索域导出 | domain | `sentrix/domain/retrieval/fusion.py`、`sentrix/domain/retrieval/models.py` | — |
| `sentrix/domain/retrieval/models.py` | 候选命中 `CandidateHit` 与融合结果 `FusedCandidate`；分值不是裸 float | domain | — | `sentrix/domain/retrieval/__init__.py`、`sentrix/domain/retrieval/fusion.py` |
| `sentrix/domain/retrieval/fusion.py` | 加权 RRF 融合与证据分级；权重/RRF_K/anchor 加成来自行为基线 | domain | `sentrix/domain/retrieval/models.py` | `sentrix/domain/retrieval/__init__.py` |
| `sentrix/application/__init__.py` | 包标记 | application | — | — |
| `sentrix/application/media/__init__.py` | 包标记 | application | — | — |
| `sentrix/application/media/import_assets.py` | 导入用例 `ImportAssets` / `ImportRequest` / `ImportResult`；按 scope 的 SHA-256 去重 | application | `sentrix/domain/media/models.py`、`sentrix/domain/media/ports.py` | `sentrix/api/routes/media.py` |
| `sentrix/infrastructure/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/persistence/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/persistence/database.py` | SQLite 连接工厂与版本化迁移执行器 | infrastructure | — | `sentrix/bootstrap/container.py`、`sentrix/infrastructure/persistence/repositories/assets.py`、`sentrix/infrastructure/persistence/repositories/spaces.py` |
| `sentrix/infrastructure/persistence/repositories/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/persistence/repositories/assets.py` | Asset / Observation 的 SQLite 仓储（`SqliteAssetRepository` / `SqliteObservationRepository`）；SQL 只出现在这一层 | infrastructure | `sentrix/domain/media/models.py`、`sentrix/infrastructure/persistence/database.py` | `sentrix/bootstrap/container.py` |
| `sentrix/infrastructure/persistence/repositories/spaces.py` | 记忆空间的 SQLite 仓储（`MemorySpace` / `SqliteSpaceRepository`） | infrastructure | `sentrix/infrastructure/persistence/database.py` | `sentrix/bootstrap/container.py` |
| `sentrix/infrastructure/media/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/media/storage.py` | 文件系统媒体存储（`FileSystemMediaStorage` / `sanitise`），实现 domain 的 MediaStorage 端口 | infrastructure | `sentrix/domain/media/models.py` | `sentrix/bootstrap/container.py` |
| `sentrix/infrastructure/media/image_io.py` | HEIC 注册与 JPEG 预览编码（`ensure_heif_support` / `encode_jpeg_preview` / `PillowPreviewEncoder`） | infrastructure | — | `sentrix/bootstrap/container.py`、`sentrix/infrastructure/media/metadata.py` |
| `sentrix/infrastructure/media/metadata.py` | EXIF / GPS / 设备抽取（`PillowMetadataReader`），失败降级为 None 并记日志 | infrastructure | `sentrix/domain/media/models.py`、`sentrix/infrastructure/media/image_io.py` | `sentrix/bootstrap/container.py` |

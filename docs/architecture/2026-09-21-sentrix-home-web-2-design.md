# Sentrix Home Web 2.0 架构设计

## 目标

在不迁移旧项目运行数据的前提下，重建一个可多人协作的 Sentrix Home 产品仓库。2.0 必须保留已验证的媒体导入、记忆、身份、家庭关系、检索、助手、运行时与评测能力；旧项目只作为代码和行为来源。

## 已确认决策

- 新仓库：`/home/asus/Github/sentrix-home-web2.0`；独立 Git 历史与独立数据空间。
- 主产品：React + TypeScript + Vite 前端，FastAPI 后端，新的 `/api/v1` HTTP 契约。
- 后端：模块化单体；SQLite 是业务事实源，Qdrant 仅为可重建检索镜像。
- PhotoBench：同仓独立内部应用，独立前后端、启动和结果目录；只消费公开评测协议。
- 不迁移旧数据库、媒体、向量索引、旧 URL 或运行配置。
- 迁移代码必须有来源映射。新写的结构性代码标记 `NEW-2.0`，不得伪造旧代码来源。

## 边界

```text
apps/web -> /api/v1 -> apps/api
apps/photobench -> /api/v1/evaluation -> apps/api

apps/api owns: SQLite, media storage, background jobs, model and vector adapters
apps/photobench owns: datasets, run results, judges, benchmark orchestration
```

主产品不得启动、停止或读取 PhotoBench。PhotoBench 不得读取主产品 Python 内部模块、SQLite 文件或日志；它只能使用版本化 HTTP 评测契约。

## 目录职责

```text
apps/
  api/                    FastAPI product API
  web/                    React family-memory UI
  photobench/             internal benchmark product
packages/
  contracts/              OpenAPI artifact and generated TypeScript client
  shared/                 cross-app, non-domain utilities only
docs/
  architecture/           component, data and API boundaries
  adr/                    immutable architecture decisions
  refactor/               source map, inventory, migration plan
  runbooks/               operation and incident procedures
infra/                    deployment assets and environment examples
scripts/                  reproducible developer and operational commands
tests/
  contract/               API and PhotoBench compatibility contracts
  e2e/                    product-level flows
```

The final `apps/api/src/sentrix` layout is:

```text
api/                      routes, schemas, dependencies and error mapping
application/              one use-case per command/query
domain/media/             assets, observations, import and processing rules
domain/memories/          events, trips, facts, stories and feedback rules
domain/identities/        faces, people, entities and portraits
domain/family/            membership, relationships and hypotheses
domain/retrieval/         query contract, evidence and ranking rules
domain/assistant/         conversations, task/evidence state and answer policy
domain/runtime/           model profiles, capabilities and telemetry contracts
infrastructure/persistence/ SQLite repositories and schema migrations
infrastructure/media/     file, image, EXIF and video adapters
infrastructure/models/    VLM, embedding, ASR, OCR and face adapters
infrastructure/vector/    Qdrant and index implementations
workers/                  explicit asynchronous job entry points
bootstrap/                configuration and dependency composition
```

## Source-map contract

`docs/refactor/source-map.jsonl` is mandatory before each migrated domain is accepted. Each record has:

```json
{"target_path":"apps/api/src/...","target_lines":"1-42","origin_kind":"migrated","source_commit":"2d2cee7c","source_path":"backend/...","source_lines":"10-80","source_symbol":"...","transformation":"split_without_behavior_change","verification":"tests/..."}
```

`origin_kind` is one of `migrated`, `adapted`, `deleted`, or `NEW-2.0`. Any destination line without an old equivalent is `NEW-2.0`; any discarded old code remains in the inventory with a concrete deletion reason.

## Non-negotiable implementation rules

- HTTP routes contain transport mapping only; they never use SQL, filesystem APIs or model clients directly.
- Application services own transactions and job submission.
- Domain code depends only on domain contracts, never FastAPI, SQLite, Qdrant or environment variables.
- Infrastructure implements domain ports and is composed only in bootstrap.
- Every public API is defined in OpenAPI first and has a contract test.
- Destructive maintenance operations are explicit commands with dry-run and audit output.
- Every retained behavior receives an ownership test before the original implementation is removed.


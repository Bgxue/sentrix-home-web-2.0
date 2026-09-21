# Sentrix Home Web 2.0 文件级重构计划

## 基线

旧代码基线固定为 /home/asus/Github/Sentrix-Home-Web 的 2d2cee7cbf1023f5019b9aaa9061af9c4cf45afa。2.0 位于 /home/asus/Github/sentrix-home-web2.0，独立数据空间，不迁移数据库、媒体、向量索引、旧 URL 或环境文件。

当前工作树的 7 个受控变更单列审核：backend/runtime_providers.py、backend/video/processor.py、PhotoBench 编排器和 Vue UI 及其测试。所有 .codex-*、*.bak.*、*.rej、dataset backup、staging、dist 和根目录未引用副本禁止迁入。

## 已确认架构

主产品使用 React + TypeScript + Vite 与 FastAPI 模块化单体，公开 API 只有 /api/v1。SQLite 是业务事实源；Qdrant 与 HNSW 是可重建的检索投影。PhotoBench、vLLM profile manager、E2B Ollama-compatible LoRA 服务均为独立部署单元。

## 目标文件与旧来源

|目标文件|职责|旧来源|
|---|---|---|
|apps/api/src/sentrix/main.py|FastAPI 装配与 v1 路由注册|backend/app.py 的应用创建和路由注册|
|apps/api/src/sentrix/config.py|类型化配置|start.sh、scripts/runtime、platform_profile.py|
|apps/api/src/sentrix/bootstrap/container.py|唯一依赖装配点|app.py 的 store、pipeline、model client 全局初始化|
|apps/api/src/sentrix/api/routes/{health,runtime,spaces,media,events,people,entities,family,knowledge,assistant,conversations,stories,maintenance,evaluation}.py|每个文件仅做 HTTP 映射|backend/app.py 对应路由段|
|apps/api/src/sentrix/domain/media/{models,ports}.py|Asset、Observation、导入领域契约|db.py 表语义、pipeline.py|
|apps/api/src/sentrix/application/media/{import_assets,process_asset,process_video}.py|导入、处理、视频用例|IngestionPipeline、VideoMemoryAdapter、app.py 后台任务|
|apps/api/src/sentrix/infrastructure/media/{storage,metadata,image_io,ocr,video}.py|文件、EXIF、HEIC、OCR、WorldMM|image_io.py、video/、agent_runtime/ocr_tool.py|
|apps/api/src/sentrix/domain/memories/{events,trips,facts,stories,feedback}.py|记忆领域规则|db.py、structured_memory.py、pipeline.py|
|apps/api/src/sentrix/application/memories/{summarize_events,manage_events,manage_trips,manage_facts,manage_stories}.py|记忆用例|pipeline.py 和 app.py 路由|
|apps/api/src/sentrix/infrastructure/persistence/repositories/{assets,observations,events,trips,facts,stories}.py|SQL 仓储|MemoryStore 相应方法|
|apps/api/src/sentrix/infrastructure/persistence/migrations/0001_core.sql|全新初始 schema|db.py 的表和索引语义；新增 SQL 标记 NEW-2.0|
|apps/api/src/sentrix/domain/identities/{models,face_rules,entity_rules,portrait_rules}.py|身份领域规则|face_clustering.py、person_appearance.py、person_portraits.py|
|apps/api/src/sentrix/application/identities/{detect_faces,manage_clusters,manage_people,manage_entities,build_portraits}.py|身份用例|FaceAdapter、MemoryStore、app.py 人物路由|
|apps/api/src/sentrix/infrastructure/models/face.py|人脸模型适配|model_clients.py FaceAdapter、face_embeddings.py|
|apps/api/src/sentrix/domain/family/{models,relationship_rules,hypothesis_rules}.py|家庭关系规则|family_graph.py、person_graph.py|
|apps/api/src/sentrix/application/family/{manage_graph,run_analysis,manage_hypotheses}.py|家庭用例|family_graph_service.py、app.py family 路由|
|apps/api/src/sentrix/domain/retrieval/{query,evidence,ranking,filters}.py|查询、证据、硬条件与排序|query_contracts.py、evidence_retrieval.py、retrieval/fusion.py、retrieval/ranking.py|
|apps/api/src/sentrix/application/retrieval/retrieve_evidence.py|六路召回编排与 trace|EvidenceRetrievalKernel.retrieve|
|apps/api/src/sentrix/infrastructure/retrieval/{metadata,entity,lexical,visual_ann,text_ann,adjacency}.py|六个召回器|backend/retrieval 对应文件|
|apps/api/src/sentrix/infrastructure/vector/{router,qdrant,hnsw,indexes}.py|embedding、镜像和索引|embeddings、qdrant_memory.py、retrieval_ann.py、retrieval_indexes.py|
|apps/api/src/sentrix/domain/assistant/{task_state,evidence_ledger,result_set,tool_policy,final_guard}.py|无模型的助手状态规则|agent_runtime 对应模块|
|apps/api/src/sentrix/application/assistant/{run_turn,manage_conversations,inspect_photo}.py|助手用例、SSE、照片线程|runtime.py、agent_conversation.py、app.py assistant 路由|
|apps/api/src/sentrix/infrastructure/assistant/{planner,tool_registry,writer,judge}.py|模型型助手适配|goal_planner.py、tools.py、final_writer.py、judge.py|
|apps/api/src/sentrix/domain/runtime/contracts.py|profile、能力和遥测 DTO|runtime_providers.py、platform_profile.py|
|apps/api/src/sentrix/infrastructure/runtime/{platform,telemetry,providers}.py|能力探测和遥测|platform_profile.py、runtime_providers.py；先审核候选补丁|
|apps/web/src/app/router.tsx|Web 页面装配|src/app.js 的导航和 view 分支|
|apps/web/src/features/{dashboard,assistant,timeline,people,knowledge,library,stories,imports,settings}|按页面的 React 功能模块|src/app.js 相应 render 和 action 段|
|apps/web/src/shared/api/client.ts|唯一 HTTP 与 SSE client|src/api.js|
|apps/web/src/shared/ui|无业务组件|src/app.js 可复用视图片段、src/styles.css|
|apps/photobench/backend/{app,run_service,result_repository,telemetry,judge,datasets}.py|评测服务拆分|services/photobench/backend/benchmark_orchestrator.py|
|apps/photobench/frontend/src/{App.vue,views,features,api}.ts|评测控制台|services/photobench/frontend/src/App.vue|
|services/model-manager/{app,manager,registry_schema}.py|模型 profile 管理|services/vllm_manager|
|services/e2b-compat/{app,model,ollama_protocol}.py|LoRA 推理兼容服务|services/e2b_server|
|packages/contracts/openapi/sentrix-v1.yaml|唯一公开 API 协议|NEW-2.0；由旧行为和新 DTO 归纳|
|packages/contracts/typescript|生成的 Client|NEW-2.0|
|tests/contract 与 tests/e2e|契约和端到端验证|旧 backend tests、test、PhotoBench tests 按功能迁移|

## 保留功能

必须保留：图片、音频、文本、视频导入；EXIF/GPS；离线 OCR；关键帧/场景；事件和行程；人脸簇、人物、实体和画像；家庭关系；事实、故事、反馈；视觉、文本、词法、元数据、实体和邻接检索；证据包和检索 trace；对话、结果集、照片检查、工具策略、最终回答守卫；模型 profile、硬件能力、遥测；PhotoBench；vLLM manager；E2B compatibility service。

不迁移：根目录 App.vue、tools.py、qdrant_memory.py、ocr_tool.py、final_writer.py、result_set.py、task_state.py、evidence_ledger.py、未跟踪 pipeline.py 和 budget_manager.py；各类备份、构建产物、临时目录、旧端口实验启动脚本。维护和 benchmark 脚本不直接复制：有可复现价值的转换为 API 回归、PhotoBench、fixtures 或 runbook，其余删除。

## 执行顺序

1. 创建 source-inventory.jsonl：旧提交每个可执行文件记录 SHA-256、行数、符号和能力；单独审核 7 个脏差异。
2. 写 OpenAPI、错误码、scope 规则和 SSE 事件，并先写 API contract tests。
3. 建立 pyproject、main、container、config、空路由、SQLite 初始迁移；只做导入和语法验证。
4. 迁移媒体垂直切片：Asset/Observation/图片导入，再音频、文本、视频。
5. 迁移记忆和身份切片：事件/事实，脸簇/人物/实体，家庭关系。
6. 迁移检索：QuerySpec 和 EvidencePacket 先行，再接入六路召回、融合和索引。
7. 迁移助手：Conversation/ResultSet/TaskState，再 ToolPolicy/ToolRegistry，最后 planner、writer、judge。
8. 迁移 React Web：八个产品页面逐页交付，仅使用 TypeScript API client。
9. 拆分 PhotoBench 与推理服务，先建立评测 HTTP contract，禁止任何内部代码或生命周期耦合。
10. 清理：没有来源映射、测试、runbook 和模块负责边界的代码禁止合入。

每一项都是先写失败测试、运行确认失败、迁移最小实现、运行目标测试、更新来源映射、提交。用户负责模型和真实功能验证；自动验证只做语法、单元、契约和静态边界。

## 逐行来源门禁

docs/refactor/source-map.jsonl 是强制交付物。每条记录包含 target_path、target_lines、origin_kind、source_commit、source_path、source_lines、source_symbol、transformation、verification。

origin_kind 只能是 migrated、adapted、deleted、NEW-2.0。目标文件的所有非空非注释代码行必须被 source-map 覆盖；新建的 DTO、依赖注入、数据库迁移、生成 client 和测试夹具标 NEW-2.0。拆分函数使用一对多映射，合并函数记录多个源区段。source-map 检查脚本必须拒绝任何无源区段、越界旧行号和未归类的旧可执行文件。

## 完成定义

所有可用能力有新的 v1 contract、明确模块、测试和运行手册。主 API、Web、PhotoBench、model-manager、e2b-compat 可独立构建、启动和停止。主产品不包含 PhotoBench 生命周期；PhotoBench 不导入主产品内部代码。所有生产代码可追溯到固定基线的旧区段或 NEW-2.0。


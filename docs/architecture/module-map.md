# 模块地图

**本文件由 `scripts/check_module_map.py` 校验**：源码里每个 `.py` 必须在下面有
一行，文档里有但代码里不存在的条目会让 CI 失败。文件级内容随切片生长。

列含义：**层** = domain / application / infrastructure / api / bootstrap ·
**依赖谁** 与 **被谁依赖** 是模块级的有向边，由脚本从真实 import 校验。

| 路径 | 职责 | 层 | 依赖谁 | 被谁依赖 |
|---|---|---|---|---|
| `sentrix/__init__.py` | 包版本号 | — | — | `main`、`health` |
| `sentrix/config.py` | 类型化配置；`data_dir` 是唯一可配根，其余路径派生 | — | pydantic-settings | `main`、`container` |
| `sentrix/main.py` | FastAPI 装配与 v1 路由注册 | — | `api.routes`、`bootstrap`、`config` | 进程入口 |
| `sentrix/bootstrap/__init__.py` | 包标记 | bootstrap | — | — |
| `sentrix/bootstrap/container.py` | 唯一依赖装配点；只有这里构造适配器 | bootstrap | `config`、`infrastructure` | `main`、`api.deps` |
| `sentrix/api/__init__.py` | 包标记 | api | — | — |
| `sentrix/api/routes/__init__.py` | 包标记 | api | — | `main` |
| `sentrix/api/routes/health.py` | `GET /api/v1/health`，只做 HTTP 映射 | api | `config` | `main` |
| `sentrix/domain/__init__.py` | 包标记 | domain | — | — |
| `sentrix/application/__init__.py` | 包标记 | application | — | — |
| `sentrix/infrastructure/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/persistence/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/persistence/database.py` | SQLite 连接工厂与版本化迁移执行器 | infrastructure | sqlite3 | `bootstrap` |

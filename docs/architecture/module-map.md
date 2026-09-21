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
| `sentrix/main.py` | FastAPI 装配与 v1 路由注册；进程入口 | — | `sentrix/__init__.py`、`sentrix/api/routes/__init__.py`、`sentrix/api/routes/health.py`、`sentrix/bootstrap/container.py`、`sentrix/config.py` | — |
| `sentrix/bootstrap/__init__.py` | 包标记 | bootstrap | — | — |
| `sentrix/bootstrap/container.py` | 唯一依赖装配点；只有这里构造适配器 | bootstrap | `sentrix/config.py`、`sentrix/infrastructure/persistence/database.py` | `sentrix/main.py` |
| `sentrix/api/__init__.py` | 包标记 | api | — | — |
| `sentrix/api/routes/__init__.py` | 包标记 | api | — | `sentrix/main.py` |
| `sentrix/api/routes/health.py` | `GET /api/v1/health`，只做 HTTP 映射 | api | `sentrix/__init__.py` | `sentrix/main.py` |
| `sentrix/domain/__init__.py` | 包标记 | domain | — | — |
| `sentrix/application/__init__.py` | 包标记 | application | — | — |
| `sentrix/infrastructure/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/persistence/__init__.py` | 包标记 | infrastructure | — | — |
| `sentrix/infrastructure/persistence/database.py` | SQLite 连接工厂与版本化迁移执行器 | infrastructure | — | `sentrix/bootstrap/container.py` |

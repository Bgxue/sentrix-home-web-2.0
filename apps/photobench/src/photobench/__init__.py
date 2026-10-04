"""PhotoBench：同仓的**内部**评测应用（独立前后端、独立启动与结果目录）。

实施设计 §138-145 定死了它与主产品的关系：PhotoBench 只消费**版本化 HTTP 评测契约**，
不得读取主产品（``apps/api`` / ``sentrix``）的 Python 内部模块、SQLite 文件或日志。
这条不是靠自觉：``photobench-isolation`` 契约（仓库根 ``pyproject.toml``）禁止
``photobench.*`` import 任何 ``sentrix.*``。

包结构与 ``apps/api/src/sentrix`` 同构（domain / application / infrastructure / api）
——用户 2026-10-04 裁决 §3.5：PhotoBench 受与 ``apps/api`` **同一套门禁**，
所以分层也必须一样。S9a 落了 domain 与 infrastructure，S9b-1 补上 application、
api、bootstrap 与进程入口 ``main.py``。
"""

__version__ = "0.1.0"

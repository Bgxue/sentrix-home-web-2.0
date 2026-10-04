"""PhotoBench 配置文件的读写端口。

1.0 直接把 ``PROJECT_ROOT/config/*.json`` 的路径写死在模块常量里
（``RUNTIME_CONNECTION_CONFIG_PATH`` ``:67``、``VLLM_TARGETS_PATH`` ``:203``、
``JUDGE_PROVIDERS_PATH`` ``:206``），并在导入时读一次、把值摊到模块级全局变量上
（``DEFAULT_SENTRIX_URL`` / ``DEFAULT_JUDGE_URL`` / ``DEFAULT_VLLM_*``）。2.0 把「哪个文件」
留给 infrastructure 决定（2.0 的配置根是 ``apps/photobench/config/``，不是 1.0 的
``services/photobench/config/``），application 只说**逻辑名**。

**端口只描述读写，不描述取值范围**：哪些键必填、缺省填什么、``judge_provider_id``
必须命中已登记 provider —— 那些是规则，属 application，不属文件系统。

**``path_for`` 为什么在端口上。** ``GET /api/config`` 的响应里有一项
``runtime_config_file``，1.0 给的是**绝对路径字符串**（``:7551``）。application 不许
import ``os``/``pathlib`` 去拼路径吗——其实 application 层没有被禁，但路径的拼法是
部署知识（配置根在哪），按分层应当留在 infrastructure。所以端口暴露一个「这个逻辑名
落在哪个文件」的查询，而不是暴露配置根。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ConfigStore(Protocol):
    """配置文件的最小面：读一个 JSON、写一个 JSON、问一个路径。"""

    def read_json(self, name: str) -> dict[str, Any] | None:
        """读 ``name`` 对应的 JSON 对象。

        **文件不存在返回 ``None``**（1.0 的 ``_load_runtime_connection_config`` 就是这么
        处理缺失的，``:72-76``）；**文件存在但内容不是 JSON 对象则抛 ``ValueError``**
        ——那是配置损坏，与「没配」是两件事，不能吞成同一个值。
        """
        ...

    def write_json(self, name: str, value: Mapping[str, Any]) -> None:
        """原子写回（写临时文件 + ``os.replace``，1.0 ``persist_runtime_connection_config``
        ``:445-447`` 的做法）。写一半被读到的配置文件比写失败更糟：进程重启后它是一份
        语法完好的**错误**配置。"""
        ...

    def path_for(self, name: str) -> str:
        """``name`` 对应的绝对路径字符串，只用于响应里给运维看的那个字段。"""
        ...

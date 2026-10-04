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


@runtime_checkable
class SecretEnvStore(Protocol):
    """``.env.local`` 的读写端口——**只服务密钥，不服务配置**。

    1.0 把 ``BENCH_JUDGE_API_KEY`` 写进 ``services/photobench/.env.local`` 并同时改
    ``os.environ``（``_write_local_env_secret`` ``:317`` + ``persist_runtime_connection_config``
    ``:449``）。密钥不进 ``runtime_connection.json``：那是会被复制、会被贴进工单的
    东西，而密钥不该跟着走。

    **为什么不并进 ``ConfigStore``。** 那个端口只认 JSON（``read_json`` / ``write_json``）
    ——``.env.local`` 是 ``KEY=VALUE`` 的行格式，硬塞进去要么给这个端口加第二种格式，
    要么在 adapter 里假装它是 JSON。分开之后每条规则（单行、去重、``0o600``）都落在
    一个说得出理由的地方。
    """

    def read_all(self) -> dict[str, str]:
        """整份文件解析成键值对（坏行跳过）。

        1.0 的 ``_load_dotenv_file`` 是**逐行**读整份文件并把每个键补进 ``os.environ``
        （``:79``），不是按名字查——所以端口要能给出整份，否则那条「文件里任意的
        ``BENCH_*`` 覆盖项都生效」的行为会在 2.0 里悄悄变窄。
        """
        ...

    def read(self, name: str) -> str | None:
        """读一个键；文件不存在或键不存在都返回 ``None``（「没配」与「配成空」是两回事，
        但两者从环境里都读不到值，调用方只看得到空）。"""
        ...

    def write(self, name: str, value: str) -> None:
        """写一个键（``value`` 为空表示删除它）。**必须是单行值**——多行会被
        ``shlex`` 解析成别的东西，1.0 在写之前就拒绝它（``:319``）。"""
        ...

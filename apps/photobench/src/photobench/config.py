"""PhotoBench 的目录与运行参数。对应 1.0 文件头那批 ``PROJECT_ROOT / ...`` 常量。

1.0（``:63-67``）：项目根 ``services/photobench``，数据在 ``data/``、结果在
``results/``、配置在 ``config/``、前端产物在 ``frontend/dist/``。2.0 的 PhotoBench 是
``apps/photobench``，于是数据根是 ``datasets/``（2.0 已有的那 1.4 GB 数据集），
配置与结果同名。

**三个环境变量是新增的**（1.0 全写死）：``PHOTOBENCH_CONFIG_ROOT`` /
``PHOTOBENCH_RESULTS_ROOT`` / ``PHOTOBENCH_DATA_ROOT``。理由不是「灵活」，是**迁移期
必须能指到别处**：2.0 的验收要在真机上列出 1.0 ``results/`` 里那 270 个 run（19 GB，
不可能拷进仓库），而部署时结果目录也该跟数据盘走。命名与 S9a 的
``PHOTOBENCH_SYSTEM_PROCESS_*`` 同一族。

**S9c 又加了五个**（``sentrix_*``）：换模型 = 重启 2.0（裁决③，T923）要 PhotoBench
知道「2.0 怎么起、起在哪个端口、pid 记在哪」。它们**全是部署参数**，默认值只对
「2.0 与 PhotoBench 同机、走 ``make dev``」这一种部署成立——换部署形态就是改这五个
环境变量，不改代码。这正是把它们抽出来的理由：那套知识不该长在业务逻辑里。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: ``apps/photobench``：``config.py`` 的上三级是 ``photobench`` 包 → ``src`` → 应用根。
APP_ROOT = Path(__file__).resolve().parents[2]


def _root(env_name: str, default: Path) -> Path:
    raw = os.environ.get(env_name)
    return Path(raw).expanduser() if raw else default


#: 2.0 主 API 的默认启动命令。与仓库 ``Makefile`` 的 ``dev`` 目标逐字一致——那一条就是
#: 2.0 的进程入口（``uvicorn --factory sentrix.main:create_app --port 8191
#: --app-dir apps/api/src``）。换部署形态（容器 / systemd / 另一个端口）只需覆盖
#: ``PHOTOBENCH_SENTRIX_START_COMMAND``，**不改这里、也不改重启模块**。
DEFAULT_SENTRIX_START_COMMAND = (
    ".venv/bin/python -m uvicorn --factory sentrix.main:create_app "
    "--host 0.0.0.0 --port 8191 --app-dir apps/api/src"
)


@dataclass(frozen=True)
class Settings:
    config_root: Path
    results_root: Path
    data_root: Path
    #: 2.0 主 API 的健康检查基址（不含 ``/api/v1``）。重启后拿它确认真的起来了。
    sentrix_api_url: str = "http://127.0.0.1:8191"
    #: 2.0 的检出根：启动命令的 ``cwd``（``--app-dir apps/api/src`` 是相对的）。
    #: 默认取 ``APP_ROOT.parents[1]``——``apps/photobench`` 上两级就是仓库根。
    sentrix_repo_root: Path = APP_ROOT.parents[1]
    #: 启动命令。默认等于 ``make dev``，见上面的常量。
    sentrix_start_command: str = DEFAULT_SENTRIX_START_COMMAND
    #: 上一次由 PhotoBench 启动的 2.0 的 pid 与端点记录。**它同时是「当前端点」的
    #: 真相来源**——重启前要拿它判断「是不是已经指着这里了，不用动」。
    sentrix_pid_file: Path = APP_ROOT / "logs" / "sentrix-process.json"
    #: 2.0 的 stdout/stderr 落在这里（``start_new_session`` + 重定向，**不用 shell**）。
    sentrix_log_path: Path = APP_ROOT / "logs" / "sentrix.log"

    @classmethod
    def load(cls) -> Settings:
        return cls(
            config_root=_root("PHOTOBENCH_CONFIG_ROOT", APP_ROOT / "config"),
            results_root=_root("PHOTOBENCH_RESULTS_ROOT", APP_ROOT / "results"),
            data_root=_root("PHOTOBENCH_DATA_ROOT", APP_ROOT / "datasets"),
            sentrix_api_url=os.environ.get("PHOTOBENCH_SENTRIX_API_URL", "http://127.0.0.1:8191").strip(),
            sentrix_repo_root=_root("PHOTOBENCH_SENTRIX_REPO_ROOT", APP_ROOT.parents[1]),
            sentrix_start_command=os.environ.get(
                "PHOTOBENCH_SENTRIX_START_COMMAND", DEFAULT_SENTRIX_START_COMMAND
            ).strip(),
            sentrix_pid_file=_root("PHOTOBENCH_SENTRIX_PID_FILE", APP_ROOT / "logs" / "sentrix-process.json"),
            sentrix_log_path=_root("PHOTOBENCH_SENTRIX_LOG_PATH", APP_ROOT / "logs" / "sentrix.log"),
        )

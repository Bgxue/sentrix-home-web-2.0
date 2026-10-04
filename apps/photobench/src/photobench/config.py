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


@dataclass(frozen=True)
class Settings:
    config_root: Path
    results_root: Path
    data_root: Path

    @classmethod
    def load(cls) -> Settings:
        return cls(
            config_root=_root("PHOTOBENCH_CONFIG_ROOT", APP_ROOT / "config"),
            results_root=_root("PHOTOBENCH_RESULTS_ROOT", APP_ROOT / "results"),
            data_root=_root("PHOTOBENCH_DATA_ROOT", APP_ROOT / "datasets"),
        )

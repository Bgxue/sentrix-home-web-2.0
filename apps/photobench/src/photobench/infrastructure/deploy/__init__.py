"""部署适配器：真正去停/起 2.0 进程的那些实现。

S9c 落了子进程那一份（裸进程 + 信号 + 按端口反查）；S11 · T1103 加了 systemd
``--user`` 那一份。两份实现同一个 ``SentrixProcess`` 端口，装配点按
``Settings.sentrix_service_unit`` 选，见 ``bootstrap/container.py``。
"""

from __future__ import annotations

from photobench.infrastructure.deploy.sentrix_process import SubprocessSentrixProcess
from photobench.infrastructure.deploy.systemd_process import SystemdSentrixProcess

__all__ = ["SubprocessSentrixProcess", "SystemdSentrixProcess"]

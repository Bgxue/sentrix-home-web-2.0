"""部署适配器：真正去停/起 2.0 进程的那一份（子进程 + 信号 + 健康探测）。"""

from __future__ import annotations

from photobench.infrastructure.deploy.sentrix_process import SubprocessSentrixProcess

__all__ = ["SubprocessSentrixProcess"]

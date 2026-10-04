"""部署用例：把 2.0 的模型端点对准本轮评测要用的那个（S9c · T923）。"""

from __future__ import annotations

from photobench.application.deploy.service import SentrixModelSwitch

__all__ = ["SentrixModelSwitch"]

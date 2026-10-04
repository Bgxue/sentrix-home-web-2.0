"""``SentrixModelSwitch``：1.0 ``bind-runtime`` 那一步在 2.0 的等价物（T923）。

**它只做一件事**：本轮要用的模型端点与 2.0 当前记着的端点是否一致——一致就什么都不做，
不一致就重启 2.0。判断与动作分开写在这里，是为了让「每轮评测都重启一遍主 API」这种
副作用不会悄悄发生：**只有 ``running_endpoint()`` 与请求的端点不同才会 ``restart``**。

**与 S11 的重叠**（计划书 §1 登记的）：这一层是「部署知识」——它知道 2.0 是个能被重启的
进程。S11 做「服务与部署」时可能把它接管走。**端口在 ``domain/deploy``、实现在
``infrastructure/deploy``、判断在这一层**，三块加起来就是那次接管的全部范围，
业务逻辑（``run_execution``）只看见 ``ensure_endpoint`` 一个方法。
"""

from __future__ import annotations

from photobench.domain.deploy.ports import RestartOutcome, SentrixProcess


class SentrixModelSwitch:
    """把 2.0 指到本轮要用的模型端点，必要时重启它。"""

    def __init__(self, process: SentrixProcess) -> None:
        self._process = process

    def ensure_endpoint(self, endpoint: str) -> RestartOutcome:
        """确保 2.0 的 ``vlm_base_url`` 是本轮请求的这个端点。

        三种结果，**都不抛**（1.0 的 ``gamma_sync`` 语义：这一步失败不该让整轮评测炸掉
        在 ``model_deploy`` 上——调用方读 ``ok`` 自己决定要不要继续）：

        - 没要求端点（``model_base_url`` 为空）：``ok=False``、``reason=no_endpoint_requested``。
          这是**真的没法判**——不知道该换到哪，如实报，不猜一个默认值。
        - 已经指着这里：``ok=True``、``restarted=False``。**代价最小的一条路**。
        - 指着别处：重启，``restarted=True``，``pid`` 是新进程的。
        """
        requested = endpoint.strip()
        if not requested:
            return RestartOutcome(
                ok=False,
                restarted=False,
                endpoint="",
                reason="no_endpoint_requested",
                error="model_base_url is empty; nothing to point Sentrix at",
            )
        current = self._process.running_endpoint()
        if current == requested:
            return RestartOutcome(
                ok=True,
                restarted=False,
                endpoint=requested,
                previous_endpoint=current,
                reason="already_pointing_here",
            )
        return self._process.restart(endpoint=requested)

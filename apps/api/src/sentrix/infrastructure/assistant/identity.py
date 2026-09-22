"""S3 的人物目录实现：**还没有身份层**，如实报告而不是编造。

2.0 的身份能力（人脸聚类 → 簇确认 → 命名 → 实体）是 S5。S3 在这之前上线，所以
``get_person_profile`` 这个工具在当前装配下只有一种诚实的结果：没有可用的人物档案。

这不是一个「空实现占位」，而是 1.0 已经写好的两条降级路径的 2.0 版本
（``tools.py:2655-2664``：``readiness="limited"`` + ``insufficient_evidence=True``）。
产品硬约束第 5 条要求「无证据时输出 gap 不编造」——把「没有数据」返回成 ``None``、
由工具如实说明，正是这条约束在人物问题上的落点。

S5 落地时替换的是这个适配器（实现同一个 ``PersonDirectory`` 端口），工具契约、账本与
门禁一行都不用动。
"""

from __future__ import annotations

from sentrix.domain.assistant.models import PersonProfile


class NoIdentityDirectory:
    """S3 的装配。永远返回 ``None``——「查不到」与「没有这个人」在这里是同一件事，
    因为身份层根本还没建起来。"""

    def lookup(self, name: str, scope_id: str) -> PersonProfile | None:
        return None

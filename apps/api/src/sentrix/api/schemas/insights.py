"""人物画像端点的请求 / 响应模型。

**只声明需要校验的输入。** 读视图（``GET /person-insights``）返回的是一堆「仓储记录 +
几个现算的派生字段」，没有一条规则要在这里表达，所以那条路由直接拼字典——给它写一套
pydantic 模型等于把同一份形状抄两遍，抄错的那一遍还会被 ``export_openapi --check``
当成契约固化下来。

写侧不一样：``scope_id`` 是**必填**（1.0 ``app.py:1822`` 就对空值报 400），这个判据
要有一条契约能看得见，所以它有模型。
"""

from __future__ import annotations

from pydantic import BaseModel


class RunInsightsRequest(BaseModel):
    """跑一趟画像流水线。"""

    scope_id: str


class PortraitFeedbackRequest(BaseModel):
    """用户对某一版画像的反馈。

    ``feedback`` 允许 ``None``：**清空反馈与没写过反馈是同一件事**，用 ``None`` 表达
    比空串准确（``PortraitRepository.set_feedback`` 的签名也是 ``str | None``）。
    """

    feedback: str | None = None

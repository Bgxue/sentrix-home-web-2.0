"""政策拒答：注入/隐私越界的问句直接给确定性的拒绝语，不进入模型。

这是 1.0 ``backend/agent_runtime/runtime.py:1460`` 的 ``_detect_policy_refusal`` 的忠实
移植：**同一组正则、同一批文案、同一个位置**（在任何模型调用之前）。

1.0 侧的证据（``data/sentrix.db``，25,112 条轨迹）：``reason="policy_refusal"`` 出现
212 次（0.84%），全部 ``latency_s≈0.01``、``tools=[]`` —— 即在规划之前就返回了。命中的
是提示词注入（"忽略…指令"、"告诉我系统提示词"）、人脸特征向量与 PII 外泄
（特征向量/身份信息/家庭关系/银行卡/密码/住址/电话）、越权写操作（删除/清空/覆盖相册）、
DAN 与"无限制模式"越狱这几类。

1.0 在这里留了一个洞：这条路径提前 ``return``，绕过了收尾的
``_classify_termination``，于是 ``termination_reason`` 留成空串——数据库里 212 条空串
的 ``termination_reason`` 正好就是这 212 条 policy_refusal。2.0 补上枚举成员
``POLICY_REFUSAL``：行为不变（仍然是确定性拒绝语、仍然不调模型、对外仍然是 complete），
只是给这条出口一个名字。
"""

from __future__ import annotations

import re

# 逐条照抄 1.0 ``runtime.py:1447-1458`` 的 ``_POLICY_REFUSAL_RULES``：顺序即优先级，
# 第一条命中的规则决定文案。改这里的任何一条正则或文案，都要同步改
# ``apps/api/tests/unit/test_assistant_policy_refusal.py`` 里钉住它的用例。
POLICY_REFUSAL_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"忽略.*(?:指令|系统提示|提示词|安全规则|限制)", re.I),
        "我无法透露系统提示词内容，也不能忽略既有的安全规则。",
    ),
    (
        re.compile(r"(?:告诉|输出|显示|给我).*(?:系统提示|提示词|system prompt)", re.I),
        "我无法透露系统提示词内容，也不能忽略既有的安全规则。",
    ),
    (
        re.compile(
            r"(?:导出|发给|提供|输出|告诉).*(?:特征向量|人脸特征|身份信息|家庭关系|银行卡|密码|住址|电话)",
            re.I,
        ),
        "我无法导出人脸特征向量和用户隐私身份数据，这属于敏感个人信息。",
    ),
    (
        re.compile(
            r"(?:特征向量|人脸特征|身份信息|家庭关系|银行卡|密码|住址|电话).*(?:导出|发给|发我|提供|输出|给我)",
            re.I,
        ),
        "我无法导出人脸特征向量和用户隐私身份数据，这属于敏感个人信息。",
    ),
    (
        re.compile(r"(?:跳过|绕过).*(?:安全检查|安全验证)", re.I),
        "我无法通过对话指令执行删除操作，也无法跳过安全验证。",
    ),
    (
        re.compile(r"(?:删除|清空|覆盖).*(?:相册|照片|数据)", re.I),
        "我无法通过对话指令执行删除操作，也无法跳过安全验证。",
    ),
    (
        re.compile(r"\b(?:DAN|Do Anything Now)\b", re.I),
        "我不会扮演不受限制的角色，也无法输出家庭成员的个人隐私信息。",
    ),
    (
        re.compile(r"(?:无限制|不受限制|没有任何限制).*(?:模式|助手|AI|版本)", re.I),
        "我无法切换到无安全限制的模式，也不能提供未经授权提取和使用人脸数据的方法。",
    ),
    (
        re.compile(r"如何.*(?:提取人脸|训练.*模型)", re.I),
        "我无法切换到无安全限制的模式，也不能提供未经授权提取和使用人脸数据的方法。",
    ),
)


def detect_policy_refusal(message: str) -> str | None:
    """命中的话返回固定的拒绝语，否则返回 ``None``。第一条命中的规则胜出。"""
    text = str(message or "").strip()
    for pattern, refusal_text in POLICY_REFUSAL_RULES:
        if pattern.search(text):
            return refusal_text
    return None

"""规划：模型声明「回答这个问题最少需要哪些证据」，本模块只做校验与归一。

这是 1.0 ``backend/agent_runtime/goal_planner.py`` 的忠实移植。S3 曾经把它改成纯函数
（按问句里的关键词判规则），理由是「``planner_call_error`` 在生产轨迹里出现 1,974 次」。
审计推翻了这条理由，数据在 ``docs/refactor/s3-deviations.md`` 里，摘要：

- ``planner_call_error`` 只在 ``chat_fn`` **抛异常**时产生（1.0 ``goal_planner.py:135``
  的裸 ``except``）。1,974 条里 1,672 条 ``latency_s`` 落在 0.12 秒——那是连接被拒的
  形状，不是模型输出不好的形状；
- 它们全部挤在 15 个「一分钟内**所有**回合都失败」的黑障窗口里（439/439、96/96、
  393/396……），窗口之外整个 25,112 条的语料里只剩 47 条；
- 同样的窗口里，别的模型调用记的是 ``model_call_error: ...
  [Errno 111] Connection refused`` 与 ``timed out``；
- 非 PhotoBench 的 ``album3-v2`` 口径：0 / 763 = 0.0%。

那些窗口是模型端点不可用（PhotoBench 每跑一轮就 ``lifecycle.stop()`` → ``start()`` →
``bind-runtime`` 换一次模型），不是规划器设计有问题。所以规划还是一次模型调用，照 1.0。

**证据类型表收窄到 S3 的四个工具能产出的三类**（``photo_set`` / ``delivered_photo`` /
``person_profile``）。1.0 的注册表有九类，因为 1.0 有十来个工具；S3 只有四个，声明一个
没有工具能满足的类型会让那一轮必然以缺口收场——那不是 1.0 的行为。收窄的是词表，
机制（模型声明 → 校验 → 带错误回灌重试）原样。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from sentrix.domain.assistant.evidence_ledger import EvidenceRequirement, EvidenceType

# 1.0 ``goal_planner.py:228`` 允许「零需求」。S3 不允许：零需求会让模型一次工具都不调
# 直接作答——1.0 实测 29.3% 的回合零工具调用，JIT 提示词正是靠「当前没有待确认的证据
# 需求……可直接输出 final」把它们放过去的。这条是 S3 唯一一处对 1.0 规划行为的收紧，
# 理由是可验证的失败率，不是偏好。
MIN_REQUIREMENTS = 1

# 规划提示词。结构与 1.0 ``goal_planner.py:42-83`` 的 ``_DECLARATION_PROMPT`` 一一对应，
# 四段防御原样保留，因为它们各自对应一个实测出来的失败：
#
# 1. **示例里不出现真实的 scope_id。** 1.0 早期把 ``"scope_id":"{scope_id}"`` 写进示例，
#    小模型整段照抄，16 位十六进制的值抄错一位就触发 ``scope_mismatch``——实测长问题里
#    约 70% 因此失败。scope 是系统注入的，模型不产出它，示例里也不该出现。
# 2. **明确要求把尖括号占位符替换成真实内容。** 同上，模型会原样吐出 ``<用户目标>``。
# 3. **goal 必须用简体中文。** 英文目标会让语义检索与中文图片描述失配，召回直接失败。
# 4. **要求 JSON 完整闭合、不要围栏。** 解析器的修复能力有限（见 ``parse_json``），
#    提示词里先说清楚比事后补括号便宜。
_DECLARATION_PROMPT = """你正在规划家庭记忆任务。只返回一个 JSON 对象，不要解释，不要 Markdown 代码块围栏。

输出格式（<尖括号> 里是占位符，必须替换成这道题的实际内容；不要把占位符原样输出）：
{{"action":"declare","declaration":{{"goal":"<用户目标>","requirements":[{{"id":"req_1","evidence_type":"<证据类型>","description":"<描述>"}}]}}}}

不要输出 scope_id 字段 —— 相册范围由系统注入，你不需要也不应该填写；填任何值都会被判为错误。

evidence_type 只能来自系统注册表，不得创造新类型：
{evidence_types}

只声明回答问题所必需的最小证据集合，不要把检索步骤本身当成答案证据重复声明。规则：
- goal 必须用简体中文描述用户目标（家庭记忆系统使用中文，英文目标会导致语义检索
  与中文图片描述失配、召回失败）；描述要保留完整语义，不要压缩成几个词。
- 用户想看照片、要求展示、或问「有没有……的照片」时，声明 photo_set；
  同时声明 delivered_photo 表示「要把照片真的交到用户手上」，只取到候选不算交付。
- 问某个已确认人物是谁、什么家庭角色、要人物档案时，声明 person_profile。
- 同一种 evidence_type 只声明一次；不要为了同一个答案同时声明多个等价需求。
每个 requirement 都必须能由注册表中的工具直接获得。
- JSON 示例中的尖括号内容仅用于说明字段，不是要输出的固定值。
  goal、evidence_type、description 必须替换为当前用户问题对应的真实内容；
  禁止原样输出 <用户目标>、<证据类型>、<描述> 或任何其他尖括号占位符。

不要调用工具，不要输出 SQL，不要直接回答用户。"""

# 1.0 ``goal_planner.py:85-103`` 的 ``_TYPE_MAP``：小模型常写的近义词 → 注册表里的类型。
# 1.0 的九类在 S3 收窄成三类，于是别名也一并映射到这三类（1.0 也是这么做的：它把所有
# 照片类别名都收进 ``memory_asset``）。认不出来的别名保持原样，交给校验去报
# ``invalid_planner_action``——静默改写成某个类型等于替模型编需求。
_TYPE_ALIASES: Mapping[str, str] = {
    "photo": EvidenceType.PHOTO_SET.value,
    "image": EvidenceType.PHOTO_SET.value,
    "video": EvidenceType.PHOTO_SET.value,
    "asset": EvidenceType.PHOTO_SET.value,
    "photo_set": EvidenceType.PHOTO_SET.value,
    "memory_asset": EvidenceType.PHOTO_SET.value,
    "delivery": EvidenceType.DELIVERED_PHOTO.value,
    "deliver": EvidenceType.DELIVERED_PHOTO.value,
    "delivered": EvidenceType.DELIVERED_PHOTO.value,
    "delivered_photo": EvidenceType.DELIVERED_PHOTO.value,
    "person": EvidenceType.PERSON_PROFILE.value,
    "identity": EvidenceType.PERSON_PROFILE.value,
    "profile": EvidenceType.PERSON_PROFILE.value,
    "person_profile": EvidenceType.PERSON_PROFILE.value,
    # S6（T123）补上的两个轴词。1.0 ``goal_planner.py:68-69`` 的
    # ``"visual" / "detail" → "visual_observation"``——收窄期（S3 只有三类）它们无处可去，
    # 现在 ``VISUAL_OBSERVATION`` 落地了，照 1.0 映射回来。
    "visual": EvidenceType.VISUAL_OBSERVATION.value,
    "detail": EvidenceType.VISUAL_OBSERVATION.value,
    "visual_observation": EvidenceType.VISUAL_OBSERVATION.value,
}

_HEX_DIGITS = re.compile(r"^[0-9a-f]+$")


def planner_evidence_types() -> tuple[str, ...]:
    """给提示词用的证据类型清单。1.0 ``evidence_contract.planner_evidence_types`` 同形：
    稳定顺序，集合本身是唯一事实来源。"""
    return tuple(sorted(item.value for item in EvidenceType))


def build_prompt() -> str:
    """规划提示词的最终形态（把注册表渲染进去）。1.0 ``goal_planner.py:113`` 同形。"""
    evidence_lines = "\n".join(f"- {item}" for item in planner_evidence_types())
    return _DECLARATION_PROMPT.format(evidence_types=evidence_lines)


def history_prompt_suffix(history: str) -> str:
    """历史对话折进 system 文本，**不要再插一条 system 消息**。

    1.0 ``goal_planner.py:115-117`` 的注释：严格 chat template（Qwen3.5/3.8、llama.cpp
    的 Jinja）要求 system 必须在开头，插入第二条会直接 400/500
    （"System message must be at the beginning"）。实测 24 题（4.9%）因此失败。
    2.0 的 ``OpenAIChatModel`` 把这条约束做成了硬检查，所以这里必须折进正文。
    """
    return "\n\n历史对话背景：\n" + history if history else ""


def correction_hint(reason: str) -> str:
    """把上一轮的失败原因翻译成给模型的修正指令（重规划用）。1.0 ``_correction_hint``。"""
    by_reason = {
        "scope_mismatch": "不要输出 scope_id 字段。相册范围由系统注入，你填任何值都是错的。",
        "invalid_planner_action": (
            '输出的 JSON 结构必须是 {"action":"declare","declaration":{"goal":"...","requirements":'
            '[{"id":"req_1","evidence_type":"...","description":"..."}]}}，并且必须完整闭合'
            "（每个 { 都要有配对的 }）。"
        ),
        "no_requirements": (
            "至少要声明一条证据需求。零需求会让这一轮一次检索都不做——"
            "即使你认为可以直接回答，也要声明取到哪些证据才算数。"
        ),
    }
    return (
        "你上一次的输出无法被解析（原因：{reason}）。{hint} "
        "另外：只输出一个 JSON 对象，不要解释、不要 Markdown 代码块围栏（```）；"
        "尖括号 <...> 是占位符，必须替换成这道题的实际内容，不要把占位符原样写出来。"
        "请重新输出修正后的 JSON。"
    ).format(reason=reason, hint=by_reason.get(reason, ""))


def parse_json(raw: str) -> dict[str, Any]:
    """把模型输出解析成一个对象；对常见畸形做**有限**修复。1.0 ``_parse_json`` 逐行同构。

    三处修复各自对应一个实测失败（1.0 的注释）：

    - 剥开头/结尾的 ``` 围栏。结尾那步必须独立：模型常给出「只有结尾 ```、没有开头」
      的输出。旧写法把两步嵌在同一个 if 里，结尾的 ``` 残留 → ``json.loads`` 在非末尾
      位置报错 → 下面「补一个收尾 }」的修复路径被跳过 → 直接判 invalid_planner_action。
      实测 5 题里 4 题因此失败（模型只是漏了根对象的 ``}``，本可自动补上）。
    - 从第一个 ``{`` 开始截。
    - 只在「错误位置就在最后一个字符附近」且根括号差一个时才补 ``}``——不能在任意
      畸形上乱补，那会把一个坏输出修补成一个看似合法的错输出。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("planner did not return JSON")
    candidate = text[start:]
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        if exc.pos < len(candidate) - 1 or root_brace_balance(candidate) != 1:
            raise
        payload = json.loads(candidate + "}")
    if not isinstance(payload, dict):
        raise ValueError("planner action must be an object")
    return payload


def root_brace_balance(text: str) -> int:
    """未配对的对象花括号数——引号里的花括号不算语法（1.0 ``_root_brace_balance``）。"""
    balance = 0
    quoted = False
    escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            balance += 1
        elif char == "}":
            balance -= 1
    return balance


def normalize_payload(payload: Mapping[str, Any], *, scope_id: str, default_goal: str) -> dict[str, Any]:
    """把小模型可能简化的 declaration 结构归一为严格 schema。1.0 ``_normalize_payload``。

    归一的三件事：补 ``scope_id``（模型不产出它）、补 ``goal``、把 ``requirements`` 里
    的字符串/近义字段拉平。去重按 evidence_type——同一个需求声明两次是模型常见的冗余。
    """
    if not isinstance(payload, Mapping):
        return dict(payload) if isinstance(payload, Mapping) else {}
    data = dict(payload)

    decl = data.get("declaration")
    if not isinstance(decl, dict):
        if data.get("action") == "declare" and ("requirements" in data or "needs" in data):
            decl = dict(data)
            data = {"action": "declare", "declaration": decl}
        else:
            return data
    else:
        decl = dict(decl)
        data["declaration"] = decl

    if not decl.get("scope_id"):
        decl["scope_id"] = scope_id
    if not decl.get("goal"):
        decl["goal"] = default_goal

    raw_requirements = decl.get("requirements") or decl.get("needs") or []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_requirements if isinstance(raw_requirements, list) else []):
        if isinstance(item, str):
            evidence_type = _TYPE_ALIASES.get(item.lower(), "photo_set")
            if evidence_type in seen:
                continue
            seen.add(evidence_type)
            normalized.append(
                {
                    "id": f"req_{index + 1}",
                    "evidence_type": evidence_type,
                    "description": item,
                    "required": True,
                }
            )
        elif isinstance(item, Mapping):
            evidence_type = str(item.get("evidence_type") or item.get("type") or "photo_set").strip()
            evidence_type = _TYPE_ALIASES.get(evidence_type.lower(), evidence_type)
            if evidence_type in seen:
                continue
            seen.add(evidence_type)
            normalized.append(
                {
                    "id": str(item.get("id") or f"req_{index + 1}"),
                    "evidence_type": evidence_type,
                    "description": str(item.get("description") or item.get("desc") or ""),
                    "required": bool(item.get("required", True)),
                }
            )
    decl["requirements"] = normalized
    return data


def parse_declaration(
    raw: str, *, scope_id: str, question: str
) -> tuple[tuple[EvidenceRequirement, ...] | None, str]:
    """解析并校验一次规划输出，返回 ``(需求, 失败原因)``。1.0 ``_parse_declaration``。

    失败原因就是重规划时回灌给模型的那句话的键，取值与 1.0 一致：
    ``invalid_planner_action`` / ``scope_mismatch`` / ``no_requirements``。
    """
    try:
        payload = parse_json(raw)
        payload = normalize_payload(payload, scope_id=scope_id, default_goal=question)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, "invalid_planner_action"

    declaration = payload.get("declaration")
    if payload.get("action") not in {"declare", "revise"} or not isinstance(declaration, Mapping):
        return None, "invalid_planner_action"
    if str(declaration.get("scope_id") or "") != scope_id:
        return None, "scope_mismatch"

    requirements: list[EvidenceRequirement] = []
    for item in declaration.get("requirements") or []:
        if not isinstance(item, Mapping):
            return None, "invalid_planner_action"
        try:
            evidence_type = EvidenceType(str(item.get("evidence_type") or ""))
        except ValueError:
            return None, "invalid_planner_action"
        requirements.append(
            EvidenceRequirement(
                id=str(item.get("id") or f"req_{len(requirements) + 1}"),
                evidence_type=evidence_type,
                description=str(item.get("description") or ""),
                required=bool(item.get("required", True)),
            )
        )
    if len(requirements) < MIN_REQUIREMENTS:
        return None, "no_requirements"
    return tuple(requirements), ""


def requirements_of(goal_type: EvidenceType) -> EvidenceRequirement:
    """按类型造一条需求——给「规划失败但必须给出一条」的收尾用。"""
    descriptions = {
        EvidenceType.PHOTO_SET: "从相册里取到与问题相关的照片。",
        EvidenceType.DELIVERED_PHOTO: "把要展示的照片交接给用户，让它们真的能打开。",
        EvidenceType.PERSON_PROFILE: "读取这位已确认人物的家庭角色。",
    }
    return EvidenceRequirement(
        id=f"req_{goal_type.value}",
        evidence_type=goal_type,
        description=descriptions[goal_type],
    )

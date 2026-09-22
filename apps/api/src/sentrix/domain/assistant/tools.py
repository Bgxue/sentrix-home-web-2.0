"""S3 上线的四个工具的契约：名字、描述、参数 schema、能满足哪类证据。

对应 1.0 ``agent_runtime/tool_registry.py`` + ``tools.py`` 的注册段（``tools.py:2681``）。
两处与 1.0 的**结构性差异**：

1. **参数是标准 JSON Schema，走原生 ``tools`` 字段。** 1.0 的 ``ToolSpec.input_schema``
   是「示例默认值」的 dict（``{"query": "", "mode": "best|all|representative"}``），被
   ``json.dumps`` 之后拼进提示词的文本里（``runtime.py:1544``）——模型看到的是一段不像
   schema 的文字，而真正的契约藏在 handler 的手写 ``arguments.get(...)`` 里。
   实测 8100 的 llama.cpp（gemma4 模板）原生支持 ``tools`` / ``finish_reason="tool_calls"``，
   所以 2.0 直接用原生协议：没有 JSON 修复器，没有「输出无法解析」的收尾。
2. **``satisfies`` 是必填的。** 1.0 的 ``produces_evidence`` 只是声明，从没有任何地方
   校验工具真的产出了它声称的证据。2.0 用它驱动账本（见 ``evidence_ledger.py``）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sentrix.domain.assistant.evidence_ledger import EvidenceType


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping[str, Any]
    satisfies: tuple[EvidenceType, ...]
    required: tuple[str, ...] = ()

    def as_openai_tool(self) -> Mapping[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


def _object_schema(properties: Mapping[str, Any], required: Sequence[str]) -> Mapping[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


SEARCH_MEMORIES = ToolSpec(
    name="search_memories",
    description=(
        "按内容词检索家庭照片（人/物/场景/衣着/颜色/地点特征）。"
        "返回一个结果集摘要，其中 preview 是排在最前的几张，每条带 handle 与拍摄时间。"
        "一次对话只需要检索一次；要看更多候选用 get_result_page 翻页，不要改词重搜。"
        "要把照片展示给用户，必须先调 get_original_photos 把 preview 里的 handle 交出去。"
    ),
    parameters=_object_schema(
        {
            "query": {
                "type": "string",
                "description": "检索用的内容词，例如 海边、桥、猫、红色。用用户问题里的实词，不要写整句。",
            },
            "limit": {
                "type": "integer",
                "description": "结果集最多容纳多少张候选，默认 12。",
                "minimum": 1,
                "maximum": 12,
            },
        },
        ["query"],
    ),
    satisfies=(EvidenceType.PHOTO_SET,),
    required=("query",),
)

GET_RESULT_PAGE = ToolSpec(
    name="get_result_page",
    description=(
        "查看指定结果集的某一页（每页最多 6 张，page 从 1 开始）。"
        "result_set_id 用 search_memories 返回的那个；要换检索条件请重新调用 search_memories。"
    ),
    parameters=_object_schema(
        {
            "result_set_id": {"type": "string", "description": "search_memories 返回的结果集 id。"},
            "page": {"type": "integer", "description": "页码，从 1 开始。", "minimum": 1},
        },
        ["result_set_id", "page"],
    ),
    satisfies=(EvidenceType.PHOTO_SET,),
    required=("result_set_id", "page"),
)

GET_ORIGINAL_PHOTOS = ToolSpec(
    name="get_original_photos",
    description=(
        "把选中的照片交付给用户查看。handles 必须来自当前结果集的 preview（例如 photo_1）。"
        "用户说「给我看看…」时必须在给出最终回答之前调用本工具，否则用户看不到任何照片。"
    ),
    parameters=_object_schema(
        {
            "result_set_id": {"type": "string", "description": "search_memories 返回的结果集 id。"},
            "handles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要交付的 handle 列表，最多 6 个，必须来自该结果集的 preview。",
                "minItems": 1,
                "maxItems": 6,
            },
        },
        ["result_set_id", "handles"],
    ),
    satisfies=(EvidenceType.DELIVERED_PHOTO,),
    required=("result_set_id", "handles"),
)

GET_PERSON_PROFILE = ToolSpec(
    name="get_person_profile",
    description=(
        "读取一位**已确认人物**的家庭角色。2.0 目前只有这一项人物数据；"
        "人物关系、常去地点、性格等尚未建立，问到时如实说明数据不足，不要编造。"
    ),
    parameters=_object_schema(
        {"person": {"type": "string", "description": "人物名字，例如 妈妈、浦思航。"}},
        ["person"],
    ),
    satisfies=(EvidenceType.PERSON_PROFILE,),
    required=("person",),
)

TOOL_SPECS: tuple[ToolSpec, ...] = (
    SEARCH_MEMORIES,
    GET_RESULT_PAGE,
    GET_ORIGINAL_PHOTOS,
    GET_PERSON_PROFILE,
)

TOOLS_BY_NAME: Mapping[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}

# 能产出一组照片的检索工具。门禁用它判断「有没有检索过」。
RETRIEVAL_TOOLS = frozenset({"search_memories", "get_result_page"})


def openai_tools(specs: Sequence[ToolSpec] = TOOL_SPECS) -> tuple[Mapping[str, Any], ...]:
    return tuple(spec.as_openai_tool() for spec in specs)

"""工具契约：名字、描述、参数 schema、能满足哪类证据、贵不贵（``cost_class``）。

S3 上线四个，S6 加了 ``query_memory_facts``（T121）、``query_photo_people``（T122）
与 ``inspect_photo``（T123），现在六个。

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
from enum import StrEnum
from typing import Any

from sentrix.domain.assistant.evidence_ledger import EvidenceType


class CostClass(StrEnum):
    """一次工具调用有多贵。**它不是一个标签，是一条预算的开关。**

    1.0 用的是裸字符串（``cost_class="expensive"``，``tools.py:2755`` 与 ``:2769``），
    而它唯一的用途是喂给 ``BudgetManager``：

        self.budget.can_tool_call(inspection=spec.cost_class == "expensive")   # tool_policy.py:38
        self.budget.record_tool_call(inspection=spec.cost_class == "expensive")  # :62

    而 ``BudgetState.max_inspections = 1``（``budget_manager.py:17``）——所以
    ``"expensive"`` 的实际含义是「**它算一次精读，而一轮总共只准精读一次**」。
    2.0 收成枚举（同 ``EvidenceType`` 的理由）：拼错的 ``"expensiv"`` 在 1.0 里会
    静默退化成``cheap``、那条预算永不触发，而这里会当场报错。
    """

    CHEAP = "cheap"
    EXPENSIVE = "expensive"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping[str, Any]
    satisfies: tuple[EvidenceType, ...]
    required: tuple[str, ...] = ()
    # 默认便宜：1.0 的 ``ToolSpec`` 默认值就是 ``"cheap"``，只有两个视觉工具是昂贵的。
    cost_class: CostClass = CostClass.CHEAP

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

QUERY_MEMORY_FACTS = ToolSpec(
    name="query_memory_facts",
    description=(
        "精确统计当前相册，答案是一个确定的数字或时间，不经过相似度检索。"
        "能回答：有多少张原始照片/视频、某个已确认人物出现在多少张照片里、"
        "最早或最近一次拍摄是什么时候、按月/地点/媒体/处理状态分组各有多少。"
        "颜色/物体/场景/活动/关系/金额/桌数这类条件本工具答不了，不要拿它硬套；"
        "也不要把 search_memories 返回的候选数当成统计答案——那个数是候选池的大小，"
        "不是总数。当前相册范围由系统注入。"
    ),
    parameters=_object_schema(
        {
            "operation": {
                "type": "string",
                "enum": ["count", "exists", "first", "last", "group"],
                "description": "count 数量；exists 有没有；first 最早一次；last 最近一次；group 分组计数。",
            },
            "subject": {
                "type": "string",
                "enum": ["asset", "person_appearance", "processing"],
                "description": (
                    "统计什么：asset 原始照片/视频；"
                    "person_appearance 某位已确认人物出现过的照片；processing 导入处理状态。"
                ),
            },
            "group_by": {
                "type": "string",
                "enum": ["month", "place", "media", "status"],
                "description": "只在 operation=group 时需要。status 只能配 subject=processing。",
            },
            "filters": _object_schema(
                {
                    "time": {
                        "type": "string",
                        "description": (
                            "时间说法，例如 去年、2023年7月、前年夏天。"
                            "相对时间原样填，由系统换算，不要自己算年份。"
                        ),
                    },
                    "media": {
                        "type": "string",
                        "enum": ["image", "video"],
                        "description": "只统计照片或只统计视频；不区分就省略。",
                    },
                    "place": {
                        "type": "string",
                        "description": (
                            "结构化地名（城市/区县/景区/地标），例如 杭州、邓村。"
                            "活动、主题、目标不要填进来，不确定就留空。"
                        ),
                    },
                    "person": {
                        "type": "string",
                        "description": "用户明确提到的已确认人名。角色词（伴娘/兄弟/亲戚）不要填。",
                    },
                },
                [],
            ),
        },
        ["operation", "subject"],
    ),
    satisfies=(EvidenceType.STRUCTURED_FACT,),
    required=("operation", "subject"),
)


QUERY_PHOTO_PEOPLE = ToolSpec(
    name="query_photo_people",
    description=(
        "读取一张已检索照片自己的已确认人物和未确认同行者。"
        "asset_handle 必须来自当前 search_memories preview；不会把其他候选照片的人名移植过来。"
        "未确认的人**没有名字**，只报个数，不要给他们安名字。"
        # 1.0 这里逐个人回的是 `get_effective_family_membership`（家庭图谱，S7）。
        # 2.0 没有图谱，只有实体上的 family_role——描述里如实说清楚，
        # 而不是让模型把 family_role 当成「这一家的人」去用。
        "membership 一栏来自人物实体上的家庭角色，**尚无关系图谱**，"
        "不要据此推断两个人之间的亲属关系。"
    ),
    parameters=_object_schema(
        {
            "result_set_id": {
                "type": "string",
                "description": "search_memories 或 get_result_page 返回的结果集 id。",
            },
            "asset_handle": {
                "type": "string",
                "description": "要查的那张照片的 handle（photo_1…），必须来自当前结果集的 preview。",
            },
        },
        ["result_set_id", "asset_handle"],
    ),
    # 两条都声明，理由见 ``evidence_ledger.EvidenceType.PHOTO_IDENTITY`` 的注释：
    # ``PHOTO_IDENTITY`` 是本工具自己的产物，``PERSON_PROFILE`` 是 1.0 那条
    # ``confirmed_identity`` 在 2.0 的名字——只声明前者的话，「问照片里的人是谁」
    # 这类需求在本工具被调用之后依然开着，门禁会逼模型再去找一个查不到的东西。
    satisfies=(EvidenceType.PHOTO_IDENTITY, EvidenceType.PERSON_PROFILE),
    required=("result_set_id", "asset_handle"),
)


INSPECT_PHOTO = ToolSpec(
    name="inspect_photo",
    description=(
        "复核已检索照片的视觉细节（物体/衣着/颜色/场景）。"
        "asset_handle 使用 search_memories preview 里的 handle（photo_1…），可省略（默认用预览第一张）。"
        "当前 handle 不含目标时换下一张（photo_2、photo_3…）复核，同一张图最多复核一次，不要反复检查同一张。"
        "昂贵，默认每轮最多 1 次。"
    ),
    parameters=_object_schema(
        {
            "result_set_id": {
                "type": "string",
                "description": "search_memories 或 get_result_page 返回的结果集 id。",
            },
            "question": {
                "type": "string",
                "description": "要用这张图回答的问题，用用户问题里的实词，不要改写成别的意思。",
            },
            "asset_handle": {
                "type": "string",
                "description": "要复核的那张照片的 handle（photo_1…）。省略则用结果集第一张。",
            },
            "target_person": {
                "type": "string",
                "description": "问题只针对某一位已确认人物时填他的名字；不针对某个人就省略。",
            },
        },
        ["result_set_id", "question"],
    ),
    # 1.0 ``tools.py:2756`` 的两条。``visual_observation`` 是「看细节」，
    # ``photo_identity`` 是「这是谁」——两个不同的需求都可能被这一次调用满足。
    satisfies=(EvidenceType.VISUAL_OBSERVATION, EvidenceType.PHOTO_IDENTITY),
    # 1.0 的 ``required_inputs=("asset_handle", "question")`` 与它自己的描述
    # （「asset_handle …可省略」）**互相矛盾**。2.0 保留描述里那条行为（省略就用第一张），
    # 所以这里只把 ``question`` 与 ``result_set_id`` 列为必填——照 required_inputs 抄会
    # 让「可省略」当场失效（策略层会以 ``missing_argument`` 拒掉）。
    required=("result_set_id", "question"),
    cost_class=CostClass.EXPENSIVE,
)


TOOL_SPECS: tuple[ToolSpec, ...] = (
    SEARCH_MEMORIES,
    QUERY_MEMORY_FACTS,
    QUERY_PHOTO_PEOPLE,
    INSPECT_PHOTO,
    GET_RESULT_PAGE,
    GET_ORIGINAL_PHOTOS,
    GET_PERSON_PROFILE,
)

TOOLS_BY_NAME: Mapping[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}

# 能产出一组照片的检索工具。门禁用它判断「有没有检索过」。
RETRIEVAL_TOOLS = frozenset({"search_memories", "get_result_page"})


def openai_tools(specs: Sequence[ToolSpec] = TOOL_SPECS) -> tuple[Mapping[str, Any], ...]:
    return tuple(spec.as_openai_tool() for spec in specs)

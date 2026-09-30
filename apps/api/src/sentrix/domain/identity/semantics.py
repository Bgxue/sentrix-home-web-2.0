"""语义实体分组的**纯规则**（S6 · T129）。

对应 1.0 ``db.py:4144-4165`` 的 ``_semantic_entity_key`` 与 ``db.py:203-247`` 的三张词表。
分组本身（遍历、聚合、排序）在 ``application/identity/read_entity_groups.py``；
这里只做「一个实体归到哪个语义概念上」这一件事，因为它可以穷举。

**四级判定按顺序短路**，策略名是 1.0 的字面量、也是响应里 ``rationale.strategy`` 的取值：

1. ``semantic_primary`` —— 实体属性 ``semantic_primary`` 有值且类型属于 {place, object,
   atmosphere} 时，直接用那个值；
2. ``controlled_equivalence`` —— 查 ``SEMANTIC_ENTITY_EQUIVALENTS[类型]``，先整串相等、
   再子串命中；
3. ``semantic_concept`` —— place 查 ``SEMANTIC_PLACE_CONCEPTS``、object 查
   ``SEMANTIC_OBJECT_CONCEPTS``，子串命中即取归一化词；
4. ``exact_label`` —— 兜底：去空白后的 ``canonical_name``。

**一处按 T127 的记录留白**：``SEMANTIC_ENTITY_EQUIVALENTS`` 在 1.0 里还有一个
``"emotion": MOOD_NORMALIZATION`` 的键，而 ``MOOD_NORMALIZATION`` 与 ``atmosphere``
那一类在 2.0 **没有来源**（``domain/identity/extraction.py`` 的模块 docstring 已经把
这件事记下了，T127 的抽取也因此不建 atmosphere 实体）。这里的词表就**不放 emotion 键**：
放一个永远取不到的词表比不放更像「有这个东西」。

**两处顺序不能反**：``semantic_primary`` 只在三种类型上生效（person 与 time 有该属性也
不用它）；``controlled_equivalence`` 的「整串相等」要排在「子串命中」前面，否则
「湖边栈道」会先被子串规则吃掉，而 1.0 是整串匹配优先。
"""

from __future__ import annotations

from dataclasses import dataclass

from sentrix.domain.identity.models import (
    ENTITY_TYPE_ATMOSPHERE,
    ENTITY_TYPE_EMOTION,
    ENTITY_TYPE_OBJECT,
    ENTITY_TYPE_PLACE,
)

# ---- 取值常量（1.0 的字面量，逐字）------------------------------------------------

STRATEGY_SEMANTIC_PRIMARY = "semantic_primary"
STRATEGY_CONTROLLED_EQUIVALENCE = "controlled_equivalence"
STRATEGY_SEMANTIC_CONCEPT = "semantic_concept"
STRATEGY_EXACT_LABEL = "exact_label"

# 类型名从 ``models`` 来，**不在这里另写一份**——它们同时出现在落库的列与别处的判据里，
# 两处各写一个字面量，改一处就是一条静默的空结果。

# 1.0 ``db.py:4199``：**组的 key** 把 emotion 当成 atmosphere。注意它只发生在这一处——
# 传给 ``semantic_entity_key`` 的仍然是原始的 ``"emotion"``（1.0 就是这样，两处别弄反）。
EMOTION_AS_ATMOSPHERE = ENTITY_TYPE_ATMOSPHERE

# ``semantic_primary`` 生效的类型（1.0 ``db.py:4152`` 的字面量集合）。
PRIMARY_TYPES = frozenset({ENTITY_TYPE_PLACE, ENTITY_TYPE_OBJECT, ENTITY_TYPE_ATMOSPHERE})

# ---- 词表（1.0 ``db.py:214-247``，逐字）------------------------------------------

# 受控等价：整串相等或子串命中。**只有 place 一支**——emotion 那一支见模块 docstring。
SEMANTIC_ENTITY_EQUIVALENTS: dict[str, dict[str, str]] = {
    "place": {
        "湖边": "滨水区域",
        "水边": "滨水区域",
        "河边": "滨水区域",
        "河岸": "滨水区域",
        "湖畔": "滨水区域",
        "博物馆": "展览空间",
        "展厅": "展览空间",
        "展览厅": "展览空间",
        "展览馆": "展览空间",
        "美术馆": "展览空间",
        "画廊": "展览空间",
    },
}

# 概念归一：(归一化词, 子串词表)。**顺序有语义**——先命中的赢（1.0 就是按这个顺序遍历）。
SEMANTIC_PLACE_CONCEPTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("滨水空间", ("湖", "河", "海", "港", "码头", "水域", "水边", "水池", "滨水")),
    ("展览空间", ("博物馆", "展厅", "展览", "美术馆", "画廊", "展柜", "科普馆")),
    ("餐饮空间", ("餐厅", "餐馆", "餐饮", "厨房", "餐桌", "咖啡", "烘焙", "快餐", "茶室")),
    ("园林与公园", ("园林", "公园", "花园", "植物园", "温室", "果园", "庭院")),
    ("商业空间", ("商店", "商场", "购物", "店铺", "超市", "摊位")),
    ("交通出行空间", ("机场", "地铁", "车厢", "停机坪", "登机桥", "车站", "高架", "公路")),
    ("演出活动空间", ("剧场", "舞台", "演艺", "活动现场")),
    ("居住室内空间", ("卧室", "客厅", "室内", "房间", "走廊", "门口", "室内环境")),
    ("户外公共空间", ("广场", "街道", "户外", "室外", "景区", "道路", "观景")),
)

SEMANTIC_OBJECT_CONCEPTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("手机与移动设备", ("手机", "平板", "相机")),
    ("展示与标识", ("海报", "横幅", "宣传", "标牌", "展板", "告示", "路标", "灯牌", "菜单")),
    ("餐具与容器", ("碗", "杯", "盘", "勺", "叉", "筷", "锅", "茶具", "餐具", "容器")),
    (
        "食物与饮品",
        ("蛋糕", "甜点", "饮料", "面条", "沙拉", "肉", "虾", "玉米", "番茄", "薯条", "牛角", "饭", "菜"),
    ),
    ("花卉与植物", ("花", "树", "草", "绿植", "盆栽", "棕榈", "樱花", "梅花")),
    (
        "建筑与工程",
        ("建筑", "房屋", "大坝", "高塔", "电线杆", "厂房", "机械", "起重机", "桥", "围墙"),
    ),
    ("围栏与公共设施", ("围栏", "栏杆", "护栏", "路灯", "扶手", "井盖")),
    ("交通工具", ("汽车", "车辆", "自行车", "摩托", "飞机", "船", "轮渡", "公交", "三轮车")),
    ("服饰与配件", ("背包", "项链", "戒指", "眼镜", "手表", "鞋", "钥匙", "项圈")),
    ("毛绒与玩具", ("毛绒", "玩偶", "气球")),
)


@dataclass(frozen=True, slots=True)
class SemanticKey:
    """一个实体归到哪个语义概念上，以及**为什么**。

    ``strategy`` 与 ``matched_label`` 是 1.0 ``rationale`` 的那两个键——它们不是调试
    信息：``matched_label`` 要进 ``source_labels`` 之外的说明，前端拿它解释「为什么
    这几张卡被并成一组」。
    """

    label: str
    strategy: str
    matched_label: str


def group_entity_type(entity_type: str) -> str:
    """**组的 key** 用的类型名。1.0 ``db.py:4199``：emotion 当成 atmosphere，其余原样。"""
    return EMOTION_AS_ATMOSPHERE if entity_type == ENTITY_TYPE_EMOTION else entity_type


def _concepts_for(entity_type: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if entity_type == ENTITY_TYPE_PLACE:
        return SEMANTIC_PLACE_CONCEPTS
    if entity_type == ENTITY_TYPE_OBJECT:
        return SEMANTIC_OBJECT_CONCEPTS
    return ()


def semantic_entity_key(*, entity_type: str, name: str, semantic_primary: str | None = None) -> SemanticKey:
    """一个实体 → 它的语义概念。1.0 ``db.py:4144-4165``，四级短路。

    ``semantic_primary`` 由调用方取好传进来（1.0 是就地查属性表）——**判断与取数分开**，
    这样四级顺序可以穷举，而不用造库来验。
    """
    label = "".join(str(name or "").split())
    if semantic_primary and entity_type in PRIMARY_TYPES:
        return SemanticKey(
            label=str(semantic_primary),
            strategy=STRATEGY_SEMANTIC_PRIMARY,
            matched_label=str(semantic_primary),
        )
    equivalents = SEMANTIC_ENTITY_EQUIVALENTS.get(entity_type, {})
    # **整串相等排在子串命中前面。** 反过来的话「湖边栈道」会被 `湖边` 这条子串先吃掉，
    # 而 1.0 是整串匹配优先。
    if label in equivalents:
        return SemanticKey(
            label=equivalents[label],
            strategy=STRATEGY_CONTROLLED_EQUIVALENCE,
            matched_label=label,
        )
    for term, normalized in equivalents.items():
        if term in label:
            return SemanticKey(
                label=normalized,
                strategy=STRATEGY_CONTROLLED_EQUIVALENCE,
                matched_label=term,
            )
    for normalized, terms in _concepts_for(entity_type):
        matched = [term for term in terms if term in label]
        if matched:
            return SemanticKey(
                label=normalized,
                strategy=STRATEGY_SEMANTIC_CONCEPT,
                matched_label=matched[0],
            )
    return SemanticKey(label=label, strategy=STRATEGY_EXACT_LABEL, matched_label=label)

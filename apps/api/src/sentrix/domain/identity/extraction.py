"""从一次观察里抽出**非人物实体**的候选：纯规则，零 IO。

对应 1.0 ``db.py`` 的 ``maintain_observation_entities``（``:3755-3954``）里**只做决定**
的那一半——``time_semantics``（``:254``）、``object_category``（``:272``）、
``OBJECT_CATEGORIES``（``:168-175``），以及 ``:3798-3805`` 那张 ``values`` 表。
建实体、写属性、挂关联那三件在 ``application/media/maintain_entities.py``。

**四类实体里这里只产出三类，另一类是有意留白的：**

| entity_type | 1.0 的来源 | 这里 |
|---|---|---|
| ``place`` | 语义词表的 ``place.primary``，**退到** ``observation.place`` 自由文本 | ✅ 用回退那一支 |
| ``object`` | 语义词表的 ``objects[].label``，**退到** ``observation.objects`` | ✅ 用回退那一支 |
| ``time`` | ``captured_at`` 派生 | ✅ |
| ``atmosphere`` | 语义词表的 ``atmosphere.labels``，退到 ``emotions`` | ❌ **两个来源 2.0 都没有** |

``atmosphere`` 缺的是**受控词表那一族**（``semantic`` 的 ``atmosphere`` 与
``emotions`` / ``MOOD_NORMALIZATION``）——``vlm_observer.py`` 的模块注释写着那个词表
「是 S6 的精读」，而 S4 明确没搬 ``detail`` 族里带它的部分。**先不建这一类**：
按 1.0 的形状，词表不可用时那一类的名字列表本来就是空的，所以「不建」正是 1.0
在同样输入下的行为，不是一处省略。
"""

from __future__ import annotations

from datetime import datetime

from sentrix.domain.identity.models import EntitySpec

# 1.0 ``db.py:168-175`` 的六个类别，逐字。它是 ``object_category`` 的全部依据——
# 一份受控词表而不是「智能判断」，所以照抄、不做同义扩展：扩一个词就是扩一类实体的归类。
OBJECT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "食物": ("蛋糕", "餐", "饭", "水果", "面包", "饮料", "咖啡", "茶", "酒"),
    "交通工具": ("自行车", "汽车", "车辆", "火车", "飞机", "摩托", "轮船", "船"),
    "宠物": ("猫", "狗", "鸟", "兔", "宠物"),
    "电子设备": ("手机", "相机", "电脑", "屏幕", "耳机", "麦克风", "电视"),
    "文具与出版物": ("书", "笔", "证书", "毕业证", "杂志"),
    "服饰": ("衣", "帽", "鞋", "包", "眼镜", "领带"),
}

# 实体类型 → 它在 ``event_entities`` 上的关系名。1.0 ``db.py:3935`` 那张 dict 的四条
# （``emotion`` 那条这里没有：见模块 docstring 的留白）。
EVENT_RELATIONS: dict[str, str] = {
    "place": "地点",
    "time": "时间",
    "object": "包含物件",
    "atmosphere": "画面氛围",
}

# 1.0 ``db.py:3798-3805`` 那张表里的三段摘要文案，逐字。
PLACE_SUMMARY = "由图片观察或采集地点维护"
OBJECT_SUMMARY = "由图片观察到的物体"
TIME_SUMMARY = "由原始拍摄时间维护"

# 语义词表不可用时地点的兜底名。1.0 ``:3789`` 的 ``place_name = selected_scene or
# visual_place or "其他或不确定"``——**没有地点信息的照片会共用这一个实体**，
# 那是 1.0 的行为，照抄。
UNKNOWN_PLACE = "其他或不确定"


def time_semantics(value: datetime) -> dict[str, object]:
    """1.0 ``db.py:254-269`` 逐字：时刻 → 六个确定的日历标签。

    ``part_of_day`` 的六段与 ``season`` 的四段都是**边界值**，改一个数字就是改产品行为：
    五点前算凌晨、八点前算清晨、十二点前算上午、十七点前算午后、二十点前算傍晚，
    其余夜晚；3/4/5 春、6/7/8 夏、9/10/11 秋、其余冬。
    """
    if value.hour < 5:
        part_of_day = "凌晨"
    elif value.hour < 8:
        part_of_day = "清晨"
    elif value.hour < 12:
        part_of_day = "上午"
    elif value.hour < 17:
        part_of_day = "午后"
    elif value.hour < 20:
        part_of_day = "傍晚"
    else:
        part_of_day = "夜晚"
    if value.month in (3, 4, 5):
        season = "春"
    elif value.month in (6, 7, 8):
        season = "夏"
    elif value.month in (9, 10, 11):
        season = "秋"
    else:
        season = "冬"
    return {
        "date": value.date().isoformat(),
        "year": value.year,
        "month": value.month,
        "season": season,
        "part_of_day": part_of_day,
    }


def object_category(label: str) -> str:
    """1.0 ``db.py:272-277``：按 ``OBJECT_CATEGORIES`` 做**子串**匹配，命中不了给「其他」。

    子串而不是等值是有意的：名字是模型写的自由文本（「生日蛋糕」「一只橘猫」），
    等值匹配永远命中不了。**六个类别的先后顺序是判据的一部分**——一个词同时出现在两个
    类别里时取先出现的那个（1.0 的 ``for category, terms in ...`` 就是这个语义）。
    """
    text = str(label or "").strip()
    for category, terms in OBJECT_CATEGORIES.items():
        if any(term in text for term in terms):
            return category
    return "其他"


def build_entity_specs(
    *, place: str, objects: tuple[str, ...], captured_at: datetime | None
) -> tuple[EntitySpec, ...]:
    """一次观察 → 要建的实体清单。1.0 ``db.py:3798-3811`` 的 ``values`` + ``specs``。

    四类里产出三类（``atmosphere`` 见模块 docstring）。``time`` 那一类在 1.0 的写法是
    ``("time", captured_day, ...) if captured_day else ("time", [], "")``——**没有拍摄
    时间就产出一个空名字列表、等于不建**，这里直接跳过，形状等价。
    """
    specs: list[EntitySpec] = [
        # 1.0 ``:3789`` 的回退链 ``selected_scene or visual_place or "其他或不确定"``：
        # 2.0 没有语义词表那一支，所以 ``place`` 为空时就是那个兜底名。
        EntitySpec("place", place.strip() or UNKNOWN_PLACE, PLACE_SUMMARY),
    ]
    specs.extend(EntitySpec("object", name, OBJECT_SUMMARY) for name in objects if name)
    if captured_at is not None:
        specs.append(EntitySpec("time", captured_at.date().isoformat(), TIME_SUMMARY))
    return tuple(specs)

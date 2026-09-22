"""地点条件 vs 解析结果的三条确定性匹配规则。纯函数，零 IO。

对应 1.0 ``backend/geocoding.py:269-308`` 的 ``place_text_matches``。规则一条没改，
改的只是入参形态：1.0 吃的是那个扁平 dict，2.0 吃 ``Place``。

1.0 的三条规则（原文照抄）：
1) 约束值整串出现在 geocode label/name/行政区文本里；
2) 存储的行政区（省/市/区/县，去后缀后）出现在约束值里（'秦皇岛' 匹配
   '秦皇岛如是海度假村'）；
3) 中英别名命中（'清迈' 匹配 'Chiang Mai'）。

第 2 条是关键：它让「用户说了一个比区县更细的地名」也能匹配上——
``PLACE_ADMIN_SUFFIXES`` 归一后做**双向**子串，而不是相等。
"""

from __future__ import annotations

from sentrix.domain.places.aliases import PlaceAliases
from sentrix.domain.places.models import Place

# 1.0 参与第 2 条规则的槽位。``country`` 不在其中：拿国名去匹配会命中一切。
_ADMIN_KEYS = ("province", "city", "district")


def place_label(place: Place) -> str:
    """参与第 1 条规则的整串。与 1.0 的 ``label`` 拼接范围一致（含国名）。"""
    parts = [
        place.admin.province,
        place.admin.city,
        place.admin.district,
        place.nearest.township,
        place.nearest.locality,
    ]
    return " ".join(part for part in parts if part)


def place_text_matches(value: str, place: Place | None, aliases: PlaceAliases) -> bool:
    """地点约束 ``value`` 是否被这次解析结果支持。

    ``place`` 为空一律返回 False：1.0 在这一步返回 False 之后，调用方
    (``MetadataRetriever``) 把「没有 geocode」当成「预筛通过」放行，于是一个纯地点
    查询会把整个 scope 灌进 metadata 通道。2.0 把「没解析出地点」与「地点不匹配」
    分开表达——前者由调用方显式决定要不要放行，不再藏在这个布尔值里。
    """
    text = str(value or "").strip()
    if not text or place is None:
        return False

    label = place_label(place)
    if not label:
        return False
    if text in label:
        return True

    for key in _ADMIN_KEYS:
        part = aliases.strip_admin_suffix(getattr(place.admin, key, ""))
        if len(part) >= 2 and part in text:
            return True

    lower_label = label.lower()
    return any(alias.lower() in lower_label for alias in aliases.names_for(text))

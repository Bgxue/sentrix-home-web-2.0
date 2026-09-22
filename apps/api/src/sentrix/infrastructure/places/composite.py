"""离线地点解析：PyGeoCN（面） + GeoNames（点）合成一个 ``Place``。

**与 1.0 的分支结构不同，但这是设计文档 §3.2 要求的差别的直接后果。**

1.0 的 ``OfflineReverseGeocoder.lookup()``（``geocoding.py:196-216``）是**二选一**：

    result = self._lookup_pygeo(lat, lon)      # 国内
    if result:
        return result
    result = self._lookup_reverse_geocoder(...)  # 国外
    if result:
        return result
    return {}

先到先得，后者根本不会被调用。那是对的——1.0 只有一个 ``label`` 槽位，两个来源写进去
会互相盖掉。

2.0 有 ``admin`` 与 ``nearest`` 两个**语义不同、不可互换**的槽位（设计 §3.2），所以两个
来源是**互补**而不是互斥的，必须都问一遍，才能拼出设计给的展示形式
``浙江省杭州市萧山区 · 赵州镇大石桥村附近``——前半段来自 PyGeoCN，后半段来自 GeoNames。

置信度沿用 1.0 的两个常数：PyGeoCN 0.90、GeoNames 0.70（``geocoding.py:134`` / ``:190``）。
两个来源都在时取 PyGeoCN 的——``admin`` 是权威归属那一段。
"""

from __future__ import annotations

from sentrix.domain.media.models import GpsPoint
from sentrix.domain.places.models import (
    AdminArea,
    NearestPlace,
    Place,
    PlacePrecision,
    PlaceSource,
)
from sentrix.domain.places.ports import AdminResolver, NearestResolver

PYGEOCN_CONFIDENCE = 0.90
GEONAMES_CONFIDENCE = 0.70


def _precision_for(admin: AdminArea, nearest: NearestPlace) -> PlacePrecision:
    """这次解析到达了哪一级。

    顺序是**权威优先、再谈精细**：有区县归属时先说区县（那是唯一能做硬条件过滤的
    一级），没有区县才按最近邻点报乡镇/村庄，最后兜市/省级。
    """
    if admin.district:
        return PlacePrecision.DISTRICT
    if nearest.locality:
        return PlacePrecision.LOCALITY
    if nearest.township:
        return PlacePrecision.TOWNSHIP
    return PlacePrecision.CITY


class OfflinePlaceResolver:
    """实现 ``PlaceResolver`` 端口。纯离线，运行时不碰网络。"""

    def __init__(self, *, admin: AdminResolver, nearest: NearestResolver) -> None:
        self._admin = admin
        self._nearest = nearest

    def resolve(self, gps: GpsPoint) -> Place | None:
        admin = self._admin.resolve_admin(gps)
        nearest = self._nearest.resolve_nearest(gps)
        if admin is None and nearest is None:
            return None
        resolved_admin = admin or AdminArea()
        resolved_nearest = nearest or NearestPlace()
        from_pygeocn = admin is not None
        return Place(
            admin=resolved_admin,
            nearest=resolved_nearest,
            precision=_precision_for(resolved_admin, resolved_nearest),
            source=PlaceSource.PYGEOCN if from_pygeocn else PlaceSource.GEONAMES,
            gps=gps,
            confidence=PYGEOCN_CONFIDENCE if from_pygeocn else GEONAMES_CONFIDENCE,
        )

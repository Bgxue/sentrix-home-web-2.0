"""地点解析端口。domain 只声明「要什么」，怎么拿到由 infrastructure 决定。

拆成两个端口而不是一个，是因为两个数据源的**性质不同**、也必须能独立缺席：
``AdminResolver`` 是面（行政区边界，权威归属），``NearestResolver`` 是点（最近邻，
只能说附近）。1.0 把两者塞进同一个 ``lookup()`` 的返回值里，谁缺席都只能靠字段空不空
去猜。分开之后「PyGeoCN 装了但 GeoNames 数据没下」这件事在类型上就看得见。

``PlaceResolver`` 是组合后的成品：喂一个 GPS，出一个 ``Place``。解析不出来返回
``None``——不是返回一个两个槽位都空的 ``Place``（那个构造会直接抛）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from sentrix.domain.media.models import Asset, GpsPoint
from sentrix.domain.places.models import AdminArea, NearestPlace, Place


@runtime_checkable
class AdminResolver(Protocol):
    """按坐标解析行政区（面）。数据源缺席时返回 ``None``，不抛异常。"""

    def resolve_admin(self, gps: GpsPoint) -> AdminArea | None: ...

    @property
    def available(self) -> bool: ...


@runtime_checkable
class NearestResolver(Protocol):
    """按坐标解析最近邻地点（点）。数据源缺席时返回 ``None``，不抛异常。"""

    def resolve_nearest(self, gps: GpsPoint) -> NearestPlace | None: ...

    @property
    def available(self) -> bool: ...


class AssetPlaceRepository(Protocol):
    """``asset_places`` 的读写。

    ``save`` 是幂等的：同一个 asset 重复解析只覆盖那一行，不追加——地点是 asset 的
    **属性**而不是事件流，重复解析（换数据源、补下 GeoNames）必须能就地纠正。
    """

    def save(self, *, asset_id: str, scope_id: str, gps: GpsPoint | None, place: Place) -> None: ...

    def get(self, asset_id: str) -> Place | None: ...

    def get_many(self, asset_ids: Sequence[str]) -> dict[str, Place]: ...

    def list_scope(self, scope_id: str, *, limit: int) -> dict[str, Place]: ...

    def count(self, scope_id: str | None = None) -> int: ...


class AssetPlaceIndex(Protocol):
    """把一张 Asset 的 GPS 变成 ``asset_places`` 里的一行。

    与 ``AssetVisualIndexer`` 同构：导入用例依赖这个抽象，不依赖具体解析器。
    """

    def index(self, asset: Asset) -> bool: ...


@runtime_checkable
class PlaceResolver(Protocol):
    """GPS → ``Place``。纯离线，运行时零网络依赖。

    实现必须**逐条**解析：整册导入时每条 Observation 都会走一次，数据源不可用时
    要快速返回 ``None`` 而不是每条都去重试一次加载。
    """

    def resolve(self, gps: GpsPoint) -> Place | None: ...

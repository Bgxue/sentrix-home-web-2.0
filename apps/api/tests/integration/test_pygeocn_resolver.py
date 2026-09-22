"""PyGeoCN 面解析适配器：真数据、真反编码。

PyGeoCN 内置 16 MB 天地图边界 GeoJSON，随包发布，运行时零网络。单次反编码约 0.30 秒
（每次调用都 ``geopandas.read_file`` 三份 GeoJSON），所以这里只取两个坐标。

**用真数据而不是合成数据**，因为这个适配器没有可注入的数据源：它要验的正是「PyGeoCN
在这个坐标上真的给出省/市/区县三级」。合成数据只能验我们自己的分支，验不了这条。
"""

from __future__ import annotations

import pytest

from sentrix.domain.media.models import GpsPoint
from sentrix.infrastructure.places.pygeocn import PYGEOCN_CONFIDENCE, PyGeoCNAdminResolver


@pytest.fixture(scope="module")
def resolver() -> PyGeoCNAdminResolver:
    return PyGeoCNAdminResolver()


def test_available_is_true_when_the_package_is_installed(resolver: PyGeoCNAdminResolver):
    assert resolver.available is True


def test_disabled_resolver_is_unavailable():
    assert PyGeoCNAdminResolver(enabled=False).available is False


def test_resolve_admin_returns_all_three_levels(resolver: PyGeoCNAdminResolver):
    """赵县（37.75, 114.77）——设计 §3.3 与 1.0 ``geocoding.py`` 实测用的同一个坐标。"""
    admin = resolver.resolve_admin(GpsPoint(37.75, 114.77))

    assert admin is not None
    assert (admin.province, admin.city, admin.district) == ("河北省", "石家庄市", "赵县")
    assert admin.label() == "河北省石家庄市赵县"


def test_ocean_coordinate_resolves_to_nothing(resolver: PyGeoCNAdminResolver):
    """太平洋正中：PyGeoCN 的 ``status`` 为 0，适配器必须返回 ``None``。

    不能返回一个三级皆空的 ``AdminArea``——那会让「不在数据覆盖内」与「解析出了空地名」
    变成同一件事，1.0 返回 ``{}`` 正是这个形状（``geocoding.py:118-119``）。
    """
    assert resolver.resolve_admin(GpsPoint(0.0, -160.0)) is None


def test_confidence_constant_is_the_1_0_value():
    """1.0 ``geocoding.py:134`` 的 ``confidence = 0.90``，逐值对齐。"""
    assert PYGEOCN_CONFIDENCE == 0.90

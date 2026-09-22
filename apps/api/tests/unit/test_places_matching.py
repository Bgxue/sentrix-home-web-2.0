"""地点别名展开与地点条件匹配的三条规则。对应 1.0 ``geocoding.py:262-308``。"""

from __future__ import annotations

import pytest

from sentrix.domain.places.aliases import EMPTY_ALIASES, PlaceAliases
from sentrix.domain.places.matching import place_text_matches
from sentrix.domain.places.models import (
    AdminArea,
    NearestPlace,
    Place,
    PlacePrecision,
    PlaceSource,
)
from sentrix.infrastructure.places.aliases import load_place_aliases

GENERIC = PlaceAliases(
    table={
        "清迈": ("Chiang Mai", "Hang Dong"),
        "泰国": ("Thailand", "TH"),
        "日本": ("Japan",),
    }
)


def make_place(admin: AdminArea | None = None, nearest: NearestPlace | None = None) -> Place:
    return Place(
        admin=admin or AdminArea(),
        nearest=nearest or NearestPlace(),
        precision=PlacePrecision.DISTRICT,
        source=PlaceSource.PYGEOCN,
        confidence=0.9,
    )


def test_rule1_whole_value_appears_in_label():
    place = make_place(admin=AdminArea("浙江省", "杭州市", "萧山区"))
    assert place_text_matches("杭州市", place, EMPTY_ALIASES) is True


def test_rule2_admin_part_appears_inside_constraint():
    """规则 2：``秦皇岛`` 匹配 ``秦皇岛如是海度假村``——比区县更细的说法也要能命中。"""
    place = make_place(admin=AdminArea("河北省", "秦皇岛市", "北戴河区"))
    assert place_text_matches("秦皇岛如是海度假村", place, EMPTY_ALIASES) is True


def test_rule2_needs_two_characters():
    """单字行政区不参与规则 2：``县`` 会命中一切带「县」的句子。"""
    place = make_place(admin=AdminArea("", "县", ""))
    assert place_text_matches("我们县里", place, EMPTY_ALIASES) is False


def test_rule3_alias_bridges_chinese_to_english():
    place = make_place(nearest=NearestPlace(locality="Chiang Mai", distance_m=3417.0))
    assert place_text_matches("清迈", place, GENERIC) is True


def test_rule3_alias_table_is_the_only_bridge():
    """没有别名表时同一条查询不命中——别名是唯一的中英桥，不是内置常识。"""
    place = make_place(nearest=NearestPlace(locality="Chiang Mai", distance_m=3417.0))
    assert place_text_matches("清迈", place, EMPTY_ALIASES) is False


def test_none_place_never_matches():
    """解析不出地点时不再假装「通过预筛」——1.0 的 metadata 通道就是这么被灌满的。"""
    assert place_text_matches("清迈", None, GENERIC) is False


def test_empty_value_never_matches():
    place = make_place(admin=AdminArea("浙江省"))
    assert place_text_matches("", place, GENERIC) is False
    assert place_text_matches("   ", place, GENERIC) is False


def test_names_for_returns_sorted_unique_targets():
    aliases = PlaceAliases(table={"清迈": ("Chiang Mai", "Hang Dong"), "泰国": ("Thailand",)})
    assert aliases.names_for("清迈和泰国") == ("Chiang Mai", "Hang Dong", "Thailand")


def test_strip_admin_suffix_only_strips_once():
    """只砍一次，且按 1.0 的后缀顺序取第一个命中的。

    ``内蒙古自治区`` 因此得到 ``内蒙古自治``——``区`` 排在 ``特别行政区`` 之前，
    先被命中。看着别扭，但这是 1.0 ``geocoding.py:264-270`` 的确切行为，而规则 2 只做
    子串包含，结果仍然正确（``内蒙古`` 是 ``内蒙古自治`` 的前缀）。改成「最长后缀优先」
    会让 strip 的结果与 1.0 不同，属于没有证据的行为变更。
    """
    assert EMPTY_ALIASES.strip_admin_suffix("秦皇岛市") == "秦皇岛"
    assert EMPTY_ALIASES.strip_admin_suffix("内蒙古自治区") == "内蒙古自治"
    assert EMPTY_ALIASES.strip_admin_suffix("萧山") == "萧山"


def test_shipped_alias_file_loads_30_generic_entries():
    """数据文件里是 1.0 的 32 条去掉 2 条评测地标泄漏之后的 30 条。"""
    aliases = load_place_aliases()
    assert len(aliases.table) == 30
    assert "清迈" in aliases.table
    assert "赵州桥" not in aliases.table
    assert "三峡坝址" not in aliases.table


def test_shipped_alias_file_has_no_landmark_answer_text():
    """泄漏条目的判据：值是**具体景点**名而不是通用地名/国家名。"""
    aliases = load_place_aliases()
    for key in aliases.table:
        assert key not in {"赵州桥", "三峡坝址", "赵县", "夷陵区"}


@pytest.mark.parametrize("path", ["/nonexistent/aliases.json"])
def test_missing_alias_file_degrades_to_empty(path):
    from pathlib import Path

    assert load_place_aliases(Path(path)).table == {}


def test_corrupt_alias_file_degrades_to_empty(tmp_path):
    import json

    broken = tmp_path / "aliases.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert load_place_aliases(broken) == EMPTY_ALIASES

    wrong_shape = tmp_path / "shape.json"
    wrong_shape.write_text(json.dumps({"aliases": {"清迈": 5}}), encoding="utf-8")
    assert load_place_aliases(wrong_shape).table == {}

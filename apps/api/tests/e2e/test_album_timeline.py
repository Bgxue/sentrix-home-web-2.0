"""整册导入端到端：真照片、真 EXIF、真 GeoNames 数据、真 PyGeoCN（设计 §8 的 S4 行）。

夹具是 1.0 仓库里的 ``data/album3-v2-source/photos``（只读）：**53 张真照片、13 个不同
的拍摄日期、带真实 EXIF GPS**。合成夹具抓不住的东西它抓得住——S4a 规划阶段就是靠它
发现了 ``CN.zip`` 里 ``readme.txt`` 排在 ``CN.txt`` 前面、以及 GeoNames 中国记录的
``name`` 列是罗马字这两个 bug。

**模型不在这里调**：8100 的 VLM 是 1.0 的服务，生命周期不归 2.0 管，而 CI 运行时对
同一块单卡 GPU 发起真推理也不是这条用例要验的东西。观察器换成确定性的假件；
真链路是「真 EXIF → 真地点解析（PyGeoCN 面 + GeoNames 点）→ 真归并 → 真时间线」。
视觉嵌入同样换成假件（真模型是 1.6GB 权重 + GPU 推理）。

断言里的两个地名是**实测值**，不是推断：

    2017-11-05  ->  湖北省宜昌市夷陵区 · 邓村白狮系附近
    2023-08-06  ->  河北省邯郸市永年区 · Nanlvgu西大屯附近
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentrix.config import Settings
from sentrix.domain.retrieval import scheme
from sentrix.main import create_app

ALBUM = Path("/home/asus/Github/Sentrix-Home-Web/data/album3-v2-source/photos")
GEO = Path("/home/asus/s4verify/repo/data/geo")

EXPECTED_DAYS = {
    "2017-11-05",
    "2018-02-18",
    "2018-03-31",
    "2018-04-01",
    "2019-01-10",
    "2019-07-22",
    "2020-03-17",
    "2020-04-10",
    "2022-06-23",
    "2022-07-16",
    "2022-07-31",
    "2023-01-05",
    "2023-08-06",
}
EXPECTED_PLACES = {
    "2017-11-05": "湖北省宜昌市夷陵区 · 邓村白狮系附近",
    "2023-08-06": "河北省邯郸市永年区 · Nanlvgu西大屯附近",
}


class FakeVisualEmbedder:
    """与 ``conftest.py`` 的那一个同形（8 维、由内容哈希决定）。

    这里不复用 conftest 的版本：``tests/`` 不是包，跨目录 import 要靠 pytest 往
    sys.path 里塞目录，那种隐式依赖比这十几行更脆。真模型是 1.6GB 权重 + GPU 推理，
    不属于 ``make ci``。
    """

    model_id = scheme.IMAGE_MODEL
    dimension = 8

    @staticmethod
    def _vector(seed: str) -> list[float]:
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        return [digest[index] / 255.0 for index in range(8)]

    def embed_image(self, path) -> list[float]:  # noqa: ANN001
        return self._vector(Path(path).read_bytes().hex())

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FakeVlmObserver:
    """确定性观察器：真模型不可用时导入链仍然要能跑完整条（见模块 docstring）。"""

    model_id = "fake-vlm"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def observe(self, path: Path):  # noqa: ANN201
        from sentrix.domain.media.ports import ObservationSemantics

        self.seen.append(Path(path).name)
        return ObservationSemantics(
            caption="一张家庭照片",
            place="户外",
            activity="拍照",
            event_type="家庭活动",
            objects=("相机",),
            confidence=0.65,
        )


def photos() -> list[Path]:
    if not ALBUM.is_dir():
        pytest.skip(f"真相册夹具不可用: {ALBUM}")
    return sorted(list(ALBUM.glob("*.jpg")) + list(ALBUM.glob("*.JPG")))


@pytest.fixture
def album_client(tmp_path, monkeypatch) -> TestClient:
    """真 SQLite、真迁移、真地点解析；模型换成假件。"""
    if not GEO.is_dir():
        pytest.skip(f"GeoNames 数据不可用（见 docs/runbooks/geonames-data.md）: {GEO}")
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    settings = Settings()
    settings.ensure_directories()
    for source in sorted(GEO.glob("*.zip")):
        os.symlink(source, settings.geo_dir / source.name)
    app = create_app(settings)
    app.state.container = dataclasses.replace(
        app.state.container, visual_embedder=FakeVisualEmbedder(), vlm_observer=FakeVlmObserver()
    )
    return TestClient(app)


def import_album(client: TestClient, *, scope_id: str = "album1") -> dict:
    response = client.post(
        "/api/v1/imports",
        json={"directory": str(ALBUM), "scope_id": scope_id, "limit": 1000},
    )
    assert response.status_code == 201, response.text
    return response.json()


def timeline(client: TestClient, *, scope_id: str = "album1") -> dict:
    response = client.get("/api/v1/timeline", params={"scope_id": scope_id, "limit": 200})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_whole_album_imports_without_failures(album_client: TestClient):
    expected = photos()

    body = import_album(album_client)

    assert body["failures"] == []
    assert body["imported"] == len(expected)
    assert body["events"] > 0


def test_timeline_forms_one_group_per_real_capture_day(album_client: TestClient):
    """设计 §8 的 S4 行：「导入整个相册，时间线按日期成型」。"""
    import_album(album_client)

    body = timeline(album_client)

    assert {day["date"] for day in body["days"]} == EXPECTED_DAYS
    assert body["total"] == sum(len(day["events"]) for day in body["days"])


def test_days_are_newest_first(album_client: TestClient):
    import_album(album_client)

    dates = [day["date"] for day in timeline(album_client)["days"]]

    assert dates == sorted(dates, reverse=True)


def test_every_photo_appears_exactly_once(album_client: TestClient):
    expected = photos()
    import_album(album_client)

    body = timeline(album_client)
    seen = [photo["asset_id"] for day in body["days"] for event in day["events"] for photo in event["photos"]]

    assert len(seen) == len(expected)
    assert len(set(seen)) == len(expected)


def test_same_activity_photos_fold_into_one_event_per_day(album_client: TestClient):
    """同一场活动的照片聚在同一个事件里——这是事件归并存在的理由。

    两个真簇（2017-11-05 的 16 张、2023-08-06 的 5 张）都是同一小时内、同一地点拍的，
    必须各自折成一个事件。合成夹具里这两簇不存在（它只有一个坐标），这条断言只有
    真相册能提。
    """
    import_album(album_client)

    body = timeline(album_client)
    by_date = {day["date"]: day["events"] for day in body["days"]}

    assert len(by_date["2017-11-05"]) == 1
    assert len(by_date["2023-08-06"]) == 1
    assert len(by_date["2017-11-05"][0]["photos"]) == 16
    assert len(by_date["2023-08-06"][0]["photos"]) == 5


def test_real_gps_resolves_to_the_county_and_the_village(album_client: TestClient):
    """国内坐标解析到区县 + 乡镇/村庄，且展示用的是后端算好的那一串。"""
    import_album(album_client)

    body = timeline(album_client)
    places = {
        day["date"]: day["events"][0]["photos"][0]["place"]
        for day in body["days"]
        if day["date"] in EXPECTED_PLACES
    }

    assert places == EXPECTED_PLACES


def test_event_place_carries_the_admin_district_for_hard_filtering(album_client: TestClient):
    import_album(album_client)

    body = timeline(album_client)
    event = next(day["events"][0] for day in body["days"] if day["date"] == "2017-11-05")

    assert event["admin_district"] == "夷陵区"
    assert event["place"].startswith("湖北省宜昌市夷陵区")


def test_place_filter_returns_only_that_place(album_client: TestClient):
    """地点筛选在后端做，且真的能筛出东西——这是「按地名找图」的入口。

    **真数据纠正过一次这条用例的期望**：第一版拿「清迈」当反例，跑出来是红的——
    相册里 2018-03-31 那两张的外国坐标（18.77, 98.97）**真的解析成了「清迈附近」**。
    反例换成相册里确定没有的「悉尼」。
    """
    import_album(album_client)

    hit = album_client.get("/api/v1/timeline", params={"scope_id": "album1", "place": "夷陵"}).json()
    miss = album_client.get("/api/v1/timeline", params={"scope_id": "album1", "place": "悉尼"}).json()

    assert [day["date"] for day in hit["days"]] == ["2017-11-05"]
    assert miss["days"] == []
    assert miss["total"] == 0


def test_foreign_coordinates_use_the_world_pool(album_client: TestClient):
    """国外坐标走 GeoNames 的点（不是 PyGeoCN 的面）：区县为空、只到城市。

    ``清迈附近`` 与 ``Hang Dong附近`` 都是**实测值**；后者是清迈的一个县，
    GeoNames 那条记录的 name 列没有汉字（CN.zip 之外的记录几乎没有中文备选名）。
    """
    import_album(album_client)

    body = timeline(album_client)
    by_date = {day["date"]: day["events"][0] for day in body["days"]}

    assert by_date["2018-03-31"]["admin_district"] == ""
    assert by_date["2018-03-31"]["photos"][0]["place"] == "清迈附近"
    assert by_date["2018-04-01"]["photos"][0]["place"] == "Hang Dong附近"


def test_import_is_resumable_and_does_not_duplicate_events(album_client: TestClient):
    """重跑整册导入：照片走去重，事件数不变——使用者唯一的补救动作必须真的有效。"""
    first = import_album(album_client)
    before = timeline(album_client)["total"]

    second = import_album(album_client)

    assert second["imported"] == 0
    assert second["deduplicated"] == first["imported"]
    assert timeline(album_client)["total"] == before

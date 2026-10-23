"""端到端：整册导入之后，「按真实地名找照片」真的能召回。

夹具与 ``test_album_timeline.py`` 同一份：本仓库 ``data/album3-v2-source/photos``
的 **53 张真照片、真 EXIF GPS**，配仓库里那份真 GeoNames / PyGeoCN 数据。合成夹具抓不住
这一类：地点锚点的值来自**真实反地理编码的结果**，只有真数据才会出现「GeoNames 的
最近邻点带的是汉字名」这种形状。

**模型换成假件**（同 ``test_album_timeline.py``）：观察器是确定性的，视觉嵌入器由内容
哈希决定。被测的是**地点解析 → 词法锚点 → 词法召回 → 融合**这条链，不是模型质量；
真模型的验收在 ``test_visual_search.py``。

判据是**两条互不依赖的事实**对齐：

1. 期望集合来自 ``GET /api/v1/assets`` 的 ``captured_at``（EXIF 直接读出来的日期）；
2. 实际集合来自 ``GET /api/v1/search``，只取命中里带 ``lexical`` 名次的那几条。

两者在数据集上不同源：前者是元数据读取，后者是派生投影 + FTS + 融合。它们相等才说明
「这个词真的从词法通道召回了这些照片」。断言里的地名与张数是**实测值**，不是推断。
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_album_timeline import FakeChatModel, FakePersonVision

from sentrix.config import Settings
from sentrix.domain.retrieval import scheme
from sentrix.main import create_app

# Requires real album data, local weights or a running model; run via make test-real.
pytestmark = pytest.mark.real_environment

ALBUM = Path(__file__).resolve().parents[4] / "data/album3-v2-source/photos"
SCOPE = "album1"


class FakeVisualEmbedder:
    """8 维、由内容哈希决定（与 conftest / test_album_timeline 同形）。"""

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
    """确定性观察器：观察文本里**没有**地名 / 日期 / 文件名，锚点只能来自 asset。"""

    model_id = "fake-vlm"

    def observe(self, path):  # noqa: ANN001, ANN201
        from sentrix.domain.media.ports import ObservationSemantics

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


def geo_sources() -> list[Path]:
    """部署方按 ``docs/runbooks/geonames-data.md`` 装进 ``$SENTRIX_DATA_DIR/geo`` 的数据。

    同 ``test_album_timeline.py``：取**配置里的那一个数据目录**（默认即仓库根的
    ``data/geo``），且必须在夹具改写 ``SENTRIX_DATA_DIR`` 之前取。扫描规则与
    ``GeoNamesNearestResolver`` 一致。缺数据时跳过并说明原因——锚点的值来自真地点解析，
    没有 GeoNames 就没有 ``nearest`` 那一级，这条用例验不了。
    """
    directory = Settings().geo_dir
    sources = sorted(
        path for path in directory.glob("*") if path.is_file() and path.suffix.lower() in {".txt", ".zip"}
    )
    if not sources:
        pytest.skip(f"GeoNames 数据不可用（见 docs/runbooks/geonames-data.md）: {directory}")
    return sources


@pytest.fixture(scope="module")
def album_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """整册导入一次，整个模块共用（53 张图 + 真 GeoNames 索引，逐用例重建太慢）。"""
    expected = photos()
    sources = geo_sources()
    previous = os.environ.get("SENTRIX_DATA_DIR")
    os.environ["SENTRIX_DATA_DIR"] = str(tmp_path_factory.mktemp("search-channels"))
    try:
        settings = Settings()
        settings.ensure_directories()
        for source in sources:
            os.symlink(source, settings.geo_dir / source.name)
        app = create_app(settings)
        app.state.container = dataclasses.replace(
            app.state.container,
            visual_embedder=FakeVisualEmbedder(),
            vlm_observer=FakeVlmObserver(),
            # 批末事件总结、人物画像也会访问主模型。地点专项的全部模型路径须隔离。
            chat_model=FakeChatModel(),
            person_vision=FakePersonVision(),
        )
        client = TestClient(app)
        response = client.post(
            "/api/v1/imports", json={"directory": str(ALBUM), "scope_id": SCOPE, "limit": 1000}
        )
        assert response.status_code == 201, response.text
        assert response.json()["imported"] == len(expected), response.text
        yield client
    finally:
        if previous is None:
            os.environ.pop("SENTRIX_DATA_DIR", None)
        else:
            os.environ["SENTRIX_DATA_DIR"] = previous


def photos_by_day(client: TestClient) -> dict[str, set[str]]:
    """EXIF 那一侧的真相：每张照片的拍摄日期（不经过检索）。"""
    items = client.get("/api/v1/assets", params={"scope_id": SCOPE, "limit": 200}).json()["items"]
    grouped: dict[str, set[str]] = {}
    for item in items:
        grouped.setdefault((item["captured_at"] or "")[:10], set()).add(item["id"])
    return grouped


def lexical_hits(client: TestClient, word: str) -> set[str]:
    """这个词经词法通道召回的照片集合。"""
    body = client.get("/api/v1/search", params={"q": word, "scope_id": SCOPE, "limit": 100}).json()
    return {item["asset"]["id"] for item in body["items"] if "lexical" in item["channels"]}


def test_a_real_district_name_finds_the_photos_taken_there(album_client: TestClient):
    """「夷陵区」不在任何观察文本里，只能靠 geo 锚点召回。

    实测：2017-11-05 的 16 张照片经 PyGeoCN 全部落在湖北省宜昌市夷陵区。没有锚点列时
    这条查询在词法通道上恒为空——``caption`` 是「一张家庭照片」。
    """
    expected = photos_by_day(album_client)["2017-11-05"]
    assert len(expected) == 16, "相册夹具变了，先复核这条断言里的实测值"

    assert lexical_hits(album_client, "夷陵区") == expected


def test_a_foreign_nearest_place_is_searchable_too(album_client: TestClient):
    """连 GeoNames 最近邻点解析出的国外地名也能召回。

    实测：2018-03-31 的 2 张照片坐标在清迈。这里走的**不是** admin 槽（那是面），
    而是 ``nearest`` 槽经过 GeoNames 找到的那个点——GeoJSON 边界数据里没有它。
    """
    expected = photos_by_day(album_client)["2018-03-31"]
    assert len(expected) == 2, "相册夹具变了，先复核这条断言里的实测值"

    assert lexical_hits(album_client, "清迈") == expected


def test_a_word_nobody_photographed_returns_no_lexical_hit(album_client: TestClient):
    """反向：「悉尼」既不是观察文本、也不是任何一张的地点或文件名。"""
    assert lexical_hits(album_client, "悉尼") == set()

"""整册导入端到端：真照片、真 EXIF、真 GeoNames 数据、真 PyGeoCN（设计 §8 的 S4 行）。

夹具是 本仓库的 ``data/album3-v2-source/photos``（只读）：**53 张真照片、13 个不同
的拍摄日期、带真实 EXIF GPS**。合成夹具抓不住的东西它抓得住——S4a 规划阶段就是靠它
发现了 ``CN.zip`` 里 ``readme.txt`` 排在 ``CN.txt`` 前面、以及 GeoNames 中国记录的
``name`` 列是罗马字这两个 bug。

**模型不在这里调**：8100 的 VLM 是 1.0 的服务，生命周期不归 2.0 管，而 CI 运行时对
同一块单卡 GPU 发起真推理也不是这条用例要验的东西。观察器换成确定性的假件；
真链路是「真 EXIF → 真地点解析（PyGeoCN 面 + GeoNames 点）→ 真归并 → 真时间线」。
视觉嵌入同样换成假件（真模型是 1.6GB 权重 + GPU 推理）。批末的事件总结也会打 8100
（同一个端点的文本模型），所以文本模型一并换成假件——见 ``FakeChatModel``。
T86i 之后还多了第四条：**导入批末自动跑一趟画像**（裁决④），它走 ``PersonVision``，
所以那一个也换成假件——见 ``FakePersonVision``。**每加一条模型路径，这里就要多一个桩**：
少一个，这条 e2e 就会去打真端点，而它本来连的是 8100（1.0 的服务，生命周期不归 2.0）。

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

from sentrix.application.memories.summarize_event import PLACEHOLDER_TITLES
from sentrix.config import Settings
from sentrix.domain.assistant.models import ModelReply
from sentrix.domain.retrieval import scheme
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository
from sentrix.main import create_app

# Requires real album data, local weights or a running model; run via make test-real.
pytestmark = pytest.mark.real_environment

ALBUM = Path(__file__).resolve().parents[4] / "data/album3-v2-source/photos"

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


class FakePersonVision:
    """画像流水线的那三个模型入口（Task 86e）。

    **T86i 之后才有这个桩**：导入批末会自动跑一趟画像（裁决④），而那趟走的是
    ``PersonVision``——与上面三个适配器并列的第四条模型路径。少这个桩，e2e 的导入
    就会打 8100 的真端点。

    三个方法都返回确定性的形状，让流水线走完五阶段：瞬间抽取拿到空表（不落瞬间）、
    画像写手返回一段**过得了校验**的文本（``portrait.validate_portrait`` 对
    ``source="model"`` 有汉字数与主题数两条下限，见 T86c）。这里给一段足够长的、
    带两个主题的文本，好让 ``write_portraits`` 真的落一版——**这条 e2e 只关心
    「导入 + 批末画像不会把整批弄挂」，画像内容本身的规则在 test_portrait.py 里验。**
    """

    model_id = "fake-person-vision"

    def write_person_portrait(self, pack):  # noqa: ANN001
        return {
            "portrait_text": self._text(pack),
            "themes": [
                {
                    "title": "常出现的场合",
                    "summary": "从照片看，这个人常出现在家庭聚会与其他人的合影里。",
                    "evidence_refs": self._refs(pack),
                },
                {
                    "title": "常做的事",
                    "summary": "从照片看，这个人常在饭桌旁与家人一起活动，偶尔在户外。",
                    "evidence_refs": self._refs(pack),
                },
            ],
        }

    def analyze_person_moments(self, path, labels, context=None):  # noqa: ANN001
        # 空表：批末画像这一步不该在 e2e 里产生瞬间——要么没有核心人物，要么有也不
        # 需要在这里验它的内容。``ExtractMoments`` 对空表是正常返回 0。
        return {"moments": []}

    def analyze_person_appearance(self, path, metadata=None):  # noqa: ANN001
        return {"clothing": [], "confidence": 0.0}

    @staticmethod
    def _text(pack) -> str:  # noqa: ANN001
        # 长度要过 ``MIN_HANZI``（80）。证据包是嵌套的：``pack["person"]["display_name"]``
        # （``compile_portrait_evidence`` 的返回形状）。
        person = (pack or {}).get("person") or {}
        name = str(person.get("display_name") or "这个人")
        return (
            f"从这批照片看，{name}经常出现在家庭活动的场合里：多数照片是室内聚会，"
            "也有几张在户外。照片里这个人总是和其他家人在一起，偶尔单独出镜。"
            "这些描述只依据画面中可见的动作与场合，不涉及健康、收入或其他敏感属性。"
        )

    @staticmethod
    def _refs(pack) -> list[dict[str, object]]:  # noqa: ANN001
        # 引用要落在证据包里真有的瞬间上——``normalize_writer_output`` 的引用守卫会丢掉
        # 不存在的 id（T86c 修掉的那个后门）。没有瞬间时给空表。
        moments = list((pack or {}).get("moments") or [])
        for item in moments:
            if isinstance(item, dict) and item.get("id"):
                return [{"kind": "person_moment", "id": item["id"]}]
        return []


class FakeChatModel:
    """批末事件总结用的确定性文本模型（真件是 8100 上的 llama-server）。

    模型名与那条降级路径都不在这里验——``test_event_summary.py`` 单独验。
    这里要的是：批末真的调了一次总结、事件因此拿到非占位的标题/类型。
    """

    model_id = "fake-chat"

    def complete(self, messages, tools) -> ModelReply:  # noqa: ANN001
        return ModelReply(
            content=(
                '{"title":"户外拍照","event_type":"家庭活动","activity":"拍照",'
                '"summary":"一批家庭照片记录了户外拍照。","confidence":0.8}'
            ),
            finish_reason="stop",
        )


def photos() -> list[Path]:
    if not ALBUM.is_dir():
        pytest.skip(f"真相册夹具不可用: {ALBUM}")
    return sorted(list(ALBUM.glob("*.jpg")) + list(ALBUM.glob("*.JPG")))


def geo_sources() -> list[Path]:
    """部署方按 ``docs/runbooks/geonames-data.md`` 装进 ``$SENTRIX_DATA_DIR/geo`` 的数据。

    取的是**配置里的那一个数据目录**（默认即仓库根的 ``data/geo``），不是测试自己的
    临时目录——**必须在夹具改写 ``SENTRIX_DATA_DIR`` 之前取**，改完之后那个目录是空的，
    拿不到任何东西。扫描规则与 ``GeoNamesNearestResolver`` 一致。

    没有数据就跳过并说明原因：地点解析是这条用例的验收对象，缺数据时它验不了，
    但缺数据不是「代码坏了」——服务本身没有 GeoNames 也照常启动（只少最细那一级）。
    """
    directory = Settings().geo_dir
    sources = sorted(
        path for path in directory.glob("*") if path.is_file() and path.suffix.lower() in {".txt", ".zip"}
    )
    if not sources:
        pytest.skip(f"GeoNames 数据不可用（见 docs/runbooks/geonames-data.md）: {directory}")
    return sources


@pytest.fixture
def album_client(tmp_path, monkeypatch) -> TestClient:
    """真 SQLite、真迁移、真地点解析；三个模型适配器换成假件。"""
    sources = geo_sources()
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    settings = Settings()
    settings.ensure_directories()
    for source in sources:
        os.symlink(source, settings.geo_dir / source.name)
    app = create_app(settings)
    app.state.container = dataclasses.replace(
        app.state.container,
        visual_embedder=FakeVisualEmbedder(),
        vlm_observer=FakeVlmObserver(),
        chat_model=FakeChatModel(),
        # 第四条模型路径，T86i（裁决④）之后才有：导入批末的画像流水线。
        person_vision=FakePersonVision(),
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


def test_events_are_summarized_into_real_content_not_placeholders(album_client: TestClient):
    """**这条断言的是内容，不是数量。**

    原来的整册验收只断言了 ``events > 0`` 这个数量——而事件即使一行总结都没跑，
    数量也是 53。占位的 ``待总结事件`` / ``待判断`` 是 1.0 新建事件时的字面量
    （``db.py:2310-2325``），它们落库、出现在接口响应里，看起来像「有内容」。

    ``SummarizeEvent`` 在 S4b 建好之后**没有任何生产调用方**，于是 53 张照片导完，
    每个事件的 title 都还是 ``待总结事件``。这条用例把它钉住。
    """
    body = import_album(album_client)

    timeline_body = timeline(album_client)
    events = [event for day in timeline_body["days"] for event in day["events"]]

    assert events, "时间线里没有事件，这条断言就失去意义"
    assert body["summarized"] > 0
    assert {event["title"] for event in events} & PLACEHOLDER_TITLES == set()
    assert all(event["title"].strip() for event in events)
    assert all(event["event_type"] not in PLACEHOLDER_TITLES for event in events)
    assert all(event["activity"].strip() and event["summary"].strip() for event in events)


def test_each_event_is_summarized_once_not_once_per_photo(album_client: TestClient):
    """总结是**事件级**的（1.0 ``batch_event_ids`` → 逐个 ``_summarize_event_worker``）。

    53 张照片折成 17 个事件，总结就该跑 17 次；按张跑会把同一个事件写 3 遍。
    """
    body = import_album(album_client)

    total = timeline(album_client)["total"]

    assert body["events"] == 53
    assert body["summarized"] == total


def test_no_facts_are_written_because_s4_has_no_confirmed_people(album_client: TestClient):
    """事实表按设计是空的，原因有两个，都不在接线里。

    1. 候选事实没有来源：1.0 的候选来自模型自报的 ``facts``
       （``model_clients.py:1130-1198``），2.0 的观察器明确不搬
       （``s4-deviations.md`` 第 30 条）；
    2. 门禁关着：1.0 的 ``is_confirmed_person_name`` 查 ``entities`` 里
       ``status='confirmed'`` 的人物，身份层是 S5。

    **接线本身是活的**——``test_the_import_path_calls_fact_maintenance_after_event_assignment``
    与 ``test_the_import_route_wires_fact_maintenance`` 分别钉住调用点与装配点。
    这条用例钉住的是「S4 的取值」，谁放宽门禁或补上候选来源，它会红——那正是要用户
    裁决的时刻。
    """
    import_album(album_client)

    facts = SqliteFactRepository(album_client.app.state.container.database)
    assert list(facts.list_facts("album1", limit=100)) == []

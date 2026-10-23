"""S5c 端到端验收：真照片 → 人脸簇 → 确认 → 提及 / 事实 / 外观 / 事件 → 人物画像。

夹具与 ``test_album_timeline.py`` 同一套：真照片、真 EXIF、真 SQLite、真迁移、真人脸
检测（buffalo_l 的 RetinaFace 分块 + SCRFD 复核）。四个模型适配器换成确定性假件。

**假件必须四个，少一个这条 e2e 就会去打真端点。** 前三条是 ``visual_embedder`` /
``vlm_observer`` / ``chat_model``；第四条 ``person_vision`` 是 Task 86i 才接上的
（导入批末自动跑一趟画像，裁决④）。上一轮正是漏了它，而它连的是 8100——1.0 的服务，
生命周期不归 2.0 管。每加一条模型路径，这里就要多一个桩。

**每一条断言的都是内容，不只数量。** 设计文档的原话是「S4 栽在 ``events: 53`` 上」：
那一版用例只断言事件的个数，于是「总结一次都没跑」也照样绿。所以下面每条都挑一个能被
真实错误实现弄红的量，并且尽量**直接查库对拍**，不只信响应体。

**GeoNames 数据不在这份夹具里**：这一批断言的六个对象全在身份侧。唯一沾到地点的是事件
归并的 ``location`` 分量，而它读的是 EXIF 里的**原始 GPS**（``asset.captured_location``），
不经过解析器——缺 GeoNames 只少最细一级地名，不影响这里的任何一条。验地名的那一份是
``test_album_timeline.py``。

断言里的取值都是**实测**（2026-09-29，53 张真照片）：117 张脸、11 个簇，最大的一个
11 张脸、跨 3 个不同拍摄日。
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

import pytest
from conftest import skip_or_fail_on_load_error
from fastapi.testclient import TestClient
from PIL import Image

from sentrix.config import Settings
from sentrix.domain.assistant.models import ModelReply
from sentrix.domain.retrieval import scheme
from sentrix.infrastructure.persistence.repositories.facts import SqliteFactRepository
from sentrix.main import create_app

# Requires real album data, local weights or a running model; run via make test-real.
pytestmark = pytest.mark.real_environment

ALBUM = Path(__file__).resolve().parents[4] / "data/album3-v2-source/photos"

# 两个空间：``album1`` 是整册导入（五条用例共用同一个导入形状），``identity-pair`` 是
# 事件合流那一条构造出来的小相册。分空间而不是分数据库：同一个 app 上并存，互不干扰。
SCOPE = "album1"
PAIR_SCOPE = "identity-pair"

CONFIRMED_NAME = "妈妈"
CONFIRMED_ROLE = "母亲"
# 1.0 ``db.py:5169`` 的谓词字面量。它**不是展示用的标签，是事实的键**（会落进 facts 表
# 被检索命中），所以写在用例里而不是从源码 import：改了值就是改了产品契约，用例要跟着
# 红；跟着 import 就跟着一起绿，那条断言也就白写了。
FAMILY_ROLE_PREDICATE = "家庭角色"
# 假画像里的一句独有措辞。它是「**模型写的那段真的落库了**」的凭证：确定性兜底那份模板
# 只有 41 个汉字、主题名是「反复X」，两边都不可能是这一句。
PORTRAIT_MARK = "眼睛弯成一道缝"
# 假件回的那件衣服。断言「有一行」与「这一行是模型写的」是两件事，后者要具体到内容。
CLOTHING_SUBJECT = "深色外套"

# EXIF 标签号（``infrastructure/media/metadata.py`` 读的就是这三个）。
DATETIME_ORIGINAL_TAG = 36867
DATETIME_TAG = 306
GPS_INFO_TAG = 34853
# 构造那一对照片的拍摄时间：同一天、相隔 5.5 小时。
# 6 小时是 ``CANDIDATE_WINDOW_HOURS`` 的硬窗，5.5 小时在窗内（合得起来），而时间分
# ``0.25 · (1 − 330/360) ≈ 0.021`` 低到总分够不着 0.50（拆得开）；确认前 ``person_score``
# 恒 0、确认后给 ``0.20 · 0.8 = 0.16``，正好跨过门槛。
PAIR_STAMPS = ("2023:08:06 15:00:00", "2023:08:06 20:30:00")

# 检测器探活时最多试前几张照片。相册里 46/53 张有人脸，头几张足够命中；一张都没有时
# 也快（每次尝试约 0.3 秒）。
PROBE_PHOTO_COUNT = 8


class FakeVisualEmbedder:
    """与 ``conftest.py`` 的那一个同形（8 维、由内容哈希决定）。

    这里不复用 conftest 的版本：``tests/`` 不是包，跨目录 import 要靠 pytest 往
    sys.path 里塞目录，那种隐式依赖比这十几行更脆。真模型是 1.6GB 权重 + GPU 推理。
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
    """确定性观察器：真模型（8100 上的 llama-server）不在这里调。

    它给的四样东西（place / activity / event_type / objects）是事件归并八个分量里
    **四个**的来源，所以这个桩不只是「省一次网络请求」——它把归并的语义分量固定下来，
    让「两张照片为什么分成两个事件」只由时间与 GPS 决定（见 ``build_pair``）。
    """

    model_id = "fake-vlm"

    def observe(self, path):  # noqa: ANN001
        from sentrix.domain.media.ports import ObservationSemantics

        return ObservationSemantics(
            caption="一张家庭照片",
            place="户外",
            activity="拍照",
            event_type="家庭活动",
            objects=("相机",),
            confidence=0.65,
        )


class FakeChatModel:
    """批末事件总结用的确定性文本模型（真件是 8100 上的 llama-server）。"""

    model_id = "fake-chat"

    def complete(self, messages, tools) -> ModelReply:  # noqa: ANN001
        return ModelReply(
            content=(
                '{"title":"户外拍照","event_type":"家庭活动","activity":"拍照",'
                '"summary":"一批家庭照片记录了户外拍照。","confidence":0.8}'
            ),
            finish_reason="stop",
        )


class FakePersonVision:
    """画像流水线与外观取样共用的第三条模型路径（``PersonVision``，Task 85d + 86e）。

    **这是上一轮漏掉的那个桩。** 它的三个方法都返回**能走完整条流水线**的形状，而不是
    最小空值——空值会让「画像内容合格」那条断言永远绿不了，而实现是对的：

    - ``analyze_person_moments`` 返回真瞬间。瞬间是画像唯一的证据来源：返回空表，
      ``compile_portrait_evidence`` 收到空证据包，模型的引用无处可指、三次校验全不过，
      最后落到 41 个汉字的确定性兜底；
    - ``write_person_portrait`` 返回一段**过得了校验**的文本（汉字数与主题数两条下限
      见 ``domain/identity/portrait.validate_portrait``），引用指向证据包里真有的瞬间 id
      ——``normalize_writer_output`` 的引用守卫会丢掉编造的 id（T86c 修掉的那个后门），
      编的引用到不了库里；
    - ``analyze_person_appearance`` 返回**非空**的 clothing，让外观那条断言能钉到内容。
    """

    model_id = "fake-person-vision"

    # 三个动作轮流出现，主题因此不会全挤在一个动作上。
    MOMENT_ACTIONS = ("一起吃饭", "在户外散步", "与家人合影")

    def analyze_person_moments(self, path, labels, context=None):  # noqa: ANN001
        return {
            "moments": [
                {
                    "label": label,
                    "action_text": self.MOMENT_ACTIONS[index % len(self.MOMENT_ACTIONS)],
                    "interaction_text": "和家人在一起",
                    "participation_style": "主动",
                    "visible_affect": "放松",
                    "social_role_cues": [],
                    "narrative_note": "",
                    "interaction_labels": [],
                    "confidence": 0.7,
                }
                for index, label in enumerate(sorted(labels))
            ]
        }

    def write_person_portrait(self, pack):  # noqa: ANN001
        person = (pack or {}).get("person") or {}
        name = str(person.get("display_name") or "这个人")
        return {
            "portrait_text": (
                f"从这批照片看，{name}总是出现在家庭聚会的场合里：多数画面是室内餐桌旁的合影，"
                f"也有几张在户外走动时拍的。照片里这个人常站在家人中间，笑起来{PORTRAIT_MARK}，"
                "偶尔也单独出镜。画面里的动作和场合都很日常，看不出特别的安排。"
                "这些描述只依据画面里看得见的东西。"
            ),
            "themes": [
                {
                    "title": "常出现的场合",
                    "summary": "从照片看，这个人常出现在家庭聚会里。",
                    "evidence_refs": self._refs(pack, 0),
                },
                {
                    "title": "常做的事",
                    "summary": "从照片看，这个人常在饭桌旁与家人一起活动。",
                    "evidence_refs": self._refs(pack, 1),
                },
            ],
        }

    def analyze_person_appearance(self, path, metadata=None):  # noqa: ANN001
        return {"clothing": [CLOTHING_SUBJECT], "confidence": 0.66}

    @staticmethod
    def _refs(pack, index):  # noqa: ANN001
        """引用落在证据包里真有的瞬间上；没有瞬间就给空表（那条画像会被校验拒掉）。"""
        moments = [item for item in (pack or {}).get("moments") or [] if item.get("id")]
        if not moments:
            return []
        return [{"kind": "person_moment", "id": moments[index % len(moments)]["id"]}]


# ---- 夹具与读侧 helper -------------------------------------------------------------


def album_photos() -> list[Path]:
    """真相册的照片。夹具缺席是**机器上的事实**，跳过并说明，不是红。"""
    if not ALBUM.is_dir():
        pytest.skip(f"真相册夹具不可用: {ALBUM}")
    return sorted(list(ALBUM.glob("*.jpg")) + list(ALBUM.glob("*.JPG")))


@pytest.fixture(scope="module")
def face_detector():
    """整个模块**共用一套** ONNX 会话（Task 77 的 ``FaceAdapter``）。

    为什么共享而不是每个用例新建一套：会话建在 GPU 上，而这块单卡上还跑着 8100 的
    vLLM（实测占 18.5 GB / 24 GB，空闲常在 1–2 GB 之间浮动）。每建一套就多一次
    「显存不够 → 会话建不起来 → ``detect`` 静默返回空表」的机会，而那时的表现是
    「整册导入完一个簇都没有」，读起来像实现坏了。建一套，这个风险只付一次。

    建完**先在真照片上探一次**，检不出脸就跳过整个模块并说明原因。判据与
    ``test_face_detector.py`` 一致：不是「权重在不在」（代理判据），而是**真的加载并
    真的检一次**；环境原因跳过、其余照常红——权重缺席、显存不足都是允许的部署形态，
    这两条链路验不了，但也不能假装验过了。
    """
    from sentrix.infrastructure.models.face_adapter import FaceAdapter

    photos = album_photos()
    detector = FaceAdapter()
    # 只探前几张：相册里 46/53 张有人脸，头几张就能命中；一张都没有时也快。
    for path in photos[:PROBE_PHOTO_COUNT]:
        if detector.detect(str(path)):
            return detector
    # **分类，不是一律 skip。** ``FaceAdapter.detect`` 会把自己的异常吞掉、返回空表，
    # 所以「检测器整个坏了」与「这台机器显存不够」在下面这两行之前长得一模一样。
    # 一律跳过的话，实现坏掉时这个模块会静默不跑，而输出读起来完全正常——
    # 那正是本项目反复出现的「声称在检查、其实没有」。
    if detector.error:
        skip_or_fail_on_load_error(detector.error, what="人脸检测")
    pytest.fail(
        f"人脸检测没报错，却在 {PROBE_PHOTO_COUNT} 张确认有人脸的真照片上一张都没检出来"
        "——这不是部署形态，是代码问题（相册里 46/53 张有人脸，探针取的又是头几张）"
    )


@pytest.fixture
def album_client(tmp_path, monkeypatch, face_detector) -> TestClient:
    """真 SQLite、真迁移、真人脸检测；四个模型适配器换成假件。

    与 ``test_album_timeline.py`` 同形，只少了两件事：GeoNames 数据的符号链接（理由见
    模块 docstring）与它带来的 ``pytest.skip``。检测器换成模块共用的那一个——
    ``FaceAdapter`` 构造期不建会话，所以这里换掉不浪费 GPU。
    """
    album_photos()
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    settings = Settings()
    settings.ensure_directories()
    app = create_app(settings)
    app.state.container = dataclasses.replace(
        app.state.container,
        face_detector=face_detector,
        visual_embedder=FakeVisualEmbedder(),
        vlm_observer=FakeVlmObserver(),
        chat_model=FakeChatModel(),
        # 第四条模型路径。少了它就是打 8100（见模块 docstring）。
        person_vision=FakePersonVision(),
    )
    return TestClient(app)


def import_album(client: TestClient, *, scope_id: str = SCOPE) -> dict:
    response = client.post(
        "/api/v1/imports",
        json={"directory": str(ALBUM), "scope_id": scope_id, "limit": 1000},
    )
    assert response.status_code == 201, response.text
    return response.json()


def timeline(client: TestClient, *, scope_id: str = SCOPE) -> dict:
    response = client.get("/api/v1/timeline", params={"scope_id": scope_id, "limit": 200})
    assert response.status_code == 200, response.text
    return response.json()


def clusters(client: TestClient, *, scope_id: str = SCOPE) -> list[dict]:
    response = client.get("/api/v1/face-clusters", params={"scope_id": scope_id})
    assert response.status_code == 200, response.text
    return response.json()["clusters"]


def biggest_cluster(client: TestClient, *, scope_id: str = SCOPE) -> dict:
    """成员最多的那个簇。**不写死 id**：簇 id 是随机 uuid（``_new_id``），写死的只能
    是从另一次运行抄来的、这次不存在的值。"""
    found = clusters(client, scope_id=scope_id)
    detector = client.app.state.container.face_detector
    assert found, (
        "空间里一个簇都没有，后面的断言就没有对象"
        f"（检测器 error={detector.error!r}——导入中途显存不够时表现就是这个）"
    )
    return max(found, key=lambda item: item["member_count"])


def instance_rows(client: TestClient, cluster_id: str) -> list:
    """簇里的实例行（真仓储，带 asset 的文件名）。"""
    return list(client.app.state.container.face_repository.list_instances(cluster_id))


def confirm(
    client: TestClient,
    cluster_id: str,
    *,
    name: str = CONFIRMED_NAME,
    family_role: str = CONFIRMED_ROLE,
) -> str:
    response = client.post(
        f"/api/v1/face-clusters/{cluster_id}/confirm",
        json={"name": name, "family_role": family_role},
    )
    assert response.status_code == 200, response.text
    return response.json()["entity"]["id"]


def event_by_file_name(client: TestClient, *, scope_id: str = SCOPE) -> dict[str, str]:
    """文件名 → 事件 id。**读的是时间线端点**：这一条要钉的就是接口上看得见的归属。"""
    body = timeline(client, scope_id=scope_id)
    return {
        photo["file_name"]: event["id"]
        for day in body["days"]
        for event in day["events"]
        for photo in event["photos"]
    }


def mention_rows(container, entity_id: str) -> list[tuple[str, str | None]]:  # noqa: ANN001
    """直接查库对拍 ``entity_mentions``。

    这张表没有 HTTP 读侧（1.0 与 2.0 都没有「看某人写了哪些提及」的端点），所以只能
    直接查——而这正是设计文档要的「对拍」：响应体里根本没有这个数。
    """
    with container.database.connect() as connection:
        rows = connection.execute(
            "SELECT observation_id, face_instance_id FROM entity_mentions WHERE entity_id = ?",
            (entity_id,),
        ).fetchall()
    return [(row["observation_id"], row["face_instance_id"]) for row in rows]


# ---- 事件合流那一条的构造 -----------------------------------------------------------


def pair_sources(client: TestClient) -> tuple[Path, ...]:
    """从真相册成员最多的那个簇里，取两张**不同照片**上的脸的源文件。

    **为什么从库里挑而不是写死两个文件名**：相册换一版、聚类的成员变了，写死的名字要么
    指向一个不在这个簇里的人，要么指向一张不存在的文件——而这条用例的前提是「这两张是
    同一个人」。从簇里挑，前提由数据自己保证。
    """
    cluster = biggest_cluster(client)
    picked: dict[str, Path] = {}
    for row in instance_rows(client, cluster["id"]):
        if row.asset_id in picked:
            continue
        picked[row.asset_id] = ALBUM / row.file_name
        if len(picked) == 2:
            break
    assert len(picked) == 2, f"最大的簇里没有两张不同照片: {cluster['id']}"
    sources = tuple(picked.values())
    assert len({path.name for path in sources}) == 2, sources
    assert all(path.is_file() for path in sources), sources
    return sources


def build_pair(directory: Path, sources: Sequence[Path]) -> Path:
    """把两张真照片复制一份，**只改 EXIF 的拍摄时间与 GPS**。

    改的正是决定事件归并的那两路：时间决定在不在 6 小时硬窗内，GPS 决定 ``location``
    分量（权重 0.25）。改掉它们，两张照片才会分成两个事件——那就是这条用例要造的局面。
    照片本身、人脸、向量、检测器全是真的，构造的只有这两个字段。
    """
    directory.mkdir(parents=True, exist_ok=True)
    for source, stamp in zip(sources, PAIR_STAMPS, strict=True):
        with Image.open(source) as image:
            exif = image.getexif()
            exif[DATETIME_ORIGINAL_TAG] = stamp
            exif[DATETIME_TAG] = stamp
            exif.pop(GPS_INFO_TAG, None)
            image.save(directory / source.name, exif=exif)
    return directory


# ---- 簇 ---------------------------------------------------------------------------


def test_real_photos_form_face_clusters_backed_by_real_instances(album_client: TestClient):
    """真照片能聚出**至少两张脸**的簇，而且那个数是库里的实例行撑起来的。

    「``member_count >= 2``」单独写太软：一个把成员数写死成 2、实例表一条不写的实现
    照样过。所以往下对拍到 ``face_instances``——行数相等、跨 ≥2 张照片、接口给的样本
    真的是这个簇的实例。三者一起，``member_count`` 才是一个有据可查的数。
    """
    import_album(album_client)

    cluster = biggest_cluster(album_client)
    rows = instance_rows(album_client, cluster["id"])

    assert cluster["member_count"] >= 2
    assert len(rows) == cluster["member_count"]
    assert len({row.asset_id for row in rows}) >= 2, "这个簇的脸全来自同一张照片，不是「同一个人」"
    assert rows, "簇的样本与实例都为空，上面的相等是 0 == 0 的假绿"
    assert {sample["face_instance_id"] for sample in cluster["samples"]} <= {row.id for row in rows}
    assert all(row.cluster_id == cluster["id"] for row in rows)


def test_confirming_a_cluster_writes_one_mention_per_face_instance(album_client: TestClient):
    """确认之后 ``entity_mentions`` 的行数**等于该簇的实例数**（直接查库对拍）。

    这张表是事件侧的地基：``AssignEvents`` 的 ``person_score``（权重 0.20）读的就是它，
    少了它事件照分照建、只是分错。只数行数还不够——「把 A 照片的脸记到 B 照片上」的
    实现行数照样对，所以两个集合（脸、观察）逐一对齐。
    """
    import_album(album_client)
    cluster = biggest_cluster(album_client)
    rows = instance_rows(album_client, cluster["id"])

    entity_id = confirm(album_client, cluster["id"])

    recorded = mention_rows(album_client.app.state.container, entity_id)

    assert len(recorded) == len(rows)
    assert {face for _observation, face in recorded} == {row.id for row in rows}
    assert {observation for observation, _face in recorded} == {row.observation_id for row in rows}


def test_confirming_folds_two_events_of_the_same_person_back_together(
    album_client: TestClient, tmp_path: Path
):
    """确认把**同一个人**被拆成两个事件的照片并回一个事件。

    做法：先取两张属于同一个簇、当前分属两个不同事件的照片，记下各自的事件 id；确认之后
    再查，两者应在同一个事件里。

    **真相册给不出这一对，这一点是实测的，不是省事。** 53 张照片的拍摄日期两两不同
    （13 个日期），归并因此一天一个事件；而 ``ConfirmedPersonEvents.resegment`` 只在
    ``CANDIDATE_WINDOW_HOURS``（6 小时）硬窗内合并——一个簇里的照片哪怕跨了好几天，
    也没有任何两个事件落进那个窗。实测确认 11 张脸的那个簇之后，它牵涉的 3 个事件
    （2019-01-10 / 2019-07-22 / 2023-08-06）一个都没合并。

    所以改用**构造的夹具**：从真相册里取同一个簇的两张照片，只把 EXIF 的拍摄时间改到
    同一天相隔 5.5 小时、并去掉 GPS（见 ``build_pair``）。这样它们确认前分属两个事件，
    而「同一个人 ↔ 事件里的人」那一项确认前恒 0、确认后给 0.16，正好跨过 0.50。
    """
    import_album(album_client)
    sources = pair_sources(album_client)
    pair_dir = build_pair(tmp_path / "pair", sources)

    response = album_client.post(
        "/api/v1/imports",
        json={"directory": str(pair_dir), "scope_id": PAIR_SCOPE, "limit": 10},
    )
    assert response.status_code == 201, response.text
    assert response.json()["failures"] == [], response.text

    before = event_by_file_name(album_client, scope_id=PAIR_SCOPE)
    assert set(before) == {path.name for path in sources}, "构造出来的小相册里不是这两张照片"
    left, right = (path.name for path in sources)
    assert before[left] != before[right], "两张照片本来就在同一个事件里，这条用例测不到合并"
    left_event, right_event = before[left], before[right]

    cluster = biggest_cluster(album_client, scope_id=PAIR_SCOPE)
    rows = instance_rows(album_client, cluster["id"])
    assert cluster["member_count"] >= 2
    assert {row.file_name for row in rows} == {left, right}, "两张照片的脸没有落进同一个簇"

    confirm(album_client, cluster["id"])

    after = event_by_file_name(album_client, scope_id=PAIR_SCOPE)
    assert after[left] == after[right], f"{left_event} 与 {right_event} 没有并起来"
    days = timeline(album_client, scope_id=PAIR_SCOPE)["days"]
    merged = [event for day in days for event in day["events"]]
    assert len(merged) == 1
    assert merged[0]["id"] == after[left]
    assert {photo["file_name"] for photo in merged[0]["photos"]} == {left, right}


# ---- 事实与外观 -------------------------------------------------------------------


def test_confirming_writes_the_family_role_fact(album_client: TestClient):
    """``confirm`` 写一条 ``(名字, 家庭角色, 角色)`` 的事实，证据指向这个簇的观察。

    「facts 表非空」太软：一条 ``(随便, 随便, 随便)`` 也能过。三元组 + status +
    evidence 一起断言，才钉住「这条事实是关于**这个**人、**这个**角色的」——而证据集合
    必须正好是这个簇的观察，那是「事实有据可查」的唯一凭证。
    """
    import_album(album_client)
    cluster = biggest_cluster(album_client)
    rows = instance_rows(album_client, cluster["id"])

    confirm(album_client, cluster["id"])

    facts = SqliteFactRepository(album_client.app.state.container.database)
    records = [item for item in facts.list_facts(SCOPE, limit=100) if item.predicate == FAMILY_ROLE_PREDICATE]

    assert len(records) == 1, "家庭角色事实不止一条：确认这条路写重了"
    fact = records[0]
    assert fact.subject == CONFIRMED_NAME
    assert fact.object == CONFIRMED_ROLE
    assert fact.status == "active"
    # 用户确认出来的角色是确定的（1.0 ``db.py:5170`` 的 ``confidence=1.0``）。
    assert fact.confidence == 1.0
    assert set(fact.evidence_ids) == {row.observation_id for row in rows}


def test_confirming_collects_appearance_evidence_for_the_cluster(album_client: TestClient):
    """确认会顺手采外观证据（Task 85d），脸一定落在该簇的实例里，内容来自模型。

    ``list_for`` 默认滤掉空 clothing，这里传 ``include_empty=True``：**要断言的是
    「有没有采到」**，不是「模型这次说的衣服非空」——用默认值的话，「一条都没采到」与
    「采到了但模型没看出衣服」会得到同一个绿。另外两条钉的是这个规则本身：
    每人每事件一张（不是每张脸一张），以及落库的是模型回的那件衣服。
    """
    import_album(album_client)
    cluster = biggest_cluster(album_client)
    rows = instance_rows(album_client, cluster["id"])

    entity_id = confirm(album_client, cluster["id"])

    container = album_client.app.state.container
    evidence = container.appearance_repository.list_for(entity_id, include_empty=True)

    assert evidence, "确认一个人之后一条外观证据都没有——取样那一步没接上或没跑"
    assert {item.face_instance_id for item in evidence} <= {row.id for row in rows}
    assert {item.observation_id for item in evidence} <= {row.observation_id for row in rows}
    assert all(item.person_id == entity_id for item in evidence)
    assert all(item.clothing == (CLOTHING_SUBJECT,) for item in evidence)
    assert all(item.model_name == FakePersonVision.model_id for item in evidence)

    events = _person_events(container, entity_id)
    assert len(evidence) == len(events), "取样是**每人每事件一张**，不是每张脸一张"


def _person_events(container, entity_id: str) -> set[str]:  # noqa: ANN001
    """这个人牵涉的事件 id（从提及与事件成员表对拍）。"""
    with container.database.connect() as connection:
        rows = connection.execute(
            "SELECT DISTINCT eo.event_id FROM entity_mentions em"
            " JOIN event_observations eo ON eo.observation_id = em.observation_id"
            " WHERE em.entity_id = ?",
            (entity_id,),
        ).fetchall()
    return {row["event_id"] for row in rows}


# ---- 画像 -------------------------------------------------------------------------


def test_insight_run_writes_a_qualified_portrait_for_a_core_person(album_client: TestClient):
    """跑一趟画像（Task 86g/86i），核心人物的画像要**内容合格**地落库。

    三条是设计文档点名要钉的：汉字数 80–500、主题 2–6 条、每条主题的引用非空。它们正是
    「内容不只数量」的落点——只断言「有一版画像」的话，一版 41 个汉字的确定性兜底、甚至
    一版空文本，都能过。

    三条之上再加两条**只有真链路满足**的：模型写的那句独有措辞真的在库里（兜底模板
    没有这一句，长度也不够），以及每条引用都指向这个人真有的瞬间（``validate_portrait``
    的引用守卫会丢掉不存在的 id——编的引用进不来）。
    """
    import_album(album_client)
    cluster = biggest_cluster(album_client)
    entity_id = confirm(album_client, cluster["id"])

    run = album_client.post("/api/v1/person-insights/run", json={"scope_id": SCOPE})
    assert run.status_code == 200, run.text
    assert run.json()["core"] >= 1, "没有核心人物，画像那一趟就没有对象"
    assert run.json()["portraits"] >= 1, "一趟跑完一版画像都没落库"

    response = album_client.get(f"/api/v1/people/{entity_id}/portrait")
    assert response.status_code == 200, response.text
    portrait = response.json()

    text = str(portrait["portrait_text"])
    # 数的是**汉字**，不是字符总数：标点与空白不该替一段空话凑够长度。
    hanzi = len(re.findall(r"[一-鿿]", text))
    assert 80 <= hanzi <= 500, f"画像汉字数 {hanzi} 不在 80–500 之间: {text}"

    themes = list(portrait["themes"])
    assert 2 <= len(themes) <= 6, f"主题条数 {len(themes)} 不在 2–6 之间"

    for theme in themes:
        assert theme["evidence_refs"], f"主题「{theme.get('title')}」一条证据引用都没有"

    assert PORTRAIT_MARK in text, "库里那一段不是模型写的——三次校验没过，落的是确定性兜底"

    container = album_client.app.state.container
    moments = {item.id for item in container.moment_repository.list_for(person_id=entity_id, scope_id=SCOPE)}
    assert moments, "这个人一条瞬间都没有，画像的引用就无从谈起"
    for theme in themes:
        refs = list(theme["evidence_refs"])
        assert {ref["kind"] for ref in refs} == {"person_moment"}
        assert {ref["id"] for ref in refs} <= moments

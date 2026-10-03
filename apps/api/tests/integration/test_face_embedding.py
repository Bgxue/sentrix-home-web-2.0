"""buffalo_l 人脸链路：向量、复核判据、真权重。

三层，各自的证明力不同：

1. **纯算术**（blob 的缩放与通道序、``_normalize_sharpness``、``align_face_crop``）——
   不建任何 session，用一个记录入参的假 session 读它收到的张量；
2. **判据表**——注入假检测器与假 FaceAnalysis，把 ``0.7`` / ``0.003`` / ``0.55`` /
   ``64`` 四个阈值逐个**骑在边界上**验（阈值两侧各一条）；
3. **真权重**（缺席时 ``skip``）——512 维、真照片上真的有 ``verified``。

**第二层是这份用例的主体。** 真权重那一层只能证明「这台机器上跑得通」：把 0.7 写成
0.6、把 0.003 写成 0.0003，真照片上大概率仍然全绿，因为真实人脸的分数与面积都离阈值
很远。边界只有构造出来的图上才有。

**假件不比真契约宽松**（S4 的教训）：``StubFace`` 复刻 insightface ``Face`` 的五个字段
与 numpy 数组类型，``StubAnalysis.get`` 收的是子图本身，判据读的每个字段都真的被断言
读到过。

**为什么真权重那层要断言「有 verified」。** 实测（``s5-deviations`` 第 59 条）RetinaFace
自己的关键点在 ``landmark_sanity`` 下一个都过不了——实现里如果忘了把判据的关键点换成
SCRFD 的，真照片上会每一张都是 ``uncertain``，而「检出了 N 张脸」那句断言照样绿。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from conftest import skip_or_fail_on_load_error

from sentrix.infrastructure.models.face_adapter import (
    DEFAULT_MODEL_NAME,
    EMBEDDING_MODEL,
    MODELS_SUBDIR,
    RECOGNITION_SIZE,
    RECOGNITION_WEIGHTS,
    UPSCALE_TARGET,
    FaceAdapter,
    _laplacian_variance,
    _normalize_sharpness,
    align_face_crop,
)
from sentrix.infrastructure.models.face_detector import expand_crop

PHOTO_DIR = Path("/home/asus/Github/Sentrix-Home-Web/data/album3-v2-source/photos")
REAL_RECOGNITION = Path.home() / ".insightface" / MODELS_SUBDIR / DEFAULT_MODEL_NAME / RECOGNITION_WEIGHTS

# 真权重那层取前 10 张。**不是 6**：实测这一册相册的前几张是远景合影，所有候选都小于
# ``FACE_MIN_SIZE``（64px），取 6 张会得到「一张脸都没有」——那是**数据**如此，不是链路坏了，
# 但用例会红，而红的是测试的假设。10 张覆盖到有脸的那几张，同时把耗时压在十几秒。
SAMPLE_SIZE = 10
# 真权重的门槛**不能只看文件在不在**——那是代理判据。可见的守卫挪进 ``real_faces``
# 夹具里（真加载一次），理由写在那里。


# ---- 假件 --------------------------------------------------------------------------


@dataclass
class StubFace:
    """insightface ``Face`` 的最小复刻：只带这条链路真读的五个字段。"""

    bbox: Any
    det_score: float
    kps: Any
    embedding: Any
    pose: Any


class StubAnalysis:
    """``FaceAnalysis`` 的最小复刻：``get`` 收的是子图，返回预置的人脸表。"""

    def __init__(self, faces: list[StubFace]) -> None:
        self.faces = faces
        self.subs: list[Any] = []

    def get(self, sub: Any) -> list[StubFace]:
        self.subs.append(sub)
        return self.faces


class StubDetector:
    """假检测器。``available`` 恒真——「权重在不在」是另一条用例的事。

    ``candidates`` 可以是列表，也可以是 ``(image) -> 列表``：三级降级要按**图的大小**
    给出不同的答案，静态列表表达不了那件事。``sizes`` 记下每次调用看到的图有多宽，
    于是「有没有真的放大重试」变成一个可观测量，而不是从结果反推的猜测。
    """

    available = True

    def __init__(self, candidates: Any) -> None:
        self._candidates = candidates
        self.sizes: list[tuple[int, int]] = []

    def detect(self, image: Any) -> list[dict[str, Any]]:
        self.sizes.append((int(image.shape[1]), int(image.shape[0])))
        return list(self._candidates(image) if callable(self._candidates) else self._candidates)


class VectorSession:
    """假 ONNX session：``get_inputs`` + ``run`` 就够这条链路跑完。"""

    def __init__(self, *, outputs: int = 512, record: bool = False) -> None:
        self.blobs: list[Any] = []
        self.outputs = outputs
        self._record = record

    def get_inputs(self) -> list[Any]:
        return [SimpleNamespace(name="input")]

    def run(self, output_names: Any, feed: dict[str, Any]) -> list[Any]:
        if self._record:
            self.blobs.append(next(iter(feed.values())))
        return [np.full((1, self.outputs), 0.25, dtype=np.float32)]


# ---- 构造 --------------------------------------------------------------------------


def model_root_with_weights(tmp_path: Path, model_name: str = DEFAULT_MODEL_NAME) -> Path:
    """一个只有识别权重的假模型目录：``available`` 只看文件在不在，不看内容。"""
    root = tmp_path / "insightface"
    (root / MODELS_SUBDIR / model_name).mkdir(parents=True, exist_ok=True)
    (root / MODELS_SUBDIR / model_name / RECOGNITION_WEIGHTS).write_bytes(b"stub")
    return root


def flat_image(tmp_path: Path, width: int, height: int) -> Path:
    """一张纯色图。**纯色的 Laplacian 方差是 0**，于是锐度项恰好取 0——质量边界那条
    用例靠这一点把 ``0.55`` 骑住（真实纹理的锐度会把质量推过门槛）。"""
    import cv2

    path = tmp_path / f"flat-{width}x{height}.jpg"
    cv2.imwrite(str(path), np.full((height, width, 3), 128, dtype=np.uint8))
    return path


def load(path: Path) -> Any:
    import cv2

    return cv2.imread(str(path))


def retina_candidate(bbox: list[float], confidence: float = 0.9) -> dict[str, Any]:
    x1, y1, x2, y2 = bbox
    width, height = x2 - x1, y2 - y1
    landmarks = [
        [x1 + width * 0.3, y1 + height * 0.4],
        [x1 + width * 0.7, y1 + height * 0.4],
        [x1 + width * 0.5, y1 + height * 0.6],
        [x1 + width * 0.35, y1 + height * 0.8],
        [x1 + width * 0.65, y1 + height * 0.8],
    ]
    return {"bbox": [float(value) for value in bbox], "confidence": float(confidence), "landmarks": landmarks}


def scrfd_face(
    image: Any,
    bbox: list[float],
    *,
    score: float = 0.9,
    yaw: float = 0.0,
    shift: tuple[float, float] = (0.0, 0.0),
    eyes: float = 0.4,
) -> StubFace:
    """一个 SCRFD 结果，坐标落在 ``expand_crop`` 给出的**子图**坐标系里。

    子图原点由被测代码自己的 ``expand_crop`` 算出来，不在这里手抄：margin 或 min_side
    改掉时，手抄的偏移量会让复核悄悄错位，而用例仍然绿。
    """
    _sub, sub_x, sub_y = expand_crop(image, bbox)
    x1, y1, x2, y2 = bbox
    width, height = x2 - x1, y2 - y1
    kps = [
        [x1 + width * 0.5 - width * eyes / 2, y1 + height * 0.4],
        [x1 + width * 0.5 + width * eyes / 2, y1 + height * 0.4],
        [x1 + width * 0.5, y1 + height * 0.6],
        [x1 + width * 0.35, y1 + height * 0.8],
        [x1 + width * 0.65, y1 + height * 0.8],
    ]
    return StubFace(
        bbox=np.asarray(
            [x1 - sub_x + shift[0], y1 - sub_y + shift[1], x2 - sub_x + shift[0], y2 - sub_y + shift[1]],
            dtype=np.float32,
        ),
        det_score=float(score),
        kps=np.asarray(
            [[point[0] - sub_x + shift[0], point[1] - sub_y + shift[1]] for point in kps], dtype=np.float32
        ),
        embedding=np.asarray([0.1] * 512, dtype=np.float32),
        pose=np.asarray([0.0, float(yaw), 0.0], dtype=np.float32),
    )


def build_adapter(
    tmp_path: Path,
    *,
    candidates: list[dict[str, Any]],
    sub_faces: list[StubFace] | None = None,
    model_name: str | None = None,
    detector: StubDetector | None = None,
) -> tuple[FaceAdapter, StubAnalysis]:
    """一个不碰真权重的适配器：检测器与 FaceAnalysis 都注入，识别 session 给假向量。"""
    adapter = FaceAdapter(
        detector if detector is not None else StubDetector(candidates),
        model_name=model_name,
        model_root=str(model_root_with_weights(tmp_path, model_name or DEFAULT_MODEL_NAME)),
    )
    analysis = StubAnalysis(sub_faces or [])
    adapter._app = analysis
    adapter._recognition_session = VectorSession()
    return adapter, analysis


# ---- 第一层：纯算术 ----------------------------------------------------------------


def test_the_recognition_blob_is_normalized_rgb_at_112_by_112(tmp_path: Path) -> None:
    """112×112、``(x - 127.5) / 128.0``，**通道序是 RGB**。

    三个数一起钉。只验形状的话，把 ``convert("RGB")`` 去掉、或把常数写成 127.0，都验不
    出来；像素取 255 / 128 / 0 三个点，正好把均值与尺度一起夹住。输入图特意不是方的，
    好让「resize 到 112×112」这一步真的发生。
    """
    from PIL import Image

    adapter, _ = build_adapter(tmp_path, candidates=[])
    session = VectorSession(record=True)
    adapter._recognition_session = session

    embedding = adapter._recognition_embed(Image.new("RGB", (300, 200), (255, 128, 0)))

    assert len(session.blobs) == 1, "识别 session 没有被调用"
    blob = session.blobs[0]
    assert blob.shape == (1, 3, RECOGNITION_SIZE, RECOGNITION_SIZE)
    assert blob[0, 0, 0, 0] == pytest.approx((255 - 127.5) / 128.0, abs=1e-6)
    assert blob[0, 1, 0, 0] == pytest.approx((128 - 127.5) / 128.0, abs=1e-6)
    assert blob[0, 2, 0, 0] == pytest.approx((0 - 127.5) / 128.0, abs=1e-6)
    assert len(embedding) == 512
    assert all(isinstance(value, float) for value in embedding)


def test_an_aligned_crop_comes_back_in_rgb() -> None:
    """OpenCV 的 BGR 图裁出来必须翻成 RGB——少翻一次不报错，只是向量全废。"""
    bgr = np.zeros((40, 40, 3), dtype=np.uint8)
    bgr[:, :] = (10, 20, 30)  # B=10, G=20, R=30

    crop = align_face_crop(bgr, [0, 0, 40, 40])

    assert crop.size == (40, 40)
    assert crop.getpixel((0, 0)) == (30, 20, 10)


def test_a_box_that_cannot_be_cropped_raises() -> None:
    with pytest.raises(ValueError):
        align_face_crop(np.zeros((40, 40, 3), dtype=np.uint8), [10, 10, 10, 40])


def test_normalize_sharpness_clips_at_the_reference_bounds() -> None:
    assert _normalize_sharpness(0.0) == 0.0
    assert _normalize_sharpness(float(np.expm1(7.0))) == 1.0
    assert _normalize_sharpness(float(np.expm1(2.5))) == pytest.approx(0.0, abs=1e-9)
    assert 0.0 < _normalize_sharpness(float(np.expm1(4.75))) < 1.0


def test_laplacian_variance_is_zero_on_a_flat_crop() -> None:
    from PIL import Image

    assert _laplacian_variance(Image.new("RGB", (112, 112), (128, 128, 128))) == 0.0


# ---- 第二层：判据表 ----------------------------------------------------------------

# 判据表统一用同一张图与同一个框，只改被验的那一项——这样「红」指向的一定是被改的那条判据。
BOX = [200.0, 200.0, 600.0, 600.0]


def test_a_confirmed_candidate_becomes_verified_and_eligible(tmp_path: Path) -> None:
    image_path = flat_image(tmp_path, 1200, 1200)
    adapter, analysis = build_adapter(
        tmp_path,
        candidates=[retina_candidate(BOX)],
        sub_faces=[scrfd_face(load(image_path), BOX, score=0.9)],
    )

    faces = adapter.detect(image_path)

    assert len(faces) == 1
    face = faces[0]
    assert analysis.subs, "SCRFD 复核没拿到子图"
    assert face.validity == "verified"
    assert face.identity_eligible is True
    assert face.quality is not None and face.quality >= 0.55
    # 框仍是 RetinaFace 的那一个：复核只改判定，不改这条候选的坐标。
    assert face.bbox == tuple(BOX)
    assert face.embedding_model == EMBEDDING_MODEL
    assert len(face.embedding) == 512


def test_the_score_gate_is_seventy_hundredths(tmp_path: Path) -> None:
    """骑在 ``0.7`` 上：正好 0.7 算过，0.699 不算。"""
    image_path = flat_image(tmp_path, 1200, 1200)
    image = load(image_path)

    at_gate, _ = build_adapter(
        tmp_path,
        candidates=[retina_candidate(BOX)],
        sub_faces=[scrfd_face(image, BOX, score=0.7)],
    )
    below, _ = build_adapter(
        tmp_path,
        candidates=[retina_candidate(BOX)],
        sub_faces=[scrfd_face(image, BOX, score=0.699)],
    )

    assert [face.validity for face in at_gate.detect(image_path)] == ["verified"]
    assert [face.validity for face in below.detect(image_path)] == ["uncertain"]


def test_the_area_gate_is_three_thousandths(tmp_path: Path) -> None:
    """骑在 ``0.003`` 上。图 1200×1200 时门槛面积是 4320 像素：4352 过、4224 不过。"""
    image_path = flat_image(tmp_path, 1200, 1200)
    image = load(image_path)
    above = [300.0, 300.0, 364.0, 368.0]  # 64 × 68 = 4352 → 0.003022
    below = [300.0, 300.0, 364.0, 366.0]  # 64 × 66 = 4224 → 0.002933

    over, _ = build_adapter(
        tmp_path, candidates=[retina_candidate(above)], sub_faces=[scrfd_face(image, above)]
    )
    under, _ = build_adapter(
        tmp_path, candidates=[retina_candidate(below)], sub_faces=[scrfd_face(image, below)]
    )

    assert [face.validity for face in over.detect(image_path)] == ["verified"]
    assert [face.validity for face in under.detect(image_path)] == ["uncertain"]


def test_landmarks_that_fail_the_geometry_gate_fall_back_to_uncertain(tmp_path: Path) -> None:
    """两眼挤在一起 → ``landmark_sanity`` 拒 → 合格的脸也只能当证据。"""
    image_path = flat_image(tmp_path, 1200, 1200)
    adapter, _ = build_adapter(
        tmp_path,
        candidates=[retina_candidate(BOX)],
        sub_faces=[scrfd_face(load(image_path), BOX, eyes=0.02)],
    )

    faces = adapter.detect(image_path)

    assert [face.validity for face in faces] == ["uncertain"]
    assert faces[0].identity_eligible is False


def test_a_second_detector_that_disagrees_on_the_location_falls_back_to_uncertain(
    tmp_path: Path,
) -> None:
    image_path = flat_image(tmp_path, 1200, 1200)
    adapter, _ = build_adapter(
        tmp_path,
        candidates=[retina_candidate(BOX)],
        sub_faces=[scrfd_face(load(image_path), BOX, shift=(400.0, 400.0))],
    )

    assert [face.validity for face in adapter.detect(image_path)] == ["uncertain"]


def test_the_quality_gate_is_what_separates_verified_from_eligible(tmp_path: Path) -> None:
    """``identity_eligible`` 比 ``verified`` 多一道 0.55 的质量门。

    构造：纯色图（锐度项 0）、正脸（姿态项满分）、检测分压到 0.7（检测项 0.28），于是
    质量只剩 ``0.28 + 0.2 × 面积项 + 0.2``，而面积项唯一能调的就是框的大小：
    200×200 给 0.5494（差一点），201×201 给 0.5501（刚过）。两张都仍是 ``verified``。
    """
    image_path = flat_image(tmp_path, 1200, 1200)
    image = load(image_path)
    small = [200.0, 200.0, 400.0, 400.0]
    large = [200.0, 200.0, 401.0, 401.0]

    below, _ = build_adapter(
        tmp_path,
        candidates=[retina_candidate(small)],
        sub_faces=[scrfd_face(image, small, score=0.7)],
    )
    above, _ = build_adapter(
        tmp_path,
        candidates=[retina_candidate(large)],
        sub_faces=[scrfd_face(image, large, score=0.7)],
    )

    below_face = below.detect(image_path)[0]
    above_face = above.detect(image_path)[0]

    assert below_face.validity == "verified" and below_face.identity_eligible is False
    assert above_face.validity == "verified" and above_face.identity_eligible is True
    assert below_face.quality is not None and above_face.quality is not None
    assert below_face.quality < 0.55 <= above_face.quality


def test_a_face_below_the_minimum_size_is_dropped_entirely(tmp_path: Path) -> None:
    """``FACE_MIN_SIZE`` 是**丢弃**不是降级：63 像素的框连一条证据都不留。"""
    image_path = flat_image(tmp_path, 1200, 1200)
    bbox = [200.0, 200.0, 263.0, 263.0]
    adapter, analysis = build_adapter(
        tmp_path,
        candidates=[retina_candidate(bbox)],
        sub_faces=[scrfd_face(load(image_path), bbox)],
    )

    assert adapter.detect(image_path) == []
    assert analysis.subs == [], "小框在复核之前就该被丢掉"


def test_a_candidate_the_second_detector_misses_survives_only_with_a_vector(tmp_path: Path) -> None:
    """SCRFD 没找到脸的那一支：有向量就是 ``uncertain`` 证据，没向量就整条丢掉。

    1.0 ``model_clients.py:1770`` 把「向量拿不到」写成 ``continue``——这一支存在的理由
    就是留证据，没有向量就没有证据可留。
    """
    image_path = flat_image(tmp_path, 1200, 1200)

    kept, _ = build_adapter(tmp_path, candidates=[retina_candidate(BOX)], sub_faces=[])
    dropped, _ = build_adapter(tmp_path, candidates=[retina_candidate(BOX)], sub_faces=[])
    dropped._recognition_session = None

    faces = kept.detect(image_path)

    assert [face.validity for face in faces] == ["uncertain"]
    assert faces[0].identity_eligible is False
    assert faces[0].identity_ready is True
    assert dropped.detect(image_path) == []


def test_the_embedding_carries_both_the_model_name_and_the_model_version(tmp_path: Path) -> None:
    """``embedding_model`` 恒为 buffalo_l；``embedding_version`` 取模型包名。

    两者一起进 ``(model_name, model_version)`` 签名比对，所以换一个模型包时旧簇会被整体
    跳过——这正是那个「变量名叫 MODEL_NAME、存的却是 version」的字段的真实后果。
    """
    image_path = flat_image(tmp_path, 1200, 1200)
    adapter, _ = build_adapter(
        tmp_path,
        candidates=[retina_candidate(BOX)],
        sub_faces=[scrfd_face(load(image_path), BOX)],
        model_name="buffalo_l_custom",
    )

    face = adapter.detect(image_path)[0]

    assert face.embedding_model == EMBEDDING_MODEL
    assert face.embedding_version == "buffalo_l_custom"
    assert adapter.model_name == "buffalo_l_custom"


def test_the_recognition_weights_live_under_the_model_root(tmp_path: Path) -> None:
    """权重路径是 ``<root>/models/<model>/w600k_r50.onnx``——insightface 的目录约定。

    这条路径写错**不会报错**：``FaceAnalysis`` 找不到模型会去 GitHub 下载，进程就挂在一条
    到 github.com 的 HTTPS 连接上（实测：CPU 空转、日志一个字没有、pytest 卡住直到超时）。
    """

    root = tmp_path / "insightface"
    adapter = FaceAdapter(StubDetector([]), model_root=str(root))

    assert adapter.recognition_path == str(root / MODELS_SUBDIR / DEFAULT_MODEL_NAME / RECOGNITION_WEIGHTS)
    assert adapter.available is False
    assert adapter.detect(flat_image(tmp_path, 32, 32)) == []

    (root / MODELS_SUBDIR / DEFAULT_MODEL_NAME).mkdir(parents=True)
    (root / MODELS_SUBDIR / DEFAULT_MODEL_NAME / RECOGNITION_WEIGHTS).write_bytes(b"stub")
    assert adapter.available is True


def test_a_detector_that_raises_does_not_take_the_import_down(tmp_path: Path) -> None:
    """检测抛错时记进 ``error`` 并返回空表——人脸是可选能力，不该让整条导入链失败。"""

    class ExplodingDetector(StubDetector):
        def detect(self, image: Any) -> list[dict[str, Any]]:
            raise RuntimeError("解码器炸了")

    adapter = FaceAdapter(ExplodingDetector([]), model_root=str(model_root_with_weights(tmp_path)))
    adapter._app = StubAnalysis([])
    adapter._recognition_session = VectorSession()

    assert adapter.detect(flat_image(tmp_path, 64, 64)) == []
    assert adapter.error is not None and "解码器炸了" in adapter.error


# ---- 三级降级：身份预置照片 --------------------------------------------------------


def identity_photo(tmp_path: Path, width: int, height: int, name: str = "seed.jpg") -> Path:
    import cv2

    path = tmp_path / name
    cv2.imwrite(str(path), np.full((height, width, 3), 128, dtype=np.uint8))
    return path


UPSCALE_BOX = [100.0, 100.0, 300.0, 300.0]


def upscaled_only_candidates(image: Any) -> list[dict[str, Any]]:
    """只在**放大到 1280 宽**的那张图上给候选。

    用宽度**相等**而不是 ``>=`` 区分两级：1600px 的原图也大于 1280，用 ``>=`` 的话
    「大图不放大」那条用例会拿到原图上的候选，于是它验的就不再是「跳过了第二级」。
    """
    return [retina_candidate(UPSCALE_BOX)] if int(image.shape[1]) == int(UPSCALE_TARGET) else []


def test_an_identity_photo_falls_back_to_an_upscaled_retry(tmp_path: Path) -> None:
    """第二级：放大 1.6 倍重试，框按同一比例换算回原图坐标。

    放大倍数由 1.0 的两条判据算出来：``min(2.0, 1280 / 800) = 1.6``，且 ``1.6 > 1.05``。
    **框换算这一步最容易漏**：不换算的话框会整体放大 1.6 倍，下游拿它去裁剪会裁到
    另一个人的脸上，而任何一层都不会报错。
    """
    image_path = identity_photo(tmp_path, 800, 600)
    detector = StubDetector(upscaled_only_candidates)
    adapter, _ = build_adapter(tmp_path, candidates=[], detector=detector)

    faces = adapter.detect_identity_photo(image_path)

    assert detector.sizes == [(800, 600), (1280, 960)], "放大重试的尺寸不对"
    assert len(faces) == 1
    assert faces[0].bbox == pytest.approx(tuple(value / 1.6 for value in UPSCALE_BOX))


def test_an_image_whose_upscale_would_shrink_it_is_not_upscaled(tmp_path: Path) -> None:
    """``scale > 1.05`` 这条判据：1600px 的图算出来 scale 是 0.8，那是缩小，不做。

    少了它，大图会被**缩小**再检测一遍——小脸变得更小，白跑一次还更差。
    """
    image_path = identity_photo(tmp_path, 1600, 1200)
    detector = StubDetector(upscaled_only_candidates)
    adapter, _ = build_adapter(tmp_path, candidates=[], detector=detector)

    faces = adapter.detect_identity_photo(image_path)

    assert detector.sizes == [(1600, 1200)], "1600px 的图不该被放大重试"
    assert [face.bbox for face in faces] == [(0.0, 0.0, 1600.0, 1200.0)]


def test_a_small_identity_photo_goes_straight_to_the_whole_image_fallback(tmp_path: Path) -> None:
    """``max_dim > 600`` 这条判据：500px 的图连放大都不试，直接进整图兜底。"""
    image_path = identity_photo(tmp_path, 500, 400)
    detector = StubDetector(upscaled_only_candidates)
    adapter, _ = build_adapter(tmp_path, candidates=[], detector=detector)

    faces = adapter.detect_identity_photo(image_path)

    assert detector.sizes == [(500, 400)]
    assert len(faces) == 1
    assert faces[0].bbox == (0.0, 0.0, 500.0, 400.0)
    assert faces[0].quality == 0.8
    assert faces[0].pose_bucket == "frontal"
    assert len(faces[0].embedding) == 512


def test_the_whole_image_fallback_is_not_a_dead_path_any_more(tmp_path: Path) -> None:
    """**第三级在 1.0 是死路径**（``identity_adapter`` 恒 ``None``），2.0 接在识别 session 上。

    没有这一条，「把死路径接活」与「照抄一条永远返回 ``[]`` 的分支」在测试里长得一模一样。
    """
    image_path = identity_photo(tmp_path, 200, 200)
    live, _ = build_adapter(tmp_path, candidates=[])
    dead, _ = build_adapter(tmp_path, candidates=[])
    dead._recognition_session = None

    assert len(live.detect_identity_photo(image_path)) == 1
    assert dead.detect_identity_photo(image_path) == []


# ---- 第三层：真权重 ----------------------------------------------------------------


@pytest.fixture(scope="module")
def real_faces() -> list[tuple[str, list[Any]]]:
    """前 ``SAMPLE_SIZE`` 张真照片各跑一次 ``detect``，**整个模块共用这一次结果**。

    两条真权重用例都读它，而不是各跑一遍：每张照片要走一遍 RetinaFace 分块 + 每张
    候选一次 SCRFD 复核，重复跑等于把 CI 的时间翻倍而一条新信息都不加。

    **守卫也在这里。** 原先两条真权重用例各挂 ``@needs_weights``，而那个标记只查
    ``REAL_RECOGNITION.is_file()``——文件在、显存不够时不跳过，用例在运行期报
    「前 10 张照片一张脸都没检出」，读起来像数据问题或实现 bug，实际是环境
    （2026-09-26 实测：8100 的 vLLM 占住 24 GB 里的 22 GB）。判据改成**真的加载
    一次**。
    """
    adapter = FaceAdapter()
    if not adapter.available:
        pytest.skip(f"缺 buffalo_l 识别权重 {REAL_RECOGNITION}")
    try:
        faces = [(name, adapter.detect(path)) for name, path in real_photos(SAMPLE_SIZE)]
    except Exception as error:  # noqa: BLE001 - 只有 CudaProviderMissingError 会穿出来
        skip_or_fail_on_load_error(str(adapter.error or error), what="buffalo_l")
    # **异常接不住的那一路。** ``detect()`` 对除 ``CudaProviderMissingError`` 外的异常是
    # **吞掉并返回 []**（它自己的理由：「人脸是可选能力，不该让整条导入链失败」），错因
    # 记在 ``adapter.error``。于是「模型加载不了」与「这批照片本来就没脸」在返回值上
    # 长得一模一样，断言会去怪数据——实测全量 CI 里正是这样红了 2 条，而单独跑同一个
    # 文件却是绿的（两个场景的内存压力不同）。
    if adapter.error:
        skip_or_fail_on_load_error(adapter.error, what="buffalo_l")
    return faces


def real_photos(limit: int) -> list[tuple[str, Path]]:
    """真照片的**路径**——适配器的入口收的是路径，不是已解码的图。"""
    import cv2

    if not PHOTO_DIR.is_dir():
        pytest.skip(f"缺照片目录 {PHOTO_DIR}")
    photos: list[tuple[str, Path]] = []
    for path in sorted(PHOTO_DIR.iterdir())[:limit]:
        if cv2.imread(str(path)) is not None:
            photos.append((path.name, path))
    if not photos:
        pytest.skip("照片目录里没有 OpenCV 读得出来的图")
    return photos


def test_real_photos_yield_buffalo_embeddings(real_faces: list[tuple[str, list[Any]]]) -> None:
    """真权重下的形状：每一张脸都是 512 维、都标着 buffalo_l。

    实测（2026-09-23，整册 53 张）：315 个 RetinaFace 候选 → 117 个过 ``FACE_MIN_SIZE``
    → 117 张带向量 → 其中 30 张 ``verified``。**数量不写进断言**：换一册相册就变的是数据，
    不是代码；这里只断言「这一批里真的有脸，而且每一张的形状都对」。
    """
    faces = [face for _name, items in real_faces for face in items]
    assert faces, f"前 {SAMPLE_SIZE} 张照片一张脸都没检出——换一批照片，或把 SAMPLE_SIZE 调大"
    for name, items in real_faces:
        for face in items:
            assert len(face.embedding) == 512, f"{name} 的向量不是 512 维: {len(face.embedding)}"
            assert face.embedding_model == EMBEDDING_MODEL
            assert face.validity in {"verified", "uncertain"}


def test_scrfd_landmarks_are_what_lets_a_real_face_be_verified(
    real_faces: list[tuple[str, list[Any]]],
) -> None:
    """**真照片上必须有 ``verified``。** 这条才是第 59 条那个发现的验收。

    实现里如果忘了把判据的关键点换成 SCRFD 的（照抄 RetinaFace 的），每一张脸都会停在
    ``uncertain``——而「检出了 N 张脸」那句断言照样绿。所以这里断言的是**判定本身**。

    实测的对照很硬：整册 53 张里 117 张脸带向量，而 ``verified`` 只有 30 张。也就是说
    「有向量」与「够格进簇」这两件事在这册数据上差了一个量级——把两者混成一条断言，
    这个区别就消失了。
    """
    verified = sum(1 for _name, items in real_faces for face in items if face.validity == "verified")
    total = sum(len(items) for _name, items in real_faces)

    assert verified >= 1, f"{total} 张脸里没有一张 verified——判据读的可能还是 RetinaFace 的关键点"

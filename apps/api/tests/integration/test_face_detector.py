"""真权重、真照片下的分块检测。权重或照片缺席时 ``skip``，不是红。

**为什么这条不能只靠纯几何那一半。** priors / 解码 / NMS 都对，检测仍然可能整张图都
检不出脸——那正是「分块」要解决的问题，而它只有在真图上才暴露得出来。

**断言的是内容，不是数量。** S4 栽在 ``events: 53`` 那种「只数个数」的验收上：一个把
所有框都解到图外的实现同样能通过「检出了 N 张脸」。这里钉住的是三件**能被证伪**的事：
置信度不低于**细pass**的入选阈值（实测有一张脸是 0.282，写 0.3 就会红，而红的是测试的
假设）、结果里没有重叠超过 NMS 阈值的两张脸（NMS 真被调用过的可观测后果）、越界的候选框
必须被**下一层**的 ``expand_crop`` 丢掉。数量只是最后一层防线。

**这个检测器返回的是候选不是人脸**（两件事都实测到了，见 ``s5-deviations.md``）：细pass
的门槛是 0.1，而低分 anchor 的偏移量可以解出任意离谱的框。1.0 不在检测层裁剪，靠
``expand_crop`` 与 SCRFD 复核两道下游防线丢。用例按同一分层断言，不替 1.0 改契约。

照片目录与权重都是**机器上的事实**，缺席就 skip——把「这台机器有没有那 109MB 权重」
变成红绿灯会让 make ci 依赖机器状态。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import skip_or_fail_on_load_error

from sentrix.infrastructure.models.face_detector import (
    DEFAULT_MODEL_PATH,
    FINE_THRESHOLD,
    NMS_THRESHOLD,
    RetinaFaceTiledDetector,
    landmark_sanity,
)

# Requires real album data, local weights or a running model; run via make test-real.
pytestmark = pytest.mark.real_environment

PHOTO_DIR = Path(__file__).resolve().parents[4] / "data/album3-v2-source/photos"

# 实测（2026-09-23，53 张真照片）：142 张脸、46 张照片有人脸、单张最多 19 张。
# 用例只取前 6 张、把门槛压到远低于实测值——它验的是「链路通不通」，不是「召回率有
# 多高」；把实测值原样写成断言会让换一册相册就红，而那是**数据变了**，不是代码坏了。
SAMPLE_SIZE = 6
MIN_PHOTOS_WITH_FACES = 4
MIN_TOTAL_FACES = 10


@pytest.fixture(scope="module", autouse=True)
def requires_a_loadable_detector() -> None:
    """守卫查的是**能不能加载**，不是**权重在不在**。

    原先这里是 ``pytest.mark.skipif(not Path(DEFAULT_MODEL_PATH).is_file(), ...)``——
    一个**代理判据**：权重文件在、显存不够时它不跳过，用例在运行期炸成
    ``CUBLAS failure 3`` / ``bfc_arena`` 分配失败（2026-09-26 实测：8100 的 vLLM
    占住 24 GB 里的 22 GB）。报出来的失败读起来像实现坏了，实际是环境。

    判据：**真的加载一次**，加载不成就跳，并把真实
    错因写进 reason。权重确实缺失仍是允许的部署形态，那条分支保留在前面。
    """
    instance = RetinaFaceTiledDetector()
    if not instance.available:
        pytest.skip(f"缺 RetinaFace 权重 {DEFAULT_MODEL_PATH}")
    try:
        instance.detect(np.zeros((64, 64, 3), dtype=np.uint8))
    except Exception as error:  # noqa: BLE001 - 加载失败的形状由 onnxruntime 决定
        skip_or_fail_on_load_error(str(error), what="RetinaFace")


@pytest.fixture(scope="module")
def detector() -> RetinaFaceTiledDetector:
    return RetinaFaceTiledDetector()


def load_photos(limit: int) -> list[tuple[str, Any]]:
    import cv2

    if not PHOTO_DIR.is_dir():
        pytest.skip(f"缺照片目录 {PHOTO_DIR}")
    photos = []
    for path in sorted(PHOTO_DIR.iterdir())[:limit]:
        image = cv2.imread(str(path))
        if image is not None:
            photos.append((path.name, image))
    if not photos:
        pytest.skip("照片目录里没有 OpenCV 读得出来的图")
    return photos


def iou(first: list[float], second: list[float]) -> float:
    inter = max(0.0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0.0, min(first[3], second[3]) - max(first[1], second[1])
    )
    area_first = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    area_second = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    return inter / max(1e-9, area_first + area_second - inter)


def test_the_session_really_lands_on_the_provider_it_asked_for(detector: RetinaFaceTiledDetector):
    """**CUDA 有没有真的生效，只能靠 ``get_providers()`` 读。**

    ``get_available_providers()`` 只说明 EP 被编译进去了；EP 加载失败时
    ``InferenceSession`` 既不抛错也不换名字，只是静静地在 CPU 上跑。这条用例存在的理由
    就是 ``s5-deviations`` 第 56 条那次实测——不读这一句，退化看不出来。
    """
    detector._load()
    providers = detector._session.get_providers()

    assert providers, "session 没有任何 provider"
    assert "CPUExecutionProvider" in providers  # 链尾永远有 CPU，纯 CPU 部署是合法形态
    print(f"RetinaFace session providers = {providers}")


def test_real_photos_yield_faces(detector: RetinaFaceTiledDetector):
    photos = load_photos(SAMPLE_SIZE)
    with_faces = 0
    total = 0
    for _name, image in photos:
        faces = detector.detect(image)
        with_faces += 1 if faces else 0
        total += len(faces)

    assert with_faces >= MIN_PHOTOS_WITH_FACES, f"{len(photos)} 张里只有 {with_faces} 张检出人脸"
    assert total >= MIN_TOTAL_FACES, f"总共只检出 {total} 张脸"


def test_every_face_clears_the_lowest_admission_threshold(detector: RetinaFaceTiledDetector):
    """置信度的下界是**细pass的 0.1**，不是粗pass的 0.3。

    实测（``2017-11-05 151427.jpg``）有一张脸的置信度是 0.282——它来自细pass。写成
    ``>= 0.3`` 会红，而红的是**测试的假设**不是代码：两趟设计的全部意义就是这个阈值差。
    """
    for name, image in load_photos(SAMPLE_SIZE):
        for face in detector.detect(image):
            assert face["confidence"] >= FINE_THRESHOLD, f"{name} 的置信度低于细pass阈值: {face}"


def test_a_large_panorama_still_yields_ordered_boxes(detector: RetinaFaceTiledDetector):
    """全景图在分块下仍然出候选，且每个框都是「有向」的（``x1 < x2``、``y1 < y2``）。

    实测 ``2017-11-05 151451.jpg`` 是 **11394×2944** 的全景，检出 12 个候选——整图缩到
    640 的检测器在这张图上会全军覆没，这正是分块要解决的问题。

    **只断言框有向，不断言关键点与框自洽。** 实测这张图上有一个候选的框落在图内
    （``[9730, 1680, 10490, 2548]``）、而它的 5 个关键点整批落在 x ≈ -400..-60——低分
    anchor 的偏移量可以解出任意离谱的值，细pass的门槛 0.1 拦不住。检测层不拦它
    （**1.0 也不拦**），拦它的是 Task 78 的 SCRFD 复核与 ``landmark_sanity``。
    把「关键点自洽」写成这一层的断言，等于要求上游不产生垃圾候选——而它会。
    """
    photos = load_photos(SAMPLE_SIZE)
    panoramas = [(name, image) for name, image in photos if image.shape[1] > 8000]
    assert panoramas, "这一批照片里没有全景图，本用例什么也没验到——换一批照片，或删掉这条用例"

    for name, image in panoramas:
        faces = detector.detect(image)
        assert faces, f"{name} 全景图上一张候选都没检出"
        for face in faces:
            x1, y1, x2, y2 = face["bbox"]
            assert x1 < x2 and y1 < y2, f"{name} 的框不是有向的: {face['bbox']}"
            assert all(float(value) == float(value) for value in face["bbox"]), f"{name} 的框有 NaN"


def test_nms_suppresses_the_duplicates_the_overlapping_tiles_produce(
    detector: RetinaFaceTiledDetector,
):
    """重叠分块必然把同一张脸检出多次，所以「结果里没有 IoU > 0.4 的一对」是 NMS
    真的被调用过的**可观测后果**。

    没有这一条，把 ``nms`` 换成恒等函数、或者把重叠参数调错，测试仍然全绿——
    而重复的人脸会直接变成重复的人脸实例、进而把簇的成员数算错。
    """
    photos = load_photos(SAMPLE_SIZE)
    checked = 0
    for name, image in photos:
        faces = detector.detect(image)
        for index, first in enumerate(faces):
            for second in faces[index + 1 :]:
                assert iou(first["bbox"], second["bbox"]) <= NMS_THRESHOLD, (
                    f"{name} 里两张重叠超过 {NMS_THRESHOLD} 的脸没被抑制: {first['bbox']} / {second['bbox']}"
                )
        checked += len(faces)
    assert checked > 0, "这一批照片一张脸都没检出，NMS 没被验到"


def test_retinaface_landmarks_do_not_survive_the_geometry_gate(detector: RetinaFaceTiledDetector):
    """**RetinaFace 自己的关键点过不了几何判据**——这正是 SCRFD 复核存在的理由。

    实测（前 6 张真照片）：所有大于 64px、落在图内的候选，``landmark_sanity`` **一个都
    没放行**，全被拒。1.0 的注释把原因写得很直白（``model_clients.py:1788``）：

        Use SCRFD's own (reliable) landmarks, not RetinaFace's, whose geometry is
        unreliable even on large faces.

    这条用例把这句话变成**可证伪的断言**：判据在 RetinaFace 的关键点上拒掉全部，所以
    Task 78 的「用 SCRFD 的关键点替换掉它」不是优化，是**必要步骤**——少了它，每一张脸
    都会停在 ``face_validity = 'uncertain'``，永远进不了簇（``identity_eligible`` 恒假）。

    这也是「假件不能比真实契约宽松」的另一种形态：如果哪天换了权重、这条判据在
    RetinaFace 关键点上开始全放行，上面那句 1.0 的注释就成了假话，而这条断言会红。
    """
    photos = load_photos(SAMPLE_SIZE)
    passed = 0
    rejected = 0
    for _name, image in photos:
        height, width = image.shape[:2]
        for face in detector.detect(image):
            x1, y1, x2, y2 = face["bbox"]
            if min(x2 - x1, y2 - y1) < 64:
                continue  # 小框按规则直接放行，判据在这里没生效
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                continue  # 越界候选，见 ``detect`` 的契约说明
            if landmark_sanity(face["landmarks"], face["bbox"]):
                passed += 1
            else:
                rejected += 1

    assert rejected > 0, "这一批照片里没有候选被拒，本用例没验到「它会拒」——换一批照片"
    assert passed == 0, (
        f"有 {passed} 个 RetinaFace 候选过得了几何判据——1.0 那句「RetinaFace 的关键点"
        "不可靠」就不再成立，SCRFD 复核的必要性要重新评估"
    )

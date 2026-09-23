"""人脸检测的纯几何：priors / 解码 / NMS / 三个复核 helper。

全是纯函数，不需要权重、不需要 GPU、不需要 cv2——**这是 Task 77 能在 make ci 里跑的
那一半**。另一半（真权重下的分块检测）在 ``tests/integration/test_face_detector.py``。

对应 1.0 ``backend/face_detector.py`` 的 ``_generate_priors`` / ``_decode`` /
``_decode_landms`` / ``_nms``，以及 ``backend/model_clients.py`` 的 ``_bbox_agreement``
:1841、``_landmark_sanity`` :1878、``_expand_crop`` :1918。
"""

from __future__ import annotations

import numpy as np
import pytest

from sentrix.infrastructure.models.face_detector import (
    INPUT_SIZE,
    MIN_SIZES,
    NMS_THRESHOLD,
    STEPS,
    bbox_agreement,
    decode_boxes,
    decode_landmarks,
    expand_crop,
    generate_priors,
    landmark_sanity,
    nms,
)

# ---- priors ----------------------------------------------------------------------


def test_priors_cover_every_step_and_min_size():
    """anchor 总数 = 每个步长的格子数 × 该步长的 min_size 个数。

    这个数字（16800）是 RetinaFace 模型输出维度的**另一端**：对不上时 ``_decode``
    不会报错，只会把偏移量按错的 anchor 解出一堆位置离谱的框。所以它值得被钉死。
    """
    priors = generate_priors(INPUT_SIZE)

    expected = sum(
        (INPUT_SIZE[0] // step) * (INPUT_SIZE[1] // step) * len(sizes)
        for step, sizes in zip(STEPS, MIN_SIZES, strict=True)
    )
    assert priors.shape == (expected, 4)
    assert expected == 16800


def test_the_first_priors_are_the_two_smallest_anchors_of_the_first_cell():
    """第一个格子（左上角）的两个 anchor：中心都在 (4, 4)，边长是 16 与 32。"""
    priors = generate_priors(INPUT_SIZE)

    assert priors[0].tolist() == [4.0, 4.0, 16.0, 16.0]
    assert priors[1].tolist() == [4.0, 4.0, 32.0, 32.0]


# ---- 解码 ------------------------------------------------------------------------


def test_decode_boxes_applies_variance_to_the_center_only():
    """中心 = anchor 中心 + 偏移 × 0.1 × 边长；边长 = 边长 × exp(偏移 × 0.1)。

    用一个手算得出来的最小例子钉住系数：anchor ``[0, 0, 2, 2]`` + 偏移 ``[0.1, 0.2, 0, 0]``
    → 中心 ``(0.02, 0.04)``、边长仍是 2 → 角点 ``(-0.98, -0.96, 1.02, 1.04)``。
    """
    priors = np.array([[0.0, 0.0, 2.0, 2.0]], dtype=np.float32)
    loc = np.array([[0.1, 0.2, 0.0, 0.0]], dtype=np.float32)

    boxes = decode_boxes(loc, priors)

    assert boxes.shape == (1, 4)
    # float32 的运算结果，逐位相等会因精度而假红；用 approx 表达「就是这几个值」。
    assert boxes[0].tolist() == pytest.approx([-0.98, -0.96, 1.02, 1.04], abs=1e-6)


def test_decode_boxes_scales_the_side_by_exp():
    priors = np.array([[0.0, 0.0, 10.0, 10.0]], dtype=np.float32)
    loc = np.array([[0.0, 0.0, 1.0, 0.0]], dtype=np.float32)

    boxes = decode_boxes(loc, priors)

    # 宽 = 10 * exp(0.1) ≈ 11.0517，中心不动在 (0, 0)
    assert float(boxes[0][2] - boxes[0][0]) == pytest.approx(10.0 * np.exp(0.1), abs=1e-4)


def test_decode_landmarks_uses_the_second_variance():
    """关键点用 ``variance[1] = 0.2``，**不是**框用的 ``0.1``。

    用错不会报错，只会让关键点整体少偏一半——而关键点决定裁剪对齐，偏移会一路传到
    向量质量上。这里用同一个 anchor 把两条系数的差别摆出来：0.5 的偏移 × 0.2 × 4 = 0.4。
    """
    priors = np.array([[10.0, 20.0, 4.0, 4.0]], dtype=np.float32)
    landmarks = np.zeros((1, 10), dtype=np.float32)
    landmarks[0][0], landmarks[0][1] = 0.5, -0.25

    decoded = decode_landmarks(landmarks, priors)

    assert decoded.shape == (1, 5, 2)
    assert decoded[0][0].tolist() == pytest.approx([10.4, 19.8], abs=1e-6)
    # 若误用 variance[0]，第一个点会是 10.2 —— 这条断言把两者分开。
    assert float(decoded[0][0][0]) != pytest.approx(10.2, abs=1e-3)


# ---- NMS -------------------------------------------------------------------------


def iou_of(first: np.ndarray, second: np.ndarray) -> float:
    """测试自己的 IoU，不复用实现里的任何东西（复用了就变成拿实现验实现）。"""
    inter = max(0.0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0.0, min(first[3], second[3]) - max(first[1], second[1])
    )
    area_first = (first[2] - first[0]) * (first[3] - first[1])
    area_second = (second[2] - second[0]) * (second[3] - second[1])
    return float(inter / (area_first + area_second - inter))


def test_nms_keeps_the_highest_scoring_of_two_overlapping_boxes():
    """IoU ≈ 0.68 > 0.4：留分数高的那个，且返回的是**下标**。"""
    boxes = np.array([[0.0, 0.0, 10.0, 10.0, 0.9], [1.0, 1.0, 11.0, 11.0, 0.8]])

    keep = nms(boxes, NMS_THRESHOLD)

    assert keep.tolist() == [0]


def test_nms_keeps_identical_boxes_as_one():
    boxes = np.array([[0.0, 0.0, 10.0, 10.0, 0.9], [0.0, 0.0, 10.0, 10.0, 0.8]])

    assert nms(boxes, NMS_THRESHOLD).tolist() == [0]


def test_nms_keeps_disjoint_boxes():
    boxes = np.array([[0.0, 0.0, 10.0, 10.0, 0.9], [20.0, 20.0, 30.0, 30.0, 0.8]])

    assert nms(boxes, NMS_THRESHOLD).tolist() == [0, 1]


def test_a_box_at_exactly_the_threshold_is_kept():
    """**IoU 恰好等于阈值时留，不留由 ``<=`` 与 ``<`` 决定。**

    这一条是这条用例存在的**唯一**理由，所以构造必须让 IoU 精确等于阈值：
    A ``[0,0,10,10]``（面积 100）与 B ``[0,0,5,10]``（面积 50）的并集是 100、交集是 50，
    IoU = 0.5，在二进制浮点下**精确**。1.0 写的是 ``iou <= threshold``——阈值是**含等号
    的上界**，所以 B 被留（它不算「重叠过多」）。

    第一版这条用例拿阈值 1.0 去测，而那一对框的 IoU 只有 0.68：``<= 1.0`` 与 ``< 1.0``
    结果一样，**变异测试因此没有变红**——用例看起来在钉边界，其实一次都没走到边界上。
    边界用例必须真的落在边界上。
    """
    boxes = np.array([[0.0, 0.0, 10.0, 10.0, 0.9], [0.0, 0.0, 5.0, 10.0, 0.8]])
    assert iou_of(boxes[0], boxes[1]) == 0.5  # 前提本身也要被验，否则下面的断言是空的

    assert nms(boxes, 0.5).tolist() == [0, 1]
    # 反向：阈值略低一点，B 就落进「重叠过多」被抑制。
    assert nms(boxes, 0.49).tolist() == [0]


def test_nms_on_an_empty_array_is_empty():
    assert nms(np.zeros((0, 5))).tolist() == []


# ---- bbox_agreement --------------------------------------------------------------


def test_identical_boxes_agree():
    assert bbox_agreement([0.0, 0.0, 10.0, 10.0], [0.0, 0.0, 10.0, 10.0]) is True


def test_a_far_away_box_disagrees():
    """中心距离远超框对角线 → 不同意（两个检测器说的不是同一张脸）。"""
    assert bbox_agreement([0.0, 0.0, 10.0, 10.0], [100.0, 100.0, 110.0, 110.0]) is False


def test_a_small_box_inside_a_big_one_agrees():
    """框大小差很多但中心近、IoU 过 0.05 的下限 → 同意。

    这正是这条判据要保住的那一批：SCRFD 在放大的子图上重跑，小脸上给出的框比
    RetinaFace 的小得多，严格的 IoU 会把它们全杀掉。
    """
    assert bbox_agreement([0.0, 0.0, 100.0, 100.0], [40.0, 40.0, 70.0, 70.0]) is True


def test_center_ratio_is_what_rejects_the_off_center_box():
    """同一对框，只收紧 ``center_ratio`` 就从不一致变一致——证明这一半真的在起作用。

    没有这条，实现里把 ``center_ratio`` 硬写成 1.0 而忽略形参，测试仍然全绿。
    """
    retina = [0.0, 0.0, 100.0, 100.0]
    scrfd = [40.0, 40.0, 70.0, 70.0]

    assert bbox_agreement(retina, scrfd, center_ratio=1.0) is True
    assert bbox_agreement(retina, scrfd, center_ratio=0.01) is False


def test_iou_threshold_is_what_rejects_the_small_box():
    """同一对框，只抬 ``iou_threshold`` 就从一致变不一致——证明 IoU 那一半也在起作用。"""
    retina = [0.0, 0.0, 100.0, 100.0]
    scrfd = [40.0, 40.0, 70.0, 70.0]

    assert bbox_agreement(retina, scrfd, iou_threshold=0.05) is True
    assert bbox_agreement(retina, scrfd, iou_threshold=0.5) is False


# ---- landmark_sanity -------------------------------------------------------------


def test_landmarks_with_fewer_than_five_points_are_insane():
    assert landmark_sanity([[1.0, 1.0]] * 4, [0.0, 0.0, 100.0, 100.0]) is False
    assert landmark_sanity([], [0.0, 0.0, 100.0, 100.0]) is False


def test_a_small_box_is_always_sane():
    """框小于 64 → 直接放行。小脸上关键点本来就抖，几何判据在那里只会误杀。"""
    junk = [[-999.0, -999.0]] * 5

    assert landmark_sanity(junk, [0.0, 0.0, 63.0, 63.0]) is True
    assert landmark_sanity(junk, [0.0, 0.0, 64.0, 64.0]) is False


def test_eyes_too_close_are_insane():
    """两眼距离小于框宽的 ``min_eye_ratio``（0.1）→ 不像一张脸。"""
    landmarks = [[10.0, 10.0], [15.0, 10.0], [30.0, 40.0], [50.0, 40.0], [30.0, 60.0]]

    assert landmark_sanity(landmarks, [0.0, 0.0, 100.0, 100.0]) is False
    # 眼睛拉开到 30（>= 100 * 0.1）就恢复了
    landmarks[1] = [40.0, 10.0]
    assert landmark_sanity(landmarks, [0.0, 0.0, 100.0, 100.0]) is True


def test_landmarks_outside_the_box_are_insane():
    """至少 3 个点要落在框外扩 20% 的范围内；只有 2 个在里面就是 False。"""
    landmarks = [
        [10.0, 10.0],
        [40.0, 10.0],
        [1000.0, 1000.0],
        [1000.0, 1000.0],
        [1000.0, 1000.0],
    ]

    assert landmark_sanity(landmarks, [0.0, 0.0, 100.0, 100.0]) is False
    # 第三个点挪回框内 → 3 个在里面 → True
    landmarks[2] = [30.0, 60.0]
    assert landmark_sanity(landmarks, [0.0, 0.0, 100.0, 100.0]) is True


# ---- expand_crop -----------------------------------------------------------------


def test_expand_crop_grows_by_margin_and_upscales_to_min_side():
    """100×100 的框按 0.75 外扩成 250×250，不足 256 再以中心放大到 256×256。

    手算：x1 = 100-75 = 25、x2 = 200+75 = 275 → 250；scale = 256/250 = 1.024；
    中心 (150, 150) → x1 = 150-128 = 22。偏移要跟着返回值一起给出，否则调用方
    把子图坐标还原回原图时会整体偏。
    """
    image = np.zeros((1000, 1000, 3), dtype=np.uint8)

    sub, offset_x, offset_y = expand_crop(image, [100.0, 100.0, 200.0, 200.0])

    assert sub is not None
    assert sub.shape[:2] == (256, 256)
    assert (offset_x, offset_y) == (22, 22)


def test_expand_crop_clips_at_the_image_edge():
    image = np.zeros((1000, 1000, 3), dtype=np.uint8)

    sub, offset_x, offset_y = expand_crop(image, [0.0, 0.0, 300.0, 300.0])

    assert sub is not None
    # 300×300 外扩 0.75 → 750×750，已经超过 min_side，不放大；左上角被裁到 0。
    assert sub.shape[:2] == (525, 525)
    assert (offset_x, offset_y) == (0, 0)


def test_expand_crop_of_a_degenerate_box_is_none():
    """宽或高 ≤ 0 → ``(None, 0, 0)``。调用方跳过这张脸，不抛错。"""
    image = np.zeros((1000, 1000, 3), dtype=np.uint8)

    assert expand_crop(image, [100.0, 100.0, 100.0, 200.0]) == (None, 0, 0)
    assert expand_crop(image, [100.0, 100.0, 200.0, 100.0]) == (None, 0, 0)


def test_expand_crop_fully_outside_the_image_is_none():
    image = np.zeros((1000, 1000, 3), dtype=np.uint8)

    assert expand_crop(image, [2000.0, 2000.0, 2100.0, 2100.0]) == (None, 0, 0)

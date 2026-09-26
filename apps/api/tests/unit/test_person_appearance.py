"""外观裁剪几何（Task 85）——1.0 ``backend/person_appearance.py`` 的逐条移植。

这一层是**纯几何**：收一个框、给一个框，不读文件、不碰像素。所以用例里的图片是
一个只有 ``width`` / ``height`` / ``crop`` 的假件——它的 ``crop`` 只记录被裁的框。
"""

from __future__ import annotations

import pytest

from sentrix.domain.identity.appearance import (
    BODY_HEIGHT_SCALE,
    FACE_TOP_EXPANSION,
    HEAD_SCALE,
    MIN_CROP_HEIGHT,
    MIN_CROP_WIDTH,
    expanded_person_crop,
)


class FakeImage:
    def __init__(self, width: int, height: int, name: str = "root") -> None:
        self.width = width
        self.height = height
        self.name = name
        self.cropped: list[tuple[int, int, int, int]] = []

    def crop(self, box: tuple[int, int, int, int]) -> FakeImage:
        self.cropped.append(box)
        return FakeImage(box[2] - box[0], box[3] - box[1], name=f"{self.name}>crop")


def test_coefficients_are_the_1_0_literals():
    """三个系数与两个下限是**产品行为**（裁剪松紧直接决定模型看到多少衣物），
    散在实现里就没人能一眼核。"""
    assert HEAD_SCALE == 1.35
    assert BODY_HEIGHT_SCALE == 4.5
    assert FACE_TOP_EXPANSION == 0.35
    assert (MIN_CROP_WIDTH, MIN_CROP_HEIGHT) == (24, 48)


def test_crop_box_follows_the_three_coefficients():
    """脸 [100, 200, 200, 300]：宽高各 100，中心 x=150。

    - 宽度 = 100 * 1.35 = 135，左右各 67.5 -> [82.5, 217.5] -> round [82, 218]
    - 上边 = 200 - 100*0.35 = 165
    - 下边 = 300 + 100*4.5 = 750
    """
    image = FakeImage(1000, 1000)
    _, box = expanded_person_crop(image, [100, 200, 200, 300])
    assert box == [82, 165, 218, 750]


def test_crop_is_clamped_to_the_image_bounds():
    """贴边的脸不能给出负坐标或越界的框——1.0 用 max(0, ...) / min(width, ...)。

    脸 [0, 200, 60, 300] 在 200x400 的图上：左边本该是 30-40.5 = -10.5（夹到 0），
    下边本该是 300+450 = 750（夹到图高 400）。两边各钉一次钳位。
    """
    image = FakeImage(200, 400)
    _, box = expanded_person_crop(image, [0, 200, 60, 300])
    assert box == [0, 165, 70, 400]


def test_returns_the_cropped_image_and_its_source_box():
    """两个返回值都要用：裁剪件送给模型，source box 落库（``crop_bbox_json``）。"""
    image = FakeImage(1000, 1000)
    crop, box = expanded_person_crop(image, [100, 200, 200, 300])
    assert image.cropped == [tuple(box)]
    assert (crop.width, crop.height) == (box[2] - box[0], box[3] - box[1])


def test_invalid_box_length_raises_instead_of_falling_back_to_the_whole_scene():
    """1.0 的 docstring 点名了这条：图太小**不回退整图**，直接抛。

    静默回退会让模型看到一张全景，然后把背景里别人的衣服记到这个人的外观上——
    那是「不报错、只是结果不对」那一族。
    """
    image = FakeImage(1000, 1000)
    for bad in ([], [1, 2, 3], [1, 2, 3, 4, 5], None):
        with pytest.raises(ValueError, match="four coordinates"):
            expanded_person_crop(image, bad)


def test_degenerate_box_raises_with_its_own_diagnostic():
    """宽或高 < 2 的框不是一个脸框，是检测器给的退化值。

    **这里断言的是文案，不是类型，这是有意的。** 定点变异发现：把 ``< 2`` 改成
    ``< 1`` 之后没有任何用例变红——因为所有退化框算出来的裁剪都必然小于 24x48，
    会被后面那道守卫接住，抛出的同样是 ``ValueError``。也就是说这条守卫**唯一的
    独立可观测行为就是它的诊断文案**（1.0 ``person_appearance.py:16`` 原文
    ``face bounding box is too small``）。

    两个错法的成因不同（框本身退化了 / 框不退化但夹完太小），排障时要看的就是这句，
    所以钉住它；钉类型会让这条守卫变成一条谁也验不到的装饰。
    """
    image = FakeImage(4000, 4000)
    for bad in ([10, 10, 11, 100], [10, 10, 100, 11], [10, 10, 10, 10], [500, 10, 10, 100]):
        with pytest.raises(ValueError, match="face bounding box is too small"):
            expanded_person_crop(image, bad)


def test_crop_smaller_than_24x48_raises_with_the_crop_diagnostic():
    """裁剪结果的下限。1.0 是 ``crop_right - crop_left < 24 or crop_bottom - crop_top < 48``。

    构造：脸 [100, 100, 110, 110]（宽高各 10），图 2000x2000。裁剪宽 = 10*1.35 = 13.5
    -> 宽 14 < 24。框本身是合法的（宽高都 > 2），所以走的是**后**一道守卫，
    文案与上一条不同——两条守卫各有各的诊断，这也是它们唯一的区别。
    """
    image = FakeImage(2000, 2000)
    with pytest.raises(ValueError, match="person appearance crop is too small"):
        expanded_person_crop(image, [100, 100, 110, 110])

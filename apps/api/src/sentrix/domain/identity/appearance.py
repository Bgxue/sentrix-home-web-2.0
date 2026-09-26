"""外观证据的裁剪几何：把一个已检测的人脸框扩成头肩裁剪框。

逐行移植 1.0 ``backend/person_appearance.py:4-25`` 的 ``expanded_person_crop``。
它是本切片里少见的**纯几何**——不读文件、不算像素，只做四则运算与钳位。

**为什么它值得单独一个模块。** 裁剪的松紧直接决定模型看到多少衣物：裁窄了模型只能
看见一张脸（外观证据退化成人脸证据），裁宽了会把背景里别人的衣服算到这个头上
（「不报错、只是结果不对」那一族）。三个系数是产品行为，不是实现细节，所以连下限
一起收成具名常量，让用例可以直接对它们下断言。

**``image`` 为什么是一个 Protocol 而不是 ``PIL.Image``。** 1.0 直接收 PIL 对象。
2.0 的 ``pyproject.toml`` 有一条 domain-purity 契约，``forbidden_modules`` 里点名的
就有 ``PIL``——domain 层不许 import 它。所以这里只声明真正用到的三样能力
（``width`` / ``height`` / ``crop``），PIL 依旧是实现者，只是它的名字不出现在 domain
的任何一行里（打开图片、建临时文件那些 IO 由 infrastructure 的适配器负责）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, Self, TypeVar

# 1.0 ``person_appearance.py:4`` 的默认值，一字不差。
HEAD_SCALE = 1.35
BODY_HEIGHT_SCALE = 4.5
# 1.0 ``person_appearance.py:21`` 的 ``top - face_height * 0.35``。1.0 把它写成内联的
# 字面量，这里提成常量纯粹是为了让它能被断言——它的值没变。
FACE_TOP_EXPANSION = 0.35
# 1.0 ``person_appearance.py:23`` 的下限。小于这个尺寸的裁剪没有足够的像素承载
# 衣物信息，送给模型只会得到一句「看不清」，所以直接抛。
MIN_CROP_WIDTH = 24
MIN_CROP_HEIGHT = 48


class PersonCropImage(Protocol):
    """裁剪几何需要的图片能力。实现者是 ``PIL.Image``（由 infrastructure 提供）。"""

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

    def crop(self, box: tuple[int, int, int, int]) -> Self: ...


ImageT = TypeVar("ImageT", bound=PersonCropImage)


def expanded_person_crop(
    image: ImageT,
    bbox: Sequence[float] | None,
    head_scale: float = HEAD_SCALE,
    body_height_scale: float = BODY_HEIGHT_SCALE,
) -> tuple[ImageT, list[int]]:
    """把一个人脸框扩成「头 + 上半身」的裁剪件，并给出它的源坐标。

    框保持人脸水平居中，只向下延伸去取衣物。**图太小或框退化时抛 ``ValueError``，
    不回退整图**——回退会让模型看到一整张全景，然后把别人的衣服记到这个人的外观上，
    而调用方从返回值上看不出这件事发生过。

    返回 ``(裁剪件, [left, top, right, bottom])``：源坐标要落库
    （``person_appearance_evidence.crop_bbox_json``），只给裁剪件等于把「这段描述
    对应照片的哪一块」丢掉，事后无法复核。
    """
    if bbox is None or len(bbox) != 4:
        raise ValueError("face bounding box must contain four coordinates")
    left, top, right, bottom = (float(value) for value in bbox)
    face_width, face_height = right - left, bottom - top
    if face_width < 2 or face_height < 2:
        raise ValueError("face bounding box is too small")
    center_x = (left + right) / 2
    crop_width = face_width * head_scale
    crop_left = max(0, int(round(center_x - crop_width / 2)))
    crop_right = min(image.width, int(round(center_x + crop_width / 2)))
    crop_top = max(0, int(round(top - face_height * FACE_TOP_EXPANSION)))
    crop_bottom = min(image.height, int(round(bottom + face_height * body_height_scale)))
    if crop_right - crop_left < MIN_CROP_WIDTH or crop_bottom - crop_top < MIN_CROP_HEIGHT:
        raise ValueError("person appearance crop is too small")
    return image.crop((crop_left, crop_top, crop_right, crop_bottom)), [
        crop_left,
        crop_top,
        crop_right,
        crop_bottom,
    ]

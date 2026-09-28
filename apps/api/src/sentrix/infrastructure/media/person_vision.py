"""人物视觉：带编号的预览图渲染。

逐行移植 1.0 ``backend/person_moments.py:91-124`` 的 ``render_numbered_preview``。

**一处订正，有实测数据支撑。** 1.0 那一版把 bbox 当成 ``(x, y, width, height)`` 来画：

.. code-block:: python

    x, y, width, height = bbox[0], bbox[1], bbox[2], bbox[3]
    draw.rectangle([x, y, x + width, y + height], ...)

而落库的 bbox 是 ``(x1, y1, x2, y2)``——1.0 自己的 ``face_detector.py:136`` 就是这么产的，
2.0 的 ``expanded_person_crop`` 也是这么解读的。**读 1.0 的真实库判定**（30317 条实例）：

- 按 ``(x1,y1,x2,y2)`` 解释：宽度 64.1 – 2147.0 px，**最小值 64.1 正好是 ``FACE_MIN_SIZE``**
  （人脸准入下限，这个巧合不可能出现在错的解释里）；
- 按 ``(x,y,w,h)`` 解释：宽度 76.3 – **11335.5 px**——一个 11335 像素宽的人脸；
- 0 条出现 ``x2 <= x1``。

所以 2.0 按 ``(x1, y1, x2, y2)`` 画。**影响面**：标签位置两种解释下都是对的（都画在
``bbox[0], bbox[1]`` 上方 14px，也就是人脸的左上角），错的是**矩形本身**——模型看到的
框比真脸大一倍左右。它仍然能靠标签完成归属，但那是在一个错框上做的判断。

**临时文件由调用方删除**（1.0 的 docstring 写明：预览图从不作为 Asset 或长期文件保存）。
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# 1.0 ``person_moments.py:100-113`` 的原值。红框、线宽 3、标签画在框上方 14px。
BOX_OUTLINE = "red"
BOX_WIDTH = 3
LABEL_DY = 14
LABEL_MARGIN = 10
LABEL_LINE_HEIGHT = 14
JPEG_QUALITY = 90


def render_numbered_preview(
    asset_path: str | Path,
    faces: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, dict[str, Any]]]:
    """在原图上画出人脸框与 ``P1`` / ``P2`` 标签，返回 ``(临时图路径, 标签 → 身份)``。

    ``faces`` 每项至少要有 ``bbox``（``(x1, y1, x2, y2)``）；没有框的按 1.0 的做法
    退化成「把标签画在左上角依次排下来」，**而不是丢掉这一项**——丢了他，模型会看到
    一个编号跳号（P1、P3），然后开始猜 P2 去哪了。
    """
    from PIL import Image, ImageDraw, ImageOps

    with Image.open(asset_path) as source:
        # EXIF 旋转必须在这一步做掉：手机照片的 bbox 是在**转正之后**的坐标系里算的，
        # 不转的话框会整体错位——而那种错看起来像「模型认错人」。
        image = ImageOps.exif_transpose(source).convert("RGB")
    draw = ImageDraw.Draw(image)
    label_map: dict[str, dict[str, Any]] = {}
    for index, face in enumerate(faces, start=1):
        label = f"P{index}"
        bbox = list(face.get("bbox") or [])
        if len(bbox) >= 4:
            left, top, right, bottom = (float(value) for value in bbox[:4])
            draw.rectangle([left, top, right, bottom], outline=BOX_OUTLINE, width=BOX_WIDTH)
            draw.text((left, max(0.0, top - LABEL_DY)), label, fill=BOX_OUTLINE)
        else:
            draw.text((LABEL_MARGIN, LABEL_MARGIN + index * LABEL_LINE_HEIGHT), label, fill=BOX_OUTLINE)
        label_map[label] = {
            "face_instance_id": face.get("face_instance_id"),
            "person_id": face.get("person_id"),
            "cluster_id": face.get("cluster_id"),
            "observation_id": face.get("observation_id"),
            "event_id": face.get("event_id"),
        }
    # 用 with 而不是 1.0 的「取出名字再 close」：`delete=False` 保证退出 with 之后
    # 文件仍在（这是这条的全部作用——不是 with 会把它删掉），而 with 让「异常路径上
    # 也关掉句柄」由语言保证，不靠调用顺序。
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
        image.save(handle.name, format="JPEG", quality=JPEG_QUALITY)
        return handle.name, label_map

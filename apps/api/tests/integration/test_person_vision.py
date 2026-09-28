"""带编号预览图的渲染。

**这份用例的首要职责是把 bbox 的解释钉死。** 1.0 的 ``render_numbered_preview`` 把
``(x1,y1,x2,y2)`` 当成 ``(x,y,w,h)`` 画，框会大一倍左右；而两种解释下**标签位置都是
对的**，所以只看「画出来没有红框」的用例两种实现都能过。

分辨办法：**回读像素**，找到红框左右两条边的实际 x 坐标。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from sentrix.infrastructure.media.person_vision import render_numbered_preview

BOX = [50, 60, 150, 160]  # (x1, y1, x2, y2)：一个 100x100 的框


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    """一张纯白底图。用真图而不是 mock，是因为这条用例要读像素。"""
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (300, 300), "white").save(path, format="JPEG", quality=100)
    return path


@pytest.fixture
def face() -> dict[str, object]:
    return {
        "face_instance_id": "face_1",
        "person_id": "person_1",
        "cluster_id": "cluster_1",
        "observation_id": "obs_1",
        "event_id": "event_1",
        "bbox": BOX,
    }


def reddish(pixel: tuple[int, ...]) -> bool:
    red, green, blue = pixel[:3]
    return red > 120 and green < 110 and blue < 110


def red_columns(image: Image.Image, row: int) -> list[int]:
    """某一行上所有偏红像素的 x。"""
    return [x for x in range(image.width) if reddish(image.getpixel((x, row)))]


def test_the_box_edges_land_on_x1_and_x2(tmp_path: Path, photo: Path, face: dict[str, object]) -> None:
    """**这条就是那个 bug 的门禁。**

    按 ``(x1,y1,x2,y2)`` 画：左右边在 x=50 与 x=150。
    按 ``(x,y,w,h)`` 画：会画到 ``[50, 60, 50+150, 60+160] = [50,60,200,220]``，
    右边跑到 200——这条断言当场变红。
    """
    preview, _ = render_numbered_preview(photo, [face])
    try:
        with Image.open(preview) as drawn:
            columns = red_columns(drawn, 110)  # 框的竖直中段，避开上下边与标签
        assert columns, "这一行上一个红像素都没有——框根本没画出来"
        assert min(columns) == pytest.approx(BOX[0], abs=3), f"左边不在 x1: {min(columns)}"
        assert max(columns) == pytest.approx(BOX[2], abs=3), f"右边不在 x2: {max(columns)}"
    finally:
        os.unlink(preview)


def test_the_box_edges_land_on_y1_and_y2(photo: Path, face: dict[str, object]) -> None:
    """**扫框的中间列而不是左边。** 标签 ``P1`` 是红字、画在 ``(x1, y1 - 14)``，
    与左边框共用一段 x——扫左边会把标签的 46 当成「上边」，于是断言红在一个
    与实现无关的理由上（第一版就是这么错的）。"""
    preview, _ = render_numbered_preview(photo, [face])
    try:
        middle = (BOX[0] + BOX[2]) // 2
        with Image.open(preview) as drawn:
            rows = [y for y in range(drawn.height) if reddish(drawn.getpixel((middle, y)))]
        assert min(rows) == pytest.approx(BOX[1], abs=3), f"上边不在 y1: {min(rows)}"
        assert max(rows) == pytest.approx(BOX[3], abs=3), f"下边不在 y2: {max(rows)}"
    finally:
        os.unlink(preview)


def test_the_label_map_carries_every_identity_field(photo: Path, face: dict[str, object]) -> None:
    """标签映射要带全五个身份字段——模型回的是 ``P1``，调用方要据此落库，
    少一个字段那条瞬间就缺一个外键。"""
    preview, labels = render_numbered_preview(photo, [face])
    try:
        assert labels["P1"] == {
            "face_instance_id": "face_1",
            "person_id": "person_1",
            "cluster_id": "cluster_1",
            "observation_id": "obs_1",
            "event_id": "event_1",
        }
    finally:
        os.unlink(preview)


def test_labels_are_numbered_from_one_in_order(photo: Path) -> None:
    faces = [{"face_instance_id": f"face_{index}", "bbox": BOX} for index in range(1, 4)]
    preview, labels = render_numbered_preview(photo, faces)
    try:
        assert list(labels) == ["P1", "P2", "P3"]
        assert labels["P2"]["face_instance_id"] == "face_2"
    finally:
        os.unlink(preview)


def test_a_face_without_a_box_still_gets_a_label(photo: Path) -> None:
    """没有框的人**不丢**，只是没有矩形。丢了的话模型会看到一个跳号的序列
    （P1、P3），然后开始猜 P2 去哪了。"""
    preview, labels = render_numbered_preview(
        photo, [{"face_instance_id": "face_1"}, {"face_instance_id": "face_2", "bbox": BOX}]
    )
    try:
        assert list(labels) == ["P1", "P2"]
        with Image.open(preview) as drawn:
            # P1 退化成左上角的文字，框是 P2 的——所以左侧那一列没有红
            assert not red_columns(drawn, 110) or min(red_columns(drawn, 110)) >= BOX[0] - 3
    finally:
        os.unlink(preview)


def test_the_preview_is_a_real_jpeg_on_disk(photo: Path, face: dict[str, object]) -> None:
    """1.0 的 docstring 写明：预览图是临时 JPEG，**从不作为 Asset 或长期文件保存**，
    且**必须由调用方删除**。所以这里既验它落成了 JPEG，也验路径是调用方能删的普通文件。"""
    preview, _ = render_numbered_preview(photo, [face])
    try:
        assert Path(preview).suffix == ".jpg"
        with Image.open(preview) as drawn:
            assert drawn.format == "JPEG"
    finally:
        os.unlink(preview)

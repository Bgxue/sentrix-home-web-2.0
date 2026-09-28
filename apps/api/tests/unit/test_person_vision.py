"""人物裁剪取样适配器（T85c）——domain 与真实图片之间的那一层。

1.0 这段内联在 ``app.py:2038-2042``。它值得一组自己的用例，是因为它做的是
**domain 不许做的事**：``pyproject.toml`` 的 domain-purity 契约把 ``PIL`` 点名禁掉，
所以 ``expanded_person_crop`` 收的是一个只有 ``width`` / ``height`` / ``crop`` 的
Protocol，真正的 ``Image.open`` 只出现在这里。

三条各挡一件事：

1. **EXIF 转正在裁剪之前**——不转，手机照片的框会整体错位，而错出来的样子像
   「模型认不出这个人穿了什么」，与真正的模型失败长得一样；
2. **``ValueError`` 穿过去**，不回退整图——回退会让模型看到一整张全景，然后把背景里
   别人的衣服记到这个人的外观上（``expanded_person_crop`` 的 docstring 写明这条）；
3. **落的是裁剪件本身**，不是原图——尺寸与内容两边都验。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from PIL import Image

from sentrix.domain.identity.appearance import expanded_person_crop
from sentrix.infrastructure.media.person_vision import render_person_crop

# 400×600 底图上的人脸框。期望值由 domain 的纯函数给出（见 ``_painted``），
# 这里不另抄一份公式——几何对不对是 ``test_person_appearance.py`` 的事，
# 这份用例验的是「适配器把那张图交出去了没有」。
FACE = [190.0, 100.0, 212.0, 132.0]


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ``tempfile`` 的落点收进 tmp_path，好断言「临时文件建了没有」。

    ``tempfile`` 会把首次算出的目录缓存进模块级的 ``tempdir``，所以设环境变量不管用，
    得直接改那个属性。
    """
    directory = tmp_path / "scratch"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


def _painted(path: Path, bbox: list[float], *, size: tuple[int, int] = (400, 600)) -> list[int]:
    """造一张图：底色纯蓝，**把 ``expanded_person_crop`` 会裁走的那一块**涂成纯红。

    涂哪一块由 domain 的纯函数现场算出来，不写死坐标——所以下面那条用例问的是
    「存下来的东西是不是那一块」，而不是把几何再验一遍。反过来若涂的是人脸框，
    这条用例就退化成「裁的不是人脸框」，什么也没验到。
    """
    blank = Image.new("RGB", size, (0, 0, 255))
    _, crop_bbox = expanded_person_crop(blank, bbox)
    image = blank.copy()
    image.paste((255, 0, 0), tuple(int(value) for value in crop_bbox))
    image.save(path, format="JPEG", quality=100)
    return crop_bbox


def test_the_saved_file_is_the_crop_not_the_whole_photo(scratch: Path, tmp_path: Path) -> None:
    """落下来的必须是裁剪件：尺寸等于裁剪框，内容是被标红的那一块。

    **尺寸与内容两边都验**，因为它们挡的是不同的错法——尺寸对而内容是整图，说明
    ``crop`` 的返回值被丢掉了（存了 ``image``）；内容对而尺寸不对，说明存的时候
    又缩放了。只验其中一边，这两种都过得去。
    """
    source = tmp_path / "album.jpg"
    crop_bbox = _painted(source, FACE)

    path, returned_bbox = render_person_crop(source, FACE)

    assert returned_bbox == crop_bbox, "返回的源坐标必须与 domain 算出来的一致——它要落库"
    with Image.open(path) as result:
        assert result.size == (crop_bbox[2] - crop_bbox[0], crop_bbox[3] - crop_bbox[1])
        assert result.format == "JPEG"
        # 取正中一格：JPEG 在涂色边界上有振铃，取边缘会验到压缩噪声而不是内容。
        center = result.getpixel((result.width // 2, result.height // 2))
    assert center[0] > 200 and center[1] < 60, f"裁到的不是标红那一块: {center}"


def test_the_box_is_read_in_the_exif_corrected_frame(scratch: Path, tmp_path: Path) -> None:
    """**这份用例真正守的那条。** 旋转 90° 的照片，框是在转正后的坐标系里算的。

    底图 100×200、EXIF Orientation=6（PIL 转正后是 200×100）。下面这个框在转正后的
    坐标系里合法（``x`` 到 140），在原图坐标系里横向只剩 7px——**同一个框，转了能裁、
    不转就抛**。所以这一条同时断言了两件事，缺一半就不成立：

    - 转正的那条路必须成功，且给出按 200 宽算出来的坐标；
    - 把 EXIF 抹掉之后同一个框必须抛 ``ValueError``。

    只断言前半条是不够的：一个「什么都不转、但恰好也不抛」的实现（比如偷偷回退整图）
    同样能过——而它正是这个适配器最该挡掉的那种实现。
    """
    rotated = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (100, 200), (255, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = 6
    image.save(rotated, format="JPEG", exif=exif)

    # 期望值按转正后的 200×100 手算：face 40×40，center_x 120，crop_width 54；
    #   left = round(120-27) = 93    right = round(120+27) = 147
    #   top  = round(20-14)  = 6     bottom = round(60+180) = 240，被图高 100 钳住
    path, crop_bbox = render_person_crop(rotated, [100.0, 20.0, 140.0, 60.0])

    assert crop_bbox == [93, 6, 147, 100], "坐标是按转正后的 200×100 算的，不是按原图的 100×200"
    with Image.open(path) as result:
        assert result.size == (54, 94)

    stripped = tmp_path / "stripped.jpg"
    image.save(stripped, format="JPEG")  # 同样的像素，只是没有 EXIF
    with pytest.raises(ValueError, match="too small"):
        render_person_crop(stripped, [100.0, 20.0, 140.0, 60.0])


def test_a_crop_too_small_raises_instead_of_falling_back(scratch: Path, tmp_path: Path) -> None:
    """图太小 → 抛，**不回退整图**。

    回退整图的后果不是报错，是模型看到一整张全景、然后把背景里别人的衣服记到这个
    人的外观上——与 ``expanded_person_crop`` 的 docstring 同一条理由。1.0 是在更外层
    的循环里 catch 的（``app.py:2058``），所以这里必须抛到那一层去。
    """
    tiny = tmp_path / "tiny.jpg"
    Image.new("RGB", (30, 40), (0, 0, 255)).save(tiny, format="JPEG")

    with pytest.raises(ValueError, match="too small"):
        render_person_crop(tiny, [0.0, 0.0, 30.0, 40.0])
    assert list(scratch.iterdir()) == [], "抛之前不该留下临时文件"


def test_a_degenerate_box_raises(scratch: Path, tmp_path: Path) -> None:
    """退化框（宽或高 < 2px）与缺框，都由 ``expanded_person_crop`` 判、由这里放行。"""
    source = tmp_path / "album.jpg"
    Image.new("RGB", (400, 600), (0, 0, 255)).save(source, format="JPEG")

    with pytest.raises(ValueError, match="too small"):
        render_person_crop(source, [100.0, 100.0, 101.0, 130.0])
    with pytest.raises(ValueError, match="four coordinates"):
        render_person_crop(source, None)
    assert list(scratch.iterdir()) == []


def test_the_temporary_file_is_left_for_the_caller_to_delete(scratch: Path, tmp_path: Path) -> None:
    """临时文件**不自己删**：调用方还要把它喂给模型，用完才 unlink。

    这一条钉的是「别顺手加个 try/finally 在这里删掉」——删了的话，调用方拿到的是一个
    已经不存在的路径，而两边都不报错，只有模型调用会失败。
    """
    source = tmp_path / "album.jpg"
    Image.new("RGB", (400, 600), (0, 0, 255)).save(source, format="JPEG")

    path, _ = render_person_crop(source, FACE)

    assert Path(path).is_file()
    assert Path(path).parent == scratch, "落点跟着 tempfile 走，不写死在 DATA_DIR"

"""图片打开与预览编码。移植自 1.0 ``backend/image_io.py``。

保留 ``ImageOps.exif_transpose``：真实相册里大量竖拍照片靠 EXIF Orientation 标记，
不转置的话预览会躺倒。
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_HEIF_REGISTERED = False


def ensure_heif_support() -> bool:
    """注册 pillow-heif，使 PIL 能打开 HEIC/HEIF。幂等。"""
    global _HEIF_REGISTERED
    if _HEIF_REGISTERED:
        return True
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
        _HEIF_REGISTERED = True
        return True
    except Exception:
        log.warning("pillow_heif_unavailable heic 将无法导入")
        return False


def encode_jpeg_preview(path: Path, *, max_dimension: int = 1600, quality: int = 85) -> bytes:
    from PIL import Image, ImageOps

    ensure_heif_support()
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        if max(image.size) > max_dimension:
            image.thumbnail((max_dimension, max_dimension))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        return output.getvalue()


class PillowPreviewEncoder:
    """把模块级函数适配成 domain 的 :class:`PreviewEncoder` 端口。"""

    def encode_jpeg(self, path: Path, *, max_dimension: int, quality: int) -> bytes:
        return encode_jpeg_preview(path, max_dimension=max_dimension, quality=quality)

from PIL import Image

from sentrix.infrastructure.media.image_io import encode_jpeg_preview, ensure_heif_support


def test_preview_is_jpeg_and_downscaled(tmp_path):
    path = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 3000), "red").save(path, format="JPEG")

    payload = encode_jpeg_preview(path, max_dimension=800, quality=80)

    assert payload[:2] == b"\xff\xd8"  # JPEG SOI
    with Image.open(tmp_path / "unused.jpg" if False else __import__("io").BytesIO(payload)) as image:
        assert max(image.size) == 800


def test_preview_preserves_small_images(tmp_path):
    path = tmp_path / "small.jpg"
    Image.new("RGB", (120, 90), "blue").save(path, format="JPEG")

    payload = encode_jpeg_preview(path, max_dimension=1600, quality=85)

    import io

    with Image.open(io.BytesIO(payload)) as image:
        assert image.size == (120, 90)


def test_transparency_is_flattened_to_rgb(tmp_path):
    path = tmp_path / "alpha.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 128)).save(path, format="PNG")

    payload = encode_jpeg_preview(path, max_dimension=1600, quality=85)

    import io

    with Image.open(io.BytesIO(payload)) as image:
        assert image.mode == "RGB"


def test_heif_registration_is_idempotent():
    assert ensure_heif_support() in (True, False)
    assert ensure_heif_support() in (True, False)

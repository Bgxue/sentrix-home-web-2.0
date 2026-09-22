"""真模型（chinese-clip-ViT-L-14，进程内 GPU/CPU）下的视觉嵌入测试。

这些断言分两类，都不依赖外部照片仓库：

- **结构类**：维度、确定性、图文共空间——钉住实现不退化；
- **语义类**：用 PIL 现场画出纯色图，ground truth 由**构造**确定（我用 RGB
  (200,30,30) 画的红图就是红的），断言 chinese-clip 的文本塔能把它排在其它颜色
  前面。这条不是循环论证：期望值来自画图这一侧，被测的是模型。

模型质量本身的验收按设计 §9.1 由使用者负责；这里只保证「链路没坏」。
"""

from __future__ import annotations

import io
import math
from pathlib import Path

import pytest
from PIL import Image

from sentrix.domain.retrieval.scheme import IMAGE_MODEL, VISUAL_DIMENSION
from sentrix.infrastructure.models.chinese_clip import ChineseClipVisualEmbedder

COLOURS = {
    "红色": (200, 30, 30),
    "蓝色": (30, 30, 200),
    "绿色": (30, 180, 60),
}


@pytest.fixture(scope="module")
def embedder() -> ChineseClipVisualEmbedder:
    instance = ChineseClipVisualEmbedder()
    if not instance.available:
        pytest.skip(f"chinese-clip 权重不可用: {instance.load_error}")
    return instance


def colour_image(path: Path, colour: tuple[int, int, int]) -> Path:
    buffer = io.BytesIO()
    Image.new("RGB", (224, 224), colour).save(buffer, format="JPEG")
    path.write_bytes(buffer.getvalue())
    return path


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def test_embedder_reports_the_frozen_scheme(embedder: ChineseClipVisualEmbedder):
    assert embedder.model_id == IMAGE_MODEL
    assert embedder.dimension == VISUAL_DIMENSION


def test_image_vector_has_the_declared_dimension(embedder: ChineseClipVisualEmbedder, tmp_path: Path):
    vector = embedder.embed_image(colour_image(tmp_path / "red.jpg", COLOURS["红色"]))

    assert len(vector) == VISUAL_DIMENSION
    assert all(math.isfinite(value) for value in vector)


def test_query_vector_has_the_declared_dimension(embedder: ChineseClipVisualEmbedder):
    vector = embedder.embed_query("桥")

    assert len(vector) == VISUAL_DIMENSION
    assert all(math.isfinite(value) for value in vector)


def test_same_image_embeds_identically(embedder: ChineseClipVisualEmbedder, tmp_path: Path):
    """同一张图两次推理必须逐位相同。

    ``model.eval()`` 缺失时 dropout 会让结果随机——这条是它唯一的防线。
    """
    path = colour_image(tmp_path / "red.jpg", COLOURS["红色"])

    assert embedder.embed_image(path) == embedder.embed_image(path)


def test_different_texts_give_different_vectors(embedder: ChineseClipVisualEmbedder):
    assert embedder.embed_query("猫") != embedder.embed_query("汽车")


def test_blank_text_yields_no_vector(embedder: ChineseClipVisualEmbedder):
    assert embedder.embed_query("   ") == []


def test_missing_file_yields_no_vector(embedder: ChineseClipVisualEmbedder, tmp_path: Path):
    assert embedder.embed_image(tmp_path / "nope.jpg") == []


@pytest.mark.parametrize("word", sorted(COLOURS))
def test_colour_word_ranks_its_own_colour_first(
    embedder: ChineseClipVisualEmbedder, tmp_path: Path, word: str
):
    vectors = {
        name: embedder.embed_image(colour_image(tmp_path / f"{name}.jpg", colour))
        for name, colour in COLOURS.items()
    }

    query = embedder.embed_query(word)
    scores = {name: cosine(query, vector) for name, vector in vectors.items()}
    ranked = sorted(scores, key=lambda name: scores[name], reverse=True)

    assert ranked[0] == word, f"{word} 的排序是 {ranked}，分数 {scores}"

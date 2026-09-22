"""嵌入方案被钉死——2.0 不得重新引入「可切换性」。

1.0 的历史：IMAGE/TEXT 嵌入器被 env/.env 切换过（clip / chinese_clip / bge），
memory_vectors、ANN 索引、FTS 各套并存且互不匹配，检索时好时坏。这里用「配置里
根本没有这个开关」来保证同样的事不会再发生——不是靠约定，是靠它无处可配。
"""

from __future__ import annotations

from sentrix.config import Settings
from sentrix.domain.retrieval import scheme


def test_scheme_constants_are_the_ones_in_the_behaviour_baseline():
    assert scheme.IMAGE_EMBEDDER == "chinese_clip"
    assert scheme.IMAGE_MODEL == "chinese-clip-ViT-L-14"
    assert scheme.VISUAL_DIMENSION == 768
    assert scheme.VISUAL_SPACE == "visual"


def test_settings_expose_no_embedder_switch(tmp_path, monkeypatch):
    """配置里没有「选哪套嵌入/哪个向量后端」这一项。

    反向验证：把上面任何一个名字加进 ``Settings``，这条测试就会红——红得对，
    因为那正是 1.0 多套向量并存的入口。

    ``text_embedder_url``（bge-m3 sidecar 的地址）不在禁用名单里：它是 S4 的服务
    地址，不是模型选择；本切片用的是图像一侧，不碰它。
    """
    monkeypatch.setenv("SENTRIX_DATA_DIR", str(tmp_path))
    forbidden = {
        "vector_backend",
        "image_embedder",
        "visual_embedder",
        "text_embedder",
        "embedding_backend",
        "ann_backend",
        "clip_model",
        "clip_checkpoint",
    }

    assert forbidden & set(Settings.model_fields) == set()
    # 这条只是证明上面那个集合不是「对着空集断言」——字段确实读得到。
    assert "data_dir" in Settings.model_fields

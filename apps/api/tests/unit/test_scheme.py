"""嵌入方案被钉死——2.0 不得重新引入「可切换性」。

1.0 的历史：IMAGE/TEXT 嵌入器被 env/.env 切换过（clip / chinese_clip / bge），
memory_vectors、ANN 索引、FTS 各套并存且互不匹配，检索时好时坏。这里用「配置里
根本没有这个开关」来保证同样的事不会再发生——不是靠约定，是靠它无处可配。
"""

from __future__ import annotations

from sentrix.config import Settings
from sentrix.domain.retrieval import scheme


def test_scheme_constants_are_the_ones_in_the_behaviour_baseline():
    """W1（2026-10-03）把方案换成了 wemm 统一空间，常量随之。

    两个槽现在指向同一个模型与同一个维度——**这不是「可以切」，是「已经切完」**：
    上面那条「配置里没有开关」的断言仍然成立，方案只有一个。
    """
    assert scheme.IMAGE_EMBEDDER == "wemm"
    assert scheme.TEXT_EMBEDDER == "wemm"
    assert scheme.IMAGE_MODEL == "tencent/WeMM-Embedding-2B"
    assert scheme.TEXT_MODEL == "tencent/WeMM-Embedding-2B"
    assert scheme.VISUAL_DIMENSION == 2048
    assert scheme.TEXT_DIMENSION == 2048
    # **三个 space 保留、不合并**：`visual` 里还住着人脸向量（buffalo_l 512 维，
    # 靠 source_type 与 model_name 区分），合并会把它们卷进文本检索。
    assert scheme.VISUAL_SPACE == "visual"
    assert scheme.SEMANTIC_SPACE == "semantic"
    assert scheme.EPISODIC_SPACE == "episodic"


def test_settings_expose_no_embedder_switch(tmp_path, monkeypatch):
    """配置里没有「选哪套嵌入/哪个向量后端」这一项。

    反向验证：把上面任何一个名字加进 ``Settings``，这条测试就会红——红得对，
    因为那正是 1.0 多套向量并存的入口。

    ``text_embedder_url`` / ``wemm_url`` 不在禁用名单里：它们是**服务地址**，
    不是模型选择。禁的是「选哪套嵌入」，不是「那套嵌入在哪台机器上」。
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

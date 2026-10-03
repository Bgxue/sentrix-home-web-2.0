"""单一嵌入方案（钉死，不读 env）。

历史教训（两个来源，都读过再改）：IMAGE/TEXT 嵌入器曾被 env/.env **各自**反复切换
（clip / chinese_clip / bge），结果 memory_vectors、ANN 索引、FTS 各套并存且互不匹配，
检索时好时坏，而且**没有任何一处会报错**。

所以 2.0 不提供任何切换开关：写入、查询、重建索引三处都只认这里的常量。要换模型
就改这个文件并重建索引，那是一次有版本、可审查的迁移，不是一次环境变量调整。
（1.0 后来加了 ``SENTRIX_EMBEDDING_SCHEME`` 做整方案原子切换，理由是它要分阶段
验证；2.0 的裁决是**直接钉死 wemm**，所以那条开关连同它要守的两条纪律一起不需要。）

**W1（2026-10-03）把方案换成 wemm：文本/图像/视频在同一个 2048 维空间里。**

为什么两个槽仍然保留各自的名字（``IMAGE_MODEL`` / ``TEXT_MODEL`` 指向同一个字符串）：
它们标注的是**两个不同的 space**（``visual`` 对阵 ``semantic``/``episodic``），
而这个区分在 wemm 下仍然成立——见下面 space 那段。改名会让 20 多处按槽位引用的
消费点全部改一遍，换不来任何语义上的准确，所以留着。
"""

from __future__ import annotations

# 1.0 ``backend/embeddings/wemm_sidecar.py:34-35`` 的模型名与原生维度。
# 侧车在 8102，实测 ``/health`` 报 ``{"dimension": 2048,
# "matryoshka_dimensions": [64,128,256,512,1024,2048]}``。
WEMM_MODEL = "tencent/WeMM-Embedding-2B"
# **不截断。** 1.0 的 ``SENTRIX_WEMM_TRUNCATE_DIM`` 只在显式设置时才带该字段，
# 默认走满维；用户验证 wemm 效果时用的就是这个配置。MRL 截断（64..1024）是
# 一个可选的省空间手段，但它会改变向量，**换截断档等同于换模型**，要伴随重建。
WEMM_DIMENSION = 2048

# ---- 两个槽：W1 起由同一个模型服务 ------------------------------------------------
IMAGE_EMBEDDER = "wemm"
TEXT_EMBEDDER = "wemm"
IMAGE_MODEL = WEMM_MODEL
TEXT_MODEL = WEMM_MODEL
VISUAL_DIMENSION = WEMM_DIMENSION
TEXT_DIMENSION = WEMM_DIMENSION

# ---- 三个 space：**保留，不合并** --------------------------------------------------
# 统一空间指的是「文本和图在同一个向量空间里，跨模态检索原生成立」，
# **不是**「所有向量都塞进一个 space」。三个 space 各有分工：
#
# - ``visual``：资产图像向量（``source_type=asset``）+ **人脸向量**
#   （``source_type=face_instance``，buffalo_l 512 维，与图像共用这个 space 靠
#   ``source_type`` 与 ``model_name`` 区分）。合并会把 512 维的人脸向量卷进文本检索；
# - ``semantic`` / ``episodic``：观察与事件的文本向量。
#
# 1.0 的 ``consistency.SPACE_ROLE`` 在 wemm 下**同样分三个**（visual→image_model，
# episodic/semantic→text_model），这是照搬而不是这里的选择。
VISUAL_SPACE = "visual"
SEMANTIC_SPACE = "semantic"
EPISODIC_SPACE = "episodic"

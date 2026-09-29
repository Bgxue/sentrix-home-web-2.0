"""单一嵌入方案（钉死，不读 env）。

对应 1.0 ``backend/embeddings/scheme.py``。历史教训写在那里：IMAGE/TEXT 嵌入器
曾被 env/.env 反复切换（clip / chinese_clip / bge），结果 memory_vectors、ANN 索引、
FTS 各套并存且互不匹配，检索时好时坏，而且**没有任何一处会报错**。

所以 2.0 不提供任何切换开关：写入、查询、重建索引三处都只认这里的常量。要换模型
就改这个文件并重建索引，那是一次有版本、可审查的迁移，不是一次环境变量调整。

本切片只用到 IMAGE 一侧；TEXT 一侧（BAAI/bge-m3，1024 维，HTTP sidecar）留给 S4。
"""

from __future__ import annotations

IMAGE_EMBEDDER = "chinese_clip"
IMAGE_MODEL = "chinese-clip-ViT-L-14"
VISUAL_DIMENSION = 768
# memory_vectors.space 与 Qdrant 的命名向量名都用这个值。
VISUAL_SPACE = "visual"

# ---- TEXT 一侧（S6 T115a 补）--------------------------------------------------
# 上面那句「留给 S4」是当时的判断，而 S4 没做——文本嵌入整条侧一直不存在，
# 直到 S6 补六路召回时才接上（T115）。常量放在这里而不是适配器里：
# 写入、查询、重建索引三处都只认这一个来源，是这个文件存在的全部理由。
TEXT_EMBEDDER = "bge_m3"
TEXT_MODEL = "BAAI/bge-m3"
TEXT_DIMENSION = 1024
# 1.0 ``pipeline.py:342-344`` 用同一条 text_embedding 写三行：
#   episodic/observation、episodic/event、semantic/observation
# 两个空间名照抄，因为 text_ann 的召回读的就是它们。
SEMANTIC_SPACE = "semantic"
EPISODIC_SPACE = "episodic"

# 1.0 行为基线

基线提交 `2d2cee7cbf1023f5019baaa9061af9c4cf45afa`。
2.0 重写时必须保持这些数值与规则不变，除非明确决策要改。

## 人脸

| 项 | 值 | 位置 |
|---|---|---|
| 在线匹配余弦阈值 | 0.30（硬编码，无 env 可配） | `backend/db.py:4815` |
| 全局重聚类阈值 | 0.30 | `backend/face_clustering.py:67` |
| 质量阈值 | 0.30 | `backend/face_clustering.py:67` |

## 检索融合

| 项 | 值 | 位置 |
|---|---|---|
| 默认策略 | `weighted_rrf` | `configs/retrieval/defaults.json` |
| RRF_K | 60 | `backend/retrieval/fusion.py:21` |
| anchor 加成 | +1.0 | `backend/retrieval/fusion.py:22` |
| 通道权重 | visual 2.5 / lexical 1.0 / metadata 1.0 / entity 1.0 / text 0.5 / adjacency 0.5 | `backend/retrieval/fusion.py:37` |
| adjacency | `kind="expander"`，默认关闭，`retrieve()` 恒返回空 | `backend/retrieval/adjacency.py:34`、`:47` |

## 助手

| 项 | 值 | 位置 |
|---|---|---|
| 默认 profile | `goal_driven_candidate` | `backend/agent_runtime/profile.py` |
| profile 预算 | 8 步 / 6 次调用 / 180s | `backend/agent_runtime/profile.py` |
| `max_search_calls` 硬收敛 | 1 | `backend/agent_runtime/runtime.py:1895` |
| 本地模型 max_tokens 上限 | min(1501, 4400 - prompt_tokens)，下限 64 | `backend/app.py:2680-2692` |

## 事件

| 项 | 值 | 位置 |
|---|---|---|
| 信号 | 时间 / GPS 距离线性评分（2km 衰减）/ 视觉相似 / 人物 | `backend/db.py:2000` 附近 |
| 归并时机 | 导入时同步 | `backend/db.py:2287` |

## 地点

| 项 | 值 | 位置 |
|---|---|---|
| 国内精度 | 区县级 | `backend/geocoding.py` |
| tianditu confidence | 0.90 | `backend/geocoding.py` |
| geonames confidence | 0.70 | `backend/geocoding.py` |

## 模型与嵌入

| 项 | 值 | 位置 |
|---|---|---|
| 视觉嵌入 | chinese-clip-ViT-L-14，768 维，进程内 GPU | `backend/embeddings/chinese_clip_visual.py` |
| 文本嵌入 | BAAI/bge-m3，1024 维，HTTP sidecar `127.0.0.1:8101`（CPU） | `backend/embeddings/bge_text.py` |
| 方案冻结 | `embeddings/scheme.py` 已钉死，不读 env | `backend/embeddings/scheme.py` |
| VLM 端点 | `http://127.0.0.1:8100/v1`（llama.cpp，OpenAI 兼容） | `backend/model_clients.py:537` |

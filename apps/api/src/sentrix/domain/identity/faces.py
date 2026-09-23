"""人脸聚类的纯规则：向量归一化、点积余弦、质量打分、姿态桶、贪心多视角聚类。零 IO。

对应 1.0 ``backend/face_clustering.py`` 全文（``cosine`` :11-14、``FaceSample`` :17-29、
``FacePrototype`` :32-37、``FaceCluster`` :40-51、``ClusterResult`` :53-56、
``FaceClusterer`` :59-147）与 ``backend/face_embeddings.py`` 的四个纯函数
（``normalize_embedding`` :14-19、``_clamp`` :220-221、``pose_bucket`` :224-230、
``compute_face_quality`` :233-245）。数值与分支一条没改。

**没搬过来的两样东西，以及为什么。**

- ``pairwise_metrics``（``face_clustering.py`` :149-182）：它评的是「预测标签 vs 真值
  标签」，只在 benchmark 的 ``scripts/benchmarks/evaluate_lfw_clusters.py`` 与
  ``scan_cluster_params.py`` 里出现，生产导入路径一次都没调过，2.0 也没有真值来源。
- ``FaceEmbeddingAdapter`` / ``AdaFaceAdapter`` / ``MagFaceAdapter`` / ``EmbeddingResult``
  / ``FaceEmbeddingUnavailable``（``face_embeddings.py`` :10-217）：除了
  ``backend/tests/test_face_clustering.py``，1.0 全仓没有任何地方构造它们，是死代码；
  它们还要 ``os.getenv("ADAFACE_MODEL_PATH")`` 之类的进程环境与显存。搬进 domain 既要
  带 ``os``（``domain-purity`` 契约明令禁止），又把「模型 IO」混进了纯规则。等人脸嵌入
  真落地时，它属于 infrastructure。**本文件因此没有任何 env 派生常量——四个纯函数本身
  一行 ``os`` 都没有，``os.getenv`` 全在被丢弃的适配器里。**

**为什么姿态是桶而不是权重。** 同一个人的侧脸与正脸不该因为姿态差异被判成两个人，但
侧脸之间、正脸与侧脸之间的相似度本就不能直接比。``pose_bucket`` 把 yaw 折成
frontal / profile_left / profile_right 三桶，簇内**按桶各留一个原型**
（``_update_prototypes``）——每个桶留「最好的那一张」，桶之间互不挤占名额，
``prototype_limit`` 截断的是桶数而不是样本数。

**为什么同姿态桶为空就直接命中。** ``_best_compatible_cluster`` 里
``if not same_view or self._matches(...)``：新样本的姿态桶在簇里一个成员都没有时没有
可比对象，1.0 的选择是直接收下。1.0 自带的用例
``test_frontal_and_profile_views_share_multi_view_cluster`` 正是靠这条把
profile_left / profile_right 并进只有正脸的簇。这不是完全无门槛——进这个分支之前，
``cluster.best_score``（对所有原型取最大）已经先过了一次阈值。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

# 1.0 ``FaceClusterer.__init__`` 的默认值，原文就是硬编码。刻意不做成 env / 装配项：
# 唯一的生产调用点 ``MemoryStore.recluster_faces(threshold=0.30, minimum_quality=0.55)``
# 只覆盖前两个，其余四个策略参数 1.0 从没在生产里改过——它们是聚类算法的定义，改一次
# 应该是一次代码变更 + 一次 benchmark，而不是一次配置变更。
DEFAULT_MATCH_THRESHOLD = 0.30
DEFAULT_MINIMUM_QUALITY = 0.30
PROTOTYPE_LIMIT = 6
DEFAULT_MATCH_STRATEGY = "all"
DEFAULT_TOPK = 3
DEFAULT_PERCENTILE = 0.1

# 1.0 ``face_embeddings.py`` :237-238 的质量公式常数。
QUALITY_WEIGHT_DETECTION = 0.40
QUALITY_WEIGHT_AREA = 0.20
QUALITY_WEIGHT_SHARPNESS = 0.20
QUALITY_WEIGHT_POSE = 0.20
# 人脸框面积占整图 0.08 时面积项给满。
FACE_AREA_RATIO_FULL = 0.08
# pose_score 的地板与满值点：``max(0.15, 1 - min(90, |yaw|) / 90)``。
POSE_SCORE_FLOOR = 0.15
POSE_SCORE_FULL_YAW = 90.0


def normalize_embedding(values: Sequence[float] | None) -> list[float]:
    """L2 归一化。零向量返回空表——空表是「没有可用向量」，不是「和谁都相似度为 0」。"""
    numbers = [float(value) for value in (values or [])]
    norm = math.sqrt(sum(value * value for value in numbers))
    if norm == 0:
        return []
    return [value / norm for value in numbers]


def cosine(left: Sequence[float] | None, right: Sequence[float] | None) -> float:
    """裸点积，**不在这里归一化**。

    ``FaceSample`` 在构造期就把向量归一化了，所以点积即余弦，「阈值 0.30」才是一个
    余弦阈值。这层归一化依赖写在构造期就只证一次，而不是每次比对再补一遍。
    """
    if not left or not right or len(left) != len(right):
        return 0.0
    # strict=True 只是把上面那个长度判断再声明一次，行为不变（ruff B905）。
    return sum(float(a) * float(b) for a, b in zip(left, right, strict=True))


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def pose_bucket(pose: Sequence[float] | None) -> str:
    """InsightFace 的 pose 是 (pitch, yaw, roll)，桶只看 yaw，即 ``pose[1]``。

    pose 缺失或长度不足时 yaw 取 0.0，也就是落到 frontal：不知道姿态的样本不该被预先
    判成侧脸，否则它连正脸簇的门都进不去。
    """
    values = [float(value) for value in (pose if pose is not None else [])]
    yaw = values[1] if len(values) > 1 else 0.0
    if abs(yaw) <= 20.0:
        return "frontal"
    return "profile_right" if yaw > 0 else "profile_left"


def compute_face_quality(
    detection_confidence: float,
    area_ratio: float,
    sharpness: float,
    pose: Sequence[float] | None,
) -> float:
    """四项加权：检测置信度 0.40 / 面积 0.20 / 清晰度 0.20 / 姿态 0.20，结果 clamp 到 [0,1]。

    姿态项有 0.15 的地板：侧脸是**弱证据**，不是没有证据。整项归零会把侧脸从质量门里
    直接筛掉，而质量门同时还兼任「这个样本能不能参与匹配」——那是两道不同的门。
    ``pose`` 为 None / 空时 yaw = 0 → 姿态项满分 1.0。
    """
    values = [float(value) for value in (pose if pose is not None else [])]
    yaw = abs(values[1]) if len(values) > 1 else 0.0
    pose_score = max(POSE_SCORE_FLOOR, 1.0 - min(POSE_SCORE_FULL_YAW, yaw) / POSE_SCORE_FULL_YAW)
    area_score = _clamp(float(area_ratio) / FACE_AREA_RATIO_FULL)
    sharpness_score = _clamp(sharpness)
    return _clamp(
        QUALITY_WEIGHT_DETECTION * _clamp(detection_confidence)
        + QUALITY_WEIGHT_AREA * area_score
        + QUALITY_WEIGHT_SHARPNESS * sharpness_score
        + QUALITY_WEIGHT_POSE * pose_score
    )


@dataclass(frozen=True, slots=True)
class FaceSample:
    id: str
    embedding: list[float]
    quality: float
    pose_bucket: str = "unknown"
    model_name: str = "adaface"
    model_version: str = "unconfigured"
    protected_cluster: str | None = None

    def __post_init__(self) -> None:
        # 两个不变量都在构造期立好：下游的余弦、质量门、原型更新全部假设「向量是单位
        # 向量、quality 在 [0,1]」，在这里证一次比在每个消费点各证一次可靠。
        object.__setattr__(self, "embedding", normalize_embedding(self.embedding))
        object.__setattr__(self, "quality", _clamp(self.quality))


@dataclass(slots=True)
class FacePrototype:
    sample_id: str
    embedding: list[float]
    quality: float
    pose_bucket: str


@dataclass(slots=True)
class FaceCluster:
    id: str
    members: list[FaceSample] = field(default_factory=list)
    prototypes: list[FacePrototype] = field(default_factory=list)
    noise: bool = False

    def best_score(self, sample: FaceSample) -> float:
        """与所有原型余弦的**最大**值：多视角下「像其中某一个视角」就够。"""
        if not self.prototypes:
            return 0.0
        return max(cosine(sample.embedding, item.embedding) for item in self.prototypes)


@dataclass(slots=True)
class ClusterResult:
    clusters: list[FaceCluster]
    labels: dict[str, str]


class FaceClusterer:
    """贪心多视角聚类：按质量从高到低过一遍样本，每个样本要么并进一个兼容簇，要么另起一簇。

    三条防线（1.0 :59-70 的 docstring 原意）：

    1. 低于 ``minimum_quality`` 的样本不参与匹配，只能另起一个 ``noise`` 簇——低质量
       样本最容易同时像好几个人，是典型的桥，一旦允许它做「第一个共同成员」，
       两个身份会被它连成一片；
    2. 高质量样本并进既有簇时，按姿态桶各留一个原型，正脸不会被侧脸的原型顶掉；
    3. 姿态未知的样本必须同时匹配簇里的**全部**成员，堵住单链式桥接。
    """

    def __init__(
        self,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
        minimum_quality: float = DEFAULT_MINIMUM_QUALITY,
        prototype_limit: int = PROTOTYPE_LIMIT,
        match_strategy: str = DEFAULT_MATCH_STRATEGY,
        topk: int = DEFAULT_TOPK,
        percentile: float = DEFAULT_PERCENTILE,
    ) -> None:
        self.match_threshold = float(match_threshold)
        self.minimum_quality = float(minimum_quality)
        self.prototype_limit = int(prototype_limit)
        self.match_strategy = str(match_strategy)
        self.topk = int(topk)
        self.percentile = float(percentile)

    def fit(self, samples: Sequence[FaceSample]) -> ClusterResult:
        clusters: list[FaceCluster] = []
        labels: dict[str, str] = {}
        # 先高质量后低质量：簇由最可信的样本先占住，低质量样本只能去附和既有簇，
        # 不能先开一个簇把后面的人拉偏。id 是平局时的确定性次级键。
        ordered = sorted(samples, key=lambda item: (-item.quality, item.id))
        for sample in ordered:
            candidate = self._best_compatible_cluster(sample, clusters)
            if candidate is None:
                candidate = FaceCluster(
                    id=f"cluster-{len(clusters) + 1}",
                    noise=sample.quality < self.minimum_quality,
                )
                clusters.append(candidate)
            candidate.members.append(sample)
            self._update_prototypes(candidate, sample)
            labels[sample.id] = candidate.id
        return ClusterResult(clusters=clusters, labels=labels)

    def _best_compatible_cluster(
        self, sample: FaceSample, clusters: Sequence[FaceCluster]
    ) -> FaceCluster | None:
        # 质量门在相似度之前：不够格的样本根本不进入匹配，见类 docstring 第 1 条。
        if sample.quality < self.minimum_quality:
            return None
        ranked = sorted(
            ((cluster.best_score(sample), cluster) for cluster in clusters if not cluster.noise),
            key=lambda item: (-item[0], item[1].id),
        )
        for score, cluster in ranked:
            if score < self.match_threshold:
                # 列表已按分数降序，这里写 continue 只是照抄 1.0：它的语义是「否掉这一个
                # 簇」，而不是「后面都不可能了」。
                continue
            # 模型身份不同就不能同簇：不同模型的向量不可比，混进同一簇等于拿两个坐标系
            # 的余弦互相比较。整簇否决，不是只忽略这几个成员。
            if any(
                item.model_name != sample.model_name or item.model_version != sample.model_version
                for item in cluster.members
            ):
                continue
            protected = {item.protected_cluster for item in cluster.members if item.protected_cluster}
            if sample.protected_cluster and protected and sample.protected_cluster not in protected:
                continue
            same_view = self._same_view_members(cluster, sample)
            if not same_view or self._matches(sample, same_view):
                return cluster
        return None

    def _same_view_members(self, cluster: FaceCluster, sample: FaceSample) -> list[FaceSample]:
        """参与相似度比对的成员。unknown 姿态用全部成员——它没有自己的桶可比。"""
        if sample.pose_bucket == "unknown":
            return cluster.members
        return [member for member in cluster.members if member.pose_bucket == sample.pose_bucket]

    def _matches(self, sample: FaceSample, members: Sequence[FaceSample]) -> bool:
        # 升序：sims[0] 是最差的那个成员，下面每个策略分支都建立在「下标 0 最差」上。
        sims = sorted(cosine(sample.embedding, member.embedding) for member in members)
        threshold = self.match_threshold
        if self.match_strategy == "p10":
            if len(sims) <= 2:
                return sims[0] >= threshold
            return sims[int(len(sims) * self.percentile)] >= threshold
        if self.match_strategy == "topk":
            k = max(1, min(self.topk, len(sims)))
            top = sims[-k:]
            return sum(top) / len(top) >= threshold
        if self.match_strategy == "mean":
            return sum(sims) / len(sims) >= threshold
        # 默认（含 "all"）：最差的那个成员也要过阈值，即全票通过。
        return sims[0] >= threshold

    def _update_prototypes(self, cluster: FaceCluster, sample: FaceSample) -> None:
        same_view = [item for item in cluster.prototypes if item.pose_bucket == sample.pose_bucket]
        if same_view:
            # 每个姿态桶只留一张，而且**就地**换掉：位置本身携带桶的语义，追加会让同一个
            # 桶占掉多个 prototype_limit 名额，把别的视角挤出去。
            current = max(same_view, key=lambda item: item.quality)
            if sample.quality > current.quality:
                current.sample_id = sample.id
                current.embedding = sample.embedding
                current.quality = sample.quality
            return
        cluster.prototypes.append(
            FacePrototype(sample.id, sample.embedding, sample.quality, sample.pose_bucket)
        )
        cluster.prototypes.sort(key=lambda item: (-item.quality, item.pose_bucket, item.sample_id))
        del cluster.prototypes[self.prototype_limit :]

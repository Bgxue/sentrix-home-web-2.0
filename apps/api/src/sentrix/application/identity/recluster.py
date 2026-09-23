"""全局重聚类：把整个 scope 的已验证人脸按当前阈值重新聚一遍。

逐行对应 1.0 ``backend/db.py:4956-5132`` 的 ``recluster_faces`` +
``_attach_uncertain_faces``（:5048）+ ``_sync_face_entity_statuses``（:5118）。
**三个方法是一组**：后两个的唯一调用点就是第一个（``db.py:5038-5039``），
所以要么一起搬，要么一起不搬。

**它是显式维护入口，不在任何自动路径上**（用户 2026-09-23 的裁决③）。1.0 的调用点
只有两个 benchmark 脚本（``scripts/benchmarks/ingest_album3_v2.py:67`` 与
``ingest_household_face_benchmark.py:57``），生产导入路径一次都没调过。裁决③要的是
「聚类质量差了有补救手段」——补救手段是一个**可以手动触发的入口**，不是「让它每次导入
都跑一遍」：整表重算的代价、以及它会改变历史归属这件事，都不适合放在导入链上一次
看不见的地方。

**入参取类默认值，并允许调用方覆盖。** 1.0 那两个 benchmark 传的是 ``threshold=0.30`` /
``minimum_quality=0.55``；``0.55`` 与 ``FaceClusterer`` 的类默认 ``0.30`` 不一致
（登记在 ``s5-deviations.md`` 证据 39），而那对实参属于被判死的调用方。所以默认取类默认，
覆盖走请求——一个只能按写死口径跑的入口只是一个按钮，不是一条维护路径。

**整表重算，不是增量。** 建新分组 → 迁移归属 → 撤掉没人要的簇。每一步各一条事务，
且每一步之后的状态都自洽：成员数、原型、向量 metadata 三处同步。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sentrix.domain.identity.faces import (
    DEFAULT_MATCH_THRESHOLD,
    DEFAULT_MINIMUM_QUALITY,
    FaceClusterer,
    FaceSample,
    cosine,
)
from sentrix.domain.identity.models import (
    CLUSTER_STATUS_CONFIRMED,
    CLUSTER_STATUS_PENDING,
    FaceInstanceRecord,
)
from sentrix.domain.identity.ports import EntityRepository, FaceRepository
from sentrix.domain.retrieval.ports import VectorRepository

# 认领不确定人脸时的相似度门槛。1.0 ``_attach_uncertain_faces``（``db.py:5051``）读的是
# ``os.getenv("FACE_ATTACH_THRESHOLD", "0.75")``。**2.0 不读 env**：``domain-purity``
# 禁止 domain 碰 ``os``，而这类阈值一旦能从环境改，同一个库在两台机器上会聚出不同的
# 结果，差别只藏在部署脚本里。要调就改这个常数——改一次是一次代码变更。
FACE_ATTACH_THRESHOLD = 0.75
# 1.0 ``db.py:5101``：认领还必须**领先第二名这么多**。挂错人比不挂更糟——不挂只是少
# 一张证据，挂错会把别人的脸写进这个人的历史。
FACE_ATTACH_MARGIN = 0.08
# 1.0 ``db.py:5087``：认领目标至少要有两张脸。只有一张脸的「已确认」簇还没有稳定的
# 代表向量，把不确定的脸挂上去等于放大噪声。
FACE_ATTACH_MINIMUM_MEMBERS = 2


@dataclass(frozen=True, slots=True)
class ReclusterResult:
    """一次重聚类改了什么。

    **计数不是装饰**：维护入口跑完如果只回一句「完成」，就没有任何可核的东西——
    改了哪些、撤了几个簇、认领了几张脸，全都只能再去查库。1.0 的返回里也有这几个数
    （``db.py:5040-5045``）。
    """

    instances: int
    clusters: int
    noise: int
    attached_uncertain: int
    retired_clusters: int
    retired_candidates: int
    threshold: float
    minimum_quality: float


class ReclusterFaces:
    def __init__(
        self,
        *,
        faces: FaceRepository,
        entities: EntityRepository,
        vectors: VectorRepository,
    ) -> None:
        self._faces = faces
        self._entities = entities
        self._vectors = vectors

    def execute(
        self,
        *,
        scope_id: str | None = None,
        threshold: float = DEFAULT_MATCH_THRESHOLD,
        minimum_quality: float = DEFAULT_MINIMUM_QUALITY,
    ) -> ReclusterResult:
        instances = self._faces.list_verified_instances(scope_id=scope_id, minimum_quality=minimum_quality)
        if not instances:
            # 1.0 在同一个位置提前返回（``db.py:4968-4969``）。**不做收尾动作**：
            # 一张可用的脸都没有时「把所有簇撤掉」不是重聚类，是清库。
            return ReclusterResult(
                instances=0,
                clusters=0,
                noise=0,
                attached_uncertain=0,
                retired_clusters=0,
                retired_candidates=0,
                threshold=float(threshold),
                minimum_quality=float(minimum_quality),
            )

        confirmed = self._confirmed_cluster_ids(instances)
        result = FaceClusterer(match_threshold=float(threshold), minimum_quality=float(minimum_quality)).fit(
            self._samples(instances, confirmed)
        )

        by_id = {item.id: item for item in instances}
        active: set[str] = set()
        for cluster in result.clusters:
            member_ids = [sample.id for sample in cluster.members]
            old_cluster_ids = list(
                dict.fromkeys(
                    item for item in (by_id[item_id].cluster_id for item_id in member_ids) if item is not None
                )
            )
            # 一个分组里若有已确认的簇，**保留它**：用户认过的那个人不该因为一次重算
            # 换一个 id，否则提及、事件参与者、画像全都指向一个被撤掉的簇。
            keep_id = next((item for item in old_cluster_ids if item in confirmed), old_cluster_ids[0])
            active.add(keep_id)
            self._faces.assign_instances(member_ids, keep_id)
            self._faces.set_cluster_state(
                keep_id,
                status=CLUSTER_STATUS_CONFIRMED if keep_id in confirmed else CLUSTER_STATUS_PENDING,
                member_count=len(member_ids),
            )
            self._faces.refresh_prototypes(keep_id)
            for old_cluster_id in old_cluster_ids:
                if old_cluster_id != keep_id:
                    self._faces.retire_cluster(old_cluster_id)
            self._retarget_vectors(member_ids, keep_id)

        stale = sorted({item for item in (instance.cluster_id for instance in instances) if item} - active)
        for cluster_id in stale:
            self._faces.retire_cluster(cluster_id)

        attached = self._attach_uncertain(scope_id=scope_id)
        retired_candidates = self._entities.retire_unclustered_candidates()
        return ReclusterResult(
            instances=len(instances),
            clusters=len(result.clusters),
            noise=sum(1 for cluster in result.clusters if cluster.noise),
            attached_uncertain=attached,
            retired_clusters=len(stale),
            retired_candidates=retired_candidates,
            threshold=float(threshold),
            minimum_quality=float(minimum_quality),
        )

    # ---- 内部 ------------------------------------------------------------------

    def _confirmed_cluster_ids(self, instances: Sequence[FaceInstanceRecord]) -> frozenset[str]:
        """这批实例所属的簇里，哪些是**已确认**的。

        逐个 ``get_cluster`` 而不是一次查完：保护的判据是簇的状态，而实例行上只有
        ``cluster_id``；先把 id 去重再查，查询次数等于簇数而不是实例数。
        """
        return frozenset(
            cluster_id
            for cluster_id in dict.fromkeys(
                item for item in (instance.cluster_id for instance in instances) if item
            )
            if (cluster := self._faces.get_cluster(cluster_id)) is not None
            and cluster.status == CLUSTER_STATUS_CONFIRMED
        )

    @staticmethod
    def _samples(instances: Sequence[FaceInstanceRecord], confirmed: frozenset[str]) -> list[FaceSample]:
        return [
            FaceSample(
                item.id,
                list(item.embedding),
                # 1.0 ``db.py:4975``：质量缺失时退回检测置信度——不是 0，那会让整批
                # 没有 quality 的实例一起掉进 noise。
                quality=item.quality or item.detection_confidence,
                pose_bucket=item.pose_bucket or "unknown",
                model_name=item.embedding_model or "unknown",
                model_version=item.embedding_version or "unknown",
                # 保护标记让聚类器拒绝把两个不同的已确认人物并进同一簇
                # （``FaceClusterer._best_compatible_cluster`` 的最后一道闸门）。
                protected_cluster=item.cluster_id if item.cluster_id in confirmed else None,
            )
            for item in instances
        ]

    def _retarget_vectors(self, member_ids: list[str], keep_id: str) -> None:
        """把每条实例的向量 metadata 里的 ``cluster_id`` 改成新归属。

        **读出整份再整份写回**（``update_metadata`` 是整份替换）：这里构造的正是完整的
        那一份——写入者只有 ``EnrollFace`` 与这里两处，两处都知道自己写的全部键。
        """
        for instance_id in member_ids:
            record = self._vectors.get(space="visual", source_type="face_instance", source_id=instance_id)
            if record is None:
                continue
            self._vectors.update_metadata(
                space="visual",
                source_type="face_instance",
                source_id=instance_id,
                metadata={**record.metadata, "cluster_id": keep_id},
            )

    def _attach_uncertain(self, *, scope_id: str | None) -> int:
        """1.0 ``_attach_uncertain_faces``（``db.py:5048-5117``）。

        **只认领、绝不新建簇**：不确定的脸是检出的弱证据，单独立簇会造出一堆只有一张脸
        的候选；而它们与某个已确认的人高度相似时，本来就是一伙的。

        ``scope_id`` 为空（跨空间重算）时按**每条实例自己的空间**找目标——1.0 也是
        这么传的（``self._find_attach_cluster(embedding, row["scope_id"], threshold)``，
        用的是实例所在 asset 的 scope，不是入参那个）。
        """
        candidates = self._faces.list_uncertain_unclustered(scope_id=scope_id)
        if not candidates:
            return 0
        attached: list[str] = []
        for instance in candidates:
            if not instance.embedding:
                continue
            target = self._best_attach_target(instance)
            if target is None:
                continue
            self._faces.move_instance(instance.id, target)
            attached.append(target)
        for cluster_id in dict.fromkeys(attached):
            # 成员数由原型重算带出来（``refresh_prototypes`` 写 member_count），
            # 不再单发一条 UPDATE——两处都写同一个数，迟早有一天不一致。
            self._faces.refresh_prototypes(cluster_id)
        return len(attached)

    def _best_attach_target(self, instance: FaceInstanceRecord) -> str | None:
        clusters = self._faces.list_confirmed_clusters(
            instance.scope_id, minimum_members=FACE_ATTACH_MINIMUM_MEMBERS
        )
        ranked: list[tuple[float, str]] = []
        for cluster in clusters:
            prototypes = [list(item.embedding) for item in self._faces.list_prototypes(cluster.id)]
            if not prototypes:
                prototypes = [list(cluster.representative_embedding)]
            scores = [
                cosine(instance.embedding, prototype)
                for prototype in prototypes
                if prototype and len(prototype) == len(instance.embedding)
            ]
            if scores:
                ranked.append((max(scores), cluster.id))
        if not ranked:
            return None
        # 同分按 cluster_id 升序：1.0 用的是 ``sort(reverse=True)``，同分时名次由簇 id 的
        # 降序决定——同一份数据换个插入顺序就换一个答案，而这里挂错人是要留痕的。
        ranked.sort(key=lambda item: (-item[0], item[1]))
        if ranked[0][0] < FACE_ATTACH_THRESHOLD:
            return None
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < FACE_ATTACH_MARGIN:
            return None
        return ranked[0][1]


__all__ = [
    "FACE_ATTACH_MARGIN",
    "FACE_ATTACH_MINIMUM_MEMBERS",
    "FACE_ATTACH_THRESHOLD",
    "ReclusterFaces",
    "ReclusterResult",
]

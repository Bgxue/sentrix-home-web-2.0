"""身份预置：给一个人几张照片，建出「已确认的人 + 已确认的簇 + 人脸实例 + 向量」。

此后导入的相册里与他相像的脸会被在线匹配直接挂到这个簇上，用户不用点一次确认。

1.0 两段一起搬（只搬一半等于把入口留空）：

- ``db.py:5343-5396`` ``seed_person_identity``——建什么；
- ``app.py:2253-2310`` ``_seed_face_detect``——拿哪些脸去建，也就是那**三级降级**
  （正常检测 → 放大重试 → 整图兜底）。它的实现在 ``infrastructure/models/face_adapter.py``
  的 ``detect_identity_photo``，理由是后两级都要解码 / 缩放 / 重编码图片，而 application
  层不许碰图像 IO（``pyproject.toml`` 的 ``application-purity`` 禁掉 PIL，同一条理由）。

**三级降级的第三级在 1.0 是死路径**（``identity_adapter`` 恒为 ``None`` → ``AttributeError``
→ 被裸 ``except`` 吞掉 → 永远返回 ``[]``，见 ``s5-deviations`` 证据 8）。2.0 **把它接到
真实的识别向量上**，而不是照搬一条永不执行的回退分支——S4 的先例（FTS5 已实测可用就不留
永不执行的回退）在这里同样适用。

**预置出来的实例 ``validity`` 是 ``uncertain``**，与 1.0 一致：1.0 的 INSERT 里根本没有
这一列，落的是 DDL 的默认值 ``'uncertain'``（``db.py:1238``）。预置走的是「用户说这是谁」，
不是 SCRFD 复核——这两件事的证据强度不同，不该在同一个字段上抹平。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sentrix.domain.identity.faces import normalize_embedding
from sentrix.domain.identity.models import (
    DEFAULT_SCOPE_ID,
    ENTITY_STATUS_CONFIRMED,
    FACE_VALIDITY_UNCERTAIN,
    FaceDetection,
)
from sentrix.domain.identity.ports import EntityRepository, FaceDetector, FaceRepository
from sentrix.domain.retrieval.ports import VectorRecord, VectorRepository
from sentrix.domain.retrieval.scheme import VISUAL_SPACE

# 1.0 ``db.py:5349`` 写在实体 summary 上的原文。它不是文案——``entities.summary`` 是给
# 人物视图读的字段，换一次措辞，库里就同时存在两种来源说明。
SEED_SUMMARY = "用户预置身份"

FACE_INSTANCE_SOURCE = "face_instance"


class NoSeedFacesError(Exception):
    """一张预置照片里都没检出人脸。1.0 在路由上抛 422，2.0 定义在用例里、由路由映射。"""


@dataclass(frozen=True, slots=True)
class SeedPhoto:
    """一张预置照片：资产、观察与磁盘路径。``path`` 是适配器读图用的入口。"""

    asset_id: str
    observation_id: str
    path: str


@dataclass(frozen=True, slots=True)
class SeedResult:
    """预置的结论。字段对应 1.0 ``seed_person_identity`` 的返回键。"""

    entity_id: str
    cluster_id: str
    name: str
    family_role: str | None
    aliases: tuple[str, ...]
    face_count: int
    # 检不出人脸、因而**没有**进入这个人的照片数。1.0 静默跳过它们；静默跳过与
    # 「全都没有脸」在返回里必须能分开，否则调用方只能靠总数去猜。
    photos_without_faces: int


class SeedIdentity:
    def __init__(
        self,
        *,
        detector: FaceDetector,
        faces: FaceRepository,
        entities: EntityRepository,
        vectors: VectorRepository,
    ) -> None:
        self._detector = detector
        self._faces = faces
        self._entities = entities
        self._vectors = vectors

    def execute(
        self,
        *,
        scope_id: str,
        name: str,
        family_role: str | None,
        aliases: Sequence[str],
        photos: Sequence[SeedPhoto],
    ) -> SeedResult:
        """每张照片取**质量最高**的那张脸；一张都没有就整体失败。

        「一张都没有就失败」是 1.0 的行为（路由 422）：预置一个没有任何脸的人会建出一个
        永远匹配不上任何东西的簇，而用户看到的只是「成功了」。
        """
        chosen: list[tuple[SeedPhoto, FaceDetection]] = []
        skipped = 0
        for photo in photos:
            faces = self._detector.detect_identity_photo(photo.path)
            if not faces:
                skipped += 1
                continue
            chosen.append((photo, max(faces, key=_quality_of)))
        if not chosen:
            raise NoSeedFacesError("上传的照片里检不出任何人脸")

        entity = self._entities.create_person(
            name,
            status=ENTITY_STATUS_CONFIRMED,
            family_role=family_role,
            confidence=1.0,
            summary=SEED_SUMMARY,
            scope_id=scope_id or DEFAULT_SCOPE_ID,
        )
        stored_aliases = self._entities.set_person_aliases(entity.id, aliases) if aliases else ()

        best_face = max(chosen, key=lambda item: _quality_of(item[1]))[1]
        # 簇先建出来再确认：``confirm_cluster`` 一次做完「转 confirmed + 回填归属 + 修订 +1」，
        # 与 1.0 那条直接插成 confirmed 的 INSERT 终态一致（revision 除外，见 deviations）。
        cluster = self._faces.create_cluster(
            normalize_embedding(best_face.embedding), confidence=1.0, scope_id=entity.scope_id
        )
        self._faces.confirm_cluster(cluster.id, entity.id)

        for photo, face in chosen:
            self._store_instance(photo, face, cluster.id, entity.scope_id)
        # 回写 member_count 并重算原型。原型是后续在线匹配真正读的东西（没有它，匹配会
        # 退回代表向量），所以这一步不是收尾，是让预置**生效**。
        self._faces.refresh_prototypes(cluster.id)

        return SeedResult(
            entity_id=entity.id,
            cluster_id=cluster.id,
            name=entity.canonical_name,
            family_role=entity.family_role,
            aliases=tuple(stored_aliases),
            face_count=len(chosen),
            photos_without_faces=skipped,
        )

    def _store_instance(self, photo: SeedPhoto, face: FaceDetection, cluster_id: str, scope_id: str) -> None:
        """落一条实例 + 它的权威向量。两处的 ``pose`` 都取自检测结果，不在这里重算。"""
        embedding = normalize_embedding(face.embedding)
        instance = self._faces.add_instance(
            asset_id=photo.asset_id,
            observation_id=photo.observation_id,
            cluster_id=cluster_id,
            bbox=face.bbox,
            embedding=embedding,
            detection_confidence=face.confidence,
            quality=_quality_of(face),
            pose=face.pose,
            pose_bucket_name=face.pose_bucket or "unknown",
            embedding_model=face.embedding_model,
            embedding_version=face.embedding_version,
            embedding_quality_signal=face.quality_signal,
            # 1.0 的 INSERT 不含这一列，落的是 DDL 默认值——照搬，理由见模块注释。
            validity=FACE_VALIDITY_UNCERTAIN,
            area_ratio=face.area_ratio,
            sharpness=face.sharpness,
        )
        if not embedding:
            return
        self._vectors.upsert(
            VectorRecord(
                scope_id=scope_id,
                space=VISUAL_SPACE,
                source_type=FACE_INSTANCE_SOURCE,
                source_id=instance.id,
                model_name=face.embedding_model,
                vector=tuple(float(value) for value in embedding),
                metadata={
                    "cluster_id": cluster_id,
                    "asset_id": photo.asset_id,
                    "observation_id": photo.observation_id,
                    "quality": _quality_of(face),
                    "pose_bucket": face.pose_bucket or "unknown",
                },
            )
        )


def _quality_of(face: FaceDetection) -> float:
    """这张脸的质量分。检测器没算过（``quality is None``）时退回检测置信度。

    退回而不是补算：补算要用定义域里的质量公式，而这里问的是「几张照片里哪张最好」，
    置信度作为排序键足够，且与 1.0 ``max(..., key=lambda f: f.get("quality", f.get("confidence", 0)))``
    的取值一致。
    """
    return float(face.quality) if face.quality is not None else float(face.confidence)

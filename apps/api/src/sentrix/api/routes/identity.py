"""身份端点：簇审阅、人物、人脸裁剪。

**只做 HTTP 映射与服务端字段拼装。** 审阅的四条规则（同名并入、已确认不可拒绝、两个已
确认的人不能自动合并、实例必须属于该簇）全在 ``application/identity`` 里；路由把它们
抛出的带码错误**原样放行**，由 ``api/errors.py`` 那一个处理器映射状态（口径写在
``domain/errors.py``）。路由自己在这里发现的边界问题（找不到实例、bbox 非法）也抛同一族
的类型，理由同样是「响应形状只有一种」。

**读侧为什么在这里拼而不是另开一个用例。** 1.0 的 ``list_face_clusters`` 与
``get_entity_detail`` 本身就是「仓储查询 + 几个现算的派生字段」；2.0 的读侧同构，
拼装的每一步都只是把记录字段搬进响应模型。真正带规则的两块——``canonical_name`` 对
``pending`` 的改写与 ``reviewable`` / ``single_sample``——1.0 也写在路由里
（``app.py:1518`` / ``db.py:5146``），2.0 保持同一个位置，不额外发明一层。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response

from sentrix.api.deps import get_container
from sentrix.api.errors import InvalidRequestError, NotFoundError, UnprocessableError
from sentrix.api.schemas.identity import (
    BatchConfirmFailure,
    BatchConfirmOutcome,
    BatchConfirmRequest,
    BatchConfirmResponse,
    ConfirmClusterRequest,
    ConfirmClusterResponse,
    FaceClusterListResponse,
    FaceClusterSummary,
    FaceCropRef,
    MergeClustersRequest,
    PersonDetailResponse,
    PersonListResponse,
    PersonPayload,
    RejectClusterResponse,
    RenamePersonRequest,
    RenamePersonResponse,
    SeedIdentityResponse,
    SplitClusterRequest,
)
from sentrix.application.identity.name_person import NamePerson
from sentrix.application.identity.review_cluster import ReviewCluster
from sentrix.application.identity.seed_identity import (
    NoSeedFacesError,
    SeedIdentity,
    SeedInstanceNotFoundError,
    SeedPhoto,
)
from sentrix.application.memories.assign_events import ConfirmedPersonEvents, EventVisualSimilarity
from sentrix.bootstrap.container import Container
from sentrix.domain.identity.models import (
    CLUSTER_STATUS_PENDING,
    CLUSTER_STATUS_REJECTED,
    ENTITY_STATUS_CONFIRMED,
    ENTITY_STATUS_PENDING,
    ENTITY_TYPE_PERSON,
    PENDING_CLUSTER_NAME,
    EntityRecord,
    FaceClusterRecord,
    FaceInstanceRecord,
)
from sentrix.domain.media.models import Asset, AssetStatus, MediaType, Observation

router = APIRouter(tags=["identity"])

# 1.0 ``db.py:5149`` 的 LIMIT 12：簇审阅页一屏放得下的样本数。
CLUSTER_SAMPLES_LIMIT = 12
# 1.0 ``app.py:2530-2532`` 的三个字面量。裁剪尺寸与质量是**交付契约**：换一个数字，
# 前端缓存里的缩略图与新的就再也对不上。
FACE_CROP_MAX_DIMENSION = 256
FACE_CROP_QUALITY = 88
FACE_CROP_CACHE_CONTROL = "private, max-age=86400"

# 1.0 ``app.py:2337`` 预置照片的来源标记。
SEED_SOURCE_TYPE = "identity_seed"


# ---- 用例装配 --------------------------------------------------------------------


def _confirmed_person_events(container: Container) -> ConfirmedPersonEvents:
    """确认之后的事件侧。**只在确认这一类用例里接**——重分段不是导入链的一部分。

    两个 ``confirmed_people`` 来源与 ``api/routes/media.py`` 的 ``AssignEvents`` 是同一对
    仓储方法：事件打分与重分段算的是同一个 ``person_score``，两个装配点取不同的来源，
    「按人找事件」的分歧就再也不会被发现。
    """
    return ConfirmedPersonEvents(
        events=container.event_repository,
        participants=container.event_participant_repository,
        observations=container.observation_repository,
        assets=container.asset_repository,
        entities=container.entity_repository,
        visual_similarity=EventVisualSimilarity(
            vectors=container.vector_repository,
            observations=container.observation_repository,
            assets=container.asset_repository,
        ),
        confirmed_people=container.entity_repository.confirmed_people_for_observation,
        confirmed_event_people=container.entity_repository.confirmed_people_for_event,
    )


def _review_cluster(container: Container) -> ReviewCluster:
    return ReviewCluster(
        faces=container.face_repository,
        entities=container.entity_repository,
        facts=container.fact_repository,
        events=_confirmed_person_events(container),
    )


def _name_person(container: Container) -> NamePerson:
    return NamePerson(
        faces=container.face_repository,
        entities=container.entity_repository,
        review=_review_cluster(container),
    )


def _seed_identity(container: Container) -> SeedIdentity:
    return SeedIdentity(
        detector=container.face_detector,
        faces=container.face_repository,
        entities=container.entity_repository,
        vectors=container.vector_repository,
    )


# ---- 记录 → 传输模型 --------------------------------------------------------------


def _optional_isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _person_payload(container: Container, entity: EntityRecord) -> PersonPayload:
    """一个人物实体。

    ``pending`` 的实体的内部名是「待确认人物簇 · cluster-x」（``pending_cluster_name``），
    那是**落库的数据**，不该出现在接口上——1.0 同样在这一层把它换掉
    （``app.py:1518`` 的 ``display_name``）。与 ``FaceRepository`` 的簇记录用同一个常量，
    两处漂开一次，同一个实体会在簇列表里叫一个名字、在人物列表里叫另一个。
    """
    pending = entity.status == ENTITY_STATUS_PENDING
    return PersonPayload(
        id=entity.id,
        scope_id=entity.scope_id,
        canonical_name=PENDING_CLUSTER_NAME if pending else entity.canonical_name,
        status=entity.status,
        family_role=None if pending else entity.family_role,
        summary=entity.summary,
        confidence=entity.confidence,
        confirmed=entity.status == ENTITY_STATUS_CONFIRMED,
        aliases=list(container.entity_repository.person_aliases(entity.id)),
    )


def _crop_ref(instance: FaceInstanceRecord) -> FaceCropRef:
    return FaceCropRef(
        face_instance_id=instance.id,
        asset_id=instance.asset_id,
        observation_id=instance.observation_id,
        file_name=instance.file_name,
        media_type=instance.media_type,
        bbox=list(instance.bbox),
        detection_confidence=instance.detection_confidence,
        quality=instance.quality,
        crop_url=f"/api/v1/face-instances/{instance.id}/crop",
    )


def _cluster_payload(container: Container, cluster: FaceClusterRecord) -> FaceClusterSummary:
    instances = container.face_repository.list_instances(cluster.id)
    return FaceClusterSummary(
        id=cluster.id,
        scope_id=cluster.scope_id,
        status=cluster.status,
        entity_id=cluster.entity_id,
        canonical_name=cluster.canonical_name,
        family_role=cluster.family_role,
        entity_status=cluster.entity_status,
        member_count=cluster.member_count,
        confidence=cluster.confidence,
        revision=cluster.revision,
        created_at=_optional_isoformat(cluster.created_at),
        updated_at=_optional_isoformat(cluster.updated_at),
        reviewable=cluster.status != CLUSTER_STATUS_REJECTED and cluster.member_count > 0,
        single_sample=cluster.status == CLUSTER_STATUS_PENDING and cluster.member_count <= 1,
        samples=[_crop_ref(item) for item in instances[:CLUSTER_SAMPLES_LIMIT]],
    )


def _cluster_or_404(container: Container, cluster_id: str) -> FaceClusterRecord:
    cluster = container.face_repository.get_cluster(cluster_id)
    if cluster is None:
        raise NotFoundError(f"face cluster not found: {cluster_id}")
    return cluster


def _person_or_404(container: Container, person_id: str) -> EntityRecord:
    person = container.entity_repository.get(person_id)
    if person is None or person.entity_type != ENTITY_TYPE_PERSON:
        raise NotFoundError("person not found")
    return person


# ---- 簇列表与四审阅 ---------------------------------------------------------------


@router.get("/face-clusters")
def list_face_clusters(
    status: str | None = None,
    scope_id: str | None = None,
    container: Container = Depends(get_container),
) -> FaceClusterListResponse:
    clusters = container.face_repository.list_clusters(status=status)
    if scope_id:
        # 1.0 在路由里做的同一条过滤（``app.py:1972``）：仓储的 ``list_clusters`` 是
        # 「进人物视图的全部簇」，按空间再切一刀是调用方的事。
        clusters = [item for item in clusters if item.scope_id == scope_id]
    return FaceClusterListResponse(clusters=[_cluster_payload(container, item) for item in clusters])


@router.post("/face-clusters/{cluster_id}/confirm")
def confirm_face_cluster(
    cluster_id: str,
    payload: ConfirmClusterRequest,
    container: Container = Depends(get_container),
) -> ConfirmClusterResponse:
    result = _review_cluster(container).confirm(
        cluster_id, name=payload.name, family_role=payload.family_role
    )
    return ConfirmClusterResponse(
        entity=_person_payload(container, result.entity), merged_into=result.merged_into
    )


@router.post("/face-clusters/{cluster_id}/reject")
def reject_face_cluster(
    cluster_id: str, container: Container = Depends(get_container)
) -> RejectClusterResponse:
    # 用例找不到簇时抛 ClusterNotFoundError（404），由统一处理器映射；这里不做二次判断。
    rejected = _review_cluster(container).reject(cluster_id)
    return RejectClusterResponse(cluster_id=cluster_id, rejected_entity_id=rejected)


@router.post("/face-clusters/merge")
def merge_face_clusters(
    payload: MergeClustersRequest, container: Container = Depends(get_container)
) -> FaceClusterSummary:
    # 两个 id 缺一个就是请求本身不成立（400）。1.0 在路由上判的是同一件事
    # （``app.py:2075``）；不判的话空 id 会一路走到仓储、变成一条 404，把调用方错误
    # 报成「这个簇不存在」。
    if not payload.target_cluster_id or not payload.source_cluster_id:
        raise InvalidRequestError("target_cluster_id and source_cluster_id are required")
    target = _review_cluster(container).merge(
        target_cluster_id=payload.target_cluster_id, source_cluster_id=payload.source_cluster_id
    )
    return _cluster_payload(container, target)


@router.post("/face-clusters/{cluster_id}/split")
def split_face_cluster(
    cluster_id: str,
    payload: SplitClusterRequest,
    container: Container = Depends(get_container),
) -> FaceClusterSummary:
    if not payload.face_instance_id:
        raise InvalidRequestError("face_instance_id is required")
    created = _review_cluster(container).split(cluster_id, face_instance_id=payload.face_instance_id)
    return _cluster_payload(container, created)


# ---- 人物 --------------------------------------------------------------------


@router.get("/people")
def list_people(
    status: str | None = None,
    scope_id: str | None = None,
    container: Container = Depends(get_container),
) -> PersonListResponse:
    people = container.entity_repository.list_people(status=status, scope_id=scope_id)
    return PersonListResponse(people=[_person_payload(container, item) for item in people])


@router.get("/people/{person_id}")
def get_person(person_id: str, container: Container = Depends(get_container)) -> PersonDetailResponse:
    person = _person_or_404(container, person_id)
    clusters = container.face_repository.list_clusters_for_entity(person.id)
    samples = [(cluster, container.face_repository.list_instances(cluster.id)) for cluster in clusters]
    avatar: FaceInstanceRecord | None = None
    for cluster, instances in samples:
        if cluster.status == CLUSTER_STATUS_REJECTED:
            continue
        for instance in instances:
            # 1.0 ``db.py:4576``：检测置信度降序、创建时间升序取首个。
            if avatar is None or (
                instance.detection_confidence,
                -instance.created_at.timestamp() if instance.created_at else 0.0,
            ) > (
                avatar.detection_confidence,
                -avatar.created_at.timestamp() if avatar.created_at else 0.0,
            ):
                avatar = instance
    return PersonDetailResponse(
        entity=_person_payload(container, person),
        avatar_face_instance_id=avatar.id if avatar else None,
        clusters=[_cluster_payload(container, cluster) for cluster, _ in samples],
    )


@router.patch("/people/{person_id}/name")
def rename_person(
    person_id: str,
    payload: RenamePersonRequest,
    container: Container = Depends(get_container),
) -> RenamePersonResponse:
    result = _name_person(container).rename(person_id, payload.name)
    if result is None:
        raise NotFoundError("person not found")
    return RenamePersonResponse(
        entity=_person_payload(container, result.entity), aliases=list(result.aliases)
    )


@router.post("/people/batch-confirm")
def batch_confirm_people(
    payload: BatchConfirmRequest, container: Container = Depends(get_container)
) -> BatchConfirmResponse:
    """逐件确认，逐件回执。**一件失败不影响其余**（1.0 ``app.py:2205`` 的形状）。"""
    use_case = _name_person(container)
    confirmed: list[BatchConfirmOutcome] = []
    failed: list[BatchConfirmFailure] = []
    for item in payload.items:
        try:
            result = use_case.confirm_entity(item.person_id, name=item.name, family_role=item.family_role)
        except Exception as error:  # noqa: BLE001 — 逐件回执正是要把失败收进响应
            failed.append(
                BatchConfirmFailure(person_id=item.person_id, name=item.name, error=str(error)[:200])
            )
            continue
        if result is None:
            failed.append(
                BatchConfirmFailure(person_id=item.person_id, name=item.name, error="person not found")
            )
            continue
        confirmed.append(
            BatchConfirmOutcome(
                person_id=item.person_id,
                entity_id=result.entity.id,
                name=item.name,
                family_role=item.family_role,
                merged=result.merged_into is not None,
            )
        )
    return BatchConfirmResponse(confirmed=confirmed, failed=failed, count=len(confirmed))


# ---- 身份预置 --------------------------------------------------------------------


def _store_seed_photo(
    container: Container, *, scope_id: str, file_name: str, content_type: str | None, content: bytes
) -> SeedPhoto:
    """把一张预置照片落成 asset + observation，返回用例要的那三个标识。

    **不走导入链。** 1.0 的预置是 ``create_asset`` + ``add_observation`` 两句
    （``app.py:2337-2339``），不跑 VLM、不建事件、不写词法投影——用户在说「这张脸是妈妈」，
    不是在说「这张照片属于某次旅行」。走导入链会让预置照片长出事件，把两个语义混起来。
    """
    asset_id = f"asset_{hashlib.sha256(content).hexdigest()[:12]}"
    path = container.media_storage.store(asset_id, file_name, content)
    now = datetime.now(UTC)
    asset = Asset(
        id=asset_id,
        scope_id=scope_id,
        file_name=file_name,
        media_type=MediaType.from_upload(content_type, file_name),
        path=str(path),
        mime_type=content_type,
        size_bytes=len(content),
        status=AssetStatus.PROCESSED,
        content_sha256=hashlib.sha256(content).hexdigest(),
        captured_at=now,
        captured_location=None,
        created_at=now,
        updated_at=now,
    )
    container.asset_repository.add(asset)
    observation = Observation(
        id=f"obs_{hashlib.sha256((asset_id + SEED_SOURCE_TYPE).encode()).hexdigest()[:12]}",
        scope_id=scope_id,
        asset_id=asset_id,
        source_type=SEED_SOURCE_TYPE,
        captured_at=now,
        confidence=0.0,
        created_at=now,
        updated_at=now,
    )
    container.observation_repository.add(observation)
    return SeedPhoto(asset_id=asset_id, observation_id=observation.id, path=str(path))


def _split_terms(raw: str | None) -> list[str]:
    """1.0 ``app.py:2323``：顿号或逗号分隔，去空、保序。

    别名与 ``face_instance_ids`` 共用这一条规则——两处都是「一个表单字段里的多个值」，
    两套分隔法只会让调用方猜。
    """
    return [item.strip() for item in (raw or "").replace("、", ",").split(",") if item.strip()]


@router.post("/people/seed", status_code=201)
async def seed_person_identity(
    name: str = Form(...),
    family_role: str | None = Form(None),
    aliases: str | None = Form(None),
    scope_id: str = Form("home-default"),
    face_instance_ids: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    container: Container = Depends(get_container),
) -> SeedIdentityResponse:
    """两条入口：上传照片（1.0 的口径）与**指定已检出的人脸实例**（裁决二）。

    后者说「我指的是这张脸」，前者说「这张照片里的那个人」——在多人的家庭照上前者会挑
    质量最高的那张脸，而那通常不是用户想预置的人（见 ``seed_identity`` 的模块注释）。
    两种样本可以混用。
    """
    clean_name = name.strip()
    if not clean_name:
        # 1.0 在预置路由上判的是 400（``app.py:2321``），而「图里没有人脸」才是 422。
        # 两个不同的失败原因有两个不同的码，合并成一个码会让调用方分不清该改请求还是换图。
        raise InvalidRequestError("name is required")
    instance_ids = _split_terms(face_instance_ids)
    if not files and not instance_ids:
        # 1.0 的 ``files`` 是必填，所以这条在 1.0 不存在；两条入口之后它是新的边界：
        # 一个两个都没给请求不是「预置不出人」，是请求本身不成立（400，不是 422）。
        raise InvalidRequestError("files or face_instance_ids is required")
    # 空间行（第 104 条的裁决，2026-09-24）。1.0 的预置路由走 ``create_asset``，ensure 在
    # 那个函数里（``db.py:1462``）——它是 1.0 唯一的资产写入点。2.0 的预置路由有**两条**
    # 入口，而实例那条一张资产都不写，把 ensure 接在 ``_store_seed_photo`` 上会漏掉它；
    # 落点因此取两条入口的公共点（第 102 条挑 ``ImportAssets.execute`` 是同一条理由）。
    # ``list_clusters`` **内连** ``memory_spaces``，少了这一行，全新 scope 上预置出来的簇
    # 在审阅页里读不出来——不是「少一条数据」，是「这个人不存在」。
    container.spaces.ensure(scope_id, scope_id)
    photos = [
        _store_seed_photo(
            container,
            scope_id=scope_id,
            file_name=upload.filename or "identity.bin",
            content_type=upload.content_type,
            content=await upload.read(),
        )
        for upload in files
    ]
    try:
        result = _seed_identity(container).execute(
            scope_id=scope_id,
            name=clean_name,
            family_role=(family_role or "").strip() or None,
            aliases=_split_terms(aliases),
            photos=photos,
            face_instance_ids=instance_ids,
        )
    except SeedInstanceNotFoundError as error:
        # 调用方指向了一个不存在的对象 → 404。与「图里没有人脸」（422）分开：
        # 一个要换 id，一个要换图。
        raise NotFoundError(str(error)) from error
    except NoSeedFacesError as error:
        # 1.0 在路由上抛 422（``app.py:2345``）：请求是好的，是这几张图里没有人脸。
        raise UnprocessableError(str(error)) from error
    return SeedIdentityResponse(
        entity_id=result.entity_id,
        cluster_id=result.cluster_id,
        name=result.name,
        family_role=result.family_role,
        aliases=list(result.aliases),
        face_count=result.face_count,
        photos_without_faces=result.photos_without_faces,
    )


# ---- 人脸裁剪 --------------------------------------------------------------------


@router.get("/face-instances/{face_instance_id}/crop")
def face_instance_crop(face_instance_id: str, container: Container = Depends(get_container)) -> Response:
    """一张人脸的缩略图。1.0 ``app.py:2508``。

    bbox 是**方向校正后**的坐标（检测器用 cv2 读图，而 cv2 会应用 EXIF 方向），所以
    转置这一步必须与检测时一致——少了它，竖拍照片上的框会整体偏。
    """
    instance = container.face_repository.get_instance(face_instance_id)
    if instance is None or not instance.asset_path:
        raise NotFoundError("face instance not found")
    if len(instance.bbox) < 4:
        raise UnprocessableError("face crop is unavailable: invalid face bounding box")
    try:
        payload = container.preview_encoder.crop_jpeg(
            instance.asset_path,
            instance.bbox[:4],
            max_dimension=FACE_CROP_MAX_DIMENSION,
            quality=FACE_CROP_QUALITY,
        )
    except (OSError, ValueError, TypeError) as error:
        raise UnprocessableError(f"face crop is unavailable: {error}") from error
    return Response(
        content=payload,
        media_type="image/jpeg",
        headers={"Cache-Control": FACE_CROP_CACHE_CONTROL},
    )

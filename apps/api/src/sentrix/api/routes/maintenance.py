"""维护端点。**这里只有显式请求能进来的动作，没有一条挂在导入链上。**

第一条是全局人脸重聚类（用户 2026-09-23 裁决③）。它的调用方式决定了它的性质：

- 1.0 的 ``recluster_faces`` 只在两个 benchmark 脚本里被调（``db.py`` 那两个调用点），
  生产导入路径一次都没调过；
- 2.0 把它做成**显式入口**而不是导入链上的一步。理由是它做的是整表重算——会改变历史
  归属（哪些脸属于哪一簇）、会撤掉簇、会把候选人物转成 rejected。放在导入链上意味着
  每导一批照片都可能重写全库的身份归属，而这件事没有任何人事先要求过。

**「不自动跑」这件事本身要被钉住**，所以门禁里有一条对照断言：同一份数据，走导入路径
不改变已经存在的簇，走这个端点才改变。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from sentrix.api.deps import get_container
from sentrix.api.schemas.maintenance import ReclusterFacesRequest, ReclusterFacesResponse
from sentrix.application.identity.recluster import ReclusterFaces
from sentrix.bootstrap.container import Container

router = APIRouter(tags=["maintenance"])


@router.post("/maintenance/recluster-faces")
def recluster_faces(
    payload: ReclusterFacesRequest, container: Container = Depends(get_container)
) -> ReclusterFacesResponse:
    result = ReclusterFaces(
        faces=container.face_repository,
        entities=container.entity_repository,
        vectors=container.vector_repository,
    ).execute(
        scope_id=payload.scope_id,
        threshold=payload.threshold,
        minimum_quality=payload.minimum_quality,
    )
    return ReclusterFacesResponse(
        instances=result.instances,
        clusters=result.clusters,
        noise=result.noise,
        attached_uncertain=result.attached_uncertain,
        retired_clusters=result.retired_clusters,
        retired_candidates=result.retired_candidates,
        threshold=result.threshold,
        minimum_quality=result.minimum_quality,
    )

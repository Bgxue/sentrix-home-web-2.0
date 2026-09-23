"""维护端点的传输模型。

与其余 ``api/schemas/*`` 一样只是搬运：字段与 ``ReclusterResult`` 一一对应，不做任何
派生。``threshold`` / ``minimum_quality`` 的默认值直接取 ``FaceClusterer`` 的类默认
（``0.30`` / ``0.30``）——它们是**聚类算法的定义**，不是可以随手调的配置；
放在请求模型上而不是路由里，是为了让默认值出现在 OpenAPI 契约里，调用方看得见自己
不传时会按什么口径跑。
"""

from __future__ import annotations

from pydantic import BaseModel

from sentrix.domain.identity.faces import DEFAULT_MATCH_THRESHOLD, DEFAULT_MINIMUM_QUALITY


class ReclusterFacesRequest(BaseModel):
    """``scope_id`` 为空表示跨空间重算（1.0 ``recluster_faces(scope_id=None)``）。

    **不是「默认空间」**：``None`` 与 ``"home-default"`` 在两处不同——跨空间时
    ``list_verified_instances`` 不按 asset 的 scope 过滤，而认领不确定人脸时按每条
    实例自己的空间找目标。
    """

    scope_id: str | None = None
    threshold: float = DEFAULT_MATCH_THRESHOLD
    minimum_quality: float = DEFAULT_MINIMUM_QUALITY


class ReclusterFacesResponse(BaseModel):
    """一次重聚类改了什么。**计数就是这条路径的全部可核产出**。"""

    instances: int
    clusters: int
    noise: int
    attached_uncertain: int
    retired_clusters: int
    retired_candidates: int
    threshold: float
    minimum_quality: float

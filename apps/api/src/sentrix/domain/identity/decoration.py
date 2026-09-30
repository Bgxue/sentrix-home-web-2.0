"""实体列表上那几个派生判断的**纯函数那一半**（S6 · T128a）。

1.0 把它们写在取数的 SQL 旁边：``_decorate_entities`` 的收尾循环（``db.py:4127-4137``）
与 ``public_entity``（``db.py:4674-4685``）。2.0 把两半分开——**数由仓储批量取**
（``EntityDecoration``），**判断在这里**。判断是纯的，可以穷举边界，而边界恰好是这几处
最容易写错的地方：

- ``private_flag`` 判的是 ``is True`` 而不是真值：属性值是从 JSON 解出来的任意类型，
  写成真值判断的话整数 ``1`` 会让一个地点变成私密；
- ``single_sample`` 只看 ``member_count``，**不看簇的 status**——一个被拒的簇也照样计入；
- 空簇列表下 ``all()`` 为真，于是「一个簇都还没有的 pending 人物」判为 ``single_sample``。
  听着怪，但这是 1.0 的行为，照搬。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sentrix.domain.identity.models import (
    CLUSTER_STATUS_REJECTED,
    ENTITY_STATUS_CONFIRMED,
    ENTITY_TYPE_PERSON,
    ClusterState,
)

# 1.0 ``db.py:4679-4684`` 的三个字面量。``private_flag`` 与 ``alias`` 是**用户维护的
# 属性键**——经 ``set_property`` 写进来，入口是 ``PUT /entities/{id}/properties/{key}``。
#
# **``alias`` 不是 ``aliases``。** 人物别名的属性键是 ``aliases``（复数，
# ``entities.py`` 的 ``ALIAS_PROPERTY_KEY``），地点的对外名用的却是 ``alias``（单数）。
# 两个键差一个字母，把地点的私密名接到人物别名上，读起来毫无痕迹。
PRIVATE_FLAG_KEY = "private_flag"
ALIAS_KEY = "alias"
# 取不到别名时的兜底名。1.0 同一个串，一字不差。
PRIVATE_PLACE_NAME = "私密地点"


def is_reviewable(
    *,
    entity_type: str,
    status: str,
    clusters: Sequence[ClusterState],
    evidence_count: int,
) -> bool:
    """「这个实体现在值不值得用户看一眼」。1.0 ``db.py:4128-4136``。

    两类实体的判据**完全不同，不是同一个公式换个参数**：

    - **人物**：已确认的直接算数；否则只要有一个**没被拒**且**有成员**的簇就算数。
      成员数为 0 的簇不算——那是聚类留下的空壳，点进去什么都没有。
    - **非人物**：看**证据条数**，与簇无关。地点和物件没有脸，「有没有东西可看」对它们
      就是「有没有观察提到过它」（``evidence_count`` = ``entity_observations`` 的行数）。
    """
    if entity_type != ENTITY_TYPE_PERSON:
        return evidence_count > 0
    if status == ENTITY_STATUS_CONFIRMED:
        return True
    return any(cluster.status != CLUSTER_STATUS_REJECTED and cluster.member_count > 0 for cluster in clusters)


def is_single_sample(*, status: str, clusters: Sequence[ClusterState]) -> bool:
    """「这是个只有一张脸的孤证」。1.0 ``db.py:4132-4133``。

    只对人物有意义——1.0 只给 person 写这个键，非人物那一支没有它。

    **两处别顺手「修好」的地方**：判据里没有簇的 ``status``（被拒的簇也照样计入
    ``member_count``）；空列表下 ``all()`` 为真，所以一个还没聚出任何簇的 pending
    人物判为 ``single_sample``。两处都是 1.0 的行为，照搬。
    """
    return status != ENTITY_STATUS_CONFIRMED and all(cluster.member_count <= 1 for cluster in clusters)


def private_place(canonical_name: str, properties: Mapping[str, object]) -> tuple[str, bool]:
    """地点的对外投影，返回 ``(对外名字, 是否私密)``。1.0 ``db.py:4674-4685``。

    ``properties`` 是 ``property_key → value`` 的表（**值**，不是属性行）。

    ``is True`` 不能写成真值判断：``private_flag`` 的值是 JSON 解出来的，``1`` 与
    ``"true"`` 都能通过真值判断，而 1.0 只认真正的布尔 ``true``。

    别名的兜底用 ``or`` 而不是 ``get(key, 默认值)``：**空串也要落到「私密地点」上**
    （1.0 是 ``str(... or "私密地点")``）。
    """
    if properties.get(PRIVATE_FLAG_KEY) is not True:
        return canonical_name, False
    return str(properties.get(ALIAS_KEY) or PRIVATE_PLACE_NAME), True

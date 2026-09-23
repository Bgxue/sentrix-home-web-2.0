"""身份域端口：人脸仓储与实体仓储。

domain 只声明形状，SQL 在 infrastructure。两个仓储分开不是因为表多，而是因为**写侧
是两条不同的链路**：``FaceRepository`` 的写入者是在线匹配与簇审阅（Task 72/73），
``EntityRepository`` 的写入者是命名与属性维护（Task 73/74）。合成一个
``IdentityRepository`` 会让「谁改了实体的名字」这件事在类型上消失。

**成员一律声明成方法，只读属性用 ``@property``。** 沿用
``domain/retrieval/ports.py`` 里 ``VisualEmbedder`` 的注解：适配器把只读属性实现成
property 时，mypy 的 ``arg-type`` 不允许一个只读 property 去满足「可写变量」的协议约定。
本文件目前没有属性成员，这条备忘留给下一个往这里加属性的人——写成裸属性名会红。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sentrix.domain.identity.models import (
    DEFAULT_SCOPE_ID,
    ENTITY_STATUS_PENDING,
    PROPERTY_SOURCE_DERIVED,
    EntityMentionRecord,
    EntityPropertyRecord,
    EntityRecord,
    EntityRevisionRecord,
    FaceClusterRecord,
    FaceDetection,
    FaceInstanceRecord,
    FacePrototypeRecord,
    PersonNameMatch,
)


class FaceDetector(Protocol):
    """一张图 → 人脸表。实现在 ``infrastructure/models/face_adapter.py``。

    **只声明两个检测入口，不声明适配器的其余状态。** 1.0 的 ``FaceAdapter`` 还带一串
    ``identity_*`` 字段（``identity_ready`` / ``identity_error`` / ``identity_fallback``），
    它们服务于那三个被判死的 AdaFace 适配器（见 ``s5-deviations`` 证据 1/3）；2.0 这条
    链路只剩「有向量 / 没向量」，而那是 ``FaceDetection`` 自己的字段，不是适配器的状态。

    两个入口的差别在**召回策略**，不在能力上：``detect`` 是相册导入用的常规检测；
    ``detect_identity_photo`` 是用户预置身份照片用的三级降级（常规 → 放大重试 → 整图
    兜底），它更慢、更宽松，也因此只该用在「用户明确说这张图里有这个人」的场合。
    """

    def detect(self, path: str) -> Sequence[FaceDetection]:
        """常规检测：返回的人脸都已经带好向量（没有向量的候选被丢掉）。"""
        ...

    def detect_identity_photo(self, path: str) -> Sequence[FaceDetection]:
        """预置照片的检测：检不出就放大重试，再检不出就把整张图当成一张脸。"""
        ...


class FaceRepository(Protocol):
    """人脸簇 / 实例 / 原型。

    ``list_clusters`` 强制 ``memory_spaces.include_in_people = 1``——这是端口的契约，
    不是某个调用方的过滤：不给这一条，任何人写一次 ``/api/people`` 都会把评测空间
    聚出来的人脸当家庭成员展示。
    """

    def list_clusters(self, *, status: str | None = None) -> Sequence[FaceClusterRecord]:
        """``status`` 为空时返回除 ``rejected`` 外的全部簇，且只含进人物视图的空间。"""
        ...

    def list_clusters_for_entity(self, entity_id: str) -> Sequence[FaceClusterRecord]:
        """这个人名下的全部簇（含 rejected），按 ``updated_at DESC``。

        与 ``list_clusters`` 有三处不同，三处都是 1.0 ``db.py:4580`` 的原样：按实体过滤
        （人物详情页要的是这个人的簇）、**不过滤** ``include_in_people``（详情页已经知道
        这个人是谁，再按空间挡一次只会让他的簇凭空少一半）、**不过滤** ``rejected``
        （被拒的簇是这个人历史的一部分，界面要能解释「这簇曾经属于他」）。
        """
        ...

    def list_candidates(self, scope_id: str) -> Sequence[FaceClusterRecord]:
        """在线匹配的候选：同 scope、``status IN ('pending','confirmed')``。

        与 ``list_clusters`` 有两处**故意**不同，两处都照抄 1.0 ``db.py:4867``：
        按 scope 过滤（匹配是空间内的事），且**不过滤** ``include_in_people``
        （评测空间也要聚类，只是它的簇不进人物视图）。
        """
        ...

    def get_cluster(self, cluster_id: str) -> FaceClusterRecord | None:
        """簇审阅的入口：四个动作都先按 id 找它，找不到就是 404。"""
        ...

    def get_instance(self, instance_id: str) -> FaceInstanceRecord | None:
        """实例带出它所属 asset 的路径 / 文件名 / 媒体类型与 scope。"""
        ...

    def list_instances(self, cluster_id: str) -> Sequence[FaceInstanceRecord]: ...

    def find_instance(self, cluster_id: str, instance_id: str) -> FaceInstanceRecord | None:
        """只在**这个簇里**找这张脸。拆分的合法性就是这一条：实例不属于该簇 → 404。"""
        ...

    def add_instance(
        self,
        *,
        asset_id: str,
        observation_id: str,
        cluster_id: str | None,
        bbox: Sequence[float],
        embedding: Sequence[float],
        detection_confidence: float,
        quality: float,
        pose: Sequence[float],
        pose_bucket_name: str,
        embedding_model: str,
        embedding_version: str,
        embedding_quality_signal: float,
        validity: str,
        area_ratio: float,
        sharpness: float,
    ) -> FaceInstanceRecord:
        """落一条人脸实例。``cluster_id`` 可空——那是「这张脸还算不上某个人」的表达。

        ``pose_bucket_name`` 收的是**算好的桶名**，不是算它的那个纯函数
        （``domain/identity/faces.pose_bucket``）——仓储不重算，重算就会有两份规则。
        """
        ...

    def create_cluster(
        self, embedding: Sequence[float], *, confidence: float, scope_id: str
    ) -> FaceClusterRecord:
        """新建一个簇，``entity_id`` 先留空。

        1.0 ``create_face_cluster`` 一次做三件事（建簇 → 建实体 → 回填 ``entity_id``），
        2.0 拆开：实体属于另一个聚合，在这里顺手写它会让「谁改了实体」在类型上消失。
        ``confidence`` 收的是**质量**——1.0 的形参就叫 confidence，同一条语句里传进去的
        却是 quality，两处语义一致，这里跟着叫 confidence 以免与第二个 0.30 混起来。
        """
        ...

    def link_entity(self, cluster_id: str, entity_id: str) -> FaceClusterRecord:
        """把簇挂到一个实体上（新簇建实体的那一步）。"""
        ...

    def confirm_cluster(self, cluster_id: str, entity_id: str) -> FaceClusterRecord:
        """簇转 ``confirmed`` 并回填归属，``revision + 1``。"""
        ...

    def retire_cluster(self, cluster_id: str, *, entity_id: str | None = None) -> FaceClusterRecord:
        """簇转 ``rejected``、``member_count`` 归零、``revision + 1``。

        ``entity_id`` 给了就一并改归属（同名并入一个已确认的人，``db.py:5230``），
        不给就保持原归属（用户把两个簇并成一个，``db.py:4790``）。
        1.0 那两条 SQL 的差别只有这一个字段，所以是一个方法而不是两个。
        """
        ...

    def move_instances(self, source_cluster_id: str, target_cluster_id: str) -> int:
        """把源簇的实例整体改挂到目标簇，返回移动条数（1.0 的合并只改这一列）。"""
        ...

    def move_instance(self, instance_id: str, target_cluster_id: str) -> None:
        """把一张脸移到另一个簇（拆分）。"""
        ...

    def active_cluster_for_entity(self, entity_id: str) -> FaceClusterRecord | None:
        """这个人当前**可以拿来确认**的簇：``pending`` 优先，其次 ``confirmed``，都要有成员。

        与 ``confirmed_cluster_for_entity`` 有两处**故意**不同，两处都照抄 1.0
        ``db.py:5258``：``status`` 收的是两种（先确认一个还没认过的簇，而不是只找已确认
        的），且 ``member_count > 0`` 是硬条件——空簇确认出来是一个没有任何脸的人。
        ``ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END`` 让待确认的簇排前面：
        用户按下确认时，想认的正是那个刚聚出来、还没归属的簇。
        """
        ...

    def confirmed_cluster_for_entity(self, entity_id: str) -> FaceClusterRecord | None:
        """这个人当前作为门面的已确认簇：``member_count DESC, updated_at DESC`` 取首个。

        同名合并要把实例挂到一个**已确认**的簇上，没有可挂的就不挂——1.0 允许
        ``target_cluster`` 为空，那时实例留在原地。
        """
        ...

    def list_prototypes(self, cluster_id: str) -> Sequence[FacePrototypeRecord]: ...

    def refresh_prototypes(self, cluster_id: str) -> Sequence[FacePrototypeRecord]:
        """按当前实例重算该簇的原型并回写 ``member_count``。

        先删后插：同一 ``pose_bucket`` 上留着旧行时，新行会撞 ``UNIQUE(cluster_id,
        pose_bucket)``。返回重算后的原型，省掉调用方再查一次。
        """
        ...


class EntityRepository(Protocol):
    """实体 / 修订 / 属性 / 提及。

    属性写入分两条路，这是 1.0 验证过的契约：``set_property`` 是**用户**写下当前值，
    ``maintain_property`` 是**派生**值入账。派生永远不许顶掉用户值。
    """

    def get(self, entity_id: str) -> EntityRecord | None: ...

    def list_people(
        self, *, status: str | None = None, scope_id: str | None = None
    ) -> Sequence[EntityRecord]:
        """人物列表。1.0 ``db.py:4041`` ``list_entities`` 的 person 一支。

        空间规则与 ``FaceRepository.list_clusters`` 是同一条：``include_in_people = 0``
        的空间里的实体一律不出现——评测空间聚出来的人不该出现在家庭成员列表里。
        ``status`` 为空时排除 ``rejected`` / ``superseded``（1.0 的字面量）。
        """
        ...

    def create_person(
        self,
        name: str,
        *,
        status: str = ENTITY_STATUS_PENDING,
        family_role: str | None = None,
        confidence: float = 0.0,
        summary: str = "",
        scope_id: str = DEFAULT_SCOPE_ID,
    ) -> EntityRecord:
        """建一个 person 实体并返回它。

        ``name`` 是**内部名**（新簇的实体叫「待确认人物簇 · cluster-x」），对外叫什么
        由出口决定——``pending`` 的实体在 ``list_clusters`` 里一律显示成「待命名成员」。
        1.0 的 ``create_entity`` 是通用的；2.0 只收 person 这一种，因为本切片没有别的
        调用者，写成通用签名等于给一个没人用的参数留位置。
        """
        ...

    def confirm_person(
        self,
        entity_id: str,
        *,
        name: str,
        family_role: str | None,
        summary: str,
    ) -> EntityRecord:
        """确认一个人：置名、转 ``confirmed``、写家庭角色，``confidence = MAX(…, 1)``。

        ``family_role`` 是**覆盖**不是补写（1.0 ``db.py:5163`` 的 ``family_role = ?``）：
        确认时不带角色，会把上一次的角色抹掉。这是 1.0 的行为，照搬。
        """
        ...

    def delete_candidate(self, entity_id: str) -> bool:
        """彻底删掉一个未确认的候选实体与它的派生行，返回是否删掉了。

        人脸实例**保留**（它们是证据），只是 ``cluster_id`` 归 NULL。已确认的实体抛
        ``ValueError``——删一个人要用改名或合并，不是删行。非 person 实体返回 ``False``。
        """
        ...

    def find_confirmed_person_by_name(self, scope_id: str, name: str) -> PersonNameMatch | None:
        """本 scope 内 ``entity_type='person' AND status='confirmed'`` 的人。

        **先精确名，再遍历 aliases**：反过来的话，某人的别名会抢走与之同名的另一个人。
        """
        ...

    def record_revision(
        self,
        entity_id: str,
        field_name: str,
        old_value: str | None,
        new_value: str | None,
        *,
        source: str,
        evidence_ids: Sequence[str] = (),
    ) -> EntityRevisionRecord:
        """追加一行审计。调用它一次就写一行，不做去重。"""
        ...

    def list_properties(
        self, entity_id: str, *, include_history: bool = False
    ) -> Sequence[EntityPropertyRecord]:
        """``include_history`` 为假时只返回 ``active`` / ``pending`` 的行。"""
        ...

    def maintain_property(
        self,
        entity_id: str,
        property_key: str,
        value: object,
        *,
        confidence: float = 0.0,
        evidence_ids: Sequence[str] = (),
        source: str = PROPERTY_SOURCE_DERIVED,
    ) -> EntityPropertyRecord:
        """写入一个派生值，返回**写入后当前生效**的那一行。

        当前值是用户值 → 原样返回它、不写任何行；与当前派生值相同 → 只抬置信度并合并
        证据；不同 → 新起一行 ``status='pending'``，把被取代的那行记进
        ``supersedes_property_id``。
        """
        ...

    def set_property(
        self,
        entity_id: str,
        property_key: str,
        value: object,
        *,
        evidence_ids: Sequence[str] = (),
    ) -> EntityPropertyRecord:
        """用户写下当前值：旧行转 ``superseded``、新行 ``source='user'``，并留一条修订。"""
        ...

    def person_aliases(self, entity_id: str) -> tuple[str, ...]: ...

    def set_person_aliases(self, entity_id: str, aliases: Sequence[str]) -> tuple[str, ...]:
        """整表替换别名，空串与重复项被清掉。实体不存在时返回空。"""
        ...

    def rename_person(self, entity_id: str, new_name: str) -> EntityRecord | None:
        """改一个 person 实体的本名：把旧名折进 aliases、``name_state`` 转 confirmed、
        写一条 ``source='user_rename'`` 的修订。非 person 实体返回 ``None``，空名字抛
        ``ValueError``——空名字不是「不改名」，是调用方出错。"""
        ...

    def link_confirmed_mention(
        self,
        entity_id: str,
        observation_id: str,
        *,
        face_instance_id: str | None = None,
        confidence: float = 0.0,
    ) -> EntityMentionRecord:
        """写一条提及，并把这个人追加进观察的人物快照。

        只接受 ``status='confirmed'`` 的实体，未确认的候选人物抛 ``ValueError``：
        这个快照是事件 ``person_score`` 与 ``person_bridge`` 词法投影的输入，混进一个
        尚未确认的候选，等于让聚类的一次猜测变成检索事实。重复调用幂等，返回既有行。
        """
        ...

    def confirmed_person_names(self, scope_id: str) -> frozenset[str]:
        """本 scope 内已确认人物的**本名**集合。1.0 ``is_confirmed_person_name``（``db.py:1494``）。

        这是事实写入门禁（``MaintainFacts`` 的第二段合取）的取值来源。三条判据照抄：
        ``entity_type='person'``、``status='confirmed'``、``canonical_name`` **精确相等**。

        **不查别名**，这不是遗漏：某人的别名常常是另一个人的本名（家里人互相叫小名），
        按别名放行会让模型自报的一条事实落到错的人头上，而事实表按
        ``(scope, subject, predicate)`` 唯一——写错之后连「这条是谁的」都看不出来。
        """
        ...

    def confirmed_people_for_observation(self, observation_id: str) -> frozenset[str]:
        """这条观察里**已确认**的人物 id。1.0 ``db.py:2241``。

        只认 ``entity_type='person' AND status='confirmed'``：未确认的候选人物簇还只是
        一次聚类猜测，让它进事件打分等于用猜测去归并事件。``entity_mentions`` 本身也只
        由已确认实体写得出（``link_confirmed_mention`` 的资格闸门），这一条是第二道。

        返回 ``frozenset`` 不是 ``Sequence``：调用方拿它做交集（``people & event_people``），
        顺序没有意义。
        """
        ...

    def confirmed_people_for_event(self, event_id: str) -> frozenset[str]:
        """这个事件里**已确认**的人物 id：事件名下所有观察的提及并集。1.0 ``db.py:2250``。

        与按观察查的那条差别只在 JOIN 的那一跳——事件的人物是它的成员观察带进来的，
        所以两边用的是同一份提及数据，不会出现「观察里有、事件里没有」的第三种状态。
        """
        ...

    def event_ids(self, entity_id: str) -> Sequence[str]:
        """这个人牵涉到的事件：提及桥 / 事件参与者投影 / 实体-观察关联三条来源的并集。"""
        ...

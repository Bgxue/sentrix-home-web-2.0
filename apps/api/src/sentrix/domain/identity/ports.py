"""身份域端口：人脸仓储与实体仓储。

domain 只声明形状，SQL 在 infrastructure。两个仓储分开不是因为表多，而是因为**写侧
是两条不同的链路**：``FaceRepository`` 的写入者是在线匹配与簇审阅（Task 72/73），
``EntityRepository`` 的写入者是命名与属性维护（Task 73/74）。合成一个
``IdentityRepository`` 会让「谁改了实体的名字」这件事在类型上消失。

**成员一律声明成方法，只读属性用 ``@property``。** 沿用
``domain/retrieval/ports.py`` 里 ``VisualEmbedder`` 的注解：适配器把只读属性实现成
property 时，mypy 的 ``arg-type`` 不允许一个只读 property 去满足「可写变量」的协议约定。
本文件里 ``PersonVision.model_id`` 就是按这条写的属性成员。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from sentrix.domain.identity.models import (
    DEFAULT_SCOPE_ID,
    ENTITY_STATUS_PENDING,
    PORTRAIT_TRIGGER_MANUAL,
    PROPERTY_SOURCE_DERIVED,
    AppearanceEvidenceRecord,
    EntityMentionRecord,
    EntityPropertyRecord,
    EntityRecord,
    EntityRevisionRecord,
    FaceClusterRecord,
    FaceDetection,
    FaceInstanceRecord,
    FaceManifestRecord,
    FacePrototypeRecord,
    PersonMomentRecord,
    PersonNameMatch,
    PhotoIdentityRecord,
    PortraitRevisionRecord,
    RelationshipRecord,
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

    def list_face_manifest(self, asset_id: str) -> Sequence[FaceManifestRecord]:
        """一张照片里**检测到的每一张脸**（含没绑定到人的那些），按提示词要的顺序。T123 用。

        1.0 ``agent_runtime/tools.py:2320-2358`` 的 ``_photo_face_manifest``，逐字。
        与 ``list_photo_identities`` 的关系见 ``FaceManifestRecord`` 的 docstring——
        **两条查询、两种口径，不要合并**。

        排序是 ``quality DESC, detection_confidence DESC, id``——**三键**，第三键是
        ``id``。1.0 的 ``list_photo_identities`` 只有前两键（同分时取到哪一行由 SQLite
        定），这一条有第三键，所以 ``face_id`` 的编号是**确定的**。这个差别是必须的：
        编号要进提示词、还要被模型回指，两次调用给出不同的编号会让那条回指失效。
        """
        ...

    def list_photo_identities(self, asset_id: str) -> Sequence[PhotoIdentityRecord]:
        """一张照片里**已经绑到人物实体**的那些脸（T122/T123 共用）。

        1.0 ``agent_runtime/tools.py:2277-2316`` 的 ``_confirmed_photo_identities``，
        那条 JOIN 原样：``face_instances ⋈ face_clusters ⋈ entities``，
        只留 ``entity_type = 'person'``。

        **名字与判据不一致，这是 1.0 的，照搬不修。** 那个函数叫
        ``_confirmed_photo_identities``、docstring 也写着 *confirmed*，而 SQL 里
        **一个状态过滤都没有**——两个 JOIN 都是 INNER，所以它判的是「**绑没绑**」：
        有的脸没有簇、有的簇没有实体，那些不出现；至于簇或实体是不是 ``confirmed``，
        它一次都没看过。2.0 里簇可以带 pending 实体（``enroll_face`` 就是「建簇带
        pending 实体」），所以这两个问题不是一回事。**顺手补一个
        ``status = 'confirmed'`` 是在改产品行为，不是修 bug。**

        排序也是 1.0 的：``quality DESC, detection_confidence DESC``（**没有第三键**，
        所以完全同分的两张脸取到哪一行由 SQLite 决定——照抄）。

        ``bbox`` 这一格是给 T123 的：``_inspect_photo`` 拿它去裁目标人物的定位图
        （``tools.py:2181-2192``）。T122 自己一个都不用，但两条工具共用这一条读路径，
        少带一格就会让 T123 再开一条几乎一样的查询。
        """
        ...

    def person_candidate_features(self, scope_id: str) -> Sequence[dict[str, Any]]:
        """核心人物排序的输入：这个空间里每个**未退休**的簇一行特征。

        1.0 ``db.py:6610`` 的原样。列名就是 ``domain/identity/insights.py`` 的
        ``core_person_score`` 读的那几个（``date_count`` / ``event_count`` /
        ``co_person_count`` / ``scene_count`` / ``quality`` / ``confirmed``），
        所以这个查询与那个打分函数是一对，改一边必须改另一边。

        **按簇出而不是按人出**（``person_id`` 取 ``COALESCE(c.entity_id, c.id)``）：
        还没确认的簇也要参与排序，否则一个刚聚类出来、还没被命名的人永远进不了画像
        ——而「提醒用户这里有个常在的人」正是这个功能的一半。

        返回的是**行形状的字典**，不是 Record：``rank_core_people`` /
        ``select_representatives`` 是照着 1.0 的行形状写的纯函数（见 T86d 的决策），
        在这一层再翻译一次 Record 只会多一层要同步的映射。
        """
        ...

    def representative_candidates(self, person_id: str) -> Sequence[dict[str, Any]]:
        """代表照选择的候选：这个人的簇里所有的脸。

        1.0 ``person_insights.py:262-270`` 的 SQL。``LEFT JOIN event_observations``
        是**故意的**：没有归到事件里的照片也要进来，否则一个人在「还没分段」的那批
        照片里就一张代表照都选不出来。代价是那些行的 ``event_id`` 为 ``None``，而
        ``select_representatives`` 的「每事件最多 per_event 张」会把它们全算作同一个
        （空）事件——1.0 原样，不修。
        """
        ...

    def best_instance_for_person_in_event(self, person_id: str, event_id: str) -> FaceInstanceRecord | None:
        """这个人在这个事件里质量最高的那张脸；没有就是 ``None``。

        1.0 ``app.py:2026-2028`` 的 SQL，逐字搬：质量优先、检测置信度次之。

        一个事件里同一个人可能被拍到二十次（``ObserveAppearance`` 的 docstring 写明
        为什么只取一张）。**只认经 ``entity_mentions`` 确认给了他的实例**——那与外观
        证据仓储的第三重守卫是同一条判据，这里先挡一次，好让「他不在这个事件里」与
        「模型没看清」在调用方看来是两件事。
        """
        ...

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

    def list_verified_instances(
        self, *, scope_id: str | None, minimum_quality: float
    ) -> Sequence[FaceInstanceRecord]:
        """全局重聚类要过的那批脸。1.0 ``db.py:4961-4967`` 的四个条件，一个都不能少：

        - ``embedding_json != '[]'``——没有向量的脸不参与建簇；
        - ``cluster_id IS NOT NULL``——这张脸已经属于某个簇（整表重算算的是**已归簇**
          的那批，还没归属的由在线匹配那条路管）；
        - ``quality >= minimum_quality``——质量门；
        - ``validity = 'verified'``——复核过的脸才算数。少这一条，被复核判为
          ``uncertain`` 的脸会以「可信证据」的身份参与建簇，而它们正是最容易同时像
          好几个人的那一批。

        ``scope_id`` 为空表示跨空间（1.0 的 ``scope_id=None``）。
        """
        ...

    def assign_instances(self, instance_ids: Sequence[str], cluster_id: str) -> int:
        """把一批脸整体改挂到某个簇，返回改了几条。

        与 ``move_instances``（按源簇整体搬）不同：重聚类算出来的分组**不是**按源簇切的
        ——一个源簇的成员会被分到不同组里，所以只能按实例 id 逐个改。
        """
        ...

    def set_cluster_state(self, cluster_id: str, *, status: str, member_count: int) -> None:
        """直接写簇的状态与成员数，``revision + 1``，**不动** ``entity_id``。

        重聚类保留的那个簇（``keep_id``）要按算出来的分组改状态：组里有已确认的簇
        就是 ``confirmed``，否则 ``pending``。归属不在这条路上改——重聚类不改「这簇是
        谁」，只改「哪些脸在一簇」。
        """
        ...

    def list_uncertain_unclustered(self, *, scope_id: str | None) -> Sequence[FaceInstanceRecord]:
        """认领的候选脸：``validity='uncertain'``、有向量、且 ``cluster_id IS NULL``。

        1.0 ``_attach_uncertain_faces``（``db.py:5054-5060``）的三个条件。**只认领、
        绝不新建簇**：小图 / 遮挡检出的脸单独立簇会造出一堆只有一张脸的候选，
        而它们与已确认的人高度相似时其实是同一批人。
        """
        ...

    def list_confirmed_clusters(self, scope_id: str, *, minimum_members: int) -> Sequence[FaceClusterRecord]:
        """认领的目标：同空间、已确认、成员数不低于 ``minimum_members`` 的簇。

        1.0 ``_find_attach_cluster``（``db.py:5085-5088``）的字面量是 ``>= 2``——
        只有一张脸的「已确认」簇还没有稳定的代表向量，把不确定的脸挂上去等于放大噪声。
        """
        ...

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

    def retire_unclustered_candidates(self) -> int:
        """把已经没有活着的人脸簇的候选人物转 ``rejected``，返回转了几行。

        1.0 ``_sync_face_entity_statuses``（``db.py:5118-5132``）：全局重聚类会把一些簇
        整个撤掉（成员被并走、或全被判成 noise），那些簇的候选实体就成了孤儿——
        它们还在人物列表里显示成「待命名成员」，点进去一张脸都没有。
        **已确认的人永远不动**：用户认过的人不会因为一次聚类重算而消失。转状态而不是
        删行，理由写在 ``RETIRED_CANDIDATE_SUMMARY`` 那里。

        跨空间是**有意**的：1.0 的循环没有 scope 条件，而实体与簇都在同一次重聚类里
        被改过，只看一个空间会留下一半孤儿。没有参数也就没有「传错了 scope」这种错。
        """
        ...

    def event_ids(self, entity_id: str) -> Sequence[str]:
        """这个人牵涉到的事件：提及桥 / 事件参与者投影 / 实体-观察关联三条来源的并集。"""
        ...


class PortraitRepository(Protocol):
    """画像修订的读写。SQL 在 ``infrastructure/persistence/repositories/portraits.py``。

    **写入口有一条否决权**：``create`` 在当前生效版本是 ``user_locked`` 时
    **直接返回当前版本、不写新行**（1.0 ``db.py:6380``）。这是用户在画像上唯一的
    否决权，所以它必须是写入口的判据，而不是界面上的一个禁用按钮——界面上禁用了，
    别的调用方（导入后的自动触发）照样会写进去。
    """

    def get(self, revision_id: str) -> PortraitRevisionRecord | None:
        """按 id 取一版。找不到返回 ``None``（由调用方决定这是 404 还是别的）。"""
        ...

    def get_active(self, person_id: str) -> PortraitRevisionRecord | None:
        """当前生效的那一版：``status IN ('active', 'user_locked')`` 里 ``revision`` 最大的一条。

        **``user_locked`` 也算「生效」**：锁住的是「这一版别被顶掉」，不是「这一版作废」。
        """
        ...

    def list_for_person(self, person_id: str) -> Sequence[PortraitRevisionRecord]:
        """这个人的全部修订，按 ``revision DESC``。"""
        ...

    def create(
        self,
        person_id: str,
        *,
        portrait_text: str,
        prompt_version: str,
        themes: Sequence[dict[str, object]] = (),
        evidence_refs: Sequence[dict[str, object]] = (),
        trigger_type: str = PORTRAIT_TRIGGER_MANUAL,
        model_name: str = "unknown",
    ) -> PortraitRevisionRecord:
        """落一版新画像，并把上一版转 ``superseded``。

        ``revision`` 是上一版 +1；没有上一版时从 1 开始。
        **当前版本 ``user_locked`` 时不写新行，原样返回它。**
        """
        ...

    def set_feedback(self, revision_id: str, feedback: str | None) -> PortraitRevisionRecord | None:
        """记用户对某一版的反馈（不新建版本）。"""
        ...

    def set_lock(self, revision_id: str, locked: bool) -> PortraitRevisionRecord | None:
        """锁 / 解锁一版。锁上转 ``user_locked``、解开转回 ``active``（1.0 ``db.py:6431``）。"""
        ...


class MomentRepository(Protocol):
    """人物瞬间的读写。SQL 在 ``infrastructure/persistence/repositories/moments.py``。

    **跨空间是一条硬不变量**：一条瞬间引用的六个对象（人 / 簇 / 事件 / 观察 /
    资产 / 人脸实例）必须同属一个记忆空间，否则 ``upsert`` 抛 ``ValueError``
    （1.0 ``db.py:6176``）。一处瞬间牵扯两个空间意味着上游的归属算错了——
    这种错不会自己暴露，只会让画像把两个空间的人混成一个。
    """

    def upsert(
        self,
        *,
        person_id: str,
        observation_id: str,
        prompt_version: str,
        cluster_id: str,
        event_id: str,
        asset_id: str,
        face_instance_id: str,
        action_text: str = "",
        interaction_target_ids: Sequence[str] = (),
        interaction_text: str = "",
        participation_style: str = "",
        visible_affect: str = "",
        social_role_cues: Sequence[str] = (),
        narrative_note: str = "",
        selection: Mapping[str, object] | None = None,
        confidence: float = 0.0,
        model_name: str = "unknown",
    ) -> PersonMomentRecord:
        """落一条瞬间。同一 ``(person_id, observation_id, prompt_version)`` 再写就是更新。

        更新**不含** ``scope_id`` / ``person_id`` / ``observation_id`` /
        ``prompt_version``——那四个是 upsert 的键，改了就不是同一条了。
        """
        ...

    def list_for(
        self,
        *,
        person_id: str | None = None,
        scope_id: str | None = None,
        status: str = "active",
    ) -> Sequence[PersonMomentRecord]:
        """按条件列瞬间，``created_at DESC``。``status=None`` 表示不按状态筛。"""
        ...


class RelationshipRepository(Protocol):
    """关系（家庭图谱的边）的读写。SQL 在 ``repositories/relationships.py``。

    **只写 ``relationships`` 行。** 1.0 的 ``create_relationship`` 在
    ``status == 'active'`` 时还会调 ``maintain_relationship_claim`` 往事实/声明层写；
    2.0 把那一半留给 application 编排（``infrastructure-purity`` 契约不允许这一层
    直接调用例）。**且不回滚**：claim 维护失败时这一行留着——「关系已确认但事实层
    没有对应声明」是可能出现且被接受的中间状态。
    """

    def list_person_relationships(self, scope_id: str | None = None) -> Sequence[RelationshipRecord]:
        """家庭图谱的边：**两端都是 person 实体**、且 ``status != 'retracted'``。

        两端都是 person 这条过滤在 SQL 里（1.0 ``db.py:5603``）：实体表里还有物品、
        氛围等类型，只按 id 取会把它们一起捞进来。
        """
        ...

    def list_for_entity(self, entity_id: str, scope_id: str | None = None) -> Sequence[RelationshipRecord]:
        """这个人牵涉到的关系，**两个方向都算**（它可能是 subject 也可能是 object）。"""
        ...

    def create(
        self,
        *,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str,
        evidence_ids: Sequence[str] = (),
        confidence: float = 0.5,
        status: str = "pending",
    ) -> RelationshipRecord:
        """建一条关系，或把已有的那条并掉。

        同一 ``(scope, subject, predicate, object)`` 已有 ``active`` / ``pending``
        的，**不新建**：证据并集、置信度取 ``MAX``、``revision + 1``。
        两端不在同一个记忆空间时抛 ``ValueError``。
        """
        ...

    def confirm(self, relationship_id: str) -> RelationshipRecord | None:
        """确认：把**同 (subject, predicate, object) 的其它 active 关系转 superseded**，
        再把这一条转 active。同一对人在同一谓词上只能有一条生效的。
        """
        ...

    def retract(self, relationship_id: str) -> RelationshipRecord | None:
        """撤回：转 ``retracted`` 并 ``revision + 1``。**不删行**——撤回也要留痕。"""
        ...


class PersonCropRenderer(Protocol):
    """按人脸框裁出「头 + 上半身」并落成临时 JPEG。实现在
    ``infrastructure/media/person_vision.py``。

    **存在的理由就是分层契约。** ``application-purity`` 禁止 application import
    infrastructure，而裁剪要 PIL——几何本身是纯的（``domain/identity/appearance.py``
    的 ``expanded_person_crop`` 收的是一个只有 width/height/crop 的 Protocol），
    但「打开哪个文件、写哪个临时文件」是 IO，只能在 infrastructure。同
    ``PillowMetadataReader`` / ``PillowPreviewEncoder``：那两个类的名字里带 Pillow
    不是啰嗦，是这条契约的凭证。

    返回 ``(临时图路径, 源坐标)``。**临时文件由调用方删**——它就活在这一次模型调用里
    （``person_vision`` 的 docstring 写明：裁剪件从不作为 Asset 或长期文件保存）。

    ``ValueError`` 照常抛（框退化 / 图太小），由调用方决定这算不算一次失败：
    在 ``ObserveAppearance`` 里算——那张照片跳过，整批继续。
    """

    def render(self, asset_path: str | Path, bbox: Sequence[float] | None) -> tuple[str, list[int]]: ...


class PersonPreviewRenderer(Protocol):
    """在一张原图上画出人脸框与 ``P1`` / ``P2`` 标签，返回
    ``(临时图路径, 标签 → 身份)``。实现在 ``infrastructure/media/person_vision.py``。

    与 ``PersonCropRenderer`` 同一条理由（``application-purity`` 不许 application
    import infrastructure，而画框要 PIL），但两件事不一样：那个裁**一个人**给外观证据
    用，这个画**一屋子人**给瞬间抽取用——模型要靠标签才能把 ``interaction_labels`` 里
    的 ``P2`` 指回具体某个人。所以后者返回的那张表是**归属的唯一依据**，不是附带信息。

    没有框的人脸按 1.0 的做法把标签画在左上角依次排下来，**而不是丢掉这一项**——
    丢了他，模型会看到一个跳号的 ``P1``、``P3``，然后开始猜 ``P2`` 去哪了。

    **临时文件由调用方删。** ``ValueError`` / ``OSError`` 照常抛，由调用方决定这算不算
    一次失败（``ExtractMoments`` 里算：那张照片跳过，整批继续）。
    """

    def render(
        self, asset_path: str | Path, faces: Sequence[Mapping[str, Any]]
    ) -> tuple[str, dict[str, dict[str, Any]]]: ...


class PersonVision(Protocol):
    """人物视觉的三个模型入口：画像 / 瞬间 / 外观。实现在 ``infrastructure/media/person_vision_model.py``。

    三条方法各喂一条链路，**都不在导入的前置路径上**：模型不可用、读不了图、返回的不是
    JSON——一律返回 ``None``。1.0 把这三条写在同一个客户端上
    （``backend/model_clients.py:1291`` / ``:1316`` / ``:1332``），2.0 收成端口，是为了让
    用例拿到的是一句「谁会调模型」，而不是一个能问端点要模型名的具体类。

    三者的输入输出形状各按 1.0：

    - ``write_person_portrait`` 收 ``portrait.compile_portrait_evidence`` 的证据包，
      返回 ``normalize_writer_output`` 的形状（``portrait_text`` + ``themes``）——校验与
      兜底不在这一步，仍由 ``portrait.py`` 那两个纯函数决定；
    - ``analyze_person_moments`` 收带编号预览图（``person_vision.render_numbered_preview``
      的产物）与本次真实画出的标签表，返回 ``{"moments": [...]}``，其中每条已经过了一遍
      ``normalize_person_moments`` 的词表；
    - ``analyze_person_appearance`` 收 ``appearance.expanded_person_crop`` 的产物，返回
      ``{"clothing": [...], "confidence": float}``。

    ``model_id`` 是**这次会请求的模型名**，供落库时记 ``model_name``
    （``MomentRepository.upsert``）。1.0 把 ``self.model`` 塞在
    ``analyze_person_appearance`` 的返回值里（``:1350``），2.0 提到端口上——与
    ``VlmObserver.model_id`` 同一条口径。
    """

    @property
    def model_id(self) -> str: ...

    def write_person_portrait(self, pack: Mapping[str, object]) -> dict[str, object] | None: ...

    def analyze_person_moments(
        self,
        path: str,
        labels: Sequence[str],
        context: Mapping[str, object] | None = None,
    ) -> dict[str, object] | None: ...

    def analyze_person_appearance(
        self,
        path: str,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, object] | None: ...


class AppearanceRepository(Protocol):
    """外观证据的读写。SQL 在 ``infrastructure/persistence/repositories/appearance.py``。

    **写入口有三重守卫**（1.0 ``db.py:4485-4497``）：人存在且已确认、人脸实例存在、
    以及**这个实例真的经 ``entity_mentions`` 确认给了这个人**。第三重挡的是
    「把 A 的衣服记到 B 头上」——那正是这个功能最要紧的一种错。

    唯一键是 ``(person_id, face_instance_id, model_name)``：换模型是**另一条**证据，
    不覆盖旧模型的结论。
    """

    def record(
        self,
        *,
        person_id: str,
        face_instance_id: str,
        crop_bbox: Sequence[float],
        clothing: Sequence[str],
        confidence: float,
        model_name: str,
    ) -> AppearanceEvidenceRecord:
        """记一条，或覆盖同一 ``(人, 实例, 模型)`` 的那条。

        ``observation_id`` / ``asset_id`` **从人脸实例带出来**，不由调用方传——
        传进来的可能与实例对不上，而那种不一致事后从证据行上看不出来。
        """
        ...

    def get(self, evidence_id: str) -> AppearanceEvidenceRecord | None: ...

    def list_for(
        self, person_id: str, *, include_empty: bool = False, limit: int = 500
    ) -> Sequence[AppearanceEvidenceRecord]:
        """这个人的外观证据，置信度降序。

        ``include_empty=False`` 会滤掉 ``clothing`` 为空表的那些——**空表不是
        「没穿衣服」，是「模型没能可靠归属给目标人物」**。
        """
        ...

"""身份域的领域模型：人脸簇 / 实例 / 原型，实体 / 修订 / 属性 / 提及。

纯数据，零 IO。字段语义取自 1.0 ``backend/db.py`` 的建表语句（对应关系写在迁移
``0006_identity.sql`` 的注释里），去掉了 1.0 在展示层现拼的那些派生字段。

**为什么一律带 ``Record`` 后缀。** ``domain/identity/faces.py`` 里已经有 ``FaceCluster``
与 ``FacePrototype``——那是聚类算法**内存里**的可变结构（桶内就地替换、按限额截断）。
这里的是落库的一行。同名会让人分不清「我手里这个改了会不会写库」，所以落库侧统一带
``Record``，与 ``domain/memories/events.py`` 的 ``EventRecord`` 同一个约定。

唯一的例外是 ``FaceDetection``：它是检测适配器交给用例的**输入形状**，不落库，
所以按同一条约定它不带 ``Record`` 后缀。

**``pending`` 的名字与角色为什么在模型层就被抹掉。** 1.0 在 HTTP 路由里改写
``canonical_name``（``app.py:1976``），于是「待命名成员」这个名字只对走那条路由的人成立；
直接读仓储的内部调用方拿到的仍是「待确认人物簇 · cluster-3」。2.0 把这条规则放在记录
生成处：只要是``pending`` 的实体，``canonical_name`` 一律是 ``PENDING_CLUSTER_NAME``、
``family_role`` 一律是 ``None``。规则落在数据的出口上，不落在某个端点上。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

# ---- 取值常量 --------------------------------------------------------------------
#
# 这些串在 1.0 是散落的字面量。它们在 SQL 里出现（``status = 'confirmed'``），也在
# Python 里出现（``entity.get("status") == "confirmed"``），两边漂开一次就是一条静默的
# 空结果——所以收在这里一份。

ENTITY_TYPE_PERSON = "person"

# 没指定记忆空间时的落点。1.0 把这个字面量写了十几处（``db.py:4816`` 等），
# 2.0 收成一份：它出现在 SQL 的 ``DEFAULT``、仓储的兜底与用例的入参默认值里，
# 三处漂开一次，人脸就会落进一个查询永远查不到的空间。
DEFAULT_SCOPE_ID = "home-default"

ENTITY_STATUS_PENDING = "pending"
ENTITY_STATUS_CONFIRMED = "confirmed"
ENTITY_STATUS_REJECTED = "rejected"

CLUSTER_STATUS_PENDING = "pending"
CLUSTER_STATUS_CONFIRMED = "confirmed"
CLUSTER_STATUS_REJECTED = "rejected"

# 人脸检测的复核结论。四个取值散在检测适配器、在线匹配的资格闸门、重聚类的认领条件
# 三处，收成一份：它们同时是「落库的列」与「判据的字面量」，漂开一次就是一条静默的空集。
FACE_VALIDITY_VERIFIED = "verified"
FACE_VALIDITY_UNCERTAIN = "uncertain"

PROPERTY_SOURCE_USER = "user"
PROPERTY_SOURCE_DERIVED = "derived"

PROPERTY_STATUS_ACTIVE = "active"
PROPERTY_STATUS_PENDING = "pending"

REVISION_SOURCE_USER = "user"
REVISION_SOURCE_USER_RENAME = "user_rename"

# 未命名人物簇对外显示的名字。1.0 ``app.py:1976`` 的字面量，一字不差。
PENDING_CLUSTER_NAME = "待命名成员"

# 未命名人物簇**落库**时的内部名与说明（1.0 ``db.py:4639`` 的 ``create_face_cluster``）。
# 与上面那个不是一回事：``PENDING_CLUSTER_NAME`` 是给用户看的，这两个是存在
# ``entities`` 行里的原文，用户永远看不到它们（``_row_to_cluster`` 会换掉）。
# 一字不差是要求：换一次措辞，老库里的行与新行就再也对不上了。
PENDING_CLUSTER_NAME_PREFIX = "待确认人物簇"
PENDING_CLUSTER_SUMMARY = "由 buffalo_l embedding 生成，等待用户确认"

# 全局重聚类之后，已经没有活着的人脸簇的候选人物会被转成 rejected——**删掉的是状态，
# 不是行**：这个人在确认之前的所有痕迹（簇、实例、提及、修订）都要留在库里可查。
# 1.0 ``db.py:5129`` 的原文，一字不差。
RETIRED_CANDIDATE_SUMMARY = "该人物候选已因全局重聚类失效，保留历史审计记录"


def pending_cluster_name(cluster_id: str) -> str:
    """新簇的实体内部名。1.0 是 ``f"待确认人物簇 · {cluster_id}"``，分隔符是两个字符。

    写成函数而不是让两处各拼一次 f-string：它是**落库的数据**，不是日志文案，
    拼法漂开一次，库里就同时存在两种命名。
    """
    return f"{PENDING_CLUSTER_NAME_PREFIX} · {cluster_id}"


@dataclass(frozen=True, slots=True)
class FaceClusterRecord:
    """``face_clusters`` 一行 + 它关联实体的三个展示字段。

    ``canonical_name`` / ``family_role`` / ``entity_status`` 来自 ``LEFT JOIN entities``：
    簇自己不存名字，名字属于实体。``entity_id`` 可空（簇建出来到实体建出来之间有窗口），
    所以这三个字段在左连接落空时是 ``None``。
    """

    id: str
    scope_id: str
    status: str
    entity_id: str | None
    representative_embedding: tuple[float, ...]
    member_count: int
    confidence: float
    revision: int
    created_at: datetime | None
    updated_at: datetime | None
    canonical_name: str | None = None
    family_role: str | None = None
    entity_status: str | None = None


@dataclass(frozen=True, slots=True)
class PhotoIdentityRecord:
    """一张照片里一张**已绑到人物实体**的脸。T122 与 T123 共用（见 ``FaceRepository``
    的 ``list_photo_identities``）。

    与 ``FaceInstanceRecord`` 是两件事，不要合并：那个是「一个实例的全部字段 + 它所属
    asset 的四个字段」，这个是「这条 JOIN 命中的那一行」——它多带实体侧的
    ``entity_id`` / ``person_name`` / ``family_role``，少带嵌入与姿态那一堆
    （那张照片要对模型说话，不需要向量）。

    ``identity_status`` 是 1.0 写死的字面量 ``"bound"``（``tools.py:2297``），**不是**
    从某个状态列读来的：能出现在这条记录里，就已经意味着「绑了」。
    """

    face_instance_id: str
    asset_id: str
    cluster_id: str
    entity_id: str
    person_name: str
    family_role: str
    bbox: tuple[float, ...]
    detection_confidence: float
    quality: float
    # ``entity_mentions`` 上那条同 (实例, 实体) 的置信度。1.0 的 LEFT JOIN 带出来的
    # （``tools.py:2287-2289``），**没有命中就是 None**——不补 0.0：0.0 是「算过一次、
    # 结果是零」，None 是「没有这条提及」，两者的下游含义不同。
    mention_confidence: float | None = None


@dataclass(frozen=True, slots=True)
class FaceInstanceRecord:
    """``face_instances`` 一行 + 它所属 asset 的四个字段。

    ``asset_path`` / ``file_name`` / ``mime_type`` / ``media_type`` 是 1.0
    ``get_face_instance`` 的 JOIN 带出来的（``db.py:4467``）：裁剪端点只拿得到
    instance_id，路径与媒体类型必须由这一行给全，否则调用方得自己去拼 asset 查询。
    ``scope_id`` 同理——人脸实例自己没有 scope 列，它由 asset 决定。
    """

    id: str
    asset_id: str
    observation_id: str
    cluster_id: str | None
    bbox: tuple[float, ...]
    embedding: tuple[float, ...]
    detection_confidence: float
    quality: float
    pose: tuple[float, ...]
    pose_bucket: str
    embedding_model: str
    embedding_version: str
    embedding_quality_signal: float
    validity: str
    area_ratio: float
    sharpness: float
    created_at: datetime | None
    scope_id: str = "home-default"
    asset_path: str = ""
    file_name: str = ""
    mime_type: str = ""
    media_type: str = ""


@dataclass(frozen=True, slots=True)
class FacePrototypeRecord:
    """``face_prototypes`` 一行：某个姿态桶里质量最高的那张脸。

    ``UNIQUE(cluster_id, pose_bucket)`` 是这条语义在 SQL 层的凭证，所以这里的字段是
    「桶 → 原型」的展开，不是「桶 → 原型列表」。
    """

    id: str
    cluster_id: str
    face_instance_id: str
    pose_bucket: str
    embedding: tuple[float, ...]
    quality: float
    model_name: str
    model_version: str


@dataclass(frozen=True, slots=True)
class EntityRecord:
    """``entities`` 一行。

    ``identity_state`` / ``role_state`` / ``name_state`` 三列在 1.0 是运行期 ALTER 补的
    （``db.py:1240``），默认值 ``clustered`` / ``unknown`` / ``anonymous`` 是簇审阅状态机
    的初态——它们决定界面把这个人显示成「待命名」还是「已命名」，不是可选装饰。
    """

    id: str
    scope_id: str
    entity_type: str
    canonical_name: str
    status: str
    family_role: str | None
    summary: str
    confidence: float
    identity_state: str
    role_state: str
    name_state: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class EntityRevisionRecord:
    """``entity_revisions`` 一行：一次身份变更的审计。

    每次变更写一行、只追加不改写。``old_value`` / ``new_value`` 是**文本**不是结构化值
    ——1.0 存的就是 ``json_value(...)`` 之后的串，属性修订的旧值因此形如 ``"[\"老妈\"]"``。
    照抄这个表示，历史行才与 1.0 的库可比。
    """

    id: str
    entity_id: str
    field_name: str
    old_value: str | None
    new_value: str | None
    source: str
    evidence_ids: tuple[str, ...]
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class EntityPropertyRecord:
    """``entity_properties`` 一行。

    ``supersedes_property_id`` 与 ``revision`` 一起表达「这是第几次改口」，``status``
    表达「这次改口确认了没有」：``active`` 是当前生效的，``pending`` 是派生出的新值
    等人确认，``superseded`` 是被换掉的旧值。
    """

    id: str
    entity_id: str
    property_key: str
    value: object
    source: str
    confidence: float
    status: str
    evidence_ids: tuple[str, ...]
    supersedes_property_id: str | None
    revision: int
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class PersonNameMatch:
    """``find_confirmed_person_by_name`` 的答案。

    ``via_alias`` 不是调试信息：同名合并（Task 73）要按它决定要不要把「别名命中」
    这件事告诉用户——按别名并入一个人和按本名并入，用户看到的是两种不同的提示。
    """

    entity_id: str
    via_alias: bool


@dataclass(frozen=True, slots=True)
class EntityMentionRecord:
    """``entity_mentions`` 一行：某个实体在某条观察里被认了出来。

    ``face_instance_id`` 可空且**能重复**——SQLite 的 UNIQUE 允许多个 NULL，
    「这条观察里妈妈被提到两次但没有对应人脸」因此不会被互相顶掉。
    """

    id: str
    entity_id: str
    observation_id: str
    face_instance_id: str | None
    confidence: float
    created_at: datetime | None


# ---- 检测结果的输入形状 ----------------------------------------------------------
#
# ``FaceDetection`` 不是落库的一行，所以它**不带** ``Record`` 后缀（这正是那条约定的
# 用处：一眼看出手里这个改了不会写库）。它放在 domain 而不是 application，是因为
# 造它的是 infrastructure 的检测适配器（Task 77/78），而 infrastructure 不许 import
# application——这一层依赖方向决定了这个类型的家住哪儿。


@dataclass(frozen=True, slots=True)
class FaceDetection:
    """检测器 + 嵌入器交给在线匹配的一条人脸。1.0 里它是个无类型 dict。

    ``quality`` / ``pose_bucket`` 可空是**有语义的**：为空表示「检测器没算」，
    由匹配用例按纯规则补算；给了就原样采信，不再重算。两条路径在 1.0
    ``db.py:4831-4845`` 分叉，所以这里保留同样的空值语义。

    ``identity_eligible`` 无默认值（``None`` = 没给）而不是 ``True``：1.0 的默认值
    是 ``identity_ready``，而 ``identity_ready`` 自己缺省为真。写成 ``True`` 会让
    「显式说了不合格」与「没说」看起来一样。
    """

    embedding: tuple[float, ...] = ()
    bbox: tuple[float, ...] = ()
    confidence: float = 0.0
    quality: float | None = None
    pose: tuple[float, ...] = ()
    pose_bucket: str = ""
    area_ratio: float = 0.0
    sharpness: float = 0.0
    quality_signal: float = 0.0
    embedding_model: str = ""
    embedding_version: str = ""
    # 1.0 ``db.py:4847``：``str(face.get("face_validity") or "verified")``——缺省是合格。
    validity: str = FACE_VALIDITY_VERIFIED
    identity_ready: bool = True
    identity_eligible: bool | None = None


# ---- S5c：外观证据 / 人物瞬间 / 关系 / 画像 ----------------------------------------
#
# 这四张表的建表语句都在迁移 0006 里（外观证据与关系随 S5a 建，人物瞬间与画像修订随
# 裁决②解冻后建）。它们的**写入者**在 S5c——所以模型放在这里、写者放在本切片。

# 画像修订的来源。裁决②把「谁触发的」收成一个受控词表：``pipeline`` 是导入后自动跑
# 那一条，``manual`` 是显式请求，``repair`` 是校验不过之后的二次生成。
# 1.0 这里是裸字面量（``person_insights.py:250`` 的 ``"pipeline"``），2.0 收一份：
# 它同时是落库的值与判据，漂开一次就查不出「哪些画像是自动生成的」。
PORTRAIT_TRIGGER_PIPELINE = "pipeline"
PORTRAIT_TRIGGER_MANUAL = "manual"

# 画像校验的**来源**（裁决：缺陷 2 的修法）。
#
# 模型的输出与确定性兜底是两种东西：前者的长度是模型自由发挥的，80–500 字是对它的
# **约束**；后者的长度由模板唯一决定，把它也按 80–500 判，等于要求模板恰好落在那个
# 区间里——1.0 的兜底模板实测 41 字，于是「兜底」永远通不过自己的校验，那条降级链
# 的最后一格是假的。所以长度这条只对模型输出成立，由这个取值区分。
PORTRAIT_SOURCE_MODEL = "model"
PORTRAIT_SOURCE_DETERMINISTIC = "deterministic"

PORTRAIT_STATUS_ACTIVE = "active"
PORTRAIT_STATUS_USER_LOCKED = "user_locked"
PORTRAIT_STATUS_SUPERSEDED = "superseded"

MOMENT_STATUS_ACTIVE = "active"

RELATIONSHIP_STATUS_PENDING = "pending"
RELATIONSHIP_STATUS_CONFIRMED = "confirmed"
# 1.0 ``db.py:5604`` 的 ``r.status != 'retracted'``：撤回过的关系不是「不存在」，
# 是「存在过但已撤回」——它留在库里供审计，只是不进人物画像的证据包。
RELATIONSHIP_STATUS_RETRACTED = "retracted"


@dataclass(frozen=True, slots=True)
class PersonMomentRecord:
    """``person_moments`` 一行：这个人在这一条观察里做了什么。

    ``interaction_target_ids`` 是**已解析成 person_id** 的互动对象（1.0 在
    ``person_moments.py:167-172`` 把模型给的 P 标签翻成人，翻不出来的直接丢掉）——
    落库的因此不是标签，是 id。``status`` 只有 ``active`` 一种在 2.0 被写出来，
    ``list_person_moments`` 默认就按它筛。
    """

    id: str
    scope_id: str
    person_id: str
    cluster_id: str
    event_id: str
    observation_id: str
    asset_id: str
    face_instance_id: str
    action_text: str
    interaction_target_ids: tuple[str, ...]
    interaction_text: str
    participation_style: str
    visible_affect: str
    social_role_cues: tuple[str, ...]
    narrative_note: str
    confidence: float
    model_name: str
    prompt_version: str
    status: str
    created_at: datetime | None
    updated_at: datetime | None
    # ``select`` 阶段写在选项上的那包元数据（``insights.select_representatives`` 的
    # ``selection_json``）：版本 / 质量 / 体态可见度 / 去重阈值 / 事件覆盖数。
    # **1.0 里它从来没落过库**——那个阶段算的是 ``selection_json``，而写入端读的是
    # ``selection``，键名对不上（见 ``s5-deviations.md`` 第 135 条）。T86f 把它接上了。
    # 声明成 ``Mapping`` 而不是 ``dict``：读侧只该读它，改它没有意义。
    # **必须排在最后**：它有默认值，而 dataclass 不允许默认值后面再跟无默认值的字段。
    selection: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RelationshipRecord:
    """``relationships`` 一行 + 两端实体的名字。

    ``subject_name`` / ``object_name`` 来自 ``JOIN entities``：证据包要的是**人话的
    关系**（「张三 — 母亲 — 李四」），只给 id 的包对模型没有信息量。两端都是 person
    实体这条过滤在 SQL 里（1.0 ``db.py:5603``），由 ``list_person_relationships`` 负责。
    """

    id: str
    scope_id: str
    subject_entity_id: str
    predicate: str
    object_entity_id: str
    inverse_predicate: str | None
    status: str
    confidence: float
    evidence_ids: tuple[str, ...]
    revision: int
    created_at: datetime | None
    updated_at: datetime | None
    subject_name: str = ""
    object_name: str = ""


@dataclass(frozen=True, slots=True)
class PortraitRevisionRecord:
    """``person_portrait_revisions`` 一行：第 ``revision`` 版画像。

    ``status`` 的三态与 1.0 相同：``active`` 是当前生效的，``superseded`` 是被下一版
    顶掉的，``user_locked`` 是用户锁住的——锁住之后 ``create_portrait_revision``
    **直接返回当前版本、不写新行**（1.0 ``db.py:6380``），这是用户在画像上唯一的
    否决权，所以它必须是一条写在写入口的判据，而不是界面上的一个禁用按钮。
    """

    id: str
    scope_id: str
    person_id: str
    revision: int
    portrait_text: str
    themes: tuple[dict[str, object], ...]
    evidence_refs: tuple[dict[str, object], ...]
    trigger_type: str
    model_name: str
    prompt_version: str
    status: str
    user_feedback: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class AppearanceEvidenceRecord:
    """``person_appearance_evidence`` 一行：这个人这段裁剪里的衣物描述。

    唯一键是 ``(person_id, face_instance_id, model_name)``——换一个模型就是另一条
    证据，不覆盖旧模型的结论（否则换模型重跑一次就把可比性抹掉了）。
    """

    id: str
    person_id: str
    face_instance_id: str
    observation_id: str
    asset_id: str
    crop_bbox: tuple[float, ...]
    clothing: tuple[str, ...]
    confidence: float
    model_name: str
    status: str
    created_at: datetime | None
    updated_at: datetime | None

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

from dataclasses import dataclass
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
    validity: str = "verified"
    identity_ready: bool = True
    identity_eligible: bool | None = None

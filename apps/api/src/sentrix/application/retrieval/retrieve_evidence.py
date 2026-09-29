"""多路召回编排：把已启用的通道按加权 RRF 融成一份排名。

对应 1.0 ``backend/evidence_retrieval.py:152-243`` 的
``EvidenceRetrievalKernel._retrieve_multi`` 的**召回 + 融合**两段。1.0 那条链有四步：

    多通道召回 → rank(weighted_rrf) → 种子门控的 adjacency 扩展 → 条件证据判定

S4 只搬前两步。后两步要种子分级与 ``EvidencePacket``，属于 S6。

**接线逐条照搬 1.0：**

- 通道集合来自 ``build_default_retrievers``（``backend/retrieval/__init__.py:65-85``），
  构建顺序 ``metadata → entity → lexical → visual_ann → text_ann → adjacency``；
- 召回循环**跳过 ``kind == "expander"`` 的通道**（``evidence_retrieval.py:198-201``），
  只有 primary 通道进 ``channel_hits``；
- 融合只有一次调用，权重表用 ``DEFAULT_CHANNEL_WEIGHTS``：``evidence_retrieval.py:241-243``
  → ``retrieval/ranking.py:101-110``（``weighted_rrf`` 分支）→ ``fusion.fuse``；
- **缺通道的形态是「不出现」**，不是空列表也不是错误。1.0 里没启用的通道压根不在
  ``retrievers`` 列表里，``channel_hits`` 里就没有那个键。2.0 的 entity 到 S5b 才有类
  （Task 83），text_ann 要等 S6——后者在这里连调用都没有，不是「调了然后返回空」。

**adjacency 不接。** 1.0 ``configs/retrieval/defaults.json:10`` 里 ``"adjacency": false``，
它连实例都不会被建；即使建了，``kind="expander"`` 也让它不进并行召回——Kernel 拿
primary 融合结果里的 exact/strong 当种子再 ``expand``（``evidence_retrieval.py:246-273``），
扩展结果 append 在主结果之后。S4 没有种子分级（那是 S6 的条件判定），接进来只会多一条
恒空的通道。

**通道顺序是语义，不是排版。** ``fusion.fuse`` 在候选**第一次出现**时按当时那个通道定
``evidence_class``（``domain/retrieval/fusion.py:76-84``），而 metadata 是 anchor 通道：
一个同时被 metadata 与 lexical 命中的候选，只有 metadata 先进才拿得到 +1.0 的锚点加成。
1.0 的构建顺序就是 metadata 在前。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sentrix.application.retrieval.retrieve_entity import RetrieveEntity
from sentrix.application.retrieval.retrieve_lexical import RetrieveLexical
from sentrix.application.retrieval.retrieve_metadata import RetrieveMetadata
from sentrix.application.retrieval.retrieve_text_ann import (
    RetrieveTextAnn,
    TextSearchRequest,
    TextSearchResult,
)
from sentrix.application.retrieval.retrieve_visual import (
    RetrieveVisual,
    VisualSearchRequest,
    VisualSearchResult,
)
from sentrix.domain.media.models import MediaType
from sentrix.domain.retrieval.fusion import DEFAULT_CHANNEL_WEIGHTS, fuse
from sentrix.domain.retrieval.models import CandidateHit

METADATA_CHANNEL = "metadata"
ENTITY_CHANNEL = "entity"
LEXICAL_CHANNEL = "lexical"
VISUAL_CHANNEL = "visual_ann"
TEXT_CHANNEL = "text_ann"

# 每条通道按调用方上限过取一层再融合。1.0 把理由写在了注释里
# （``evidence_retrieval.py:171-176``）："Ask each enabled channel for the complete
# authorized scope so a relevant asset cannot disappear merely because another channel
# ranked it below an arbitrary head."——它的做法是把每条通道的 ``recall_limit`` 抬到
# ``max(requested_limit, authorized_count)``，即整个已授权 scope（``:176-181``）。
# 2.0 不整册回灌（一次查询把全 scope 拉进内存），改用与视觉 ANN 同一个过取倍数
# （``retrieve_visual.py`` 的 ``OVERSAMPLE_FACTOR = 4``）：**融合前不裁到交付上限**，
# 融合之后才按 limit 交付。
RECALL_OVERSAMPLE_FACTOR = 4


@dataclass(frozen=True, slots=True)
class EvidenceRequest:
    """一次多路召回的输入。

    结构化条件（时间窗 / 媒体类型 / 地点）对应 1.0 ``HardFilterContext``
    （``backend/retrieval/base.py:23-67``）的正向条件那几格。S4 的 HTTP 端点还传不了
    它们（硬条件与查询解析属于 S6），但 metadata 通道**只在有正向条件时才给召回信号**
    （``retrieve_metadata.py`` 的早退），所以这几个字段必须存在，否则那条通道在类型上
    就没法被喂过——它会永远返回空，而不是「被接上了」。
    """

    text: str
    # None = 跨全部相册（1.0 `db.py:1514` 的 `if scope_id:`）。原先默认 `"home-default"`，
    # 那是一个**具体的空间**——不传 scope 时去查它，而它在 2.0 的空库里不存在。
    scope_id: str | None = None
    limit: int = 20
    media_types: tuple[MediaType, ...] = ()
    negated_media: tuple[MediaType, ...] = ()
    time_bounds: tuple[datetime, datetime] | None = None
    place: str = ""
    # 人物条件（1.0 ``RetrievalQuery.constraints`` 里 ``dimension == "person"`` 的那几条）。
    # 与上面三个正向条件同一个处境：S5 的 HTTP 端点还传不了它（把「妈妈的照片」解析成
    # 人物条件属于 S6 的查询解析），但 **entity 通道只在有人物条件时才给召回信号**——
    # 这个字段不存在的话，那条通道在类型上就没法被喂过，它会永远返回空，而不是「被接上了」。
    person: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceHit:
    """一条融合后的候选。``channels`` 是「被哪些通道以第几名召回」。"""

    asset_id: str
    score: float
    channels: Mapping[str, int] = field(default_factory=dict)
    raw_scores: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvidenceResult:
    """融合结果 + 视觉通道报出来的后端健康度。

    ``backend`` / ``degraded`` 取自视觉通道：它是唯一一个「派生索引可能落后于权威
    存储」的通道（``retrieve_visual.py`` 的模块注释）。缺口必须一路传到 HTTP 响应，
    不能只有日志里那一行——1.0 就是这么把降级丢掉的。
    """

    hits: Sequence[EvidenceHit] = field(default_factory=tuple)
    backend: str = "none"
    degraded: bool = False
    degraded_reason: str = ""
    authoritative_vectors: int = 0


def _visual_candidates(result: VisualSearchResult) -> tuple[CandidateHit, ...]:
    """还原成 1.0 ``VisualAnnRetriever.retrieve`` 交给 Kernel 的那张命中表。

    ``RetrieveVisual`` 是 S2 的单通道用例，内部已经 ``fuse`` 过一次；但融合只消费
    ``rank``，而名次原样记在 ``channels["visual_ann"]``、原始分数记在 ``raw_scores`` 里，
    所以这里是无损还原——分数由编排层按名次重算，不沿用这里带回来的那个。
    """
    return tuple(
        CandidateHit(
            asset_id=hit.asset_id,
            channel=VISUAL_CHANNEL,
            rank=hit.channels[VISUAL_CHANNEL],
            raw_score=hit.raw_scores.get(VISUAL_CHANNEL, 0.0),
        )
        for hit in result.hits
        if VISUAL_CHANNEL in hit.channels
    )


def _text_candidates(result: TextSearchResult) -> tuple[CandidateHit, ...]:
    """还原成 1.0 ``TextAnnRetriever.retrieve`` 交给 Kernel 的那张命中表。

    与 ``_visual_candidates`` 同形：``RetrieveTextAnn`` 内部已经 ``fuse`` 过一次，
    但融合只消费 ``rank``，而名次记在 ``channels["text_ann"]``、原始分数记在
    ``raw_scores`` 里，所以这里是无损还原——分数由编排层按名次重算。
    """
    return tuple(
        CandidateHit(
            asset_id=hit.asset_id,
            channel=TEXT_CHANNEL,
            rank=hit.channels[TEXT_CHANNEL],
            raw_score=hit.raw_scores.get(TEXT_CHANNEL, 0.0),
        )
        for hit in result.hits
        if TEXT_CHANNEL in hit.channels
    )


class RetrieveEvidence:
    """四条 primary 通道（metadata / entity / lexical / visual_ann）→ 一次加权 RRF。"""

    def __init__(
        self,
        *,
        visual: RetrieveVisual,
        lexical: RetrieveLexical,
        metadata: RetrieveMetadata,
        entity: RetrieveEntity,
        text: RetrieveTextAnn,
    ) -> None:
        self._visual = visual
        self._lexical = lexical
        self._metadata = metadata
        self._entity = entity
        self._text = text

    def execute(self, request: EvidenceRequest) -> EvidenceResult:
        text = request.text.strip()
        if not text:
            return EvidenceResult()
        limit = max(1, request.limit)
        recall_limit = limit * RECALL_OVERSAMPLE_FACTOR

        visual = self._visual.execute(
            VisualSearchRequest(text=text, scope_id=request.scope_id, limit=recall_limit)
        )
        text_result = self._text.execute(
            TextSearchRequest(text=text, scope_id=request.scope_id, limit=recall_limit)
        )
        # 这个 dict 的插入顺序必须与 1.0 的构建顺序一致：metadata 最先，锚点加成才会
        # 落在同时被 metadata 与语义通道命中的候选上。
        channel_hits: dict[str, Sequence[CandidateHit]] = {
            METADATA_CHANNEL: self._metadata.execute(
                scope_id=request.scope_id,
                time_bounds=request.time_bounds,
                media_types=request.media_types,
                negated_media=request.negated_media,
                place=request.place,
            ),
            # entity 排在 metadata 之后、lexical 之前——1.0 ``build_default_retrievers``
            # 的构建顺序就是 metadata → entity → lexical → visual_ann。anchor 加成按
            # **候选第一次出现时**的通道给，顺序换了，同时被 entity 与 lexical 命中的
            # 候选就拿不到那 +1.0。
            ENTITY_CHANNEL: self._entity.execute(
                people=request.person, scope_id=request.scope_id, limit=recall_limit
            ),
            LEXICAL_CHANNEL: self._lexical.execute(
                whole_query=text, scope_id=request.scope_id, limit=recall_limit
            ),
            VISUAL_CHANNEL: _visual_candidates(visual),
            # text_ann 排在最后——1.0 ``build_default_retrievers`` 的构建顺序是
            # metadata → entity → lexical → visual_ann → text_ann。anchor 加成按
            # **候选第一次出现时**的通道给，顺序换了拿到的加成就不一样。
            TEXT_CHANNEL: _text_candidates(text_result),
        }
        fused = fuse(channel_hits, channel_weights=DEFAULT_CHANNEL_WEIGHTS)

        return EvidenceResult(
            hits=tuple(
                EvidenceHit(
                    asset_id=candidate.asset_id,
                    score=candidate.score,
                    channels=dict(candidate.channels),
                    raw_scores=dict(candidate.raw_scores),
                )
                for candidate in fused[:limit]
            ),
            backend=visual.backend,
            degraded=visual.degraded,
            degraded_reason=visual.degraded_reason,
            authoritative_vectors=visual.authoritative_vectors,
        )

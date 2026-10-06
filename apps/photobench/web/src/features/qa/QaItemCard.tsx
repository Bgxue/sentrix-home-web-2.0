/**
 * 单题卡（列表项 + 展开体）。移植 1.0 `App.vue:3460-3674`。
 *
 * 展开体顺序照搬 1.0：元数据标签 → 本题证据链 → 多轮对话卡 → 正文两栏
 * （模型回答 / 工具召回图 / 模型使用图 / 参考答案 / 检索 GT / Judge 说明）→
 * `qa-performance`（时延 + Agent Loop 树 + 执行结果）→ Agent2 面板 → 调试轨迹 →
 * JUDGE 输入触发 → 人工复核。
 *
 * **两个媒体区是两件事**（1.0 `:703-711` 的注释原文）：
 *   工具召回图片 `toolRecallMedia`（检索候选） / 模型使用图片 `itemEvidenceMedia`（显式交付，
 *   未交付即空、**绝不回退到候选**）。两者都直接 import `lib/evidence.ts`，渲染也分开。
 */
import type { MediaLike } from "../../api/media.ts";
import type { RejudgeTask, RunItemDetail, RunItemSummary } from "../../api/types.ts";
import {
  itemMedia,
  itemParseRate,
  itemRetrievalMetrics,
  conversationTurns,
  itemEvidenceMedia,
  toolRecallMedia,
} from "../../lib/evidence.ts";
import {
  completionLabel,
  esc,
  evidenceScoreLabel,
  fmtMs,
  fmtPct,
  judgeRoundState,
  judgeScoreLabel,
  scoreClass,
  taskDecisionLabel,
} from "../../lib/format.ts";
import { isVideoMedia, imageUrl, pauseVideoAtEvidenceFrame } from "../../api/media.ts";
import { itemLayerChain } from "../../lib/graphs.ts";
import { itemTimingBreakdown, qaLatencySegments } from "../../lib/trace.ts";

import { Agent2SummaryPanel } from "./Agent2SummaryPanel.tsx";
import { AgentLoopTree } from "./AgentLoopTree.tsx";
import { ConversationCard } from "./ConversationCard.tsx";
import { DebugTracePanel } from "./DebugTracePanel.tsx";
import { ExecutionStatusPanel } from "./ExecutionStatusPanel.tsx";
import { hasToolTrace, judgeReason, segmentStyle } from "./qaShared.ts";
import type { QaSummaryView, ReviewDraft } from "./qaShared.ts";

type Dict = Record<string, unknown>;

function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

/** 1.0 `media.file_name || media.media_id || media.image_id`。 */
function mediaLabel(media: MediaLike): string {
  return esc(media.file_name) || esc(media.media_id) || esc(media.image_id);
}

/** 1.0 `:3529-3537` 共用的图片瓦片（含视频证据帧）。 */
function MediaTile({
  media,
  onOpenImage,
  unmatched = false,
}: {
  media: MediaLike;
  onOpenImage: (media: MediaLike) => void;
  unmatched?: boolean;
}) {
  const src = imageUrl(media);
  return (
    <div className="image-tile">
      {isVideoMedia(media) && src ? (
        <video
          src={src}
          controls
          playsInline
          preload="metadata"
          onLoadedMetadata={(event) => pauseVideoAtEvidenceFrame(media, { target: event.currentTarget })}
        ></video>
      ) : src ? (
        <img src={src} alt={esc(media.file_name)} loading="lazy" onClick={() => onOpenImage(media)} />
      ) : (
        <span className="image-empty">无媒体</span>
      )}
      <span className="image-label">
        {mediaLabel(media)}
        {unmatched ? <em> · 未召回</em> : null}
      </span>
    </div>
  );
}

export interface QaItemCardProps {
  summary: RunItemSummary;
  detail: RunItemDetail | undefined;
  loading: boolean;
  open: boolean;
  albumId: string;
  modelLabel: string;
  activeRejudge: RejudgeTask | null;
  onToggle: () => void;
  onOpenImage: (media: MediaLike) => void;
  onOpenJudgeInput: (detail: RunItemDetail) => void;
  review?: ReviewDraft;
  onReviewChange?: (patch: ReviewDraft) => void;
}

export function QaItemCard({
  summary,
  detail,
  loading,
  open,
  albumId,
  modelLabel,
  activeRejudge,
  onToggle,
  onOpenImage,
  onOpenJudgeInput,
  review,
  onReviewChange,
}: QaItemCardProps) {
  const s = summary as QaSummaryView;
  return (
    <article className={`qa-item${open ? " open" : ""}`}>
      <button className="item-head qa-toggle" type="button" onClick={onToggle}>
        <span className="item-idx">{String(s.index + 1).padStart(2, "0")}</span>
        <strong>{s.question}</strong>
        <span className={`score ${scoreClass(s.judge?.score)} judge-round-${judgeRoundState(s, activeRejudge)}`}>
          {judgeScoreLabel(s, activeRejudge)}
        </span>
        {Number(s.ground_truth_count) > 0 && (
          <span className="muted small">
            精确率 {fmtPct(s.retrieval_precision)} · 召回率 {fmtPct(s.retrieval_recall)} · F1{" "}
            {fmtPct(s.retrieval_f1)} · 命中 {esc(s.matched_count)}/{esc(s.ground_truth_count)}
          </span>
        )}
        {s.evidence_judge?.score != null && (
          <span className={`score ${scoreClass(s.evidence_judge.score)}`}>证据 {esc(s.evidence_judge.score)}</span>
        )}
        <span className="muted small">
          模型 {esc(s.model_call_count)} · 工具 {esc(s.tool_call_count)}
        </span>
        <span className="qa-chevron" aria-hidden="true">
          ⌄
        </span>
      </button>
      {open && (
        <div className="qa-expanded">
          {loading ? (
            <div className="qa-results-empty">正在加载该题完整记录…</div>
          ) : detail ? (
            <QaItemDetail
              detail={detail}
              albumId={albumId}
              modelLabel={modelLabel}
              onOpenImage={onOpenImage}
              onOpenJudgeInput={onOpenJudgeInput}
              review={review}
              onReviewChange={onReviewChange}
            />
          ) : null}
        </div>
      )}
    </article>
  );
}

interface QaItemDetailProps {
  detail: RunItemDetail;
  albumId: string;
  modelLabel: string;
  onOpenImage: (media: MediaLike) => void;
  onOpenJudgeInput: (detail: RunItemDetail) => void;
  review?: ReviewDraft;
  onReviewChange?: (patch: ReviewDraft) => void;
}

function QaItemDetail({
  detail,
  albumId,
  modelLabel,
  onOpenImage,
  onOpenJudgeInput,
  review,
  onReviewChange,
}: QaItemDetailProps) {
  const turns = conversationTurns(detail);
  const chain = itemLayerChain(detail, albumId);
  const taskJudge = dict(detail.task_judge);
  const taskJudges = Array.isArray(detail.task_judges) ? detail.task_judges : [];
  // 两个媒体区：候选 vs 显式交付，分开取、分开渲染。
  const recallMedia = toolRecallMedia(detail, albumId);
  const evidenceMedia = itemEvidenceMedia(detail, albumId);
  const gtMedia = itemMedia(detail, albumId, true);
  const timing = itemTimingBreakdown(detail);
  const qaSegments = qaLatencySegments(detail);
  const toolTrace = hasToolTrace(detail);
  const qualityReason = judgeReason(detail.judge);
  const behaviorReason = judgeReason(detail.task_judge);
  const evidenceReason = judgeReason(detail.evidence_judge);

  return (
    <>
      <div className="qa-meta-tags">
        {detail.task_type ? <span>{detail.task_type}</span> : null}
        {detail.question_type ? <span>{detail.question_type}</span> : null}
        {detail.angle ? <span>{detail.angle}</span> : null}
        {detail.difficulty ? <span>{detail.difficulty}</span> : null}
        {detail.answerability ? <span>{detail.answerability}</span> : null}
        {(detail.tags ?? []).map((tag) => (
          <span key={tag} className="qa-run-tag">
            {tag}
          </span>
        ))}
      </div>
      <section className="qa-layer-chain">
        <div className="qa-layer-chain-head">
          <strong>本题证据链</strong>
          <span>关键帧若存在，显示为 L0 媒体；不会直接标成 L2 事件</span>
        </div>
        <div className="qa-layer-chain-row">
          {chain.map((node, nodeIndex) => (
            <div key={node.key} className="qa-layer-node-wrap">
              <div className={`qa-layer-node qa-layer-node-${node.status}`}>
                <b>{node.key}</b>
                <strong>{node.name}</strong>
                <span>{node.detail}</span>
                <em>{node.evidence}</em>
              </div>
              {nodeIndex < chain.length - 1 && (
                <i className="qa-layer-arrow" aria-hidden="true">
                  →
                </i>
              )}
            </div>
          ))}
        </div>
      </section>
      <ConversationCard detail={detail} />
      <div className="item-body">
        <div>
          <h4>{turns.length > 1 ? "最终一轮回答" : "模型回答"}</h4>
          <p>{esc(detail.final_answer) || esc(detail.answer) || esc(detail.error) || "未完成"}</p>
          <div className="capability-grid">
            <span>
              <small>任务判断</small>
              <b>{taskDecisionLabel(detail)}</b>
            </span>
            <span>
              <small>任务判断结果</small>
              <b>{taskJudge.correct === true ? "一致" : taskJudge.correct === false ? "不一致" : "未记录"}</b>
            </span>
            {taskJudges.length > 1 && (
              <span>
                <small>多轮评分口径</small>
                <b>每轮独立评分，并携带截至该轮的完整对话</b>
              </span>
            )}
            <span>
              <small>证据对应</small>
              <b>{evidenceScoreLabel(detail.evidence_judge)}</b>
            </span>
            <span>
              <small>媒体检索</small>
              <b>{itemRetrievalMetrics(detail)}</b>
            </span>
            <span>
              <small>JSON 解析</small>
              <b>{itemParseRate(detail)}</b>
            </span>
            <span>
              <small>步数内完成</small>
              <b>{completionLabel(detail)}</b>
            </span>
          </div>
          <h4>工具召回图片（{recallMedia.length}）</h4>
          <div className="image-grid">
            {recallMedia.map((media) => (
              <MediaTile
                key={esc(media.asset_id) || esc(media.file_name)}
                media={media}
                onOpenImage={onOpenImage}
              />
            ))}
            {recallMedia.length === 0 && <span className="muted small">检索未返回可识别媒体</span>}
          </div>
          <h4>模型使用图片（{evidenceMedia.length}）</h4>
          <div className="image-grid">
            {evidenceMedia.slice(0, 3).map((media) => (
              <MediaTile
                key={`used-${esc(media.asset_id) || esc(media.file_name)}`}
                media={media}
                onOpenImage={onOpenImage}
              />
            ))}
            {evidenceMedia.length === 0 && (
              <span className="muted small">回答依据图片为空（模型未选择交付图）</span>
            )}
          </div>
          {evidenceMedia.length > 3 && (
            <details className="qa-detail-block">
              <summary>查看更多使用图片（{evidenceMedia.length - 3}）</summary>
              <div className="image-grid">
                {evidenceMedia.slice(3).map((media) => (
                  <MediaTile
                    key={`used-more-${esc(media.asset_id) || esc(media.file_name)}`}
                    media={media}
                    onOpenImage={onOpenImage}
                  />
                ))}
              </div>
            </details>
          )}
        </div>
        <div>
          <h4>正确答案</h4>
          <p>{esc(detail.reference_answer)}</p>
          <h4>检索 GT 媒体（{gtMedia.length}）</h4>
          <div className="image-grid">
            {gtMedia.map((media) => (
              <MediaTile
                key={esc(media.asset_id) || `${esc(media.media_type)}-${esc(media.media_id)}`}
                media={media}
                unmatched={media.matched === false}
                onOpenImage={onOpenImage}
              />
            ))}
          </div>
          {qualityReason && (
            <>
              <h4>回答质量评分说明</h4>
              <p className="muted">{qualityReason}</p>
            </>
          )}
          {behaviorReason && (
            <>
              <h4>任务判断说明</h4>
              <p className="muted">{behaviorReason}</p>
            </>
          )}
          {evidenceReason && (
            <>
              <h4>媒体证据评分说明</h4>
              <p className="muted">{evidenceReason}</p>
            </>
          )}
        </div>
      </div>
      <section className="qa-performance">
        <div className="timing-breakdown">
          <span>
            端到端 <b>{fmtMs(timing.wall_clock_ms)}</b>
          </span>
          <span>
            Agent 总耗时 <b>{fmtMs(timing.agent_wall_ms)}</b>
          </span>
          <span>
            模型 <b>{fmtMs(timing.model_ms)}</b>
          </span>
          <span>
            工具 <b>{toolTrace ? fmtMs(timing.tool_ms) : "未记录"}</b>
          </span>
          <span>
            Judge <b>{fmtMs(timing.judge_ms)}</b>
          </span>
          <span>
            其他 <b>{toolTrace ? fmtMs(timing.other_ms) : "未记录"}</b>
          </span>
        </div>
        {qaSegments.length > 0 && (
          <div className="latency-composition qa-latency-composition">
            <div className="latency-composition-head">
              <strong>QA 样本时延组成</strong>
              <span>{fmtMs(timing.wall_clock_ms)}</span>
            </div>
            <div className="latency-bar" role="img" aria-label="QA 样本时延组成">
              {qaSegments.map((segment, segmentIndex) => (
                <span
                  key={`${segment.kind}-${segmentIndex}`}
                  className={`latency-segment latency-segment-${segment.kind}`}
                  style={segmentStyle(segment, qaSegments)}
                  data-tooltip={`${segment.label} · ${fmtMs(segment.ms)}`}
                >
                  <b>{fmtMs(segment.ms)}</b>
                </span>
              ))}
            </div>
            <div className="latency-segment-legend">
              {qaSegments.map((segment, segmentIndex) => (
                <span key={`${segment.kind}-legend-${segmentIndex}`}>
                  <i className={`latency-dot latency-dot-${segment.kind}`}></i>
                  {segment.shortLabel} {fmtMs(segment.ms)}
                </span>
              ))}
            </div>
          </div>
        )}
        <AgentLoopTree detail={detail} modelLabel={modelLabel} />
        <ExecutionStatusPanel detail={detail} />
      </section>
      <Agent2SummaryPanel detail={detail} />
      <DebugTracePanel detail={detail} />
      <div className="item-footer">
        <button className="judge-details-trigger" type="button" onClick={() => onOpenJudgeInput(detail)}>
          JUDGE 模型输入 <span>↗</span>
        </button>
      </div>
      <div className="review-editor">
        <label>
          人工复核
          <select
            value={review?.verdict ?? ""}
            onChange={(event) => onReviewChange?.({ ...review, verdict: event.target.value })}
          >
            <option value="">未复核</option>
            <option value="correct">正确</option>
            <option value="partial">部分正确</option>
            <option value="wrong">错误</option>
          </select>
        </label>
        <input
          value={review?.note ?? ""}
          placeholder="复核备注（可选）"
          onChange={(event) => onReviewChange?.({ ...review, note: event.target.value })}
        />
      </div>
    </>
  );
}

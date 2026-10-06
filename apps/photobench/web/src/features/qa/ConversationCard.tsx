/**
 * 多轮对话卡。移植 1.0 `App.vue:3492-3515`（`conversationTurns(item).length > 1` 才挂）。
 * 轮内字段（`final_answer` / `task_judge` / `judge` / `evidence_judge` / `turn_outcome`）
 * 在 1.0 是裸读，`ConversationTurn` 未逐字段收窄，故按 `Dict` 取值。
 */
import type { RunItemDetail } from "../../api/types.ts";
import {
  conversationContextLabel,
  conversationIdLabel,
  conversationTurns,
  turnScore,
} from "../../lib/evidence.ts";
import { actionLabel, esc, evidenceScoreLabel } from "../../lib/format.ts";

import { judgeReason } from "./qaShared.ts";

type Dict = Record<string, unknown>;

function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

export interface ConversationCardProps {
  detail: RunItemDetail;
}

export function ConversationCard({ detail }: ConversationCardProps) {
  const turns = conversationTurns(detail);
  if (turns.length <= 1) return null;

  const sharedContext = detail.conversation_context_mode === "shared_conversation_id";
  return (
    <section className="result-conversation-card">
      <header className="result-conversation-head">
        <div>
          <small>MULTI-TURN CONVERSATION</small>
          <strong>同一个多轮对话样本</strong>
          <span>{turns.length} 轮按顺序执行，后续轮次复用同一会话上下文</span>
        </div>
        <div className="conversation-identity">
          <b>{conversationIdLabel(detail)}</b>
          <span>{sharedContext ? "共享 conversation_id" : "历史结果按已保存轮序展示"}</span>
        </div>
      </header>
      <div className="result-conversation-flow">
        {turns.map((turn, turnIndex) => {
          const t = turn as Dict;
          const taskJudge = dict(t.task_judge);
          const correct = taskJudge.correct;
          const qualityReason = judgeReason(t.judge);
          const behaviorReason = judgeReason(t.task_judge);
          const evidenceReason = judgeReason(t.evidence_judge);
          return (
            <article
              key={t.index == null ? String(turnIndex) : String(t.index)}
              className="result-conversation-turn"
            >
              <div className="result-turn-marker">
                <b>{turnIndex + 1}</b>
                <span>{conversationContextLabel(turn, turnIndex)}</span>
              </div>
              <div className="result-message result-user-message">
                <small>用户 · 第 {turnIndex + 1} 轮</small>
                <p>{esc(t.message)}</p>
              </div>
              <div className="result-message result-assistant-message">
                <small>模型回答</small>
                <p>{esc(t.final_answer) || esc(t.answer) || "未完成"}</p>
              </div>
              <div className="result-turn-scores">
                <span>
                  任务行为{" "}
                  <b>
                    期望{actionLabel(t.expected_action)} / 实际{actionLabel(taskJudge.actual_action)}
                  </b>
                  <em className={correct === true ? "pass" : correct === false ? "fail" : ""}>
                    {correct === true ? "一致" : correct === false ? "不一致" : "未记录"}
                  </em>
                </span>
                <span>
                  回答质量 <b>{turnScore(dict(t.judge).score)}</b>
                </span>
                <span>
                  媒体证据 <b>{evidenceScoreLabel(t.evidence_judge)}</b>
                </span>
                <span>
                  轮次结果 <b>{esc(t.turn_outcome) || esc(t.termination_reason) || "未记录"}</b>
                </span>
              </div>
              {(qualityReason || behaviorReason || evidenceReason) && (
                <div className="result-turn-reasons">
                  {qualityReason ? (
                    <p>
                      <b>质量：</b>
                      {qualityReason}
                    </p>
                  ) : null}
                  {behaviorReason ? (
                    <p>
                      <b>行为：</b>
                      {behaviorReason}
                    </p>
                  ) : null}
                  {evidenceReason ? (
                    <p>
                      <b>证据：</b>
                      {evidenceReason}
                    </p>
                  ) : null}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

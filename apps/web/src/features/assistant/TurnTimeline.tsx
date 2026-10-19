import { useState } from "react";

import {
  fetchTurnEvents,
  type AssistantProgressEvent,
} from "../../shared/api/client";

/**
 * 一轮回答的「证据 / 推理时间线」。
 *
 * 1.0 把同一组 `public_progress` 事件渲染成一行行卡片（`src/app.js:359-527`）。
 * 2.0 的进度事件字段完全一致，这里只改呈现：按**事件自己声明的** `stage` / `kind`
 * 分组，每组一张可折叠卡片。没有新字段——卡片里出现的每个键都来自事件的
 * `payload`（`run_turn.py` 的 `_tool_event` / `plan` / `guard` / `terminal`）。
 *
 * 历史回合的过程事件不在会话详情里，只在 `GET /assistant/turns/{id}/events`
 * 这条 SSE 流里。所以时间线默认收起，用户展开且手上没有事件时才去读一次。
 */

type CardKind =
  | "planning"
  | "thinking"
  | "tool"
  | "observation"
  | "inspecting"
  | "recovering"
  | "final";

interface TimelineCard {
  key: string;
  kind: CardKind;
  toolCallId: string;
  events: AssistantProgressEvent[];
}

const CARD_LABELS: Record<CardKind, string> = {
  planning: "规划",
  thinking: "思考",
  tool: "工具调用",
  observation: "观察",
  inspecting: "精读",
  recovering: "纠正",
  final: "收尾",
};

/** 后端的阶段名（`domain/assistant/models.py` 的 `ProgressStage`）→ 卡片类别。 */
const STAGE_KIND: Record<string, CardKind> = {
  thinking: "thinking",
  planning: "planning",
  action: "tool",
  tool_result: "observation",
  inspecting: "inspecting",
  recovering: "recovering",
  finalizing: "final",
  terminal: "final",
};

function payloadOf(event: AssistantProgressEvent): Record<string, unknown> {
  return event.payload ?? {};
}

function stringOf(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * 一次工具调用的两条事件（`action` 发参数、`tool_result` 发结果）用
 * `payload.tool_call_id` 对上，合成同一张卡片。对不上的 `tool_result` 单独成卡。
 */
export function buildCards(events: AssistantProgressEvent[]): TimelineCard[] {
  const cards: TimelineCard[] = [];
  const openTools = new Map<string, TimelineCard>();

  for (const event of events) {
    const payload = payloadOf(event);
    const toolCallId = stringOf(payload.tool_call_id);

    if (event.stage === "action") {
      const card: TimelineCard = {
        key: `tool-${event.step_index}`,
        kind: "tool",
        toolCallId,
        events: [event],
      };
      cards.push(card);
      if (toolCallId !== "") openTools.set(toolCallId, card);
      continue;
    }

    if (event.stage === "tool_result") {
      const open = toolCallId !== "" ? openTools.get(toolCallId) : undefined;
      if (open !== undefined) {
        open.events.push(event);
        openTools.delete(toolCallId);
        continue;
      }
      // 终止动作（`final_answer`）只发一条 tool_result，没有前置的 action。
      const kind: CardKind = payload.tool === "final_answer" ? "final" : "observation";
      cards.push({
        key: `${event.stage}-${event.step_index}`,
        kind,
        toolCallId,
        events: [event],
      });
      continue;
    }

    // 连续的「思考」「纠正」合成一张卡；其余阶段各自成卡。
    const kind = STAGE_KIND[event.stage] ?? "thinking";
    const previous = cards[cards.length - 1];
    if (
      previous !== undefined &&
      previous.kind === kind &&
      (kind === "thinking" || kind === "recovering")
    ) {
      previous.events.push(event);
      continue;
    }
    cards.push({
      key: `${event.stage}-${event.step_index}`,
      kind,
      toolCallId,
      events: [event],
    });
  }

  return cards;
}

/** `tool_result` 的 `payload.result` 是工具的 observation，只挑已有字段做摘要。 */
export function summarizeResult(value: unknown): string[] {
  if (typeof value !== "object" || value === null) return [];
  const data = value as Record<string, unknown>;
  const parts: string[] = [];

  if (typeof data.result_set_id === "string" && data.result_set_id !== "") {
    parts.push(`结果集 ${data.result_set_id}`);
  }
  if (typeof data.total === "number") parts.push(`共 ${data.total} 张`);
  if (typeof data.has_more === "boolean") parts.push(data.has_more ? "还有更多" : "已到底");
  if (Array.isArray(data.asset_ids)) parts.push(`候选 ${data.asset_ids.length} 个`);
  if (typeof data.delivered === "number") parts.push(`交付 ${data.delivered} 张`);
  if (typeof data.found === "boolean") parts.push(data.found ? "已找到" : "未找到");
  if (typeof data.backend === "string" && data.backend !== "") {
    parts.push(`检索后端 ${data.backend}`);
  }
  if (data.degraded === true) parts.push("检索降级");
  if (typeof data.full_text === "string" && data.full_text !== "") {
    parts.push(`读到 ${data.full_text.length} 字`);
  }
  if (typeof data.observation === "string" && data.observation !== "") {
    parts.push(data.observation);
  }
  if (parts.length === 0) {
    const keys = Object.keys(data);
    if (keys.length > 0) parts.push(`返回字段：${keys.join("、")}`);
  }
  return parts;
}

function RequirementList({ requirements }: { requirements: unknown }) {
  if (!Array.isArray(requirements)) return null;
  const rows = requirements.filter(
    (item): item is Record<string, unknown> => typeof item === "object" && item !== null,
  );
  if (rows.length === 0) return null;
  return (
    <ul className="phase-requirements">
      {rows.map((item, index) => (
        <li key={stringOf(item.id) || String(index)}>
          <span>{stringOf(item.description) || stringOf(item.evidence_type) || "待确认的依据"}</span>
          <small>{stringOf(item.status)}</small>
          {stringOf(item.unmet_reason) !== "" ? <em>{stringOf(item.unmet_reason)}</em> : null}
        </li>
      ))}
    </ul>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  let text = "";
  try {
    text = JSON.stringify(value, null, 2);
  } catch {
    text = "";
  }
  if (text === "" || text === "{}") return null;
  return <pre className="phase-json">{text}</pre>;
}

function PhaseEvent({ event }: { event: AssistantProgressEvent }) {
  const payload = payloadOf(event);
  const result = payload.result;
  const summary = summarizeResult(result);

  return (
    <div className={`phase-event phase-status-${event.status}`}>
      <p>{event.text}</p>
      {event.kind === "plan" ? <RequirementList requirements={payload.requirements} /> : null}
      {event.kind === "model_action" || event.kind === "tool_result" || event.kind === "tool_error" ? (
        <>
          {payload.arguments !== undefined ? (
            <details className="phase-detail">
              <summary>参数</summary>
              <JsonBlock value={payload.arguments} />
            </details>
          ) : null}
          {summary.length > 0 ? (
            <ul className="phase-result">
              {summary.map((part) => (
                <li key={part}>{part}</li>
              ))}
            </ul>
          ) : null}
          {typeof payload.duration_ms === "number" ? (
            <small className="phase-duration">耗时 {payload.duration_ms} ms</small>
          ) : null}
        </>
      ) : null}
      {event.kind === "guard" || event.kind === "judge" ? (
        <div className="phase-guard">
          {stringOf(payload.code) !== "" ? <span>规则 {stringOf(payload.code)}</span> : null}
          {payload.hard === true ? <strong>硬性拦截</strong> : null}
          {stringOf(payload.reason) !== "" ? <span>{stringOf(payload.reason)}</span> : null}
          {stringOf(payload.evidence) !== "" ? <span>{stringOf(payload.evidence)}</span> : null}
        </div>
      ) : null}
      {event.kind === "terminal" ? (
        <p className="phase-terminal">
          {stringOf(payload.status)}
          {stringOf(payload.termination_reason) !== ""
            ? ` · ${stringOf(payload.termination_reason)}`
            : ""}
        </p>
      ) : null}
    </div>
  );
}

/**
 * 一张卡 = 一条 1.0 的 `timeline-card`：**垂直时间轴上的一个节点**，不是折叠分组。
 *
 * 1.0 `app.js:399-421` 的形状是 `<article class="timeline-card {running|blocked|complete}">`
 * + `<span class="timeline-dot">` + 标题/正文/artifact，**始终展开**——用户扫一眼就知道
 * 助手刚才做了什么，不需要再点一次。2.0 之前做成 `<details>` 折叠分组，形态与 1.0 不同，
 * 而且默认收起时整条推理链是「空白」的。
 *
 * 状态着色沿用 1.0 的三态：running（进行中）/ blocked（被拦、报错、取消）/ complete。
 */
const BLOCKED_STATUSES = new Set(["blocked", "denied", "error", "cancelled", "partial"]);

function PhaseCard({ card }: { card: TimelineCard }) {
  const last = card.events[card.events.length - 1];
  const running = card.events.some((event) => event.status === "running");
  const blocked = card.events.some((event) => BLOCKED_STATUSES.has(event.status));
  const state = running ? "running" : blocked ? "blocked" : "complete";
  return (
    <article className={`timeline-card ${state}`}>
      <span className="timeline-dot" aria-hidden="true" />
      <div>
        <strong>{CARD_LABELS[card.kind]}</strong>
        {last.text ? <p>{last.text}</p> : null}
        {card.events.map((event) => (
          <PhaseEvent key={event.step_index} event={event} />
        ))}
      </div>
    </article>
  );
}

/** `pending` / `running` 之外都是终态——只有终态才值得/才能回放事件。 */
export function isTerminalStatus(status: string): boolean {
  return status !== "" && status !== "pending" && status !== "running";
}

export interface TurnTimelineProps {
  events: AssistantProgressEvent[];
  /** 历史回合的 id；有它且手上没有事件时，展开会去读那一条 SSE。 */
  turnId?: string;
  status?: string;
  /** 测试注入点；生产走 `client.fetchTurnEvents`。 */
  loadEvents?: (turnId: string) => Promise<AssistantProgressEvent[]>;
  defaultOpen?: boolean;
  onLoaded?: (events: AssistantProgressEvent[]) => void;
}

export function TurnTimeline({
  events,
  turnId = "",
  status = "",
  loadEvents = fetchTurnEvents,
  defaultOpen = false,
  onLoaded,
}: TurnTimelineProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [loaded, setLoaded] = useState<AssistantProgressEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const available = events.length > 0 ? events : (loaded ?? []);
  const cards = buildCards(available);
  const canLoad = events.length === 0 && turnId !== "" && isTerminalStatus(status);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || !canLoad || loading) return;
    setLoading(true);
    setLoadError(null);
    try {
      const fetched = await loadEvents(turnId);
      setLoaded(fetched);
      onLoaded?.(fetched);
    } catch (cause) {
      setLoadError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="turn-timeline">
      <button
        type="button"
        className="turn-timeline-toggle"
        aria-expanded={open}
        onClick={() => void toggle()}
      >
        记忆推理{available.length > 0 ? ` · ${available.length} 个节点` : ""}
      </button>
      {open ? (
        <div className="turn-timeline-body">
          {loading ? <p className="turn-timeline-note">正在读取回合记录…</p> : null}
          {loadError !== null ? (
            <p className="error">读取回合记录失败：{loadError}</p>
          ) : null}
          {/* `.timeline-list` 才是那条竖线的容器（1.0 `:628` 的 `::before`）；
              卡片自己只负责圆点与底色。 */}
          <div className="timeline-list">
            {cards.map((card) => (
              <PhaseCard key={card.key} card={card} />
            ))}
          </div>
          {!loading && loadError === null && cards.length === 0 ? (
            <p className="turn-timeline-note">没有可展示的过程记录。</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

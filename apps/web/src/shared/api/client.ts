const BASE = "/api/v1";

export interface AssetSummary {
  id: string;
  file_name: string;
  media_type: string;
  mime_type: string | null;
  size_bytes: number;
  status: string;
  captured_at: string | null;
  gps: { latitude: number; longitude: number } | null;
}

interface AssetListResponse {
  items: AssetSummary[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Sentrix API ${response.status}: ${body}`);
  }
  return (await response.json()) as T;
}

export async function listAssets(scopeId: string): Promise<AssetSummary[]> {
  const body = await request<AssetListResponse>(`/assets?scope_id=${encodeURIComponent(scopeId)}`);
  return body.items;
}

export function assetFileUrl(assetId: string): string {
  return `${BASE}/assets/${assetId}/file`;
}

export function assetPreviewUrl(assetId: string): string {
  return `${BASE}/assets/${assetId}/preview`;
}

/** 浏览器能直接渲染的格式走原图，其余（HEIC 等）走服务端转码预览。 */
export function assetDisplayUrl(asset: AssetSummary): string {
  if (asset.mime_type === "image/heic" || asset.mime_type === "image/heif") {
    return assetPreviewUrl(asset.id);
  }
  return assetFileUrl(asset.id);
}

/** 后端已经按 mime 决定过怎么交付；助手交付的照片只带回这些字段，走同一条判断。 */
export function deliveredPhotoUrl(assetId: string, fileName: string): string {
  const lowered = fileName.toLowerCase();
  if (lowered.endsWith(".heic") || lowered.endsWith(".heif")) {
    return assetPreviewUrl(assetId);
  }
  return assetFileUrl(assetId);
}

export interface SearchHit {
  asset: AssetSummary;
  score: number;
  channels: Record<string, number>;
  raw_scores: Record<string, number>;
}

export interface SearchResponse {
  query: string;
  backend: string;
  degraded: boolean;
  degraded_reason: string;
  authoritative_vectors: number;
  items: SearchHit[];
}

/** 按内容词检索。后端负责嵌入、召回与融合，前端不做任何相关性计算。 */
export async function searchAssets(
  query: string,
  scopeId: string,
  limit = 20,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query, scope_id: scopeId, limit: String(limit) });
  return request<SearchResponse>(`/search?${params.toString()}`);
}

// ---- 助手 -------------------------------------------------------------------

export interface ConversationSummary {
  id: string;
  scope_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  last_message_at: string;
}

export interface AssistantProgressEvent {
  text: string;
  status: string;
  stage: string;
  step_index: number;
  timestamp: string;
  kind: string;
  payload: Record<string, unknown>;
}

export interface AssistantAttachment {
  handle: string;
  asset_id: string;
  file_name: string;
}

export interface AssistantRequirement {
  id: string;
  evidence_type: string;
  description: string;
  status: string;
  satisfied_by: string[];
  unmet_reason: string;
}

export interface AssistantCompleteEvent {
  type: string;
  turn_id: string;
  status: string;
  termination_reason: string;
  answer: string;
  attachments: AssistantAttachment[];
  requirements: AssistantRequirement[];
  error: string;
}

export async function createConversation(scopeId: string): Promise<ConversationSummary> {
  return request<ConversationSummary>("/assistant/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope_id: scopeId }),
  });
}

/**
 * 建一个回合。**不带 scope**：回合属于会话，scope 由后端从会话取。
 *
 * 前端曾经一路把 ``scopeId`` 传到这里。那是把「会话的 scope」这个后端事实又抄了一遍，
 * 抄错的时候（省略、传旧值）后端会静默按错的 scope 检索，用户看到的是「搜不到」。
 */
export async function startTurn(
  conversationId: string,
  message: string,
): Promise<{ turn_id: string; conversation_id: string; status: string }> {
  return request(`/assistant/conversations/${conversationId}/turns`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
}

/** 回合事件的 SSE 地址。浏览器侧的主路径，与 1.0 ``src/api.js:66`` 同形。 */
export function turnEventsUrl(turnId: string): string {
  return `${BASE}/assistant/turns/${turnId}/events`;
}

export interface AssistantTurnPoll {
  turn_id: string;
  status: string;
  public_progress: AssistantProgressEvent[];
  result: AssistantCompleteEvent | null;
  error: string;
}

/**
 * 轮询一个回合的状态——1.0 ``src/api.js:64`` 的 ``assistantTurnPoll``，同一条契约。
 *
 * 浏览器侧只在 SSE 断掉之后用它（``useTurnStream``，与 1.0 ``src/app.js:1482`` 的
 * 「优先 SSE、EventSource 不可用时回退」同一个顺序）。它同时也是 ``EventSource``
 * 用不了的调用方的唯一一条路——1.0 的 ``services/photobench`` 就是靠它跑完每个回合的
 * （``benchmark_orchestrator.py:881`` 每 0.5 秒一次，8091 日志里 668,552 次）。
 */
export async function pollTurn(turnId: string): Promise<AssistantTurnPoll> {
  return request<AssistantTurnPoll>(`/assistant/turns/${encodeURIComponent(turnId)}`);
}

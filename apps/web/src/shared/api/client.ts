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

export async function startTurn(
  conversationId: string,
  scopeId: string,
  message: string,
): Promise<{ turn_id: string; conversation_id: string; status: string }> {
  return request(`/assistant/conversations/${conversationId}/turns`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, scope_id: scopeId }),
  });
}

/**
 * 回合事件的 SSE 地址。
 *
 * 前端**只开这一条连接**：1.0 靠 700ms 轮询 ``GET /api/assistant/turn/{id}`` 拿进度，
 * 产生了 668,490 次请求（占全部流量 73%）。2.0 的后端把进度事件也落了库，断线重连
 * 由 ``Last-Event-ID`` 续上，所以前端没有任何理由再轮询。
 */
export function turnEventsUrl(turnId: string): string {
  return `${BASE}/assistant/turns/${turnId}/events`;
}

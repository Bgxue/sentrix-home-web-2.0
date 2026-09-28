/**
 * 家庭记忆助手。对应 1.0 `src/app.js:619` 的 `searchView()`（`assistant-layout` 在 `:622-623`）。
 *
 * 左边会话列表、右边消息流。1.0 的 `conversationRail()` 就是这个结构——把会话的选择
 * 放在消息流旁边，而不是藏进一个下拉里。
 *
 * **1.0 的「停止本轮回答」在 2.0 没有**：那要 `POST /assistant/turn/{id}/cancel`，
 * 2.0 没有这个端点。界面上不做假按钮。
 */

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router";

import { useScope } from "../../app/ScopeProvider";
import {
  createConversation,
  listConversations,
  type ConversationSummary,
} from "../../shared/api/client";
import { PageHeader } from "../../shared/ui/PageHeader";
import { AssistantPanel } from "./AssistantPanel";
import "../../styles/assistant.css";

export function AssistantPage() {
  const { scopeId } = useScope();
  // 概览页的提问框把问题塞在 `?q=` 里交过来（1.0 的 searchBar() 是直接 push 一个
  // assistant turn）。读出来交给面板，面板问完就不再重复。
  const [searchParams] = useSearchParams();
  const initialQuestion = searchParams.get("q") ?? "";
  const [conversations, setConversations] = useState<readonly ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const items = await listConversations(scopeId);
      setConversations(items);
      setError(null);
      return items;
    } catch (cause) {
      setError((cause as Error).message);
      return [];
    }
  }, [scopeId]);

  // 进入这一页或换相册：拉列表；一个都没有就建一个（否则用户面对的是不能输入的输入框）。
  useEffect(() => {
    let alive = true;
    void reload().then(async (items) => {
      if (!alive) return;
      if (items.length > 0) {
        setActiveId(items[0].id);
        return;
      }
      try {
        const created = await createConversation(scopeId);
        if (!alive) return;
        setConversations([created]);
        setActiveId(created.id);
      } catch (cause) {
        if (alive) setError((cause as Error).message);
      }
    });
    return () => {
      alive = false;
    };
  }, [reload, scopeId]);

  async function newConversation() {
    try {
      const created = await createConversation(scopeId);
      setConversations((previous) => [created, ...previous]);
      setActiveId(created.id);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  return (
    <>
      <PageHeader
        kicker="记忆组织 / 对话"
        title="你想找回哪一段记忆？"
        description="从人物、时间、地点、物体或一句描述开始，答案会带回原始证据。"
      />
      {error ? <p className="error">{error}</p> : null}
      <div className="assistant-layout">
        <aside className="conversation-rail">
          <button type="button" className="button primary full" onClick={() => void newConversation()}>
            新建对话
          </button>
          {conversations.length === 0 ? (
            <p className="rail-empty">还没有对话</p>
          ) : (
            conversations.map((conversation) => (
              <button
                key={conversation.id}
                type="button"
                className={
                  conversation.id === activeId ? "rail-item active" : "rail-item"
                }
                onClick={() => setActiveId(conversation.id)}
              >
                <strong>{conversation.title || "未命名对话"}</strong>
                <small>{conversation.last_message_at?.slice(0, 16) ?? ""}</small>
              </button>
            ))
          )}
        </aside>
        <div className="assistant-main">
          <AssistantPanel conversationId={activeId} initialQuestion={initialQuestion} />
        </div>
      </div>
    </>
  );
}

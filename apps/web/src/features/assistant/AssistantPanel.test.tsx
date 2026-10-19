import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    getConversation: vi.fn(),
    fetchTurnEvents: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import type { ConversationDetail } from "../../shared/api/client";
import { AssistantPanel } from "./AssistantPanel";

/** 一条完整的历史会话：用户消息、助手消息、带交付照片的回合。 */
function detail(
  id: string,
  question: string,
  answer: string,
  assetId: string,
): ConversationDetail {
  return {
    conversation: {
      id,
      scope_id: "album1",
      title: question,
      created_at: "2026-07-01T10:00:00+00:00",
      updated_at: "2026-07-01T10:00:05+00:00",
      last_message_at: "2026-07-01T10:00:05+00:00",
    },
    messages: [
      {
        id: `${id}-m1`,
        role: "user",
        content: question,
        turn_id: `${id}-t1`,
        created_at: "2026-07-01T10:00:00+00:00",
      },
      {
        id: `${id}-m2`,
        role: "assistant",
        content: answer,
        turn_id: `${id}-t1`,
        created_at: "2026-07-01T10:00:05+00:00",
      },
    ],
    turns: [
      {
        id: `${id}-t1`,
        question,
        status: "complete",
        termination_reason: "answered",
        answer,
        attachments: [{ handle: "photo_1", asset_id: assetId, file_name: "beach.jpg" }],
        created_at: "2026-07-01T10:00:00+00:00",
      },
    ],
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("助手面板的历史回放", () => {
  it("切换会话时加载并渲染后端存下的消息与交付照片", async () => {
    vi.mocked(client.getConversation)
      .mockResolvedValueOnce(detail("c1", "去年夏天在海边", "找到了两张。", "a1"))
      .mockResolvedValueOnce(detail("c2", "奶奶的生日", "找到了蛋糕。", "a2"));

    const { rerender } = render(<AssistantPanel conversationId="c1" scopeId="album1" />);

    expect(await screen.findByText("去年夏天在海边")).toBeTruthy();
    expect(await screen.findByText("找到了两张。")).toBeTruthy();
    const firstPhoto = await screen.findByAltText("beach.jpg");
    expect(firstPhoto.getAttribute("src")).toBe("/api/v1/assets/a1/file");
    expect(client.getConversation).toHaveBeenCalledWith("c1");

    rerender(<AssistantPanel conversationId="c2" scopeId="album1" />);

    expect(await screen.findByText("奶奶的生日")).toBeTruthy();
    expect(await screen.findByText("找到了蛋糕。")).toBeTruthy();
    expect(client.getConversation).toHaveBeenLastCalledWith("c2");
    // 回放的是 c2，c1 的内容不该继续留在面板里。
    expect(screen.queryByText("去年夏天在海边")).toBeNull();
  });
});

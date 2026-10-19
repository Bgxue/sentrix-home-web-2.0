import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    listAssets: vi.fn(),
    fetchTimeline: vi.fn(),
    fetchHealth: vi.fn(),
    listPeople: vi.fn(),
    listMemorySpaces: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import { ScopeProvider } from "../../app/ScopeProvider";
import { OverviewPage } from "./OverviewPage";

function renderPage() {
  return render(
    <ScopeProvider>
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>
    </ScopeProvider>,
  );
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  vi.mocked(client.listMemorySpaces).mockResolvedValue([]);
  vi.mocked(client.listAssets).mockResolvedValue([]);
  vi.mocked(client.listPeople).mockResolvedValue([]);
  vi.mocked(client.fetchTimeline).mockResolvedValue({
    scope_id: "",
    total: 0,
    offset: 0,
    limit: 5,
    days: [],
  });
  vi.mocked(client.fetchHealth).mockResolvedValue({
    status: "ok",
    version: "0.2.0",
    database: "ok",
    embeddings: "ok",
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("OverviewPage 自动刷新", () => {
  it("每 5 秒重新拉一次数据", async () => {
    vi.useFakeTimers();
    renderPage();
    await flush();
    expect(client.listAssets).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(4999);
    });
    expect(client.listAssets).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(1);
    });
    expect(client.listAssets).toHaveBeenCalledTimes(2);
  });

  it("输入框有焦点时暂停（不打断正在输入的问题）", async () => {
    vi.useFakeTimers();
    renderPage();
    await flush();

    screen.getByLabelText("问 Sentrix").focus();
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });
    expect(client.listAssets).toHaveBeenCalledTimes(1);
  });
});

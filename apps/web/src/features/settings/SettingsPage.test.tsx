import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    fetchHealth: vi.fn(),
    listFacts: vi.fn(),
    confirmFact: vi.fn(),
    rejectFact: vi.fn(),
    listMemorySpaces: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import { ScopeProvider } from "../../app/ScopeProvider";
import { ToastProvider } from "../../shared/ui/Toast";
import { SettingsPage } from "./SettingsPage";

const FACT: client.FactPayload = {
  id: "fact-1",
  scope_id: "album-1",
  subject: "小明",
  predicate: "喜欢",
  object: "海边",
  status: "pending",
  confidence: 0.82,
  evidence_ids: ["obs-1"],
  revision: 2,
  supersedes_fact_id: null,
};

function renderPage() {
  return render(
    <ToastProvider>
      <ScopeProvider>
        <MemoryRouter>
          <SettingsPage />
        </MemoryRouter>
      </ScopeProvider>
    </ToastProvider>,
  );
}

beforeEach(() => {
  vi.mocked(client.listMemorySpaces).mockResolvedValue([]);
  vi.mocked(client.fetchHealth).mockResolvedValue({
    status: "degraded",
    version: "0.2.0",
    database: "ok",
    embeddings: "unavailable",
    embeddings_error: "sidecar_unreachable",
  });
  vi.mocked(client.listFacts).mockResolvedValue([FACT]);
  vi.mocked(client.confirmFact).mockResolvedValue({ ...FACT, status: "active" });
  vi.mocked(client.rejectFact).mockResolvedValue({ ...FACT, status: "retracted" });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SettingsPage 服务网格", () => {
  it("只展示 /health 真实返回的字段，并标明模型端点未暴露", async () => {
    renderPage();

    await screen.findByText("unavailable");
    expect(screen.getByText("ok")).toBeTruthy();
    expect(screen.getAllByText("0.2.0").length).toBeGreaterThan(0);
    expect(screen.getByText("原因：sidecar_unreachable")).toBeTruthy();
    expect(screen.getByText("未暴露")).toBeTruthy();
    expect(screen.getByText(/不返回模型端点字段/)).toBeTruthy();
  });

  it("嵌入可用时不显示错误原因", async () => {
    vi.mocked(client.fetchHealth).mockResolvedValue({
      status: "ok",
      version: "0.2.0",
      database: "ok",
      embeddings: "ok",
    });
    renderPage();

    await screen.findByText("在线");
    expect(screen.queryByText(/原因：/)).toBeNull();
  });
});

describe("SettingsPage 待确认事实", () => {
  it("加载 pending 事实并可确认", async () => {
    renderPage();

    await screen.findByText("小明 喜欢 海边");
    expect(client.listFacts).toHaveBeenCalledWith("", "pending");

    fireEvent.click(screen.getByText("✓ 确认"));

    await waitFor(() => expect(client.confirmFact).toHaveBeenCalledWith("fact-1"));
    await screen.findByText("已确认事实：小明 喜欢 海边");
    // 动作完成后重新拉 pending，不本地推断版本状态。
    expect(client.listFacts).toHaveBeenCalledTimes(2);
  });

  it("驳回走 reject 端点", async () => {
    renderPage();

    await screen.findByText("小明 喜欢 海边");
    fireEvent.click(screen.getByText("× 驳回"));

    await waitFor(() => expect(client.rejectFact).toHaveBeenCalledWith("fact-1"));
    await screen.findByText("已驳回事实：小明 喜欢 海边");
  });

  it("没有待确认事实时显示空状态", async () => {
    vi.mocked(client.listFacts).mockResolvedValue([]);
    renderPage();

    await screen.findByText(/没有待确认事实/);
  });
});

describe("SettingsPage 未开放端点", () => {
  it("OCR 与相册写操作都标明后端缺端点", async () => {
    renderPage();

    await screen.findByText("OCR 设置");
    // 文案被 <code> 拆开了，按整段 textContent 断言，而不是让 getByText 猜节点。
    expect(document.body.textContent).toContain("没有 /settings/ocr 端点");
    expect(document.body.textContent).toContain("只有 GET");
  });
});

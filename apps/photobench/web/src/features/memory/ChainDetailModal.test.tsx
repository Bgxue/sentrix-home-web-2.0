import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChainDetail } from "../../lib/graphs.ts";

import { ChainDetailModal } from "./ChainDetailModal.tsx";

// 1.0 参考 `App.vue:3773-3795`：`<Teleport to="body">` → 这里必须是 `createPortal(..., document.body)`。
afterEach(cleanup);

function detail(overrides: Partial<ChainDetail> = {}): ChainDetail {
  return {
    mode: "creation",
    layer: { key: "L0", name: "原始流", color: "raw", stage: "输入与派生媒体", nodes: [], transition: "asset_id + 时间戳 → 场景观察" },
    node: { kind: "原始媒体", value: "12", evidence: "12 个原始媒体 · asset_id / file_name" },
    records: [
      {
        key: "qa-1-0",
        index: 0,
        question: "车停在哪",
        media: [
          { media_type: "image", media_id: "a.jpg", file_name: "a.jpg", asset_id: "asset-1" },
          { media_type: "video", media_id: "v-1", file_name: "v-1.mp4", asset_id: "asset-2" },
        ],
        statements: [{ label: "用户问题", value: "车停在哪" }],
        traceRows: [{ label: "1. inspect_photo", value: '{"ok":true}' }],
        reasoning: '{"agent2_trace":null}',
      },
    ],
    page: 1,
    pageSize: 20,
    total: 1,
    ...overrides,
  };
}

describe("ChainDetailModal · 证据链节点弹窗（1.0 :3773-3795）", () => {
  it("chainDetail 为 null 时什么都不渲染", () => {
    render(<ChainDetailModal chainDetail={null} onClose={vi.fn()} />);
    expect(document.querySelector(".chain-detail-backdrop")).toBeNull();
  });

  it("portal 到 document.body，标题带 mode / 图层 / 节点", () => {
    const { container } = render(<ChainDetailModal chainDetail={detail()} onClose={vi.fn()} />);

    // 不在 render 的容器里，而是挂在 body 下。
    expect(container.querySelector(".chain-detail-modal")).toBeNull();
    const modal = document.querySelector(".chain-detail-modal");
    expect(modal).not.toBeNull();
    expect(modal?.getAttribute("aria-label")).toBe("证据链节点详情");

    expect(screen.getByText("ORIGINAL CREATION CHAIN")).not.toBeNull();
    expect(screen.getByText("L0 · 原始流 · 原始媒体")).not.toBeNull();
    expect(screen.getByText("12 · 12 个原始媒体 · asset_id / file_name")).not.toBeNull();
  });

  it("生效链路用另一套英文眉题", () => {
    render(<ChainDetailModal chainDetail={detail({ mode: "effective" })} onClose={vi.fn()} />);
    expect(screen.getByText("EFFECTIVE EVALUATION CHAIN")).not.toBeNull();
  });

  it("媒体格：图片走按钮 + img（资产 id 优先），视频走 video", () => {
    render(<ChainDetailModal chainDetail={detail()} onClose={vi.fn()} />);

    const image = document.querySelector(".chain-detail-media img") as HTMLImageElement;
    expect(image).not.toBeNull();
    expect(image?.getAttribute("src")).toBe("/api/v1/assets/asset-1/file");

    const video = document.querySelector(".chain-detail-media video") as HTMLVideoElement;
    expect(video).not.toBeNull();
    expect(video?.getAttribute("src")).toBe("/api/v1/assets/asset-2/file");
  });

  it("点缩略图走 onOpenImage，不关闭弹窗；点背景关闭", () => {
    const onClose = vi.fn();
    const onOpenImage = vi.fn();
    render(<ChainDetailModal chainDetail={detail()} onClose={onClose} onOpenImage={onOpenImage} />);

    fireEvent.click(document.querySelector(".chain-detail-media img") as HTMLElement);
    expect(onOpenImage).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();

    // 点弹窗内部（不是 backdrop 本身）不关闭。
    fireEvent.click(document.querySelector(".chain-detail-body") as HTMLElement);
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(document.querySelector(".chain-detail-backdrop") as HTMLElement);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("每条 QA 记录展开：媒体数 / 语句数 / 调用数 + 空态文案", () => {
    render(
      <ChainDetailModal
        chainDetail={detail({
          records: [
            { key: "qa-9-0", index: 8, question: "未记录问题", media: [], statements: [], traceRows: [], reasoning: "{}" },
          ],
        })}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText("QA 9")).not.toBeNull();
    expect(screen.getByText(/0 媒体 · 0 语句 · 0 调用/)).not.toBeNull();
    expect(screen.getByText("该节点没有保存可展示的媒体引用。")).not.toBeNull();
    expect(screen.getByText("没有保存文字观察或回答声明。")).not.toBeNull();
    expect(screen.getByText("该节点没有保存工具调用轨迹。")).not.toBeNull();
  });

  it("当前页读不到记录时给出提示", () => {
    render(<ChainDetailModal chainDetail={detail({ records: [] })} onClose={vi.fn()} />);
    expect(screen.getByText("当前页没有成功读取到可展开的 QA 记录，请先确认 QA 接口可用。")).not.toBeNull();
  });
});

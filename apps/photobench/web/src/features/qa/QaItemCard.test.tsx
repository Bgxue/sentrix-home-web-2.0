import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { RunItemDetail } from "../../api/types.ts";

import { QaItemCard } from "./QaItemCard.tsx";

afterEach(cleanup);

function renderCard(detail: RunItemDetail) {
  return render(
    <QaItemCard
      summary={{ index: 0, question: "车停在哪" }}
      detail={detail}
      loading={false}
      open
      albumId="album3"
      modelLabel="qwen"
      activeRejudge={null}
      onToggle={() => {}}
      onOpenImage={() => {}}
      onOpenJudgeInput={() => {}}
    />,
  );
}

// 口径（1.0 App.vue:703-711 注释原文）：`toolRecallMedia` 是工具召回候选，
// `itemEvidenceMedia` 是模型**显式交付**图，**未交付即空、绝不回退到候选**。
// 夹具让两种口径给出**不同**答案：候选有、交付无。
describe("QaItemCard · 工具召回图与模型使用图是两个口径", () => {
  it("只有候选、没有交付图：召回区 1 张，使用区为空且不显示候选", () => {
    renderCard({
      index: 0,
      question: "车停在哪",
      retrieved_candidate_media: [{ media_type: "image", media_id: "cand.jpg", asset_id: "cand-1", file_name: "cand.jpg" }],
      // 故意不给 predicted_*：模型没有显式交付。
    });

    expect(screen.getByText("工具召回图片（1）")).not.toBeNull();
    expect(screen.getByText("模型使用图片（0）")).not.toBeNull();
    expect(screen.getByText("回答依据图片为空（模型未选择交付图）")).not.toBeNull();

    // 候选图只出现在召回区一次——若交付回退到候选，这里会变成 2。
    expect(screen.getAllByAltText("cand.jpg")).toHaveLength(1);
  });

  it("只有交付图、没有候选：使用区 1 张，召回区为空", () => {
    renderCard({
      index: 0,
      question: "车停在哪",
      predicted_media: [{ media_type: "image", media_id: "used.jpg", asset_id: "used-1", file_name: "used.jpg" }],
    });

    expect(screen.getByText("工具召回图片（0）")).not.toBeNull();
    expect(screen.getByText("检索未返回可识别媒体")).not.toBeNull();
    expect(screen.getByText("模型使用图片（1）")).not.toBeNull();
    expect(screen.getAllByAltText("used.jpg")).toHaveLength(1);
  });

  it("候选与交付同时存在：两区各自渲染各自的图", () => {
    renderCard({
      index: 0,
      question: "车停在哪",
      retrieved_candidate_media: [{ media_type: "image", media_id: "cand.jpg", asset_id: "cand-1", file_name: "cand.jpg" }],
      predicted_media: [{ media_type: "image", media_id: "used.jpg", asset_id: "used-1", file_name: "used.jpg" }],
    });

    expect(screen.getByText("工具召回图片（1）")).not.toBeNull();
    expect(screen.getByText("模型使用图片（1）")).not.toBeNull();
    expect(screen.getByAltText("cand.jpg")).not.toBeNull();
    expect(screen.getByAltText("used.jpg")).not.toBeNull();
  });
});

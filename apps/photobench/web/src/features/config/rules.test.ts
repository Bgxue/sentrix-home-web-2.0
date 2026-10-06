import { describe, expect, it } from "vitest";

import type { ManifestSummary } from "../../api/types.ts";
import {
  albumCountLabel,
  qaOptionsFor,
  RUN_MODES_UI,
  runModeMeta,
  startDisabledReason,
  type StartDisabledInput,
} from "./rules.ts";

/** 一份「各条件都满足」的输入：非 cloud-only、端点与管理器地址都在、runMode 非 reuse。 */
function readyInput(overrides: Partial<StartDisabledInput> = {}): StartDisabledInput {
  return {
    hasRunning: false,
    suiteRunning: false,
    selectedModels: new Set(["reg1"]),
    modelEndpoint: "127.0.0.1:8100",
    vllmManagerUrl: "127.0.0.1:8500",
    selectedEndpointModel: "",
    runMode: "full",
    existingScopeId: "",
    ...overrides,
  };
}

describe("startDisabledReason", () => {
  it("各条件都满足 → 空串（可启动）", () => {
    expect(startDisabledReason(readyInput())).toBe("");
  });

  it("差一个条件（勾了 __current__ 但没选出复用模型）→ 给出对应文案，与空串不同", () => {
    const reason = startDisabledReason(
      readyInput({ selectedModels: new Set(["__current__"]), selectedEndpointModel: "" }),
    );
    expect(reason).toBe("请先从模型服务中选择要复用的模型");
    expect(reason).not.toBe(startDisabledReason(readyInput()));
  });

  it("1.0 的判定顺序：不为空时最先报「已有任务运行中」", () => {
    expect(startDisabledReason(readyInput({ hasRunning: true }))).toBe("已有任务运行中");
    expect(startDisabledReason(readyInput({ suiteRunning: true }))).toBe("已有任务运行中");
    // 即便同时缺模型，hasRunning 也排在前面。
    expect(
      startDisabledReason(readyInput({ hasRunning: true, selectedModels: new Set() })),
    ).toBe("已有任务运行中");
  });

  it("未选模型 / 缺端点 / 缺管理器 / 缺复用相册各报各的", () => {
    expect(startDisabledReason(readyInput({ selectedModels: new Set() }))).toBe("请先选择模型");
    expect(
      startDisabledReason(readyInput({ selectedModels: new Set(["reg1"]), modelEndpoint: "  " })),
    ).toBe("请先填写模型服务地址");
    expect(
      startDisabledReason(
        readyInput({ selectedModels: new Set(["reg1"]), vllmManagerUrl: "" }),
      ),
    ).toBe("选择注册表模型需要模型管理器地址");
    expect(
      startDisabledReason(
        readyInput({ runMode: "reuse", existingScopeId: "", selectedModels: new Set(["big_model"]) }),
      ),
    ).toBe("请先选择要复用的相册");
  });

  it("cloud-only（big_model）不需要模型服务地址", () => {
    expect(
      startDisabledReason(
        readyInput({ selectedModels: new Set(["big_model"]), modelEndpoint: "" }),
      ),
    ).toBe("");
  });
});

describe("runModeMeta / RUN_MODES_UI", () => {
  it("未知 mode 回退第一条（1.0 `|| RUN_MODES_UI[0]`）", () => {
    expect(runModeMeta("nope")).toBe(RUN_MODES_UI[0]);
    expect(runModeMeta("reuse").button).toBe("启动复用测评");
  });
});

describe("albumCountLabel / qaOptionsFor", () => {
  const manifest: ManifestSummary = {
    album_id: "album3",
    album_name: "A3",
    face_count: 1,
    photo_count: 2,
    video_count: 0,
    qa_sets: ["compact-10q"],
  };

  it("没有视频时不追加「N视频」", () => {
    expect(albumCountLabel(manifest)).toBe("1人 / 2图");
    expect(albumCountLabel({ ...manifest, video_count: 3 })).toBe("1人 / 2图 / 3视频");
  });

  it("qaOptions 找不到相册时回退默认题集", () => {
    expect(qaOptionsFor([manifest], "album3")).toEqual(["compact-10q"]);
    expect(qaOptionsFor([manifest], "missing")).toEqual(["compact-10q"]);
    expect(qaOptionsFor([{ ...manifest, qa_sets: ["a", "b"] }], "album3")).toEqual(["a", "b"]);
  });
});

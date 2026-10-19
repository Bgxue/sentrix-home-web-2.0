import { describe, expect, it } from "vitest";

import { IMPORT_BATCH_SIZE, runInBatches } from "./batch";

describe("runInBatches", () => {
  it("每批最多 20 个并发，批与批之间才等待", async () => {
    let active = 0;
    let peak = 0;
    const items = Array.from({ length: 45 }, (_, index) => index);

    const results = await runInBatches(items, IMPORT_BATCH_SIZE, async (item) => {
      active += 1;
      peak = Math.max(peak, active);
      // 让同批的 worker 真正重叠，否则 peak 恒为 1，这条断言就什么也没测到。
      await new Promise((resolve) => setTimeout(resolve, 0));
      active -= 1;
      return item * 2;
    });

    expect(IMPORT_BATCH_SIZE).toBe(20);
    expect(peak).toBe(20);
    expect(results).toEqual(items.map((item) => item * 2));
  });

  it("worker 把失败转成结果时，其余项照常返回", async () => {
    const results = await runInBatches([1, 2, 3], 2, async (item) =>
      item === 2 ? { ok: false, item } : { ok: true, item },
    );
    expect(results).toEqual([
      { ok: true, item: 1 },
      { ok: false, item: 2 },
      { ok: true, item: 3 },
    ]);
  });

  it("worker 抛错时整片失败（调用方负责在 worker 里 catch）", async () => {
    await expect(
      runInBatches([1, 2, 3], 2, async (item) => {
        if (item === 2) throw new Error("boom");
        return item;
      }),
    ).rejects.toThrow("boom");
  });

  it("拒绝非法批大小", async () => {
    await expect(runInBatches([1], 0, async (item) => item)).rejects.toThrow(RangeError);
  });
});

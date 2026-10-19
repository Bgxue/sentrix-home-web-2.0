import type { AssetSummary } from "../../shared/api/client";

/**
 * 按相册（scope）汇总资产数。
 *
 * **只有 `/assets` 真的返回 `scope_id` 时才有意义。** 后端目前不返回，所以调用方
 * 拿到空对象时必须退回「当前范围总数」，不能把「字段缺失」显示成「0 个」。
 */
export function countByScope(assets: readonly AssetSummary[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const asset of assets) {
    const scopeId = asset.scope_id;
    if (scopeId === undefined || scopeId === "") continue;
    counts[scopeId] = (counts[scopeId] ?? 0) + 1;
  }
  return counts;
}

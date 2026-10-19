import type { AssetSummary, ObservationPayload } from "../../shared/api/client";

/**
 * 把实体 / 实体组详情里的 Observation 适配成 `AssetDetail` 需要的 `AssetSummary`。
 *
 * **这是一次投影，不是伪造数据**：`asset` 那四个字段是后端真的给了的；mime / 状态 /
 * GPS 这三个字段在 Observation 里没有，所以留 `null` / `"unknown"`，而不是猜一个值。
 */
export function assetFromObservation(observation: ObservationPayload): AssetSummary {
  return {
    id: observation.asset_id,
    file_name: observation.asset?.file_name ?? observation.asset_id,
    media_type: observation.asset?.media_type ?? "image",
    mime_type: null,
    size_bytes: 0,
    status: "unknown",
    captured_at: observation.captured_at,
    gps: null,
  };
}

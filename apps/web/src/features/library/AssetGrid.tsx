import { useState } from "react";

import { assetDisplayUrl, type AssetSummary } from "../../shared/api/client";
import { AssetDetail } from "./AssetDetail";

export function AssetGrid({ assets }: { assets: AssetSummary[] }) {
  const [selected, setSelected] = useState<AssetSummary | null>(null);

  if (assets.length === 0) {
    return <p className="empty">还没有照片。用 curl 导入一张：<code>curl -F file=@photo.jpg -F scope_id=album1 http://127.0.0.1:8191/api/v1/media/import</code></p>;
  }

  return (
    <>
      <ul className="grid">
        {assets.map((asset) => (
          <li key={asset.id}>
            <button type="button" onClick={() => setSelected(asset)}>
              <img src={assetDisplayUrl(asset)} alt={asset.file_name} loading="lazy" />
              <span>{asset.file_name}</span>
            </button>
          </li>
        ))}
      </ul>
      {selected ? <AssetDetail asset={selected} onClose={() => setSelected(null)} /> : null}
    </>
  );
}

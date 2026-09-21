import { assetFileUrl, type AssetSummary } from "../../shared/api/client";

export function AssetDetail({ asset, onClose }: { asset: AssetSummary; onClose: () => void }) {
  return (
    <div className="detail" role="dialog" aria-modal="true">
      <button type="button" className="close" onClick={onClose}>关闭</button>
      {/* 详情一律走 /file 原图，符合产品硬约束：查询必须返回可打开的原始 Asset */}
      <img src={assetFileUrl(asset.id)} alt={asset.file_name} />
      <dl>
        <dt>文件名</dt><dd>{asset.file_name}</dd>
        <dt>拍摄时间</dt><dd>{asset.captured_at ?? "未知"}</dd>
        <dt>地点</dt>
        <dd>{asset.gps ? `${asset.gps.latitude.toFixed(6)}, ${asset.gps.longitude.toFixed(6)}` : "无 GPS"}</dd>
        <dt>原图</dt><dd><a href={assetFileUrl(asset.id)} target="_blank" rel="noreferrer">打开原图</a></dd>
      </dl>
    </div>
  );
}

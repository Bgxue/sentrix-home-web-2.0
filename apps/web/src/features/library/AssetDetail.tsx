/**
 * 资产详情。1.0 的这张卡有「文件 / 处理状态 / Observation / 模型原始输出」四段，
 * **2.0 只做前三段里能做的**：`/observations` 端点不存在，第四段据实说明而不是留空壳。
 */

import { assetFileUrl, type AssetSummary } from "../../shared/api/client";

const MEDIA_LABEL: Readonly<Record<string, string>> = {
  image: "图片",
  video: "视频",
  audio: "音频",
  text: "文本",
};

export function AssetDetail({ asset, onClose }: { asset: AssetSummary; onClose: () => void }) {
  return (
    <div className="detail" role="dialog" aria-modal="true">
      <button type="button" className="close" onClick={onClose}>关闭</button>
      {/* 详情一律走 /file 原图，符合产品硬约束：查询必须返回可打开的原始 Asset */}
      <img src={assetFileUrl(asset.id)} alt={asset.file_name} />
      <dl>
        <dt>文件名</dt><dd>{asset.file_name}</dd>
        <dt>类型</dt><dd>{MEDIA_LABEL[asset.media_type] ?? asset.media_type}（{asset.mime_type ?? "未知 MIME"}）</dd>
        <dt>拍摄时间</dt><dd>{asset.captured_at ?? "未标注"}</dd>
        <dt>地点</dt>
        <dd>{asset.gps ? `${asset.gps.latitude.toFixed(6)}, ${asset.gps.longitude.toFixed(6)}` : "无 GPS"}</dd>
        <dt>处理状态</dt><dd>{asset.status}</dd>
        <dt>Observation</dt><dd>尚未接线（2.0 没有 /observations 端点）</dd>
        <dt>原图</dt><dd><a href={assetFileUrl(asset.id)} target="_blank" rel="noreferrer">打开原图</a></dd>
      </dl>
    </div>
  );
}

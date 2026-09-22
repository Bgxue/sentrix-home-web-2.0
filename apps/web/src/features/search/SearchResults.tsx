import { assetDisplayUrl, type SearchHit, type SearchResponse } from "../../shared/api/client";

/**
 * 展示真实相似度，而不是 ``score``。
 *
 * ``score`` 是加权 RRF 的名次推导分：每个通道的第一名都得到同一个数
 * （2.5 × 1/(60+1)），不含相似度信息——显示它会让用户看到「搜什么都一样」。
 * 真实相似度在 ``raw_scores`` 里，按通道存各通道自报的原始分。
 *
 * 单通道只显示数字；多通道带上通道名，因为各通道量纲不同（余弦 ∈ [-1,1]，词法分无
 * 上界），不加名会被当成同一把尺子。通道名排序后拼接，保证同一份数据渲染稳定。
 */
function formatSimilarity(hit: SearchHit): string {
  const channels = Object.entries(hit.raw_scores).sort(([a], [b]) => a.localeCompare(b));
  if (channels.length === 0) return "—";
  if (channels.length === 1) return channels[0][1].toFixed(4);
  return channels.map(([channel, value]) => `${channel}: ${value.toFixed(4)}`).join(" · ");
}

/** 检索结果。降级状态要显式展示——后端在降级，用户有权知道结果是怎么来的。 */
export function SearchResults({
  response,
  onClose,
}: {
  response: SearchResponse;
  onClose: () => void;
}) {
  return (
    <section className="results">
      <header className="results-header">
        <h2>
          「{response.query}」的结果（{response.items.length}）
        </h2>
        <button type="button" onClick={onClose}>
          返回全部照片
        </button>
      </header>
      {response.degraded ? (
        <p className="degraded">
          检索正在降级（{response.backend}）：{response.degraded_reason}
        </p>
      ) : null}
      {response.items.length === 0 ? (
        <p className="empty">
          {response.authoritative_vectors === 0 ? "还没有已索引的照片。" : "没有匹配的照片。"}
        </p>
      ) : (
        <ul className="grid">
          {response.items.map((item) => (
            <li key={item.asset.id}>
              {/* 结果一律给可打开的原图（HEIC 走服务端转码预览） */}
              <img src={assetDisplayUrl(item.asset)} alt={item.asset.file_name} loading="lazy" />
              <span>{item.asset.file_name}</span>
              <small>{formatSimilarity(item)}</small>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

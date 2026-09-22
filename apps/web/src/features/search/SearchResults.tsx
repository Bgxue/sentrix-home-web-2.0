import { assetDisplayUrl, type SearchResponse } from "../../shared/api/client";

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
              <small>{item.score.toFixed(4)}</small>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

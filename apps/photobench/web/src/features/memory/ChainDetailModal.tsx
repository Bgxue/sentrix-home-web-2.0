/**
 * 证据链节点详情弹窗。逐行照搬 1.0 `App.vue:3773-3795`（`<Teleport to="body">` →
 * React `createPortal(..., document.body)`）。
 *
 * 与 1.0 的两处接口差异（都是把组件外的东西显式化）：
 *   - `openChainNode` 的网络/取数半（`loadQaDetail`）留在组合层，本组件只吃
 *     `lib/graphs.ts:chainDetailFor` 算好的 `chainDetail`；
 *   - 1.0 的内联 `openImage(media)` 是 lightbox 的另一切片，这里做成可选回调
 *     `onOpenImage`（不传就只是不打开大图，`imageUrl` 仍照常渲染缩略图）。
 */
import { createPortal } from "react-dom";

import { imageUrl, isVideoMedia, mediaKey, pauseVideoAtEvidenceFrame } from "../../api/media.ts";
import type { ChainDetail, ChainMedia } from "../../lib/graphs.ts";

export interface ChainDetailModalProps {
  /** `null` 时不渲染（1.0 `v-if="chainDetail"`）。 */
  chainDetail: ChainDetail | null;
  onClose: () => void;
  /** 1.0 `openImage(media)` 的落点（lightbox 归另一切片）。 */
  onOpenImage?: (media: ChainMedia) => void;
}

export function ChainDetailModal({ chainDetail, onClose, onOpenImage }: ChainDetailModalProps) {
  if (!chainDetail) return null;
  return createPortal(
    <div
      className="chain-detail-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="chain-detail-modal" role="dialog" aria-modal="true" aria-label="证据链节点详情">
        <header className="chain-detail-head">
          <div>
            <small>{chainDetail.mode === "creation" ? "ORIGINAL CREATION CHAIN" : "EFFECTIVE EVALUATION CHAIN"}</small>
            <h3>
              {chainDetail.layer.key} · {chainDetail.layer.name} · {chainDetail.node.kind}
            </h3>
            <span>
              {chainDetail.node.value} · {chainDetail.node.evidence}
            </span>
          </div>
          <button className="judge-modal-close" type="button" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="chain-detail-body">
          <div className="chain-detail-summary">
            当前展示第 {chainDetail.page} 页的 {chainDetail.records.length} 条 QA 链路（每页 {chainDetail.pageSize}{" "}
            条，共 {chainDetail.total} 条）。切换 QA 分页后，可继续查看其他链路。
          </div>
          {chainDetail.records.map((record, recordIndex) => (
            <details className="chain-record" key={record.key} open={recordIndex === 0}>
              <summary>
                <b>QA {record.index + 1}</b>
                <span>{record.question}</span>
                <em>
                  {record.media.length} 媒体 · {record.statements.length} 语句 · {record.traceRows.length} 调用
                </em>
              </summary>
              <div className="chain-record-body">
                <section className="chain-detail-section">
                  <h4>
                    图像 / 媒体证据 <small>{record.media.length} 项</small>
                  </h4>
                  <div className="chain-detail-media-grid">
                    {record.media.map((media) => (
                      <div className="chain-detail-media" key={`${record.key}-${mediaKey(media)}`}>
                        {isVideoMedia(media) && imageUrl(media) ? (
                          <video
                            src={imageUrl(media)}
                            controls
                            playsInline
                            preload="metadata"
                            onLoadedMetadata={(event) =>
                              pauseVideoAtEvidenceFrame(media, { target: event.currentTarget })
                            }
                          ></video>
                        ) : imageUrl(media) ? (
                          <button type="button" onClick={() => onOpenImage?.(media)}>
                            <img src={imageUrl(media)} alt={media.file_name || media.media_id} loading="lazy" />
                          </button>
                        ) : (
                          <span className="image-empty">媒体文件未能解析</span>
                        )}
                        <small>{media.file_name || media.media_id || media.asset_id}</small>
                      </div>
                    ))}
                    {!record.media.length ? (
                      <p className="muted small">该节点没有保存可展示的媒体引用。</p>
                    ) : null}
                  </div>
                </section>
                <section className="chain-detail-section">
                  <h4>
                    结构化语句 / 观察 <small>{record.statements.length} 项</small>
                  </h4>
                  <div className="chain-detail-statements">
                    {record.statements.map((statement) => (
                      <article key={`${record.key}-${statement.label}-${statement.value}`}>
                        <b>{statement.label}</b>
                        <p>{statement.value}</p>
                      </article>
                    ))}
                    {!record.statements.length ? (
                      <p className="muted small">没有保存文字观察或回答声明。</p>
                    ) : null}
                  </div>
                </section>
                <section className="chain-detail-section">
                  <h4>
                    工具调用与返回 <small>{record.traceRows.length} 项</small>
                  </h4>
                  {record.traceRows.map((row) => (
                    <details className="chain-trace-row" key={`${record.key}-${row.label}`}>
                      <summary>{row.label}</summary>
                      <pre>{row.value}</pre>
                    </details>
                  ))}
                  {!record.traceRows.length ? <p className="muted small">该节点没有保存工具调用轨迹。</p> : null}
                </section>
                <section className="chain-detail-section">
                  <h4>已保存的执行过程 / 证据归因</h4>
                  <p className="chain-detail-disclaimer">
                    以下只展示系统实际保存的 execution trace、Agent 证据账本和 grounding
                    信息，不把未保存的模型隐藏思维链伪装成可复现推理。
                  </p>
                  <pre className="chain-reasoning-pre">{record.reasoning}</pre>
                </section>
              </div>
            </details>
          ))}
          {!chainDetail.records.length ? (
            <p className="muted small">当前页没有成功读取到可展开的 QA 记录，请先确认 QA 接口可用。</p>
          ) : null}
        </div>
      </section>
    </div>,
    document.body,
  );
}

/**
 * 记忆层级关系图。逐行照搬 1.0 `services/photobench/frontend/src/App.vue`（只读权威）：
 *
 *   - 树状证据链 `:3157-3172`（`memory-tree-diagram`）；
 *   - 字段证据明细图 `:3249-3260`（`memory-graph-caption` + `memory-evidence-graph`）。
 *
 * 两块渲染的是**同一份** `activeEvidenceGraphView`（1.0 `:1957`），只是节点排布不同
 * （纵向层级 / 横向泳道）。1.0 里它们分处两地——中间隔着 `memory-effectiveness-panel`
 * （`:3173-3248`）——所以这里用 `variant` 区分，节点标记不复制两份。
 */
import { Fragment } from "react";

import type { GraphLayer, GraphNode } from "../../lib/graphs.ts";

export interface EvidenceTreeProps {
  /** 1.0 `activeEvidenceGraphView`（`lib/graphs.ts:activeEvidenceGraphView`）的结果。 */
  layers: GraphLayer[];
  /** 1.0 `@click="openChainNode(chainMode, layer, node)"` 的绑定半；网络副作用留在组合层。 */
  onOpenNode: (layer: GraphLayer, node: GraphNode) => void;
  /** 1.0 `:3157` 的树状图（`tree`）或 `:3250` 的字段证据明细图（`graph`）。 */
  variant?: "tree" | "graph";
}

export function EvidenceTree({ layers, onOpenNode, variant = "tree" }: EvidenceTreeProps) {
  if (variant === "graph") {
    return (
      <>
        <div className="memory-graph-caption">字段证据明细（与上方树状关系图对应）</div>
        <div className="memory-evidence-graph" role="img" aria-label="L0 到 L4+ 逐层证据链图">
          {layers.map((layer, layerIndex) => (
            <div className={`memory-graph-lane memory-graph-${layer.color}`} key={layer.key}>
              <div className="memory-graph-label">
                <b>{layer.key}</b>
                <strong>{layer.name}</strong>
                <span>{layer.stage}</span>
              </div>
              <div className="memory-graph-track">
                {layer.nodes.map((node) => (
                  <button
                    key={`${layer.key}-${node.kind}`}
                    type="button"
                    className="memory-graph-node"
                    onClick={() => onOpenNode(layer, node)}
                  >
                    <small>{node.kind}</small>
                    <b>{node.value}</b>
                    <span>{node.evidence}</span>
                  </button>
                ))}
              </div>
              {layerIndex < layers.length - 1 ? (
                <div className="memory-graph-bridge">
                  <i aria-hidden="true">↓</i>
                  <span>{layer.transition}</span>
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </>
    );
  }
  return (
    <div className="memory-tree-diagram" aria-label="记忆层级树状关系图">
      <div className="memory-tree-title">
        <strong>层级关系图 / 树状证据链</strong>
        <span>从原始媒体向上追踪到回答；每个彩色节点都可以点击展开证据</span>
      </div>
      <div className="memory-tree-levels">
        {layers.map((layer, layerIndex) => (
          <Fragment key={`tree-${layer.key}`}>
            <div className={`memory-tree-level memory-tree-${layer.color}`}>
              <div className="memory-tree-level-label">
                <b>{layer.key}</b>
                <strong>{layer.name}</strong>
              </div>
              <div className="memory-tree-nodes">
                {layer.nodes.map((node) => (
                  <button
                    key={`tree-${layer.key}-${node.kind}`}
                    type="button"
                    className="memory-tree-node"
                    onClick={() => onOpenNode(layer, node)}
                  >
                    <small>{node.kind}</small>
                    <b>{node.value}</b>
                    <span>{node.evidence}</span>
                  </button>
                ))}
              </div>
            </div>
            {layerIndex < layers.length - 1 ? (
              <div className="memory-tree-link" aria-hidden="true">
                <i></i>
                <span>↓ {layer.transition}</span>
              </div>
            ) : null}
          </Fragment>
        ))}
      </div>
    </div>
  );
}

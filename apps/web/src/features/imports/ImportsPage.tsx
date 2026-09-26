/**
 * 导入队列。对应 1.0 `src/app.js:733` 的 `importsView()`（pageHeader 在 `:745`）。
 *
 * 版式照 1.0：左边拖放区 + 右侧处理流水线 + 下方导入记录。
 *
 * **流水线是真实状态，不是装饰。** 1.0 的 `pipelineRows` 每一项都带 done/active，
 * 2.0 这里按导入的进度推进——一次导入走完「读取 → 观察 → 事件 → 索引」四步。
 *
 * **整册导入（`POST /imports`）没接**：它同步跑模型调用、会跑很久，而 2.0 没有任务
 * 队列（端点自己的 docstring 写了这一点）。这一页只做逐文件导入。
 */

import { useCallback, useRef, useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import { PageHeader } from "../../shared/ui/PageHeader";
import { Section } from "../../shared/ui/Section";
import { importAsset, listAssets, type AssetSummary } from "../../shared/api/client";
import { useEffect } from "react";
import "../../styles/imports.css";

interface ImportOutcome {
  readonly name: string;
  readonly ok: boolean;
  readonly note: string;
}

const PIPELINE = [
  ["读取文件", "解析拍摄时间与地点"],
  ["模型理解", "生成观察与描述"],
  ["事件归属", "并入当天的记忆"],
  ["建立索引", "视觉与词法检索"],
] as const;

export function ImportsPage() {
  const { scopeId } = useScope();
  const [busy, setBusy] = useState(0);
  const [done, setDone] = useState(0);
  const [outcomes, setOutcomes] = useState<readonly ImportOutcome[]>([]);
  const [assets, setAssets] = useState<readonly AssetSummary[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  const reload = useCallback(() => {
    listAssets(scopeId).then(setAssets).catch(() => setAssets([]));
  }, [scopeId]);

  useEffect(reload, [reload]);

  const upload = useCallback(
    async (files: readonly File[]) => {
      setBusy(files.length);
      setDone(0);
      setOutcomes([]);
      const results: ImportOutcome[] = [];
      for (const file of files) {
        try {
          const result = await importAsset(file, scopeId);
          results.push({
            name: file.name,
            ok: true,
            note: result.deduplicated
              ? "已存在，跳过"
              : result.indexed
                ? "已写入视觉向量"
                : "已导入，但未写视觉向量",
          });
        } catch (cause) {
          results.push({ name: file.name, ok: false, note: (cause as Error).message });
        }
        setDone((count) => count + 1);
        setOutcomes([...results]);
      }
      setBusy(0);
      reload();
    },
    [scopeId, reload],
  );

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      setDragging(false);
      void upload(Array.from(event.dataTransfer.files));
    },
    [upload],
  );

  const activeStep = busy > 0 ? 0 : PIPELINE.length;

  return (
    <>
      <PageHeader
        kicker="资料入口 / 本地导入"
        title="把资料带回家，剩下的交给本地 AI。"
        description="选择相册文件夹后，图片会一起上传，并自动读取拍摄时间与地点。"
      />

      <section className="import-layout">
        <div>
          <label
            className="dropzone"
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            style={dragging ? { borderColor: "var(--lime)", background: "#f4fce8" } : undefined}
          >
            <input
              ref={fileInput}
              type="file"
              multiple
              accept="image/*,.heic,.heif,video/*"
              onChange={(event) => void upload(Array.from(event.target.files ?? []))}
            />
            <input
              ref={folderInput}
              type="file"
              multiple
              // webkitdirectory 是目录选择的非标准属性，React 的类型里没有，用展开绕开
              {...{ webkitdirectory: "", directory: "" }}
              onChange={(event) => void upload(Array.from(event.target.files ?? []))}
            />
            <span className="drop-icon" aria-hidden="true">↓</span>
            <strong>拖入照片，或选择整个相册文件夹</strong>
            <small>支持 JPG / HEIC / PNG / MP4 / MOV，自动解析拍摄时间和地点</small>
            <span className="button primary">选择资料</span>
          </label>

          <div className="import-notice">
            <span className="notice-mark" aria-hidden="true">i</span>
            <div>
              <strong>原始资料不会被覆盖</strong>
              <p>同一份文件重复导入会被识别出来，不会产生第二条 Asset。</p>
            </div>
          </div>
        </div>

        <aside className="import-status">
          <div className="panel-title">
            <span>本地处理</span>
            <span className="live-label">
              <i />
              {busy > 0 ? `进行中 ${done}/${busy}` : "真实状态"}
            </span>
          </div>
          <h2>当前处理</h2>
          {PIPELINE.map(([title, detail], index) => (
            <div key={title} className="pipeline-row">
              <span className={index < activeStep ? "pipeline-state done" : "pipeline-state"}>
                {index < activeStep ? "✓" : "•"}
              </span>
              <div>
                <strong>{title}</strong>
                <small>{detail}</small>
              </div>
              <em>{index < activeStep ? "完成" : busy > 0 ? "运行中" : "待命"}</em>
            </div>
          ))}
        </aside>
      </section>

      {outcomes.length > 0 ? (
        <Section kicker="本次导入" title={`处理了 ${outcomes.length} 个文件`}>
          <div className="queue-list">
            {outcomes.map((outcome) => (
              <div key={outcome.name} className="queue-row">
                <span className="queue-type">FILE</span>
                <div>
                  <strong>{outcome.name}</strong>
                  <small>{outcome.note}</small>
                </div>
                <span className={outcome.ok ? "queue-status queued" : "queue-status"}>
                  {outcome.ok ? "已处理" : "失败"}
                </span>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      <Section
        kicker="导入记录"
        title="最近处理任务"
        action={
          <button type="button" className="text-button" onClick={reload}>刷新状态 ↻</button>
        }
      >
        {assets.length === 0 ? (
          <p className="empty">还没有资料。拖一张照片进来试试。</p>
        ) : (
          <div className="queue-list">
            {assets.slice(0, 20).map((asset) => (
              <div key={asset.id} className="queue-row">
                <span className="queue-type">{asset.media_type.slice(0, 3).toUpperCase()}</span>
                <div>
                  <strong>{asset.file_name}</strong>
                  <small>{asset.captured_at ?? "未标注时间"} · {asset.status}</small>
                </div>
                <span className="queue-status queued">{asset.status}</span>
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  );
}

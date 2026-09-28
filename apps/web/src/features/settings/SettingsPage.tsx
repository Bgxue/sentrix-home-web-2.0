/**
 * 设备与隐私。对应 1.0 `src/app.js:756` 的 `settingsView()`（pageHeader 在 `:771`）。
 *
 * 1.0 这一页有四块：健康网格、OCR 设置、待确认事实、审计动作。**2.0 只做得来两块**——
 * OCR 设置要 `/settings/ocr`、待确认事实要 `/facts`，两个端点都没有。审计动作里
 * 1.0 有三条，2.0 现在有对应端点的是「重新检查失败任务」这一条。
 */

import { useCallback, useEffect, useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import { PageHeader } from "../../shared/ui/PageHeader";
import { Section } from "../../shared/ui/Section";
import { StatCard } from "../../shared/ui/StatCard";
import {
  fetchHealth,
  reclusterFaces,
  type HealthResponse,
  type ReclusterFacesResponse,
} from "../../shared/api/client";
import "../../styles/settings.css";

export function SettingsPage() {
  const { scopeId, spaces } = useScope();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ReclusterFacesResponse | null>(null);
  const scopeName = spaces.find((space) => space.id === scopeId)?.name ?? "全部相册";

  useEffect(() => {
    let cancelled = false;
    fetchHealth()
      .then((value) => !cancelled && setHealth(value))
      .catch((cause: Error) => !cancelled && setError(cause.message));
    return () => {
      cancelled = true;
    };
  }, []);

  const recluster = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      // 当前范围为空（全部相册）时传 null：那是**跨空间**重算，不是「某个默认空间」。
      setResult(await reclusterFaces(scopeId === "" ? null : scopeId));
    } catch (cause) {
      setError((cause as Error).message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  }, [scopeId]);

  return (
    <>
      <PageHeader
        kicker="系统 / 本地状态"
        title="你的记忆，运行在自己的家里。"
        description="服务、模型、存储状态都来自当前本地后端。"
      />
      {error ? <p className="error">{error}</p> : null}

      <section className="stats-grid">
        <StatCard label="本地服务" value={health?.status ?? "—"} note="GET /health" accent />
        <StatCard label="数据库" value={health?.database ?? "—"} note="SQLite" />
        <StatCard label="版本" value={health?.version ?? "—"} note="Sentrix Home API" />
      </section>

      <Section kicker="隐私边界" title="数据只在本地流动">
        <div className="privacy-list">
          <div><span>原始媒体</span><b>本地存储</b></div>
          <div><span>人物特征</span><b>本地处理</b></div>
          <div><span>记忆索引</span><b>本地实体与向量检索</b></div>
        </div>
      </Section>

      <Section kicker="审计入口" title="可操作的系统动作">
        <div className="audit-list">
          <div>
            <button type="button" className="button small ghost" disabled={busy} onClick={() => void recluster()}>
              {busy ? "重算中…" : "全局重算人脸聚类 →"}
            </button>
            <small>
              当前范围：{scopeName}。这是**显式维护入口**，不在导入链上——正常导入不需要它，
              它的用途是换过阈值或修过数据之后整表重算。
            </small>
          </div>
        </div>
        {result ? (
          <div className="audit-result">
            <strong>重算完成</strong>
            <span>实例 {result.instances} · 簇 {result.clusters} · 噪声 {result.noise}</span>
            <span>
              认领不确定 {result.attached_uncertain} · 撤下簇 {result.retired_clusters} ·
              撤下候选 {result.retired_candidates}
            </span>
            <small>口径：阈值 {result.threshold} / 最低质量 {result.minimum_quality}</small>
          </div>
        ) : null}
        <div className="audit-list">
          <div>
            <button type="button" className="button small ghost" disabled title="2.0 没有 /settings/ocr 端点">
              OCR 设置
            </button>
            <small>尚未开放：2.0 没有 <code>/settings/ocr</code> 端点。</small>
          </div>
          <div>
            <button type="button" className="button small ghost" disabled title="2.0 没有 /facts 端点">
              待确认事实
            </button>
            <small>尚未开放：2.0 没有 <code>/facts</code> 端点。</small>
          </div>
        </div>
      </Section>
    </>
  );
}

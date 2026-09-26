/** 设备与隐私。对应 1.0 `src/app.js:756` 的 `settingsView()`（pageHeader 在 `:771`）。 */

import { useEffect, useState } from "react";

import { PageHeader } from "../../shared/ui/PageHeader";
import { Section } from "../../shared/ui/Section";
import { StatCard } from "../../shared/ui/StatCard";
import { fetchHealth, type HealthResponse } from "../../shared/api/client";

export function SettingsPage() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchHealth()
      .then((value) => !cancelled && setHealth(value))
      .catch((cause: Error) => !cancelled && setError(cause.message));
    return () => {
      cancelled = true;
    };
  }, []);

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
    </>
  );
}

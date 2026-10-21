/**
 * 「实时资源遥测」卡。逐行照搬 1.0 `App.vue:3289-3330` 的模板与 `:1027-1125`
 * `renderTelemetryChart` 的 option 组装，**但生命周期不照搬**。
 *
 * 1.0 的坑（报告原文）：`renderTelemetryChart` 靠 `<details @toggle>` + `nextTick`
 * 在展开时重画 + `resize()`，否则折叠态下容器是 0×0，曲线只画在左上角一小条。
 * React 这边的落点：
 *   - `init`    —— effect 里，容器与数据都在时建**一次**，之后复用 `chartRef`
 *                  （1.0 `:1036` 的 `telemetryChartInstance ||= echarts.init(...)`）；
 *   - `setOption` —— 同一个 effect 里，`run` / 图表数据变化时重画（1.0 `:2834` 的 deep watch）；
 *   - `resize`  —— 三种时机：① 每次 setOption 之后（1.0 `:1124`）；② `<details>` 原生
 *                  `onToggle` 展开时（1.0 `:2824-2830`，React 19 的原生 `onToggle`）；
 *                  ③ 容器尺寸变化（`ResizeObserver`，1.0 靠的是重画 + 手动 resize）；
 *   - `dispose` —— 图表数据变 `null` 时（1.0 `:1029-1033`）与组件卸载时（1.0 `:2837`）。
 *
 * 数据形状来自 `lib/telemetry.ts:telemetryChart`（只给 `{ history }`），option 由本组件组装。
 */
import { useEffect, useMemo, useRef } from "react";
import type { SyntheticEvent } from "react";
import * as echarts from "echarts";
import type { EChartsOption, EChartsType } from "echarts";

import type { Run } from "../../../api/types.ts";
import { EXECUTION_PHASES } from "../../../lib/constants.ts";
import { fmtMemory } from "../../../lib/format.ts";
import { jetsonUnifiedFromRun } from "../../../lib/telemetry.ts";
import {
  liveTelemetryPhaseRows,
  liveTelemetryRows,
  telemetryChart,
  telemetryLiveState,
  telemetryProcessList,
  telemetrySampleCount,
} from "../../../lib/telemetry.ts";

type Dict = Record<string, any>;

/** 1.0 的可选链语义：非对象一律当空对象。 */
function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

/** Vue `{{ }}` 的等价：`null` / `undefined` 渲染空串。 */
function vtext(value: unknown): string {
  return value == null ? "" : String(value);
}

interface TelemetrySeriesDefinition {
  key: string;
  name: string;
  color: string;
}

/** 1.0 `:1041-1060`。按数据源三选一。 */
function telemetrySeriesDefinitions(
  source: string,
  unifiedOrin = false,
): TelemetrySeriesDefinition[] {
  const isApple = source === "apple_unified";
  const isOrin = ["orin_ssh_pss", "jetson_local_pss"].includes(source);
  if (isApple) {
    return [
      { key: "system_memory_used_mib", name: "整机内存（App+Wired+Compressed）", color: "#20a36a" },
      { key: "product_stack_memory_mib", name: "整套产品内存（相关进程 footprint+权重）", color: "#d9488b" },
      { key: "sentrix_stack_pss_mib", name: "Sentrix 周边（不含主模型，footprint+权重）", color: "#8b6de8" },
      { key: "model_process_system_memory_used_mib", name: "主模型进程占用（footprint+权重映射）", color: "#f2b84b" },
      // 1.0 `:1063-1069` 的第五条。它是 **AGXAccelerator 设备级**的 working set，
      // 含其他进程，不能归因给主模型——所以单独一条、不并入上面四条。
      { key: "gpu_in_use_unified_memory_mib", name: "全机 GPU 在用统一内存", color: "#e05a4f" },
    ];
  }
  if (isOrin) {
    return [
      { key: "system_memory_used_mib", name: "整机 RAM（Orin UMA）", color: "#20a36a" },
      { key: "product_stack_memory_mib", name: "整套产品 RAM（相关进程 PSS + GPU/UMA 页）", color: "#d9488b" },
      { key: "sentrix_stack_pss_mib", name: "Sentrix 周边 PSS（不含主模型，含嵌入/辅助）", color: "#8b6de8" },
      {
        key: "model_process_system_memory_used_mib",
        // 1.0 `:1074`：口径由 run 的 `memory_profile.scope` 决定，不是写死 VmRSS。
        name: unifiedOrin ? "主模型占用（smaps Rss + GPU/UMA 页）" : "主模型 VmRSS",
        color: "#f2b84b",
      },
    ];
  }
  return [
    { key: "memory_used_mib", name: "整卡 GPU 显存", color: "#4f7cff" },
    { key: "system_memory_used_mib", name: "整机 RAM", color: "#20a36a" },
    { key: "benchmark_process_gpu_memory_mib", name: "测评系统 GPU 显存", color: "#d9488b" },
    { key: "product_stack_memory_mib", name: "整套产品 RAM（相关进程 PSS + GPU/UMA 页）", color: "#8b6de8" },
    { key: "sentrix_stack_pss_mib", name: "Sentrix 周边 PSS（不含主模型，含嵌入/辅助）", color: "#a78bfa" },
    { key: "model_process_memory_used_mib", name: "主模型 GPU 显存（nvidia-smi 按 PID）", color: "#ef8a4b" },
    { key: "model_process_system_memory_used_mib", name: "主模型 RAM（进程 smaps Rss）", color: "#f2b84b" },
  ];
}

/** 1.0 `:1061-1065`。注意 `Number(null) === 0` 是有限数——1.0 的 null 会被当成 0 画出来。 */
function seriesValue(item: Dict, definition: TelemetrySeriesDefinition): number | null {
  const primary = Number(item[definition.key]);
  if (Number.isFinite(primary)) return primary;
  return null;
}

/** 1.0 `:1070-1096` 的 option 组装（`setOption` 之外的部分逐行照搬）。 */
function buildTelemetryOption(run: Run | null | undefined, history: Dict[]): EChartsOption {
  const r = dict(run);
  const source =
    telemetryLiveState(run).source || r.telemetry_source || dict(dict(r.phases).gpu_metrics).source || "";
  const definitions = telemetrySeriesDefinitions(source, jetsonUnifiedFromRun(run));
  const available = definitions.filter((definition) =>
    history.some((item) => seriesValue(item, definition) != null),
  );
  const firstTimestamp = Number(history[0]?.t);
  const labels = history.map((item, index) => {
    const elapsed = Number(item?.t) - firstTimestamp;
    return Number.isFinite(elapsed) ? `+${(elapsed / 60).toFixed(1)} min` : `#${index + 1}`;
  });
  interface PhaseAreaMark {
    xAxis: number;
    name?: string;
    itemStyle?: { color: string };
  }
  // 1.0 `:1080-1096`：每次阶段切换压一对 `[起点, 终点]` 的 markArea 数据。
  const phaseAreas: Array<[PhaseAreaMark, PhaseAreaMark]> = [];
  let phaseStart = 0;
  for (let index = 1; index <= history.length; index += 1) {
    const previous = history[index - 1]?.phase || "unassigned";
    const current = index < history.length ? history[index]?.phase || "unassigned" : null;
    if (current !== previous) {
      const label =
        history[index - 1]?.phase_label ||
        EXECUTION_PHASES.find((item) => item.key === previous)?.label ||
        previous;
      phaseAreas.push([
        {
          xAxis: phaseStart,
          name: label,
          itemStyle: {
            color: `rgba(${["79,124,255", "32,163,106", "239,138,75", "217,72,139", "139,109,232"][phaseAreas.length % 5]},.07)`,
          },
        },
        { xAxis: Math.max(phaseStart, index - 1) },
      ]);
      phaseStart = index;
    }
  }
  return {
    animation: false,
    color: available.map((item) => item.color),
    grid: { left: 72, right: 28, top: 74, bottom: 64 },
    legend: {
      top: 10,
      left: 12,
      right: 12,
      type: "scroll",
      selectedMode: "multiple",
      itemGap: 16,
      textStyle: { color: "#59627c", fontSize: 11 },
    },
    tooltip: {
      trigger: "axis",
      triggerOn: "mousemove",
      confine: true,
      appendToBody: true,
      axisPointer: { type: "cross", label: { backgroundColor: "#667085" } },
      formatter(params: unknown) {
        const rows = (Array.isArray(params) ? params : [params]).map(dict).filter((item) => item.value != null);
        const title = vtext(rows[0]?.axisValueLabel);
        const phase = history[Number(rows[0]?.dataIndex)]?.phase_label || history[Number(rows[0]?.dataIndex)]?.phase;
        return `<b>${title}</b>${phase ? `<br/><span>阶段：${phase}</span>` : ""}<br/>${rows.map((item) => `${vtext(item.marker)}${vtext(item.seriesName)}: <b>${Number(item.value).toFixed(2)} GiB</b>`).join("<br/>")}`;
      },
    },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: labels,
      axisLabel: { color: "#7a849e", hideOverlap: true },
      axisLine: { lineStyle: { color: "#d8deea" } },
    },
    yAxis: {
      type: "value",
      name: "GiB",
      nameTextStyle: { color: "#7a849e", padding: [0, 0, 8, 0] },
      min: 0,
      axisLabel: { color: "#7a849e", formatter: (value) => `${value} GiB` },
      splitLine: { lineStyle: { color: "#edf0f5" } },
    },
    dataZoom: [
      { type: "inside", filterMode: "none" },
      {
        type: "slider",
        height: 18,
        bottom: 14,
        borderColor: "#d8deea",
        fillerColor: "rgba(79,124,255,.14)",
        handleStyle: { color: "#4f7cff" },
      },
    ],
    series: available.map((definition) => ({
      name: definition.name,
      type: "line" as const,
      smooth: 0.18,
      showSymbol: false,
      connectNulls: false,
      lineStyle: { width: 2.5 },
      emphasis: { focus: "series" as const, lineStyle: { width: 4 } },
      data: history.map((item) => {
        const value = seriesValue(item, definition);
        return value == null ? null : value / 1024;
      }),
      markArea:
        definition === available[0] && phaseAreas.length
          ? {
              silent: true,
              label: { show: true, position: "insideTop" as const, color: "#59627c", fontSize: 10 },
              data: phaseAreas,
            }
          : undefined,
    })),
  };
}

export interface TelemetryCardProps {
  run: Run | null | undefined;
}

export function TelemetryCard({ run }: TelemetryCardProps) {
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<EChartsType | null>(null);
  const sampleCount = telemetrySampleCount(run);
  const live = telemetryLiveState(run);
  const rows = liveTelemetryRows(run);
  const phaseRows = liveTelemetryPhaseRows(run);
  const processes = telemetryProcessList(run);
  const chartData = useMemo(() => telemetryChart(run), [run]);
  // 轮询会每 2 秒换一个 run 对象，但历史点可能没变。用「点数 + 首末时间 + 相位」
  // 做数据指纹：没变就不 setOption。否则 notMerge 会重建图表，鼠标悬浮的 tooltip
  // 会在下一次轮询时被清掉（158 上 active_count 被孤儿 run 占住时尤其明显）。
  const chartKey = useMemo(() => {
    if (!chartData) return "";
    const history = chartData.history;
    const first = history[0] || {};
    const last = history[history.length - 1] || {};
    return `${history.length}:${first.t ?? ""}:${last.t ?? ""}:${last.phase ?? ""}`;
  }, [chartData]);
  const lastChartKeyRef = useRef<string>("");

  useEffect(() => {
    const element = canvasRef.current;
    if (!element || !chartData) {
      // 1.0 `:1029-1033`：没有容器或数据不足 2 点时销毁实例。
      chartRef.current?.dispose();
      chartRef.current = null;
      lastChartKeyRef.current = "";
      return;
    }
    const chart = chartRef.current ?? echarts.init(element);
    chartRef.current = chart;
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => {
            chartRef.current?.resize();
          });
    observer?.observe(element);
    if (lastChartKeyRef.current === chartKey) {
      // 数据没变，只保证尺寸正确；不要 setOption，否则 tooltip 会被重置。
      chart.resize();
      return () => observer?.disconnect();
    }
    chart.setOption(buildTelemetryOption(run, chartData.history), true);
    lastChartKeyRef.current = chartKey;
    chart.resize();
    return () => observer?.disconnect();
  }, [chartData, run]);

  // 1.0 `onUnmounted`（`:2837`）的 `telemetryChartInstance.dispose()`。
  useEffect(
    () => () => {
      chartRef.current?.dispose();
      chartRef.current = null;
    },
    [],
  );

  /** 1.0 `:2824-2830`：展开时容器才有尺寸，必须 resize，否则曲线只画在左上角一条。 */
  function handleToggle(event: SyntheticEvent<HTMLDetailsElement>) {
    if (!event.currentTarget.open) return;
    chartRef.current?.resize();
  }

  if (!sampleCount) return null;
  return (
    <details
      open
      className="phase-card result-phase-card gpu-result-card live-telemetry-card"
      onToggle={handleToggle}
    >
      <summary className="phase-title">
        <b>实时资源遥测</b>
        <span className={`phase-status ${live.status === "running" ? "running" : "completed"}`}>
          {live.status === "running" ? "实时更新中" : "已停止"}
        </span>
      </summary>
      <p className="metric-calc-time">
        测评进行中持续采样；任务失败或取消时保留已采集的最后值与峰值。
        {live.source === "apple_unified"
          ? " Mac 统一内存。五条曲线是整机内存、整套产品占用（footprint+权重映射）、Sentrix 周边、主模型占用（footprint+权重映射），以及 AGXAccelerator 设备级的 GPU 在用统一内存（含其他进程，不能归因给主模型）。"
          : ["jetson_local_pss", "orin_ssh_pss"].includes(String(live.source))
            ? jetsonUnifiedFromRun(run)
              ? " Orin 无独立显存。主指标看黄线「主模型占用（smaps Rss + GPU/UMA 页）」——主机页与 GPU 页分开算、两半不重叠。"
              : " Orin 无独立显存。主指标看黄线 VmRSS（进程驻留）。"
            : " 153 使用 NVIDIA GPU 显存；整机 RAM 为宿主机全部进程。"}
      </p>
      <div className="phase-metrics live-telemetry-metrics">
        {rows.map((row) => (
          <div key={row[0]} className="phase-metric">
            <span>{row[0]}</span>
            <strong>{row[1]}</strong>
            <small>{row[2]}</small>
          </div>
        ))}
      </div>
      {chartData ? (
        <div className="telemetry-chart">
          <div ref={canvasRef} className="telemetry-chart-canvas" role="img" aria-label="资源占用趋势"></div>
          <small className="muted">
            完整测评过程，共 {sampleCount} 个采样点；曲线按全时段抽稀绘制，峰值来自全部采样。悬浮查看时间、阶段和各项
            GiB，图例可隐藏曲线，底部可缩放。曲线只绘制实际采集到的数据，不用 0 填充缺失指标。
          </small>
        </div>
      ) : null}
      <details className="metric-definition-panel telemetry-definition-panel">
        <summary>资源曲线口径说明</summary>
        <div className="metric-definition-row">
          <strong>RAM 与 GPU 显存</strong>
          <span>
            <b>口径：</b>RAM 是 Linux 主机系统内存；153 的 GPU 显存是 NVIDIA 独立显卡 VRAM；118 Orin 是
            CPU/GPU 共用的 UMA，不显示独立 GPU 显存。两者不能直接相加成“模型总占用”。
          </span>
          <span>
            <b>判读：</b>153 主要看按模型 PID 归因的 GPU 显存；Orin（118/46）主要看主模型 smaps Rss + GPU/UMA 页、PSS 和产品栈
            RAM；旧 run 没有拆分字段时才看 VmRSS。任一指标无法按自身口径采集时留空，不用其它指标替代。
          </span>
        </div>
        <div className="metric-definition-row">
          <strong>整机 RAM</strong>
          <span>
            <b>计算式：</b>主机 MemTotal - MemAvailable，范围是整台运行模型服务主机的全部进程和内核可用内存。
          </span>
          <span>
            <b>判读：</b>它不是主模型或产品栈归因值，不能用来替代主模型 RAM、PSS 或 GPU 显存。
          </span>
        </div>
        <div className="metric-definition-row">
          <strong>主模型 RAM</strong>
          <span>
            <b>计算式：</b>新口径为主机 smaps Rss + GPU/UMA 页（GPU 侧取 max(Σ nvmap 按 PID, VmRSS − smaps Rss)）；旧 Orin run 只有 VmRSS 时按 VmRSS 显示。两部分不重叠，不能把 PSS 当替代值。
          </span>
          <span>
            <b>含义：</b>包含 Python/vLLM 或 llama.cpp runtime、tokenizer、调度结构、mmap
            页、共享库和 CUDA 用户态开销等；不表示模型权重在 RAM 里又完整复制了一份。smaps Rss
            缺失时不改用 PSS 或其它字段补写。
          </span>
        </div>
        <div className="metric-definition-row">
          <strong>整套产品 RAM</strong>
          <span>
            <b>算法：</b>所有相关进程（周边进程 + 主模型服务）的 PSS 加总，再加主模型 GPU/UMA 页（nvmap 按 PID，账本不可读时才用 VmRSS − smaps Rss 兜底）；任一输入缺失则不记录。
          </span>
          <span>
            <b>判读：</b>PSS 用于避免共享页重复归因；GPU/UMA 页补的是 PSS 看不见的设备侧内存，不是「VmRSS 减 smaps」的记账差额。该值不是整机 RAM，也不能与整卡显存相加。只有记录 scope 为{" "}
            <code>all_related_pss_plus_model_uma_extra</code> 时才按此公式解释；历史 run 缺少该
            scope 时必须重跑。
          </span>
        </div>
        <div className="metric-definition-row">
          <strong>GPU 三层口径（仅 153）</strong>
          <span>
            <b>主模型 GPU 显存：</b>nvidia-smi 按主模型 PID 汇总；无法归因则留空。
            <b>测评系统 GPU 显存：</b>按 PhotoBench/Sentrix 相关 GPU compute 进程 PID
            汇总，不等于主模型 GPU 显存，也不能与其简单相加。<b>整卡显存：</b>nvidia-smi 的 device
            memory.used，包含整卡所有进程、驱动和 CUDA 上下文。
          </span>
          <span>
            <b>vLLM：</b>主模型 GPU 显存通常包含权重、预分配 KV 池和运行时 buffer，受
            gpu_memory_utilization 影响，可能高于请求时真实活跃 KV。llama.cpp/Ollama
            独显环境同样按进程 PID 采集。
          </span>
        </div>
        <div className="metric-definition-row">
          <strong>Orin UMA</strong>
          <span>
            <b>口径：</b>Orin 的 CPU/GPU 共用物理内存，没有可与 153 VRAM 直接对应的独立显存曲线。
          </span>
          <span>
            <b>判读：</b>页面只展示真实能采到的整机 RAM、整套产品 RAM、Sentrix 周边 PSS、主模型 smaps Rss + GPU/UMA 页（旧 run 为 VmRSS/PSS）；不要把 Orin 的统一内存和 153 的 NVIDIA 显存做数值横比。
          </span>
        </div>
        <div className="metric-definition-row">
          <strong>Mac 统一内存</strong>
          <span>
            <b>整机：</b>vm_stat 的 active + wired + compressor，不含文件缓存。<b>主模型 / Sentrix 周边 /
            整套产品：</b>进程占用 = phys_footprint + 已映射的模型权重文件。它是进程提交占用，不是 GPU
            专属显存；整套产品是周边加主模型，不再加 UMA 补偿。
          </span>
          <span>
            <b>GPU：</b>AGXAccelerator 的在用/已分配统一内存是设备级，不能归因给主模型。温度和功耗来自
            macmon；缺失时留空。
          </span>
        </div>
        <div className="metric-definition-row">
          <strong>远程模型服务</strong>
          <span>
            <b>口径：</b>如果模型 endpoint 不是当前主机的 127.0.0.1、localhost、::1
            或本机局域网地址，不记录当前主机的实时 RAM/GPU 遥测来代表远程模型。
          </span>
          <span>
            <b>判读：</b>请求时延和 token 指标仍可记录；硬件指标必须来自模型实际运行主机，否则留空。
          </span>
        </div>
      </details>
      {phaseRows.length ? (
        <div className="phase-metrics">
          {phaseRows.map((row) => (
            <div key={`live-${row[0]}`} className="phase-metric">
              <span>{row[0]}阶段峰值</span>
              <strong>{row[1]}</strong>
              <small>{row[2]}</small>
            </div>
          ))}
        </div>
      ) : null}
      {processes.length ? (
        <details className="telemetry-processes">
          <summary>GPU 进程明细（{processes.length} 个）</summary>
          <div className="telemetry-process-grid">
            {processes.map((process) => (
              <div className="telemetry-process-row" key={`${process.pid}-${process.process_name}`}>
                <span>{vtext(process.process_name) || "未知进程"}</span>
                <small>PID {vtext(process.pid)}</small>
                <b>{fmtMemory(process.used_memory_mib)}</b>
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </details>
  );
}

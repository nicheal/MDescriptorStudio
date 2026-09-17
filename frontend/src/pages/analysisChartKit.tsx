// Shared Plotly/table primitives for the Analysis result views. Kept separate
// from the individual module views so a module can live in its own file
// without importing the component that renders it (no import cycle).
import Plot from "../viz/ScientificPlot";
import type { Data, Layout, PlotMouseEvent } from "plotly.js";
import { Empty, Typography } from "antd";
import type { CSSProperties, ReactNode } from "react";

/** Plotly's scattergl (regl) generates its GL commands with `new Function` at chart
 * creation. When that is unavailable — no WebGL context (some WebView2 environments)
 * or a CSP without 'unsafe-eval' — Plotly shows a misleading "WebGL is not supported"
 * banner instead of the chart, so such environments get the SVG scatter downgrade. */
let webglSupported: boolean | null = null;
export function webglAvailable(): boolean {
  if (webglSupported === null) {
    try {
      const canvas = document.createElement("canvas");
      const hasGL = Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl") || canvas.getContext("experimental-webgl"));
      webglSupported = hasGL && new Function("return 1")() === 1;
    } catch {
      webglSupported = false;
    }
  }
  return webglSupported;
}

/** Plotly trace data with scattergl traces swapped to SVG scatter when WebGL is missing. */
export function plotData(data: Data[]): Data[] {
  if (webglAvailable()) return data;
  return data.map((trace) => (trace.type === "scattergl" ? { ...trace, type: "scatter" as const } : trace));
}

/** Property-colored scatter scale: Google Turbo sampled every 1/16 — high
 * contrast between adjacent values, unlike the low-separation Viridis. Inlined
 * because plotly.js 2.x ships no Turbo colorscale. */
export const HIGH_CONTRAST_COLORSCALE: Array<[number, string]> = [
  [0, "#30123B"],
  [0.0625, "#4040A2"],
  [0.125, "#466BE3"],
  [0.1875, "#4294FF"],
  [0.25, "#28BCEB"],
  [0.3125, "#18DDC2"],
  [0.375, "#32F298"],
  [0.4375, "#6DFE62"],
  [0.5, "#A4FC3C"],
  [0.5625, "#CBED34"],
  [0.625, "#ECD13A"],
  [0.6875, "#FDAE35"],
  [0.75, "#FB8122"],
  [0.8125, "#EC530F"],
  [0.875, "#D23105"],
  [0.9375, "#AC1701"],
  [1, "#7A0403"],
];

export function PlotFrame({ data, layout: plotLayout, ariaLabel, compact = false, onClick }: { data: Data[]; layout: Partial<Layout>; ariaLabel: string; compact?: boolean; onClick?: (index: number, curve: number) => void }) {
  return <div className={compact ? "analysis-purpose-chart compact" : "analysis-purpose-chart"} aria-label={ariaLabel}><Plot data={plotData(data)} layout={plotLayout} config={{ responsive: true, displaylogo: false, showSendToCloud: false, modeBarButtonsToRemove: ["toImage"] }} style={{ width: "100%", height: "100%" }} onClick={(event) => { const point = event.points?.[0]; if (point && typeof point.pointIndex === "number") onClick?.(point.pointIndex, point.curveNumber ?? 0); }} /></div>;
}

/** Plot wrapper used by the legacy Overview modules while they are migrated. */
export function OverviewPlot({ data, layout: plotLayout, ariaLabel, compact = false, className, style, onClick }: { data: Data[]; layout: Partial<Layout>; ariaLabel: string; compact?: boolean; className?: string; style?: CSSProperties; onClick?: (event: Readonly<PlotMouseEvent>) => void }) {
  const classes = ["analysis-overview-chart-frame", compact ? "compact" : "", className ?? ""].filter(Boolean).join(" ");
  return <div className={classes} style={style} aria-label={ariaLabel}><Plot data={plotData(data)} layout={plotLayout} config={{ responsive: true, displaylogo: false, showSendToCloud: false, modeBarButtonsToRemove: ["toImage"] }} style={{ width: "100%", height: "100%" }} onClick={onClick} /></div>;
}

/** Metric strip. `text` wins over `v` so counts and rounded values keep the
 * formatting the module chose instead of being re-formatted from a string. */
export function Metrics({ values }: { values: { k: string; v?: unknown; text?: string }[] }) {
  const visible = values.filter(({ v, text }) => text !== undefined || (v !== undefined && v !== null));
  return <div className="analysis-metric-strip">{visible.map(({ k, v, text }) => <div className="analysis-metric" key={k}><Typography.Text type="secondary">{k}</Typography.Text><Typography.Text strong>{text ?? fmt(v)}</Typography.Text></div>)}</div>;
}

export function NoData({ message }: { message: string }) {
  return <div className="analysis-overview-empty"><Empty description={message} /></div>;
}

export function ChartCaption({ children }: { children: ReactNode }) {
  return <Typography.Text type="secondary" className="analysis-chart-caption">{children}</Typography.Text>;
}

export function overviewLayout(overrides: Partial<Layout> = {}): Partial<Layout> {
  return {
    autosize: true,
    margin: { l: 64, r: 28, t: 18, b: 52 },
    paper_bgcolor: "#FFFFFF",
    plot_bgcolor: "#FFFFFF",
    font: { family: "Segoe UI, sans-serif", size: 12, color: "#424242" },
    ...overrides,
  };
}

export function layout(overrides: Partial<Layout> = {}): Partial<Layout> {
  return { autosize: true, margin: { l: 62, r: 24, t: 20, b: 52 }, paper_bgcolor: "#FFFFFF", plot_bgcolor: "#FFFFFF", font: { family: "Segoe UI, sans-serif", size: 11, color: "#424242" }, ...overrides };
}

export function nums(value: unknown): number[] {
  return Array.isArray(value) ? value.map(num).filter((item): item is number => item !== null) : [];
}

export function matrix(value: unknown): number[][] {
  return Array.isArray(value) ? value.map(nums).filter((row) => row.length) : [];
}

export function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null && !Array.isArray(item)) : [];
}

export function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

export function num(value: unknown): number | null {
  const parsed = typeof value === "number" || typeof value === "string" ? Number(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

export function fmt(value: unknown): string {
  const numeric = num(value);
  if (numeric !== null) return Math.abs(numeric) >= 1_000 ? numeric.toLocaleString(undefined, { maximumFractionDigits: 2 }) : numeric.toPrecision(5);
  if (typeof value === "string") return value;
  if (value == null) return "—";
  try { return JSON.stringify(value); } catch { return String(value); }
}

export function formatCount(value: unknown): string {
  const numeric = num(value);
  return numeric === null ? "—" : Math.round(numeric).toLocaleString();
}

export function formatPercent(value: unknown): string {
  const numeric = num(value);
  return numeric === null ? "—" : `${(numeric * 100).toFixed(2)}%`;
}

export function formatFixed(value: unknown, digits: number): string {
  const numeric = num(value);
  return numeric === null ? "—" : numeric.toFixed(digits);
}

export function quantile(values: number[], q: number): number | null {
  if (!values.length) return null;
  const sorted = values.slice().sort((left, right) => left - right);
  const position = (sorted.length - 1) * q;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  if (lower === upper) return sorted[lower];
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
}

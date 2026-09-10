// Shared Plotly/table primitives for the Analysis result views. Kept separate
// from the individual module views so a module can live in its own file
// without importing the component that renders it (no import cycle).
import Plot from "react-plotly.js";
import type { Data, Layout } from "plotly.js";
import { Empty, Typography } from "antd";

export function PlotFrame({ data, layout: plotLayout, ariaLabel, compact = false, onClick }: { data: Data[]; layout: Partial<Layout>; ariaLabel: string; compact?: boolean; onClick?: (index: number, curve: number) => void }) {
  return <div className={compact ? "analysis-purpose-chart compact" : "analysis-purpose-chart"} aria-label={ariaLabel}><Plot data={data} layout={plotLayout} config={{ responsive: true, displaylogo: false, modeBarButtonsToRemove: ["toImage"] }} style={{ width: "100%", height: "100%" }} onClick={(event) => { const point = event.points?.[0]; if (point && typeof point.pointIndex === "number") onClick?.(point.pointIndex, point.curveNumber ?? 0); }} /></div>;
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

export function formatNumber(value: unknown, digits = 2): string {
  const numeric = num(value);
  return numeric === null ? "—" : numeric.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
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

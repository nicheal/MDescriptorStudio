// Overview page (UI.png layout pass): Dataset Statistics + Element Combination
// Distribution column, 2×2 histograms (E/atom, Volume, Max|Force|, Min Distance),
// Property Availability. Sized to the viewport — no scrollbar at default window
// size. Quick Actions / Recent Jobs live in the persistent right rail (RightRail.tsx).
import { useCallback, useEffect, useState } from "react";
import { Segmented, Typography } from "antd";
import ReactECharts from "echarts-for-react";
import Histogram from "../components/Histogram";
import { createCartesianDataZoom } from "../components/chartInteraction";
import { ipc } from "../ipc/client";
import { activeDataset, useWorkspace } from "../stores/workspace";
import { useT } from "../i18n";
import { elementColor } from "../util/elements";
import { formatLabel, formatSize } from "../util/format";
import type { Hist, Stats } from "../types/protocol";

export default function Overview() {
  const st = useWorkspace();
  const d = activeDataset(st);
  const { t } = useT();
  const [stats, setStats] = useState<Stats | null>(null);
  const [recalculating, setRecalculating] = useState(false);
  const [distMode, setDistMode] = useState<DistMode>("combinations");

  const loadStats = useCallback(async () => {
    if (!d) return;
    try {
      const r = await ipc.request<{ recalculating: boolean; job_id: string | null; stats: Stats | null }>(
        "dataset.statistics",
        { id: d.id },
      );
      if (r.stats) {
        setStats(r.stats);
        setRecalculating(false);
      } else if (r.recalculating && r.job_id) {
        setRecalculating(true);
        const off = ipc.on("job.finished", (data) => {
          const j = data as { job_id: string; status: string };
          if (j.job_id !== r.job_id) return;
          off();
          void loadStats();
        });
      }
    } catch (e) {
      console.error(e);
    }
  }, [d, st.statsTick]);

  useEffect(() => {
    setStats(null);
    void loadStats();
  }, [loadStats]);

  if (!d) {
    return <EmptyState />;
  }

  return (
    <div style={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column", gap: 10 }}>
      {!d.cache_valid && (
        <Typography.Paragraph
          type="warning"
          style={{ background: "#FFF7E6", border: "1px solid #F0A000", padding: "4px 12px", borderRadius: 6, marginBottom: 0 }}
        >
          ⚠ {t("Dataset changed on disk since it was scanned. Statistics may be outdated.")}
        </Typography.Paragraph>
      )}
      {recalculating && (
        <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
          {t("Recomputing statistics…")}
        </Typography.Paragraph>
      )}
      <Typography.Text style={{ fontSize: 14, fontWeight: 600 }}>{t("Overview")}</Typography.Text>

      <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 12 }}>
        {/* column 1: statistics, element donut, property availability */}
        <div style={{ width: "32%", minWidth: 290, display: "flex", flexDirection: "column", gap: 12 }}>
          <Panel title={t("Dataset Statistics")}>
            <StatsTable d={d} stats={stats} />
          </Panel>
          <Panel
            title={t("Element Combination Distribution")}
            extra={
              <Segmented
                size="small"
                value={distMode}
                onChange={(v) => setDistMode(v as DistMode)}
                options={[
                  { label: t("Combinations"), value: "combinations" },
                  { label: t("Exact compositions"), value: "formulas" },
                  { label: t("Atom counts"), value: "atomcounts" },
                ]}
              />
            }
            style={{ flex: 1, minHeight: 110 }}
          >
            {distMode === "atomcounts" ? (
              <AtomCountCharts stats={stats} />
            ) : (
              <CompositionDonut stats={stats} mode={distMode} />
            )}
          </Panel>
          <Panel title={t("Property Availability")}>
            <PropertyTable stats={stats} />
          </Panel>
        </div>

        {/* columns 2–3: 2×2 histograms (all in the primary series color, per UI.png) */}
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
          <Panel style={{ flex: 1, minHeight: 140 }}>
            <Histogram title={t("Energy / Atom")} unit="eV" hist={stats?.energy_per_atom ?? null} />
          </Panel>
          <Panel style={{ flex: 1, minHeight: 140 }}>
            <Histogram title={t("Volume")} unit="Å³" hist={stats?.volume ?? null} />
          </Panel>
        </div>
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
          <Panel style={{ flex: 1, minHeight: 140 }}>
            <Histogram title={t("Min Distance")} unit="Å" hist={stats?.min_distance ?? null} />
          </Panel>
          <Panel style={{ flex: 1, minHeight: 140 }}>
            <Histogram title={t("Max |Force|")} unit="eV/Å" hist={stats?.max_force ?? null} />
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Panel({
  title,
  extra,
  children,
  style,
}: {
  title?: string;
  extra?: React.ReactNode;
  children: React.ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div
      style={{
        background: "#FFFFFF",
        border: "1px solid #EAECF0",
        borderRadius: 6,
        padding: "10px 12px",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        overflow: "hidden",
        ...style,
      }}
    >
      {(title || extra) && (
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 8, paddingBottom: 6 }}>
          {title && <div style={{ fontSize: 13, fontWeight: 600, color: "#242424", minWidth: 0 }}>{title}</div>}
          {extra}
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0 }}>{children}</div>
    </div>
  );
}

function StatsTable({
  d,
  stats,
}: {
  d: NonNullable<ReturnType<typeof activeDataset>>;
  stats: Stats | null;
}) {
  const { t } = useT();
  const row = (k: string, v: React.ReactNode) => (
    <div
      key={k}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        padding: "3.5px 0",
        borderBottom: "1px solid #F5F6F8",
        fontSize: 12.5,
      }}
    >
      <span style={{ color: "#616161", flex: "0 0 auto" }}>{k}</span>
      <span style={{ color: "#242424", fontVariantNumeric: "tabular-nums", textAlign: "right", minWidth: 0 }}>
        {v}
      </span>
  </div>
  );
  return (
    <div>
      {row(t("Structures"), d.number_of_frames.toLocaleString())}
      {row(t("Atoms"), stats ? stats.atoms_total.toLocaleString() : "—")}
      {row(
        t("Elements"),
        <span style={{ display: "inline-flex", gap: 4 }}>
          {(stats?.elements ?? []).map((e) => (
            <span
              key={e.symbol}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                border: "1px solid #EAECF0",
                borderRadius: 4,
                padding: "0 6px",
                fontSize: 12,
              }}
            >
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: 4,
                  background: elementColor(e.symbol),
                  display: "inline-block",
                }}
              />
              {e.symbol}
            </span>
          ))}
          {!stats && "—"}
        </span>,
      )}
      {row(
        t("Properties"),
        [
          d.properties.energy?.per_structure && t("Energy"),
          d.properties.forces?.per_atom && t("Force"),
          d.properties.virial?.per_structure && t("Virial"),
        ]
          .filter(Boolean)
          .join(", ") || "—",
      )}
      {row(t("Format"), formatLabel(d.format))}
      {row("PBC", d.periodicity.flags.join("") || "—")}
      {row(t("Created"), new Date(d.created_at).toLocaleString())}
      {row(t("File Size"), d.file_size ? formatSize(d.file_size) : "—")}
    </div>
  );
}

function PropertyTable({ stats }: { stats: Stats | null }) {
  const { t } = useT();
  const rows = [
    {
      key: "energy",
      label: t("Energy"),
      perAtom: stats?.properties.energy.per_atom ?? false,
      perStruct: stats?.properties.energy.per_structure ?? false,
    },
    {
      key: "forces",
      label: t("Force"),
      perAtom: stats?.properties.forces.per_atom ?? false,
      perStruct: false,
    },
    {
      key: "virial",
      label: t("Virial"),
      perAtom: false,
      perStruct: stats?.properties.virial.per_structure ?? false,
    },
  ];
  const ok = <span style={{ color: "#107C10", fontWeight: 600 }}>✓</span>;
  const no = <span style={{ color: "#C9CDD4" }}>—</span>;
  return (
    <div style={{ fontSize: 12.5 }}>
      <div style={{ display: "flex", alignItems: "center", padding: "2px 0 4px", color: "#616161", fontSize: 12 }}>
        <span style={{ flex: 1.4 }} />
        <span style={{ flex: 1, textAlign: "center" }}>{t("Per-Atom")}</span>
        <span style={{ flex: 1, textAlign: "center" }}>{t("Per-Structure")}</span>
      </div>
      {rows.map((r) => (
        <div
          key={r.key}
          style={{
            display: "flex",
            alignItems: "center",
            padding: "3px 0",
            borderTop: "1px solid #F5F6F8",
          }}
        >
          <span style={{ flex: 1.4, fontWeight: 600, fontSize: 12 }}>{r.label}</span>
          <span style={{ flex: 1, textAlign: "center" }}>{r.perAtom ? ok : no}</span>
          <span style={{ flex: 1, textAlign: "center" }}>{r.perStruct ? ok : no}</span>
        </div>
      ))}
    </div>
  );
}

type DistMode = "combinations" | "formulas" | "atomcounts";

// exact stoichiometry can span hundreds of species (e.g. C/H/O molecule sets
// with 794 distinct formulas): only the most common N get their own slice, the
// rest fold into one "Others" slice that names how many species it absorbs
const FORMULA_TOP_N = 12;
const OTHERS_COLOR = "#C1C7CD";

function CompositionDonut({ stats, mode }: { stats: Stats | null; mode: DistMode }) {
  const { t } = useT();
  const entries: { name: string; elements: string[]; count: number }[] =
    mode === "formulas"
      ? (stats?.formulas ?? []).map((f) => ({ name: f.formula, elements: f.elements, count: f.count }))
      : (stats?.compositions ?? []).map((c) => ({ name: c.elements.join("-"), elements: c.elements, count: c.count }));
  if (!stats || entries.length === 0) {
    return (
      <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "#8A8A8A", fontSize: 12 }}>
        {t("Statistics pending…")}
      </div>
    );
  }
  const total = entries.reduce((s, c) => s + c.count, 0);
  let rows = entries;
  let others = 0;
  if (mode === "formulas" && entries.length > FORMULA_TOP_N) {
    rows = entries.slice(0, FORMULA_TOP_N);
    others = total - rows.reduce((s, c) => s + c.count, 0);
  }
  const othersSpecies = entries.length - rows.length;
  const legendRows = others > 0 ? [...rows, { name: t("Others +{count}", { count: othersSpecies }), elements: [], count: others }] : rows;
  const items = legendRows.map((r) => ({
    key: r.name,
    name: r.name,
    count: r.count,
    label: mode === "combinations" ? arityLabel(r.elements.length, t) : null,
    color: r.elements.length > 0 ? compositionColor(r.elements) : OTHERS_COLOR,
    pct: `${((r.count / total) * 100).toFixed(1)}%`,
  }));
  // backend sorts equal-arity entries contiguously, so consecutive folding works
  const groups: { label: string | null; items: typeof items }[] = [];
  for (const item of items) {
    const last = groups[groups.length - 1];
    if (last && last.label === item.label) last.items.push(item);
    else groups.push({ label: item.label, items: [item] });
  }
  const option = {
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    series: [
      {
        type: "pie",
        radius: ["48%", "74%"],
        center: ["50%", "50%"],
        label: { show: false },
        data: items.map((r) => ({
          name: r.name,
          value: r.count,
          itemStyle: { color: r.color },
        })),
        animation: false,
      },
    ],
  };
  return (
    <div style={{ height: "100%", minHeight: 0, display: "flex", alignItems: "center", gap: 8 }}>
      <ReactECharts option={option} style={{ flex: 1, height: "100%", minWidth: 0 }} notMerge />
      <div style={{ flex: "0 0 132px", alignSelf: "stretch", overflowY: "auto", minHeight: 0, display: "flex", flexDirection: "column" }}>
        <div style={{ margin: "auto 0", display: "flex", flexDirection: "column", gap: 5 }}>
        {mode === "formulas" && others > 0 && (
          <div style={{ fontSize: 11, color: "#8A8A8A", paddingBottom: 2 }}>
            {t("Compositions in total: {count}", { count: entries.length })}
          </div>
        )}
        {groups.map((g, gi) => (
          <div key={g.label ?? `g${gi}`} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {g.label && <div style={{ fontSize: 11, fontWeight: 600, color: "#8A8A8A" }}>{g.label}</div>}
            {g.items.map((row) => (
              <div key={row.key} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, minWidth: 0 }}>
                <span
                  style={{
                    width: 9,
                    height: 9,
                    borderRadius: 5,
                    background: row.color,
                    display: "inline-block",
                    flex: "0 0 9px",
                  }}
                />
                <span style={{ color: "#242424", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={row.name}>
                  {row.name}
                </span>
                <span style={{ marginLeft: "auto", color: "#616161", fontVariantNumeric: "tabular-nums", flex: "0 0 auto" }}>
                  {row.pct}
                </span>
              </div>
            ))}
          </div>
        ))}
        </div>
      </div>
    </div>
  );
}

function AtomCountCharts({ stats }: { stats: Stats | null }) {
  const { t } = useT();
  const entries = (Object.entries(stats?.element_atom_counts ?? {}).filter(
    ([, h]) => h != null,
  ) as [string, Hist][]).sort(([a], [b]) => a.localeCompare(b));
  if (!stats || entries.length === 0) {
    return (
      <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "#8A8A8A", fontSize: 12 }}>
        {t("Statistics pending…")}
      </div>
    );
  }
  return (
    <div style={{ height: "100%", minHeight: 0, overflowY: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
      {entries.map(([symbol, h]) => (
        <AtomCountRow key={symbol} symbol={symbol} hist={h} />
      ))}
    </div>
  );
}

function AtomCountRow({ symbol, hist }: { symbol: string; hist: Hist }) {
  const { t } = useT();
  const color = elementColor(symbol);
  const option = {
    grid: { left: 40, right: 8, top: 4, bottom: 16 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      // confine the tooltip to the mini chart: an overflowing tooltip would
      // toggle the panel scrollbar on hover and make the chart jitter
      confine: true,
      transitionDuration: 0,
      formatter: (params: unknown) => {
        const p = (params as { dataIndex: number }[])[0];
        const value = Math.round((hist.edges[p.dataIndex] + hist.edges[p.dataIndex + 1]) / 2);
        return `${symbol} = ${value}<br/>${t("count")}: <b>${hist.counts[p.dataIndex]}</b>`;
      },
    },
    xAxis: {
      type: "value",
      min: hist.edges[0],
      max: hist.edges[hist.edges.length - 1],
      axisLabel: { fontSize: 10, color: "#616161" },
      axisLine: { lineStyle: { color: "#E1E4E8" } },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      axisLabel: { fontSize: 10, color: "#616161" },
      splitLine: { lineStyle: { color: "#F0F1F3" } },
    },
    dataZoom: createCartesianDataZoom(),
    series: [
      {
        type: "bar",
        data: hist.counts.map((c, i) => [(hist.edges[i] + hist.edges[i + 1]) / 2, c]),
        itemStyle: { color, borderRadius: [1, 1, 0, 0] },
        barCategoryGap: "0%",
      },
    ],
    animation: false,
  };
  return (
    <div style={{ flex: "1 0 84px", minHeight: 84, display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, paddingBottom: 2 }}>
        <span style={{ width: 9, height: 9, borderRadius: 5, background: color, display: "inline-block", flex: "0 0 9px" }} />
        <span style={{ fontWeight: 600, color: "#242424" }}>{symbol}</span>
        <span style={{ color: "#8A8A8A", fontSize: 11 }}>{t("atoms per structure")}</span>
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        <ReactECharts option={option} style={{ height: "100%", width: "100%", position: "relative" }} notMerge />
      </div>
    </div>
  );
}

function arityLabel(n: number, t: (key: string) => string): string {
  if (n <= 1) return t("Unary");
  if (n === 2) return t("Binary");
  if (n === 3) return t("Ternary");
  return t("Multi-element");
}

/** Unary keeps the element's own color; a combination blends its elements'
 *  palette colors equally, so e.g. Ni-Cr sits visually between Ni and Cr. */
function compositionColor(elements: string[]): string {
  if (elements.length === 1) return elementColor(elements[0]);
  let r = 0;
  let g = 0;
  let b = 0;
  for (const s of elements) {
    const hex = elementColor(s);
    r += parseInt(hex.slice(1, 3), 16);
    g += parseInt(hex.slice(3, 5), 16);
    b += parseInt(hex.slice(5, 7), 16);
  }
  const mix = (v: number) =>
    Math.round(v / elements.length)
      .toString(16)
      .padStart(2, "0");
  return `#${mix(r)}${mix(g)}${mix(b)}`;
}

function EmptyState() {
  const { t } = useT();
  return (
    <div style={{ textAlign: "center", marginTop: 120 }}>
      <Typography.Title level={5}>{t("No datasets")}</Typography.Title>
      <Typography.Text type="secondary">{t("Add a DeepMD or extxyz dataset to begin.")}</Typography.Text>
    </div>
  );
}

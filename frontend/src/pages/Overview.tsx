// Overview page (UI.png layout pass): Dataset Statistics + Element Distribution
// column, 2×2 histograms (E/atom, Force, Volume, Max|Force|), Property
// Availability. Sized to the viewport — no scrollbar at default window size.
// Quick Actions / Recent Jobs live in the persistent right rail (RightRail.tsx).
import { useCallback, useEffect, useState } from "react";
import { Typography } from "antd";
import ReactECharts from "echarts-for-react";
import Histogram from "../components/Histogram";
import { ipc } from "../ipc/client";
import { activeDataset, useWorkspace } from "../stores/workspace";
import { elementColor } from "../util/elements";
import type { Stats } from "../types/protocol";

export default function Overview() {
  const st = useWorkspace();
  const d = activeDataset(st);
  const [stats, setStats] = useState<Stats | null>(null);
  const [recalculating, setRecalculating] = useState(false);

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
          ⚠ Dataset changed on disk since it was scanned. Statistics may be outdated.
        </Typography.Paragraph>
      )}
      {recalculating && (
        <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
          Recomputing statistics…
        </Typography.Paragraph>
      )}
      <Typography.Text style={{ fontSize: 14, fontWeight: 600 }}>Overview</Typography.Text>

      <div style={{ flex: 1, minHeight: 0, display: "flex", gap: 12 }}>
        {/* column 1: statistics, element donut, property availability */}
        <div style={{ width: "32%", minWidth: 290, display: "flex", flexDirection: "column", gap: 12 }}>
          <Panel title="Dataset Statistics">
            <StatsTable d={d} stats={stats} />
          </Panel>
          <Panel title="Element Distribution" style={{ flex: 1, minHeight: 110 }}>
            <ElementDonut stats={stats} />
          </Panel>
          <Panel title="Property Availability">
            <PropertyTable stats={stats} />
          </Panel>
        </div>

        {/* columns 2–3: 2×2 histograms (all in the primary series color, per UI.png) */}
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
          <Panel style={{ flex: 1, minHeight: 140 }}>
            <Histogram title="Energy / Atom" unit="eV" hist={stats?.energy_per_atom ?? null} />
          </Panel>
          <Panel style={{ flex: 1, minHeight: 140 }}>
            <Histogram title="Volume" unit="Å³" hist={stats?.volume ?? null} />
          </Panel>
        </div>
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
          <Panel style={{ flex: 1, minHeight: 140 }}>
            <Histogram title="Force Magnitude" unit="eV/Å" hist={stats?.force_magnitude ?? null} />
          </Panel>
          <Panel style={{ flex: 1, minHeight: 140 }}>
            <Histogram title="Max |Force|" unit="eV/Å" hist={stats?.max_force ?? null} />
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Panel({
  title,
  children,
  style,
}: {
  title?: string;
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
      {title && (
        <div style={{ fontSize: 13, fontWeight: 600, color: "#242424", paddingBottom: 6 }}>{title}</div>
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
      {row("Structures", d.number_of_frames.toLocaleString())}
      {row("Atoms", stats ? stats.atoms_total.toLocaleString() : "—")}
      {row(
        "Elements",
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
        "Properties",
        [
          d.properties.energy?.per_structure && "Energy",
          d.properties.forces?.per_atom && "Force",
          d.properties.virial?.per_structure && "Virial",
        ]
          .filter(Boolean)
          .join(", ") || "—",
      )}
      {row("Format", d.format.charAt(0).toUpperCase() + d.format.slice(1))}
      {row("PBC", d.periodicity.flags.join("") || "—")}
      {row("Created", new Date(d.created_at).toLocaleString())}
      {row("File Size", d.file_size ? formatSize(d.file_size) : "—")}
    </div>
  );
}

function PropertyTable({ stats }: { stats: Stats | null }) {
  const rows = [
    {
      key: "energy",
      label: "Energy",
      perAtom: stats?.properties.energy.per_atom ?? false,
      perStruct: stats?.properties.energy.per_structure ?? false,
    },
    {
      key: "forces",
      label: "Force",
      perAtom: stats?.properties.forces.per_atom ?? false,
      perStruct: false,
    },
    {
      key: "virial",
      label: "Virial",
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
        <span style={{ flex: 1, textAlign: "center" }}>Per-Atom</span>
        <span style={{ flex: 1, textAlign: "center" }}>Per-Structure</span>
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

function ElementDonut({ stats }: { stats: Stats | null }) {
  if (!stats || stats.elements.length === 0) {
    return (
      <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", color: "#8A8A8A", fontSize: 12 }}>
        Statistics pending…
      </div>
    );
  }
  const total = stats.elements.reduce((s, e) => s + e.count, 0);
  const option = {
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    series: [
      {
        type: "pie",
        radius: ["48%", "74%"],
        center: ["50%", "50%"],
        label: { show: false },
        data: stats.elements.map((e) => ({
          name: e.symbol,
          value: e.count,
          itemStyle: { color: elementColor(e.symbol) },
        })),
        animation: false,
      },
    ],
  };
  return (
    <div style={{ height: "100%", minHeight: 0, display: "flex", alignItems: "center", gap: 8 }}>
      <ReactECharts option={option} style={{ flex: 1, height: "100%", minWidth: 0 }} notMerge />
      <div style={{ flex: "0 0 104px", display: "flex", flexDirection: "column", gap: 8 }}>
        {stats.elements.map((e) => (
          <div key={e.symbol} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5 }}>
            <span
              style={{
                width: 9,
                height: 9,
                borderRadius: 5,
                background: elementColor(e.symbol),
                display: "inline-block",
                flex: "0 0 9px",
              }}
            />
            <span style={{ color: "#242424" }}>{e.symbol}</span>
            <span style={{ marginLeft: "auto", color: "#616161", fontVariantNumeric: "tabular-nums" }}>
              {((e.count / total) * 100).toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div style={{ textAlign: "center", marginTop: 120 }}>
      <Typography.Title level={5}>No datasets</Typography.Title>
      <Typography.Text type="secondary">Add a DeepMD or extxyz dataset to begin.</Typography.Text>
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

// Overview page (mockup, ADR-7 rulings): summary, element donut, 4 histograms,
// property availability matrix, quick actions, recent jobs (M4).
import { useCallback, useEffect, useState } from "react";
import { App as AntApp, Button, Card, Descriptions, Space, Table, Tag, Typography } from "antd";
import { Cube16Regular, Grid16Regular, ArrowSync16Regular } from "@fluentui/react-icons";
import ReactECharts from "echarts-for-react";
import Histogram from "../components/Histogram";
import { ipc } from "../ipc/client";
import { activeDataset, useWorkspace } from "../stores/workspace";
import { elementColor } from "../util/elements";
import type { Stats } from "../types/protocol";

export default function Overview() {
  const { message } = AntApp.useApp();
  const st = useWorkspace();
  const d = activeDataset(st);
  const onGo = st.setPage;
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
  }, [d]);

  useEffect(() => {
    setStats(null);
    void loadStats();
  }, [loadStats]);

  if (!d) {
    return (
      <EmptyState />
    );
  }

  return (
    <div>
      {!d.cache_valid && (
        <Typography.Paragraph
          type="warning"
          style={{ background: "#FFF7E6", border: "1px solid #F0A000", padding: "6px 12px", borderRadius: 6 }}
        >
          ⚠ Dataset changed on disk since it was scanned. Statistics may be outdated.
        </Typography.Paragraph>
      )}
      {recalculating && (
        <Typography.Paragraph type="secondary">Recomputing statistics…</Typography.Paragraph>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 280px", gap: 16 }}>
        <Section title="Dataset Statistics" span>
          <Descriptions
            column={1}
            size="small"
            colon={false}
            labelStyle={{ width: 130, color: "#616161", fontSize: 13 }}
            contentStyle={{ fontSize: 13, fontVariantNumeric: "tabular-nums" }}
          >
            <Item k="Structures">{d.number_of_frames.toLocaleString()}</Item>
            <Item k="Atoms">{stats ? stats.atoms_total.toLocaleString() : "—"}</Item>
            <Item k="Elements">
              <Space size={4}>
                {(stats?.elements ?? []).map((e) => (
                  <Tag
                    key={e.symbol}
                    style={{ color: "#242424" }}
                    icon={
                      <span
                        style={{
                          display: "inline-block",
                          width: 8,
                          height: 8,
                          borderRadius: 4,
                          background: elementColor(e.symbol),
                          marginRight: 4,
                        }}
                      />
                    }
                  >
                    {e.symbol}
                  </Tag>
                ))}
              </Space>
            </Item>
            <Item k="Properties">
              {[
                d.properties.energy?.per_structure && "Energy",
                d.properties.forces?.per_atom && "Force",
                d.properties.virial?.per_structure && "Virial",
              ]
                .filter(Boolean)
                .join(", ") || "—"}
            </Item>
            <Item k="Format">{d.format}</Item>
            <Item k="PBC">{d.periodicity.flags.join("/") || "—"}</Item>
            <Item k="Created">{new Date(d.created_at).toLocaleString()}</Item>
            <Item k="File Size">{d.file_size ? formatSize(d.file_size) : "—"}</Item>
          </Descriptions>
        </Section>

        <Section title="Distributions">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <Histogram title="Energy / Atom" unit="eV" hist={stats?.energy_per_atom ?? null} />
            <Histogram title="Force Magnitude" unit="eV/Å" hist={stats?.force_magnitude ?? null} color="#00B8A9" />
            <Histogram title="Volume" unit="Å³" hist={stats?.volume ?? null} color="#7A5AF8" />
            <Histogram title="Atoms / structure" hist={stats?.atoms_per_structure ?? null} color="#2ECC71" />
          </div>
        </Section>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <Section title="Quick Actions">
            <Space direction="vertical" style={{ width: "100%" }}>
              <Button block icon={<Cube16Regular />} onClick={() => onGo("explore")}>
                Explore Structures
              </Button>
              <Button block icon={<Grid16Regular />} onClick={() => onGo("descriptors")}>
                Compute Descriptors
              </Button>
              <Button
                block
                icon={<ArrowSync16Regular />}
                onClick={async () => {
                  await ipc.request("job.list", {});
                  void loadStats();
                  message.info("Statistics refreshed");
                }}
              >
                Dataset Statistics
              </Button>
            </Space>
          </Section>
          <Section title="Property Availability">
            <PropertyTable stats={stats} />
          </Section>
        </div>
      </div>
      {stats && stats.elements.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <Section title="Element Distribution">
            <ElementDonut stats={stats} />
          </Section>
        </div>
      )}
    </div>
  );
}

function Item({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <Descriptions.Item label={k} key={k}>
      {children}
    </Descriptions.Item>
  );
}

function Section({ title, children, span }: { title: string; children: React.ReactNode; span?: boolean }) {
  return (
    <Card
      size="small"
      title={title}
      styles={{ header: { borderBottom: "1px solid #EAECF0", minHeight: 36 } }}
      style={{ gridColumn: span ? "1 / 2" : undefined }}
    >
      {children}
    </Card>
  );
}

function PropertyTable({ stats }: { stats: Stats | null }) {
  const rows = [
    { key: "energy", label: "Energy", perAtom: stats?.properties.energy.per_atom ?? false, perStruct: stats?.properties.energy.per_structure ?? false },
    { key: "forces", label: "Force", perAtom: stats?.properties.forces.per_atom ?? false, perStruct: false },
    { key: "virial", label: "Virial", perAtom: false, perStruct: stats?.properties.virial.per_structure ?? false },
  ];
  const ok = <span style={{ color: "#107C10" }}>✓</span>;
  const no = <span style={{ color: "#C9CDD4" }}>—</span>;
  return (
    <Table
      size="small"
      pagination={false}
      dataSource={rows}
      columns={[
        { title: "", dataIndex: "label", key: "label", render: (v: string) => <b style={{ fontSize: 12 }}>{v}</b> },
        { title: "Per-Atom", dataIndex: "perAtom", key: "perAtom", render: (v: boolean) => (v ? ok : no) },
        { title: "Per-Structure", dataIndex: "perStruct", key: "perStruct", render: (v: boolean) => (v ? ok : no) },
      ]}
    />
  );
}

function ElementDonut({ stats }: { stats: Stats }) {
  const option = {
    tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
    series: [
      {
        type: "pie",
        radius: ["45%", "72%"],
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
  return <ReactECharts option={option} style={{ height: 200 }} notMerge />;
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

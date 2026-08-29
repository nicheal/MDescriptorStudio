// Results page (M5): run history + plot-first PCA + heatmap + selected-sample
// inspector with Open Explore reverse jump (design doc §29/§31/§96/§101).
import { useCallback, useEffect, useState } from "react";
import {
  App as AntApp,
  Button,
  Empty,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import ReactECharts from "echarts-for-react";
import { ArrowRight16Regular, ArrowSync16Regular } from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { activeDataset, useWorkspace } from "../stores/workspace";
import { trackJob, watchJob } from "../stores/jobs";
import type { PcaPayload, RunRow } from "../types/protocol";

export default function Results() {
  const { message } = AntApp.useApp();
  const st = useWorkspace();
  const d = activeDataset(st);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [pca, setPca] = useState<PcaPayload | null>(null);
  const [colorBy, setColorBy] = useState<string>("energy");
  const [selectedPoint, setSelectedPoint] = useState<number | null>(null);
  const [pcaBusy, setPcaBusy] = useState(false);
  const [heatmap, setHeatmap] = useState<{ atoms: number[]; features: number[]; values: number[][]; atomOffset: number } | null>(null);

  const refreshRuns = useCallback(async () => {
    if (!d) return;
    const rows = await ipc.request<RunRow[]>("result.list", { dataset_id: d.id });
    setRuns(rows);
    setSelectedRun((cur) => (cur && rows.some((r) => r.id === cur) ? cur : rows[0]?.id ?? null));
  }, [d]);

  useEffect(() => {
    void refreshRuns();
  }, [refreshRuns]);

  const runPca = async () => {
    if (!selectedRun) return;
    setPcaBusy(true);
    try {
      const r = await ipc.request<{ job_id: string }>("analysis.pca", { run_id: selectedRun });
      trackJob(r.job_id, "analysis.pca");
      const done = await watchJob(r.job_id);
      if (done.status === "COMPLETED") {
        const full = await ipc.request<PcaPayload>("result.get_pca", { analysis_id: done.result?.analysis_id });
        setPca(full);
        message.success("PCA ready");
      } else {
        message.error(`PCA ${done.status}: ${done.error?.message ?? ""}`);
      }
    } catch (e) {
      const err = e as { code: string; message: string };
      message.error(`${err.code}: ${err.message}`);
    } finally {
      setPcaBusy(false);
    }
  };

  const loadHeatmap = useCallback(async () => {
    if (!selectedRun || !d) return;
    try {
      const h = await ipc.request<{ atoms: number[]; features: number[]; values: number[][]; atomOffset: number }>(
        "result.heatmap",
        { run_id: selectedRun, frame_index: st.activeFrameIndex },
      );
      setHeatmap(h);
    } catch {
      setHeatmap(null);
    }
  }, [selectedRun, d, st.activeFrameIndex]);

  useEffect(() => {
    void loadHeatmap();
  }, [loadHeatmap]);

  if (!d) return <Empty description="Register a dataset first" style={{ marginTop: 120 }} />;

  const pcaOption = pca
    ? {
        grid: { left: 60, right: 24, top: 24, bottom: 44 },
        tooltip: {
          formatter: (p: { dataIndex: number }) => {
            const pt = pca.points[p.dataIndex];
            const lines = [`Frame <b>${pt.frame}</b>`];
            if (pt.energy != null) lines.push(`E/atom: ${pt.energy.toFixed(4)} eV`);
            if (pt.force_max != null) lines.push(`Max|F|: ${pt.force_max.toFixed(4)} eV/Å`);
            if (pt.volume != null) lines.push(`Volume: ${pt.volume.toFixed(1)} Å³`);
            return lines.join("<br/>");
          },
        },
        xAxis: {
          type: "value",
          name: pca.x_label,
          nameLocation: "middle",
          nameGap: 28,
          nameTextStyle: { fontSize: 11, color: "#616161" },
          axisLabel: { fontSize: 11, color: "#616161" },
          splitLine: { lineStyle: { color: "#F0F1F3" } },
        },
        yAxis: {
          type: "value",
          name: pca.y_label,
          nameTextStyle: { fontSize: 11, color: "#616161" },
          axisLabel: { fontSize: 11, color: "#616161" },
          splitLine: { lineStyle: { color: "#F0F1F3" } },
        },
        series: [
          {
            type: "scatter",
            symbolSize: 7,
            data: pca.points.map((pt) => [
              pt.pc1,
              pt.pc2,
              colorBy === "energy" ? pt.energy : colorBy === "force" ? pt.force_max : colorBy === "volume" ? pt.volume : null,
            ]),
            itemStyle: {
              color: colorBy === "none" ? "#0F6CBD" : undefined,
            },
            selectedMode: "single",
            select: { itemStyle: { borderColor: "#0F6CBD", borderWidth: 2, shadowBlur: 6 } },
          },
        ],
        visualMap:
          colorBy === "none"
            ? undefined
            : {
                min: Math.min(...pca.points.map((p) => (colorBy === "energy" ? p.energy : colorBy === "force" ? p.force_max : p.volume) ?? 0)),
                max: Math.max(...pca.points.map((p) => (colorBy === "energy" ? p.energy : colorBy === "force" ? p.force_max : p.volume) ?? 0)),
                calculable: true,
                right: 8,
                top: "middle",
                textStyle: { fontSize: 10 },
                inRange: { color: ["#2166AC", "#F7F7F7", "#B2182B"] },
              },
        animation: false,
      }
    : null;

  return (
    <div>
      {/* run history */}
      <div style={{ marginBottom: 12 }}>
        <Table
          size="small"
          pagination={{ pageSize: 5 }}
          rowKey="id"
          dataSource={runs}
          onRow={(r) => ({
            onClick: () => setSelectedRun(r.id),
            style: { cursor: "pointer", background: r.id === selectedRun ? "#EBF3FC" : undefined },
          })}
          columns={[
            { title: "Run", dataIndex: "id", key: "id", render: (v: string) => <Typography.Text code style={{ fontSize: 11 }}>{v}</Typography.Text> },
            { title: "Descriptor", dataIndex: "descriptor_name", key: "d" },
            { title: "Scope", dataIndex: "scope", key: "s", width: 90 },
            { title: "Status", dataIndex: "status", key: "st", width: 100 },
            { title: "Created", dataIndex: "created_at", key: "c", width: 170, render: (v: string) => new Date(v).toLocaleString() },
          ]}
        />
      </div>

      {runs.length === 0 ? (
        <Empty description="No completed runs yet — compute a descriptor first" style={{ marginTop: 60 }} />
      ) : (
        <div style={{ display: "flex", gap: 16 }}>
          {/* PCA plot-first */}
          <div style={{ flex: 4, background: "#FFFFFF", border: "1px solid #EAECF0", borderRadius: 6, padding: 12 }}>
            <Space style={{ marginBottom: 8 }}>
              <Typography.Text strong>PCA · {runs.find((r) => r.id === selectedRun)?.descriptor_name}</Typography.Text>
              <Select
                size="small"
                value={colorBy}
                style={{ width: 150 }}
                onChange={setColorBy}
                options={[
                  { value: "energy", label: "Color by Energy" },
                  { value: "force", label: "Color by Max|F|" },
                  { value: "volume", label: "Color by Volume" },
                  { value: "none", label: "No color mapping" },
                ]}
              />
              <Button size="small" icon={<ArrowSync16Regular />} loading={pcaBusy} onClick={() => void runPca()}>
                Run PCA
              </Button>
            </Space>
            {pca ? (
              <ReactECharts
                option={pcaOption}
                style={{ height: 420 }}
                notMerge
                onEvents={{
                  click: (p: { dataIndex: number }) => {
                    const pt = pca.points[p.dataIndex];
                    setSelectedPoint(pt.frame);
                    st.setActiveFrame(pt.frame);
                    message.info(`Frame ${pt.frame} selected — open Explore to view`);
                  },
                }}
              />
            ) : (
              <Empty description="Click Run PCA" style={{ marginTop: 120 }} />
            )}
          </div>

          {/* selected sample inspector + heatmap */}
          <div style={{ width: 320, display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ background: "#FFFFFF", border: "1px solid #EAECF0", borderRadius: 6, padding: 12 }}>
              <Typography.Text strong style={{ fontSize: 12, color: "#616161" }}>
                SELECTED SAMPLE
              </Typography.Text>
              {selectedPoint != null ? (
                <>
                  <div style={{ marginTop: 8, fontSize: 13 }}>
                    <Row k="Frame" v={String(selectedPoint)} />
                  </div>
                  <Button
                    size="small"
                    icon={<ArrowRight16Regular />}
                    style={{ marginTop: 8 }}
                    onClick={() => {
                      st.setActiveFrame(selectedPoint);
                      st.setPage("explore");
                    }}
                  >
                    Open in Explore
                  </Button>
                </>
              ) : (
                <Typography.Text type="secondary" style={{ fontSize: 12, display: "block", marginTop: 8 }}>
                  Click a PCA point
                </Typography.Text>
              )}
            </div>
            {heatmap && heatmap.values.length > 0 && (
              <div style={{ background: "#FFFFFF", border: "1px solid #EAECF0", borderRadius: 6, padding: 12 }}>
                <Typography.Text strong style={{ fontSize: 12, color: "#616161" }}>
                  HEATMAP · frame {st.activeFrameIndex} (atoms {heatmap.atomOffset}–{heatmap.atomOffset + heatmap.atoms.length - 1})
                </Typography.Text>
                <ReactECharts
                  option={{
                    grid: { left: 50, right: 12, top: 10, bottom: 30 },
                    xAxis: { type: "category", name: "feature", data: [], show: false },
                    yAxis: { type: "category", data: [], show: false },
                    visualMap: {
                      min: Math.min(...heatmap.values.flat()),
                      max: Math.max(...heatmap.values.flat()),
                      calculable: false,
                      show: true,
                      right: 0,
                      top: "center",
                      inRange: { color: ["#2166AC", "#F7F7F7", "#B2182B"] },
                      text: ["hi", "lo"],
                    },
                    series: [
                      {
                        type: "heatmap",
                        data: heatmap.values.flatMap((row, i) => row.map((v, j) => [j, i, v])),
                        progress: { progress: 0 },
                      },
                    ],
                    animation: false,
                  }}
                  style={{ height: 220 }}
                  notMerge
                />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "3px 0" }}>
      <span style={{ color: "#616161" }}>{k}</span>
      <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 500 }}>{v}</span>
    </div>
  );
}

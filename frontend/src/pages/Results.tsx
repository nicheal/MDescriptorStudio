// Results page (M5): run history + plot-first PCA + heatmap + selected-sample
// inspector with Open Explore reverse jump (design doc §29/§31/§96/§101).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  App as AntApp,
  Button,
  Empty,
  Popconfirm,
  Select,
  Space,
  Table,
  Typography,
} from "antd";
import ReactECharts from "echarts-for-react";
import {
  ArrowRight16Regular,
  ArrowSync16Regular,
  Delete16Regular,
} from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { activeDataset, useWorkspace, type PcaMode, type SelectedSample } from "../stores/workspace";
import { trackJob, watchJob } from "../stores/jobs";
import { createCartesianDataZoom } from "../components/chartInteraction";
import StructurePreview from "../components/StructurePreview";
import type { FramePayload, PcaAnalysisResponse, PcaPayload, RunRow } from "../types/protocol";

// Fast UI cache for tab switches; the authoritative cache is persisted by the
// backend in analysis_runs + pca.json and is consulted by analysis.pca.
const pcaCache = new Map<string, PcaPayload>();

const RUN_STATUS_COLOR: Record<string, string> = {
  QUEUED: "#616161",
  RUNNING: "#0F6CBD",
  COMPLETED: "#107C10",
  FAILED: "#C42B1C",
  CANCELLED: "#8A8A8A",
};

export default function Results() {
  const { message } = AntApp.useApp();
  const st = useWorkspace();
  const d = activeDataset(st);
  const [runs, setRuns] = useState<RunRow[]>([]);
  // Keep the selected result in the workspace so remounting Results after a
  // tab switch does not reset the table to its first row.
  const selectedRun = st.activeDescriptorRunId;
  const setSelectedRun = st.setActiveRun;
  const selectedSample = st.selectedSample;
  const setSelectedSample = st.setSelectedSample;
  const [pca, setPca] = useState<PcaPayload | null>(null);
  const [pcaMode, setPcaMode] = useState<PcaMode>("structure");
  const [colorBy, setColorBy] = useState<string>("energy");
  const [pcaBusy, setPcaBusy] = useState(false);
  const [selectedFrame, setSelectedFrame] = useState<FramePayload | null>(null);
  const [selectedFrameBusy, setSelectedFrameBusy] = useState(false);
  const [heatmap, setHeatmap] = useState<{ atoms: number[]; features: number[]; values: number[][]; atomOffset: number } | null>(null);
  // current selection key ("run:mode"); runPca completes after an async job and
  // must not paint a stale payload onto a chart that moved on meanwhile
  const activeKeyRef = useRef<string | null>(null);

  const refreshRuns = useCallback(async () => {
    if (!d) return;
    const rows = await ipc.request<RunRow[]>("result.list", { dataset_id: d.id });
    setRuns(rows);
    const currentRunId = useWorkspace.getState().activeDescriptorRunId;
    setSelectedRun(currentRunId && rows.some((r) => r.id === currentRunId) ? currentRunId : rows[0]?.id ?? null);
  }, [d]);

  useEffect(() => {
    void refreshRuns();
  }, [refreshRuns]);

  // live status: descriptor_runs flip QUEUED -> RUNNING -> COMPLETED while
  // their jobs run; job.progress re-pulls debounced (backend already throttles
  // to 200ms), job.finished settles the run the moment its job ends
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const debouncedRefresh = () => {
      if (refreshTimer.current) return;
      refreshTimer.current = setTimeout(() => {
        refreshTimer.current = null;
        void refreshRuns();
      }, 400);
    };
    const offProgress = ipc.on("job.progress", debouncedRefresh);
    const offFinished = ipc.on("job.finished", () => {
      if (refreshTimer.current) {
        clearTimeout(refreshTimer.current);
        refreshTimer.current = null;
      }
      void refreshRuns();
    });
    return () => {
      offProgress();
      offFinished();
      if (refreshTimer.current) {
        clearTimeout(refreshTimer.current);
        refreshTimer.current = null;
      }
    };
  }, [refreshRuns]);

  // selected run (or dataset) changed: show that run's cached PCA, or clear —
  // never keep the previous dataset's chart on screen (bug2)
  const pcaCacheKey = selectedRun ? `${selectedRun}:${pcaMode}` : null;
  const selectedPoint: SelectedSample | null =
    selectedSample &&
    selectedSample.datasetId === d?.id &&
    selectedSample.runId === selectedRun &&
    selectedSample.mode === pcaMode
      ? selectedSample
      : null;
  useEffect(() => {
    activeKeyRef.current = pcaCacheKey;
    setPca(pcaCacheKey ? pcaCache.get(pcaCacheKey) ?? null : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRun, d?.id, pcaMode]);

  const selectedFrameIndex = selectedPoint?.frame ?? null;
  useEffect(() => {
    const datasetId = d?.id;
    if (!datasetId || selectedFrameIndex == null) {
      setSelectedFrame(null);
      setSelectedFrameBusy(false);
      return;
    }

    let cancelled = false;
    setSelectedFrame(null);
    setSelectedFrameBusy(true);
    void ipc
      .request<FramePayload>("dataset.frame", {
        id: datasetId,
        index: selectedFrameIndex,
        bond_cutoff: 2.4,
      })
      .then((frame) => {
        if (!cancelled) setSelectedFrame(frame);
      })
      .catch(() => {
        if (!cancelled) setSelectedFrame(null);
      })
      .finally(() => {
        if (!cancelled) setSelectedFrameBusy(false);
      });

    return () => {
      cancelled = true;
    };
  }, [d?.id, selectedFrameIndex]);

  // delete one run: backend cascades analyses + linked jobs and removes the
  // stored result dirs; drop this run's cached PCA payloads so they can never
  // resurface when the same run id reappears in a later session
  const deleteRun = async (run: RunRow) => {
    try {
      await ipc.request("result.remove", { run_id: run.id });
      pcaCache.delete(`${run.id}:structure`);
      pcaCache.delete(`${run.id}:atom`);
      message.success("Run deleted");
      await refreshRuns();
    } catch (e) {
      const err = e as { code: string; message: string };
      message.error(`${err.code}: ${err.message}`);
    }
  };

  const runPca = async (mode: PcaMode) => {
    if (!selectedRun) return;
    const runId = selectedRun;
    setPcaBusy(true);
    try {
      const r = await ipc.request<PcaAnalysisResponse>("analysis.pca", { run_id: runId, mode });
      let analysisId = r.analysis_id;
      let fromCache = !r.job_id;
      if (r.job_id) {
        trackJob(r.job_id, "analysis.pca");
        const done = await watchJob(r.job_id);
        if (done.status !== "COMPLETED") {
          message.error(`PCA ${done.status}: ${done.error?.message ?? ""}`);
          return;
        }
        const completedAnalysisId = done.result?.analysis_id;
        if (typeof completedAnalysisId === "string") analysisId = completedAnalysisId;
        fromCache = false;
      }
      if (!analysisId) {
        throw new Error("analysis.pca returned no analysis_id");
      }
      const full = await ipc.request<PcaPayload>("result.get_pca", { analysis_id: analysisId });
      const key = `${runId}:${mode}`;
      pcaCache.set(key, full);
      if (activeKeyRef.current === key) setPca(full);
      message.success(fromCache ? "PCA loaded from cache" : "PCA ready");
    } catch (e) {
      const err = e as { code: string; message: string };
      message.error(`${err.code}: ${err.message}`);
    } finally {
      setPcaBusy(false);
    }
  };

  const switchPcaMode = (mode: PcaMode) => {
    if (mode === pcaMode) return;
    setPcaMode(mode);
    setSelectedSample(null);
    const key = selectedRun ? `${selectedRun}:${mode}` : null;
    const cached = key ? pcaCache.get(key) ?? null : null;
    setPca(cached);
    if (!cached && selectedRun) void runPca(mode);
  };

  const heatmapFrameIndex = selectedPoint?.frame ?? st.activeFrameIndex;
  const loadHeatmap = useCallback(async () => {
    if (!selectedRun || !d?.id) return;
    try {
      const h = await ipc.request<{ atoms: number[]; features: number[]; values: number[][]; atomOffset: number }>(
        "result.heatmap",
        { run_id: selectedRun, frame_index: heatmapFrameIndex },
      );
      setHeatmap(h);
    } catch {
      setHeatmap(null);
    }
  }, [selectedRun, d?.id, heatmapFrameIndex]);

  useEffect(() => {
    setHeatmap(null);
    void loadHeatmap();
  }, [loadHeatmap]);

  if (!d) return <Empty description="Register a dataset first" style={{ marginTop: 120 }} />;

  // memoized on [pca, colorBy]: the option embeds function closures (tooltip
  // formatter), and echarts-for-react re-applies the notMerge option whenever
  // its deep compare fails — live status refreshes re-render this page while
  // jobs run, which would otherwise reset the user's dataZoom on each refresh
  const pcaOption = useMemo(
    () =>
      pca
        ? {
            grid: { left: 60, right: 24, top: 36, bottom: 54 },
            toolbox: {
              right: 4,
              top: 0,
              feature: {
                dataZoom: { title: { zoom: "Zoom (drag a box)", back: "Back" } },
                restore: { title: "Restore view" },
                saveAsImage: { title: "Save as image", name: `pca_${pca.mode ?? "structure"}` },
              },
              iconStyle: { borderColor: "#616161" },
              emphasis: { iconStyle: { borderColor: "#0F6CBD" } },
            },
            dataZoom: [
              ...createCartesianDataZoom(),
              { type: "slider", xAxisIndex: 0, height: 14, bottom: 6, filterMode: "none" },
            ],
            tooltip: {
              formatter: (p: { dataIndex: number }) => {
                const pt = pca.points[p.dataIndex];
                const lines = [pt.atom != null ? `Frame <b>${pt.frame}</b> · atom <b>${pt.atom}</b>` : `Frame <b>${pt.frame}</b>`];
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
        : null,
    [pca, colorBy],
  );

  // click handler memoized for the same reason (onEvents is deep-compared too)
  const pcaEvents = useMemo(
    () => ({
      click: (p: { dataIndex: number }) => {
        const pt = pca?.points[p.dataIndex];
        if (!pt) return;
        if (!d) return;
        setSelectedSample({
          datasetId: d.id,
          runId: pca.run_id,
          mode: pca.mode ?? pcaMode,
          frame: pt.frame,
          atom: pt.atom,
        });
        useWorkspace.getState().setActiveFrame(pt.frame);
        message.info(`Frame ${pt.frame} selected — open Explore to view`);
      },
    }),
    [d, message, pca, pcaMode, setSelectedSample],
  );

  const heatmapOption = useMemo(() => {
    if (!heatmap) return null;
    const flatValues = heatmap.values.flat();
    if (flatValues.length === 0) return null;
    const rawMin = Math.min(...flatValues);
    const rawMax = Math.max(...flatValues);
    const rangePadding = rawMin === rawMax ? Math.max(Math.abs(rawMin) * 0.05, 1e-6) : 0;
    return {
      grid: { left: 8, right: 42, top: 8, bottom: 8 },
      tooltip: {
        formatter: (p: { value: [number, number, number] }) => {
          const [featureIndex, atomIndex, value] = p.value;
          return `Atom <b>${heatmap.atoms[atomIndex] ?? atomIndex}</b><br/>Feature <b>${heatmap.features[featureIndex] ?? featureIndex}</b><br/>Value <b>${Number(value).toFixed(5)}</b>`;
        },
      },
      xAxis: {
        type: "category",
        data: heatmap.features.map(String),
        show: false,
      },
      yAxis: {
        type: "category",
        data: heatmap.atoms.map(String),
        inverse: true,
        show: false,
      },
      visualMap: {
        min: rawMin - rangePadding,
        max: rawMax + rangePadding,
        calculable: false,
        show: true,
        right: 4,
        top: "center",
        itemWidth: 12,
        itemHeight: 160,
        inRange: { color: ["#2166AC", "#F7F7F7", "#B2182B"] },
        text: ["hi", "lo"],
        textStyle: { fontSize: 10 },
      },
      series: [
        {
          type: "heatmap",
          data: heatmap.values.flatMap((row, i) => row.map((v, j) => [j, i, v])),
          emphasis: { itemStyle: { borderColor: "#242424", borderWidth: 1 } },
        },
      ],
      animation: false,
    };
  }, [heatmap]);

  // Keep the history table dense while allowing every descriptor currently
  // listed in it to fit without wrapping or truncation.
  const descriptorColumnWidth = useMemo(() => {
    const longest = runs.reduce(
      (max, run) => Math.max(max, run.descriptor_name.length),
      "Descriptor".length,
    );
    return Math.max(112, longest * 8 + 24);
  }, [runs]);

  return (
    <div className="results-page">
      <section className="results-card results-run-card">
        <SectionHeading title="RUN" meta={`${runs.length} result${runs.length === 1 ? "" : "s"}`} />
        {runs.length > 0 ? (
          <Table
            className="results-run-table"
            size="small"
            tableLayout="fixed"
            pagination={runs.length > 5 ? { pageSize: 5 } : false}
            rowKey="id"
            dataSource={runs}
            onRow={(r) => ({
              onClick: () => setSelectedRun(r.id),
              style: { cursor: "pointer", background: r.id === selectedRun ? "#EBF3FC" : undefined },
            })}
            columns={[
                { title: "Descriptor", dataIndex: "descriptor_name", key: "d", width: descriptorColumnWidth },
                { title: "Scope", dataIndex: "scope", key: "s", width: 84 },
                {
                  title: "Shape",
                  dataIndex: "shape",
                  key: "shape",
                  width: 130,
                  align: "center",
                  render: (v: string | null | undefined) => v ? (
                    <Typography.Text code style={{ fontSize: 11 }} title={v}>{v}</Typography.Text>
                  ) : (
                    <Typography.Text type="secondary">—</Typography.Text>
                  ),
                },
                { title: "Status", dataIndex: "status", key: "st", width: 100, render: (v: string) => (
                  <Typography.Text style={{ color: RUN_STATUS_COLOR[v], fontWeight: 600, fontSize: 12 }}>{v}</Typography.Text>
                ) },
                { title: "Created", dataIndex: "created_at", key: "c", width: 168, render: (v: string) => new Date(v).toLocaleString() },
                {
                  title: "",
                  key: "actions",
                  width: 52,
                  render: (_: unknown, r: RunRow) => (
                    // stopPropagation: the delete control must not select the row
                    <span onClick={(e) => e.stopPropagation()}>
                      <Popconfirm
                        title="Delete this run?"
                        description="Removes the run together with its PCA analyses, job history and stored result files."
                        okText="Delete"
                        cancelText="Cancel"
                        okButtonProps={{ danger: true }}
                        disabled={r.status === "QUEUED" || r.status === "RUNNING"}
                        onConfirm={() => void deleteRun(r)}
                      >
                        <Button
                          size="small"
                          type="text"
                          aria-label="Delete run"
                          title={r.status === "QUEUED" || r.status === "RUNNING" ? "Cancel the running job first" : "Delete run"}
                          icon={<Delete16Regular />}
                          disabled={r.status === "QUEUED" || r.status === "RUNNING"}
                        />
                      </Popconfirm>
                    </span>
                  ),
                },
              ]}
          />
        ) : (
          <Empty description="No completed runs yet — compute a descriptor first" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </section>

      {runs.length > 0 && <div className="results-analysis-layout">
        {/* PCA plot-first */}
        <section className="results-card results-pca-card">
          <SectionHeading
            title="PCA"
            meta={runs.find((r) => r.id === selectedRun)?.descriptor_name ?? "—"}
          />
          <Space className="results-pca-controls" wrap>
            <Select
              size="small"
              value={pcaMode}
              style={{ width: 130 }}
              onChange={switchPcaMode}
              options={[
                { value: "structure", label: "By structure" },
                { value: "atom", label: "By atom" },
              ]}
            />
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
            <Button size="small" icon={<ArrowSync16Regular />} loading={pcaBusy} onClick={() => void runPca(pcaMode)}>
              Run PCA
            </Button>
          </Space>
          {pca ? (
            <div className="results-chart-frame">
              <ReactECharts
                className="results-pca-chart"
                style={{ width: "100%", height: "100%" }}
                option={pcaOption}
                notMerge
                onEvents={pcaEvents}
              />
            </div>
          ) : (
            <Empty description="Click Run PCA" style={{ marginTop: 120 }} />
          )}
        </section>

        <div className="results-side-column">
          <section className="results-card results-selected-sample-card">
            <SectionHeading title="SELECTED SAMPLE" />
            {selectedPoint != null ? (
              <div className="results-sample-details">
                <Row k="Frame" v={String(selectedPoint.frame)} />
                {selectedPoint.atom != null && <Row k="Atom" v={String(selectedPoint.atom)} />}
                <Row k="Formula" v={selectedFrame?.formula ?? (selectedFrameBusy ? "Loading…" : "—")} />
                <Row
                  k="E / atom"
                  v={selectedFrame?.energy_per_atom != null ? `${selectedFrame.energy_per_atom.toFixed(4)} eV` : "—"}
                />
                <Row
                  k="Max |F|"
                  v={selectedFrame?.force_max != null ? `${selectedFrame.force_max.toFixed(4)} eV/Å` : "—"}
                />
                <Button
                  size="small"
                  icon={<ArrowRight16Regular />}
                  onClick={() => {
                    st.setActiveFrame(selectedPoint.frame);
                    st.setPage("explore");
                  }}
                >
                  Open in Explore
                </Button>
              </div>
            ) : (
              <Typography.Text type="secondary" className="results-card-empty-copy">
                Click a PCA point to inspect its structure.
              </Typography.Text>
            )}
          </section>

          <section className="results-card results-structure-card">
            <SectionHeading title="SELECTED STRUCTURE" meta={selectedFrame ? `Frame ${selectedFrame.index}` : undefined} />
            {selectedFrame ? (
              <StructurePreview
                frame={selectedFrame}
                onOpen={() => {
                  st.setActiveFrame(selectedFrame.index);
                  st.setPage("explore");
                }}
              />
            ) : (
              <div className="results-structure-empty">
                <Typography.Text type="secondary">
                  {selectedFrameBusy ? "Loading structure…" : "Select a PCA point to preview its structure."}
                </Typography.Text>
              </div>
            )}
          </section>

          <section className="results-card results-heatmap-card">
            <SectionHeading
              title="HEATMAP"
              meta={heatmap ? `Frame ${heatmapFrameIndex}` : undefined}
            />
            {heatmapOption && heatmap && heatmap.values.length > 0 ? (
              <div className="results-chart-frame">
                <ReactECharts
                  className="results-heatmap-chart"
                  style={{ width: "100%", height: "100%" }}
                  option={heatmapOption}
                  notMerge
                />
              </div>
            ) : (
              <div className="results-heatmap-empty">
                <Typography.Text type="secondary">
                  {selectedRun ? "Heatmap is available for atom-level results." : "Select a run to load a heatmap."}
                </Typography.Text>
              </div>
            )}
          </section>
        </div>
      </div>}
    </div>
  );
}

function SectionHeading({ title, meta }: { title: string; meta?: string }) {
  return (
    <div className="results-section-heading">
      <Typography.Text strong>{title}</Typography.Text>
      {meta && <Typography.Text type="secondary">{meta}</Typography.Text>}
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

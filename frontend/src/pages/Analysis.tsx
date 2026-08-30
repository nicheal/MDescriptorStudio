/*
 * Analysis is the successor to the old Results page.  It owns one shared run
 * selector, one compact inspector, and a set of real backend-backed modules.
 * Plotly is deliberately scoped to this page; Overview remains ECharts and
 * Explore remains 3Dmol.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import Plot from "react-plotly.js";
import {
  App as AntApp,
  Button,
  Empty,
  Input,
  InputNumber,
  Progress,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import {
  ArrowRight16Regular,
  ArrowSync16Regular,
  ArrowDownload16Regular,
  CheckmarkCircle16Regular,
  Delete16Regular,
} from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { activeDataset, useWorkspace, type PcaMode } from "../stores/workspace";
import { trackJob, watchJob } from "../stores/jobs";
import StructurePreview from "../components/StructurePreview";
import { normalizePoints, type AnalysisPoint } from "./analysisPreview";
import type {
  AnalysisJobResponse,
  AnalysisPreview,
  AnalysisRow,
  FramePayload,
  PcaAnalysisResponse,
  PcaPayload,
  RunRow,
} from "../types/protocol";

type TabKey = "overview" | "projection" | "similarity" | "clusters" | "outliers" | "sampling" | "coverage" | "compare";
type ProjectionName = "pca" | "umap" | "tsne";
type OverviewAnalysis = "feature_variance" | "feature_correlation" | "effective_dimension" | "trajectory" | "drift" | "sensitivity";

type Point = AnalysisPoint;

const TAB_LABELS: Record<TabKey, string> = {
  overview: "Overview",
  projection: "Projection",
  similarity: "Similarity",
  clusters: "Clusters",
  outliers: "Outliers",
  sampling: "Sampling",
  coverage: "Coverage",
  compare: "Compare",
};

export default function Analysis() {
  const { message } = AntApp.useApp();
  const st = useWorkspace();
  const dataset = activeDataset(st);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [analyses, setAnalyses] = useState<AnalysisRow[]>([]);
  const [tab, setTab] = useState<TabKey>("overview");
  const [projection, setProjection] = useState<ProjectionName>("pca");
  const [mode, setMode] = useState<PcaMode>("structure");
  const [preprocess, setPreprocess] = useState("raw");
  const [points, setPoints] = useState<Point[]>([]);
  const [preview, setPreview] = useState<AnalysisPreview | null>(null);
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lastJobProgress, setLastJobProgress] = useState<number | null>(null);
  const [selectedIndices, setSelectedIndices] = useState<number[]>([]);
  const [inspectedPoint, setInspectedPoint] = useState<Point | null>(null);
  const [selectedFrame, setSelectedFrame] = useState<FramePayload | null>(null);
  const [selectedFrameBusy, setSelectedFrameBusy] = useState(false);
  const [colorBy, setColorBy] = useState<"none" | "energy" | "force_max" | "volume">("none");
  const [clusterAlgorithm, setClusterAlgorithm] = useState("kmeans");
  const [outlierAlgorithm, setOutlierAlgorithm] = useState("lof");
  const [samplingAlgorithm, setSamplingAlgorithm] = useState("fps");
  const [secondRun, setSecondRun] = useState<string | null>(null);
  const [exportFormat, setExportFormat] = useState("csv");
  const [exportPath, setExportPath] = useState("");
  const [k, setK] = useState(10);
  const [nClusters, setNClusters] = useState(6);
  const [nSamples, setNSamples] = useState(1000);
  const [contamination, setContamination] = useState(0.01);
  const [queryIndex, setQueryIndex] = useState(0);
  const [overviewAnalysis, setOverviewAnalysis] = useState<OverviewAnalysis>("feature_variance");
  const [trajectoryStep, setTrajectoryStep] = useState(1);

  const selectedRun = st.activeDescriptorRunId;
  const setSelectedRun = st.setActiveRun;
  const selectedPoint = inspectedPoint ?? points.find((point) => point.i === selectedIndices[0]) ?? null;

  const refresh = useCallback(async () => {
    if (!dataset) {
      setRuns([]);
      setAnalyses([]);
      return;
    }
    try {
      const [resultRows, analysisRows] = await Promise.all([
        ipc.request<RunRow[]>("result.list", { dataset_id: dataset.id }),
        ipc.request<AnalysisRow[]>("analysis.list", { }),
      ]);
      setRuns(resultRows);
      setAnalyses(analysisRows.filter((row) => (row.dataset_ids ?? []).includes(dataset.id) || row.descriptor_run_id && resultRows.some((run) => run.id === row.descriptor_run_id)));
      const current = useWorkspace.getState().activeDescriptorRunId;
      const usable = resultRows.filter((row) => row.status === "COMPLETED");
      setSelectedRun(current && resultRows.some((row) => row.id === current) ? current : usable[0]?.id ?? resultRows[0]?.id ?? null);
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "ANALYSIS"}: ${err.message ?? "Could not load analysis runs"}`);
    }
  }, [dataset, message, setSelectedRun]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const offFinished = ipc.on("job.finished", () => void refresh());
    return offFinished;
  }, [refresh]);

  useEffect(() => {
    if (!dataset || !selectedPoint) {
      setSelectedFrame(null);
      return;
    }
    let disposed = false;
    setSelectedFrameBusy(true);
    void ipc.request<FramePayload>("dataset.frame", { id: dataset.id, index: selectedPoint.frame, bond_cutoff: 2.4 })
      .then((frame) => { if (!disposed) setSelectedFrame(frame); })
      .catch(() => { if (!disposed) setSelectedFrame(null); })
      .finally(() => { if (!disposed) setSelectedFrameBusy(false); });
    return () => { disposed = true; };
  }, [dataset, selectedPoint]);

  const runRequest = useCallback(async (method: string, params: Record<string, unknown>, label: string) => {
    if (!selectedRun) {
      message.warning("Select a completed descriptor run first");
      return null;
    }
    setBusy(true);
    setLastJobProgress(0);
    setSelectedIndices([]);
    setInspectedPoint(null);
    try {
      const response = await ipc.request<AnalysisJobResponse>(method, { ...params, run_id: selectedRun, seed: params.seed ?? 42 });
      let id = response.analysis_id;
      if (response.job_id) {
        trackJob(response.job_id, method);
        const offProgress = ipc.on("job.progress", (data) => {
          const event = data as { job_id: string; progress: number };
          if (event.job_id === response.job_id) setLastJobProgress(event.progress);
        });
        const done = await watchJob(response.job_id);
        offProgress();
        if (done.status !== "COMPLETED") {
          message.error(`${label} ${done.status}: ${done.error?.message ?? ""}`);
          return null;
        }
        if (typeof done.result?.analysis_id === "string") id = done.result.analysis_id;
      }
      if (!id) throw new Error(`${method} returned no analysis_id`);
      setAnalysisId(id);
      const result = await ipc.request<AnalysisPreview>("analysis.preview", { analysis_id: id, limit: 20_000 });
      setPreview(result);
      setPoints(normalizePoints(result));
      const selected = (Array.isArray(result.selected) ? result.selected : [])
        .map((row) => Number(row.i))
        .filter((index) => Number.isInteger(index) && index >= 0);
      if (selected.length) setSelectedIndices(selected);
      setLastJobProgress(1);
      message.success(response.job_id ? `${label} complete` : `${label} loaded from cache`);
      return id;
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? label}: ${err.message ?? "analysis failed"}`);
      return null;
    } finally {
      setBusy(false);
    }
  }, [message, selectedRun]);

  const runProjection = useCallback(async () => {
    if (!selectedRun) return;
    if (projection === "pca") {
      setBusy(true);
      setLastJobProgress(0);
      setSelectedIndices([]);
      setInspectedPoint(null);
      try {
        const response = await ipc.request<PcaAnalysisResponse>("analysis.pca", { run_id: selectedRun, mode, seed: 42, preprocess: "center" });
        let id = response.analysis_id;
        if (response.job_id) {
          trackJob(response.job_id, "analysis.pca");
          const offProgress = ipc.on("job.progress", (data) => {
            const event = data as { job_id: string; progress: number };
            if (event.job_id === response.job_id) setLastJobProgress(event.progress);
          });
          const done = await watchJob(response.job_id);
          offProgress();
          if (done.status !== "COMPLETED") {
            message.error(`PCA ${done.status}: ${done.error?.message ?? ""}`);
            return;
          }
          if (typeof done.result?.analysis_id === "string") id = done.result.analysis_id;
        }
        const payload = await ipc.request<PcaPayload>("result.get_pca", { analysis_id: id });
        setAnalysisId(id);
        setPreview(null);
        setPoints(payload.points.map((point) => ({ i: point.i, frame: point.frame, row: point.atom, x: point.pc1, y: point.pc2, energy: point.energy, force_max: point.force_max, volume: point.volume })));
        setSelectedIndices([]);
        setLastJobProgress(1);
        message.success(response.job_id ? "PCA complete" : "PCA loaded from cache");
      } catch (error) {
        const err = error as { code?: string; message?: string };
        message.error(`${err.code ?? "PCA"}: ${err.message ?? "analysis failed"}`);
      } finally {
        setBusy(false);
      }
      return;
    }
    await runRequest(`analysis.${projection}`, { mode, preprocess, n_neighbors: 15, min_dist: 0.1, perplexity: 30, max_iter: 1000 }, projection.toUpperCase());
  }, [message, mode, preprocess, projection, runRequest, selectedRun]);

  const runTabAnalysis = useCallback(async () => {
    if (tab === "projection") return runProjection();
    if (tab === "similarity") {
      await runRequest("analysis.similarity", { k, query_index: queryIndex, metric: "cosine", preprocess: "raw" }, "Similarity");
    } else if (tab === "clusters") {
      await runRequest("analysis.cluster", { algorithm: clusterAlgorithm, n_clusters: nClusters, preprocess: "standardized" }, clusterAlgorithm.toUpperCase());
    } else if (tab === "outliers") {
      await runRequest("analysis.outlier", { algorithm: outlierAlgorithm, k, contamination, preprocess: "standardized" }, outlierAlgorithm.toUpperCase());
    } else if (tab === "sampling") {
      await runRequest("analysis.sampling", { algorithm: samplingAlgorithm, n_samples: nSamples, mode }, "Sampling");
    } else if (tab === "coverage" || tab === "compare") {
      if (!secondRun || !selectedRun) {
        message.warning("Select a reference/query run pair");
        return;
      }
      const method = tab === "coverage" ? "analysis.coverage" : "analysis.compare";
      const parameters = tab === "coverage" ? { reference_run_id: selectedRun, query_run_id: secondRun, metric: "euclidean", q95: undefined, q99: undefined } : { left_run_id: selectedRun, right_run_id: secondRun };
      await runRequest(method, parameters, tab === "coverage" ? "Coverage" : "Compare");
    } else if (tab === "overview") {
      if ((overviewAnalysis === "drift" || overviewAnalysis === "sensitivity") && (!secondRun || !selectedRun)) {
        message.warning("Select a reference/query run pair");
        return;
      }
      const overviewParams: Record<string, unknown> = overviewAnalysis === "drift"
        ? { reference_run_id: selectedRun, query_run_id: secondRun, metric: "euclidean" }
        : overviewAnalysis === "sensitivity"
          ? { run_ids: [selectedRun, secondRun] }
          : overviewAnalysis === "trajectory"
            ? { frame_step: trajectoryStep }
            : overviewAnalysis === "feature_correlation"
              ? { top_k: 20 }
              : overviewAnalysis === "effective_dimension"
                ? { preprocess: "center" }
                : { top_k: 20 };
      await runRequest(`analysis.${overviewAnalysis}`, overviewParams, overviewAnalysis.replaceAll("_", " "));
    }
  }, [clusterAlgorithm, contamination, k, mode, nClusters, nSamples, overviewAnalysis, queryIndex, runProjection, runRequest, samplingAlgorithm, secondRun, selectedRun, tab, trajectoryStep, message]);

  const handlePoint = useCallback((point: Point) => {
    setInspectedPoint(point);
    setSelectedIndices([point.i]);
    if (dataset && selectedRun) {
      useWorkspace.getState().setSelectedSample({ datasetId: dataset.id, runId: selectedRun, mode, frame: point.frame, atom: point.row });
      useWorkspace.getState().setActiveFrame(point.frame);
    }
  }, [dataset, mode, selectedRun]);

  const plot = useMemo(() => {
    if (!points.length) return null;
    const values = points.map((point) => colorBy === "energy" ? point.energy : colorBy === "force_max" ? point.force_max : colorBy === "volume" ? point.volume : undefined);
    const hasColor = values.some((value) => value != null && Number.isFinite(value));
    return (
      <Plot
        data={[{
          type: "scattergl",
          mode: "markers",
          x: points.map((point) => point.x),
          y: points.map((point) => point.y),
          text: points.map((point) => `frame ${point.frame}${point.row == null ? "" : ` · row ${point.row}`}`),
          customdata: points.map((point) => [point.i, point.frame, point.row ?? -1]),
          marker: hasColor ? { size: 7, color: values as number[], colorscale: "Viridis", showscale: true, colorbar: { title: { text: colorBy } } } : { size: 7, color: "#0F6CBD" },
          selectedpoints: selectedIndices,
          hovertemplate: "%{text}<br>x=%{x:.5g}<br>y=%{y:.5g}<extra></extra>",
        }]}
        layout={{ autosize: true, margin: { l: 56, r: 24, t: 16, b: 48 }, paper_bgcolor: "#FFFFFF", plot_bgcolor: "#FFFFFF", dragmode: "lasso", hovermode: "closest", xaxis: { title: projection === "pca" ? "PC1" : `${projection.toUpperCase()}-1`, gridcolor: "#F0F1F3" }, yaxis: { title: projection === "pca" ? "PC2" : `${projection.toUpperCase()}-2`, gridcolor: "#F0F1F3" }, showlegend: false }}
        config={{ responsive: true, displaylogo: false, modeBarButtonsToAdd: ["select2d", "lasso2d"], modeBarButtonsToRemove: ["toImage"] }}
        style={{ width: "100%", height: "100%" }}
        onClick={(event) => {
          const index = event.points?.[0]?.pointIndex;
          if (typeof index === "number" && points[index]) handlePoint(points[index]);
        }}
        onSelected={(event) => {
          const indices = (event?.points ?? []).map((point) => point.pointIndex).filter((index): index is number => typeof index === "number");
          setSelectedIndices(indices);
          const first = indices[0];
          if (first != null && points[first]) handlePoint(points[first]);
        }}
      />
    );
  }, [colorBy, handlePoint, points, projection, selectedIndices]);

  const exportSelection = async () => {
    if (!selectedRun || !exportPath.trim()) {
      message.warning("Choose an output path first");
      return;
    }
    const indices = selectedIndices.length ? selectedIndices : points.map((point) => point.i);
    try {
      const response = await ipc.request<AnalysisJobResponse>("analysis.export", { run_id: selectedRun, indices, mode, format: exportFormat, output_path: exportPath.trim() });
      if (!response.job_id) {
        message.success("Export loaded from cache");
        return;
      }
      trackJob(response.job_id, "analysis.export");
      const done = await watchJob(response.job_id);
      if (done.status === "COMPLETED") message.success(`Export written to ${String(done.result?.output_path ?? exportPath)}`);
      else message.error(`Export ${done.status}: ${done.error?.message ?? ""}`);
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "EXPORT"}: ${err.message ?? "export failed"}`);
    }
  };

  const deleteAnalysis = async (row: AnalysisRow) => {
    try {
      await ipc.request("analysis.delete", { analysis_id: row.id });
      if (analysisId === row.id) {
        setAnalysisId(null);
        setPreview(null);
        setPoints([]);
      }
      await refresh();
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "ANALYSIS"}: ${err.message ?? "delete failed"}`);
    }
  };

  if (!dataset) return <Empty description="Register a dataset first" style={{ marginTop: 120 }} />;
  const selectedRunRow = runs.find((run) => run.id === selectedRun);
  const completedRuns = runs.filter((run) => run.status === "COMPLETED");

  return (
    <div className="analysis-page">
      <section className="analysis-toolbar">
        <Space wrap>
          <Typography.Text strong>Run</Typography.Text>
          <Select
            aria-label="Analysis descriptor run"
            value={selectedRun ?? undefined}
            placeholder="Select completed run"
            style={{ width: 250 }}
            onChange={setSelectedRun}
            options={runs.map((run) => ({ value: run.id, label: `${run.descriptor_name} · ${run.shape ?? "unknown shape"} · ${run.status}`, disabled: run.status !== "COMPLETED" }))}
          />
          <Tag color={selectedRunRow?.status === "COMPLETED" ? "green" : "orange"}>{selectedRunRow?.status ?? "No run"}</Tag>
          <Button size="small" icon={<ArrowSync16Regular />} onClick={() => void refresh()}>Refresh</Button>
        </Space>
        {busy && <Progress percent={Math.round((lastJobProgress ?? 0) * 100)} size="small" style={{ width: 180, marginLeft: "auto" }} />}
      </section>

      <Tabs
        activeKey={tab}
        onChange={(value) => setTab(value as TabKey)}
        items={(Object.keys(TAB_LABELS) as TabKey[]).map((key) => ({ key, label: TAB_LABELS[key] }))}
      />

      <div className="analysis-workspace">
        <main className="analysis-main">
          <section className="analysis-card analysis-controls">
            {tab === "projection" && <ProjectionControls projection={projection} setProjection={setProjection} mode={mode} setMode={setMode} preprocess={preprocess} setPreprocess={setPreprocess} />}
            {tab === "similarity" && <Typography.Text type="secondary">Find the nearest descriptor points to a selected query index.</Typography.Text>}
            {tab === "clusters" && <Space wrap><Typography.Text>Algorithm</Typography.Text><Select value={clusterAlgorithm} onChange={setClusterAlgorithm} options={["kmeans", "dbscan", "hdbscan", "agglomerative"].map((value) => ({ value, label: value.toUpperCase() }))} /><Typography.Text>Clusters</Typography.Text><InputNumber min={2} value={nClusters} onChange={(value) => setNClusters(value ?? 6)} /></Space>}
            {tab === "outliers" && <Space wrap><Typography.Text>Algorithm</Typography.Text><Select value={outlierAlgorithm} onChange={setOutlierAlgorithm} options={["lof", "knn", "isolation_forest", "mahalanobis"].map((value) => ({ value, label: value.toUpperCase() }))} /><Typography.Text>Contamination</Typography.Text><InputNumber min={0.001} max={0.5} step={0.001} value={contamination} onChange={(value) => setContamination(value ?? 0.01)} /></Space>}
            {tab === "sampling" && <Space wrap><Typography.Text>Method</Typography.Text><Select value={samplingAlgorithm} onChange={setSamplingAlgorithm} options={["fps", "random", "stratified", "cluster_representative", "per_element"].map((value) => ({ value, label: value.replaceAll("_", " ") }))} /><Typography.Text>Target</Typography.Text><InputNumber min={1} value={nSamples} onChange={(value) => setNSamples(value ?? 1000)} /><Select value={mode} onChange={setMode} options={[{ value: "structure", label: "Structure" }, { value: "atom", label: "Atom" }]} /></Space>}
            {(tab === "coverage" || tab === "compare" || (tab === "overview" && (overviewAnalysis === "drift" || overviewAnalysis === "sensitivity"))) && <Space wrap><Typography.Text>{tab === "compare" ? "Left" : "Reference"}</Typography.Text><Select value={selectedRun ?? undefined} style={{ width: 220 }} options={completedRuns.map((run) => ({ value: run.id, label: run.descriptor_name + " · " + run.id }))} onChange={setSelectedRun} /><Typography.Text>{tab === "compare" ? "Right" : "Query"}</Typography.Text><Select value={secondRun ?? undefined} style={{ width: 220 }} options={completedRuns.filter((run) => run.id !== selectedRun).map((run) => ({ value: run.id, label: run.descriptor_name + " · " + run.id }))} onChange={setSecondRun} /></Space>}
            {tab === "similarity" && <Space wrap><Typography.Text>Query index</Typography.Text><InputNumber min={0} value={queryIndex} onChange={(value) => setQueryIndex(value ?? 0)} /><Typography.Text>k</Typography.Text><InputNumber min={1} value={k} onChange={(value) => setK(value ?? 10)} /></Space>}
            {tab === "overview" && <Space wrap><Typography.Text>Module</Typography.Text><Select value={overviewAnalysis} onChange={setOverviewAnalysis} options={[{ value: "feature_variance", label: "Feature variance" }, { value: "feature_correlation", label: "Feature correlation" }, { value: "effective_dimension", label: "Effective dimension" }, { value: "trajectory", label: "Trajectory" }, { value: "drift", label: "Dataset drift" }, { value: "sensitivity", label: "Parameter sensitivity" }]} /><Typography.Text type="secondary">All results stay on the backend as bounded artifacts.</Typography.Text></Space>}
            {tab === "overview" && overviewAnalysis === "trajectory" && <Space wrap><Typography.Text>Frame step</Typography.Text><InputNumber min={1} value={trajectoryStep} onChange={(value) => setTrajectoryStep(value ?? 1)} /></Space>}
            <Space wrap style={{ marginTop: 10 }}>
              <Button type="primary" icon={<CheckmarkCircle16Regular />} loading={busy} disabled={!selectedRun} onClick={() => void runTabAnalysis()}>{tab === "projection" ? `Run ${projection.toUpperCase()}` : tab === "overview" ? `Run ${overviewAnalysis.replaceAll("_", " ")}` : `Run ${TAB_LABELS[tab]}`}</Button>
              {points.length > 0 && <Select size="small" value={colorBy} onChange={setColorBy} options={[{ value: "none", label: "No color" }, { value: "energy", label: "Energy" }, { value: "force_max", label: "Max |F|" }, { value: "volume", label: "Volume" }]} />}
            </Space>
          </section>

          {tab === "projection" && <section className="analysis-card analysis-plot-card"><SectionHeading title="DESCRIPTOR SPACE" meta={`${points.length.toLocaleString()} preview points${selectedIndices.length ? ` · ${selectedIndices.length} selected` : ""}`} />{points.length ? <div className="analysis-plot-frame">{plot}</div> : <Empty description="Run PCA, UMAP, or t-SNE to populate the Plotly canvas." />}</section>}
          {tab !== "projection" && <ResultPanel preview={preview} points={points} onSelect={(row) => {
            const index = Number(row.i ?? row.sample_index ?? 0);
            const frame = Number(row.frame ?? index);
            handlePoint({ i: index, frame, row: row.row == null ? undefined : Number(row.row), sample_id: row.sample_id == null ? undefined : String(row.sample_id), x: 0, y: 0, label: row.labels == null ? undefined : Number(row.labels), score: row.scores == null ? undefined : Number(row.scores), distance: row.distances == null ? undefined : Number(row.distances) });
          }} />}

          {tab === "sampling" && <section className="analysis-card"><SectionHeading title="EXPORT SELECTED SET" meta="Source data is never modified" /><Space.Compact style={{ width: "100%" }}><Select value={exportFormat} onChange={setExportFormat} options={["json", "csv", "extxyz", "deepmd"].map((value) => ({ value, label: value.toUpperCase() }))} style={{ width: 120 }} /><Input placeholder="D:\\exports\\analysis_subset.csv" value={exportPath} onChange={(event) => setExportPath(event.target.value)} /><Button icon={<ArrowDownload16Regular />} onClick={() => void exportSelection()}>Export</Button></Space.Compact></section>}
        </main>

        <aside className="analysis-inspector">
          <section className="analysis-card"><SectionHeading title="INSPECTOR" meta={selectedPoint ? `Frame ${selectedPoint.frame}` : undefined} />{selectedPoint ? <><Row k="Sample" v={selectedPoint.sample_id ?? String(selectedPoint.i)} /><Row k="Frame" v={String(selectedPoint.frame)} />{selectedPoint.row != null && <Row k="Row" v={String(selectedPoint.row)} />}<Button size="small" icon={<ArrowRight16Regular />} onClick={() => { st.setActiveFrame(selectedPoint.frame); st.setPage("explore"); }}>Open in Explore</Button></> : <Typography.Text type="secondary">Click a point, or use box/lasso selection, to inspect a structure.</Typography.Text>}</section>
          <section className="analysis-card"><SectionHeading title="STRUCTURE PREVIEW" meta={selectedFrame ? `Frame ${selectedFrame.index}` : undefined} />{selectedFrame ? <StructurePreview frame={selectedFrame} onOpen={() => { st.setActiveFrame(selectedFrame.index); st.setPage("explore"); }} /> : <div className="analysis-empty-small">{selectedFrameBusy ? "Loading structure…" : "Select a sample to preview it."}</div>}</section>
          <section className="analysis-card"><SectionHeading title="ANALYSIS HISTORY" meta={`${analyses.length}`} />{analyses.length ? <div className="analysis-history-list">{analyses.slice(0, 10).map((row) => <div className="analysis-history-row" key={row.id}><div><Typography.Text strong>{row.analysis_type}</Typography.Text><Typography.Text type="secondary" style={{ display: "block", fontSize: 11 }}>{new Date(row.created_at).toLocaleString()}</Typography.Text></div><Space size={4}><Tag color={row.status === "COMPLETED" ? "green" : row.status === "STALE" ? "orange" : undefined}>{row.status}</Tag><Button size="small" type="text" icon={<Delete16Regular />} disabled={row.status === "RUNNING" || row.status === "QUEUED"} onClick={() => void deleteAnalysis(row)} /></Space></div>)}</div> : <Typography.Text type="secondary">No analysis artifacts yet.</Typography.Text>}</section>
        </aside>
      </div>
    </div>
  );
}

function ProjectionControls({ projection, setProjection, mode, setMode, preprocess, setPreprocess }: { projection: ProjectionName; setProjection: (value: ProjectionName) => void; mode: PcaMode; setMode: (value: PcaMode) => void; preprocess: string; setPreprocess: (value: string) => void }) {
  return <Space wrap><Typography.Text>Method</Typography.Text><Select value={projection} onChange={setProjection} options={[{ value: "pca", label: "PCA" }, { value: "umap", label: "UMAP" }, { value: "tsne", label: "t-SNE" }]} /><Typography.Text>Granularity</Typography.Text><Select value={mode} onChange={setMode} options={[{ value: "structure", label: "Structure" }, { value: "atom", label: "Atom / local" }]} /><Typography.Text>Preprocess</Typography.Text><Select value={preprocess} onChange={setPreprocess} options={[{ value: "raw", label: "Raw scale" }, { value: "center", label: "Centered" }, { value: "standardized", label: "Standardized" }]} /></Space>;
}

function ResultPanel({ preview, points, onSelect }: { preview: AnalysisPreview | null; points: Point[]; onSelect?: (row: Record<string, unknown>) => void }) {
  if (!preview && !points.length) return <section className="analysis-card"><Empty description="Run an analysis module to see its bounded result preview." /></section>;
  const rows = Array.isArray(preview?.rows)
    ? preview.rows
    : Array.isArray(preview?.selected)
      ? preview.selected
      : Array.isArray(preview?.pairs)
        ? preview.pairs as Record<string, unknown>[]
        : Array.isArray(preview?.runs)
          ? preview.runs as Record<string, unknown>[]
          : Array.isArray(preview?.top_indices)
            ? (preview.top_indices as unknown[]).map((index, position) => ({ rank: position + 1, feature: index, variance: (preview.top_values as unknown[] | undefined)?.[position] }))
            : [];
  if (rows.length) return <section className="analysis-card"><SectionHeading title={String(preview?.kind ?? "RESULT").toUpperCase()} meta={`${rows.length.toLocaleString()} rows`} /><Table size="small" pagination={{ pageSize: 12 }} rowKey={(row, index) => String(row.i ?? row.sample_id ?? row.run_id ?? index)} dataSource={rows} onRow={(row) => ({ onClick: () => onSelect?.(row) })} columns={Object.keys(rows[0]).slice(0, 7).map((key) => ({ title: key, dataIndex: key, key, render: (value: unknown) => typeof value === "number" ? value.toPrecision(6) : String(value ?? "—") }))} /></section>;
  return <section className="analysis-card"><SectionHeading title={String(preview?.kind ?? "RESULT").toUpperCase()} /><pre className="analysis-json-preview">{JSON.stringify(preview, null, 2)}</pre></section>;
}

function SectionHeading({ title, meta }: { title: string; meta?: string }) {
  return <div className="analysis-section-heading"><Typography.Text strong>{title}</Typography.Text>{meta && <Typography.Text type="secondary">{meta}</Typography.Text>}</div>;
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="analysis-inspector-row"><span>{k}</span><Typography.Text code>{v}</Typography.Text></div>;
}

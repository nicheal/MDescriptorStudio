/*
 * Analysis is the successor to the old Results page.  It owns one shared run
 * history/selector, one compact inspector, and a set of real backend-backed modules.
 * Plotly is deliberately scoped to this page; Overview remains ECharts and
 * Explore remains 3Dmol.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Plot from "react-plotly.js";
import type { Data, Layout } from "plotly.js";
import {
  App as AntApp,
  Button,
  Empty,
  Input,
  InputNumber,
  Popconfirm,
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
import { displayableDescriptorRuns, normalizePoints, selectedDisplayIndices, type AnalysisPoint } from "./analysisPreview";
import type {
  AnalysisJobResponse,
  AnalysisChunk,
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
type NumericArrays = Record<string, number[]>;

type Metric = {
  label: string;
  value: string;
};

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

const OVERVIEW_KIND_LABELS: Record<string, string> = {
  feature_variance: "FEATURE VARIANCE",
  feature_correlation: "FEATURE CORRELATION",
  effective_dimension: "EFFECTIVE DIMENSION",
  trajectory: "TRAJECTORY",
  drift: "DATASET DRIFT",
  sensitivity: "PARAMETER SENSITIVITY",
};

const RUN_STATUS_COLOR: Record<string, string> = {
  QUEUED: "#616161",
  RUNNING: "#0F6CBD",
  COMPLETED: "#107C10",
  STALE: "#F0A000",
  FAILED: "#C42B1C",
  CANCELLED: "#8A8A8A",
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
  const [tsnePerplexity, setTsnePerplexity] = useState(30);
  const [overviewArrays, setOverviewArrays] = useState<NumericArrays>({});
  const [overviewArraysBusy, setOverviewArraysBusy] = useState(false);
  const [deletingRunId, setDeletingRunId] = useState<string | null>(null);
  const operationRef = useRef(0);

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
      setSelectedRun(current && usable.some((row) => row.id === current) ? current : usable[0]?.id ?? null);
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "ANALYSIS"}: ${err.message ?? "Could not load analysis runs"}`);
    }
  }, [dataset, message, setSelectedRun]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    operationRef.current += 1;
    setBusy(false);
    setLastJobProgress(null);
    setPoints([]);
    setPreview(null);
    setAnalysisId(null);
    setSelectedIndices([]);
    setInspectedPoint(null);
    setSelectedFrame(null);
    setSecondRun(null);
    setOverviewArrays({});
  }, [dataset?.id, selectedRun]);

  useEffect(() => {
    if (secondRun && (secondRun === selectedRun || !runs.some((run) => run.id === secondRun && run.status === "COMPLETED"))) {
      setSecondRun(null);
    }
  }, [runs, secondRun, selectedRun]);

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

  useEffect(() => {
    const kind = String(preview?.kind ?? "");
    const arrayNames = kind === "effective_dimension"
      ? ["explained_variance"]
      : kind === "trajectory"
        ? ["time", "frames", "step_distance"]
        : [];
    if (tab !== "overview" || !analysisId || !arrayNames.length) {
      setOverviewArrays({});
      setOverviewArraysBusy(false);
      return;
    }

    let disposed = false;
    setOverviewArraysBusy(true);
    void Promise.all(arrayNames.map(async (name) => {
      try {
        const chunk = await ipc.request<AnalysisChunk>("analysis.chunk", {
          analysis_id: analysisId,
          array: name,
          offset: 0,
          limit: 20_000,
        });
        return [name, numericArray(chunk.data)] as const;
      } catch {
        return [name, []] as const;
      }
    })).then((entries) => {
      if (disposed) return;
      setOverviewArrays(Object.fromEntries(entries));
    }).finally(() => {
      if (!disposed) setOverviewArraysBusy(false);
    });

    return () => { disposed = true; };
  }, [analysisId, preview, tab]);

  const runRequest = useCallback(async (method: string, params: Record<string, unknown>, label: string) => {
    if (!selectedRun) {
      message.warning("Select a completed descriptor run first");
      return null;
    }
    const requestRunId = selectedRun;
    const requestDatasetId = dataset?.id;
    const operation = ++operationRef.current;
    const isCurrent = () => operationRef.current === operation
      && useWorkspace.getState().activeDescriptorRunId === requestRunId
      && useWorkspace.getState().activeDatasetId === requestDatasetId;
    setBusy(true);
    setLastJobProgress(0);
    setPoints([]);
    setPreview(null);
    setAnalysisId(null);
    setOverviewArrays({});
    setSelectedIndices([]);
    setInspectedPoint(null);
    useWorkspace.getState().setSelectedSample(null);
    try {
      const response = await ipc.request<AnalysisJobResponse>(method, { ...params, run_id: requestRunId, seed: params.seed ?? 42 });
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
          if (!isCurrent()) return null;
          message.error(`${label} ${done.status}: ${done.error?.message ?? ""}`);
          return null;
        }
        if (typeof done.result?.analysis_id === "string") id = done.result.analysis_id;
      }
      if (!id) throw new Error(`${method} returned no analysis_id`);
      if (!isCurrent()) return null;
      const result = await ipc.request<AnalysisPreview>("analysis.preview", { analysis_id: id, limit: 20_000 });
      if (!isCurrent()) return null;
      setAnalysisId(id);
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
      if (isCurrent()) message.error(`${err.code ?? label}: ${err.message ?? "analysis failed"}`);
      return null;
    } finally {
      if (operationRef.current === operation) setBusy(false);
    }
  }, [dataset?.id, message, selectedRun]);

  const runProjection = useCallback(async () => {
    if (!selectedRun) return;
    const requestRunId = selectedRun;
    const requestDatasetId = dataset?.id;
    if (projection === "pca") {
      const operation = ++operationRef.current;
      const isCurrent = () => operationRef.current === operation
        && useWorkspace.getState().activeDescriptorRunId === requestRunId
        && useWorkspace.getState().activeDatasetId === requestDatasetId;
      setBusy(true);
      setLastJobProgress(0);
      setPoints([]);
      setPreview(null);
      setAnalysisId(null);
      setOverviewArrays({});
      setSelectedIndices([]);
      setInspectedPoint(null);
      useWorkspace.getState().setSelectedSample(null);
      try {
        const response = await ipc.request<PcaAnalysisResponse>("analysis.pca", { run_id: requestRunId, mode, seed: 42, preprocess });
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
            if (!isCurrent()) return;
            message.error(`PCA ${done.status}: ${done.error?.message ?? ""}`);
            return;
          }
          if (typeof done.result?.analysis_id === "string") id = done.result.analysis_id;
        }
        if (!isCurrent()) return;
        const payload = await ipc.request<PcaPayload>("result.get_pca", { analysis_id: id });
        if (!isCurrent()) return;
        setAnalysisId(id);
        setPreview(null);
        setPoints(payload.points.map((point) => ({ i: point.i, frame: point.frame, row: point.atom, x: point.pc1, y: point.pc2, energy: point.energy, force_max: point.force_max, volume: point.volume })));
        setSelectedIndices([]);
        setLastJobProgress(1);
        message.success(response.job_id ? "PCA complete" : "PCA loaded from cache");
      } catch (error) {
        const err = error as { code?: string; message?: string };
        if (isCurrent()) message.error(`${err.code ?? "PCA"}: ${err.message ?? "analysis failed"}`);
      } finally {
        if (operationRef.current === operation) setBusy(false);
      }
      return;
    }
    const projectionParams = projection === "umap"
      ? { mode, preprocess, n_neighbors: 15, min_dist: 0.1 }
      : { mode, preprocess, perplexity: tsnePerplexity === 30 ? undefined : tsnePerplexity, max_iter: 1000 };
    await runRequest(`analysis.${projection}`, projectionParams, projection.toUpperCase());
  }, [dataset?.id, message, mode, preprocess, projection, runRequest, selectedRun, tsnePerplexity]);

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

  const inspectPoint = useCallback((point: Point) => {
    setInspectedPoint(point);
    if (dataset && selectedRun) {
      useWorkspace.getState().setSelectedSample({ datasetId: dataset.id, runId: selectedRun, mode, frame: point.frame, atom: point.row });
      useWorkspace.getState().setActiveFrame(point.frame);
    }
  }, [dataset, mode, selectedRun]);

  const handlePoint = useCallback((point: Point) => {
    setSelectedIndices([point.i]);
    inspectPoint(point);
  }, [inspectPoint]);

  const plot = useMemo(() => {
    if (!points.length) return null;
    const values = points.map((point) => colorBy === "energy" ? point.energy : colorBy === "force_max" ? point.force_max : colorBy === "volume" ? point.volume : undefined);
    const hasColor = values.some((value) => value != null && Number.isFinite(value));
    const displayedSelected = selectedDisplayIndices(points, selectedIndices);
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
           selectedpoints: displayedSelected,
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
          const displayIndices = (event?.points ?? []).map((point) => point.pointIndex).filter((index): index is number => typeof index === "number");
          const indices = displayIndices.map((index) => points[index]?.i).filter((index): index is number => typeof index === "number");
          setSelectedIndices(indices);
          const first = displayIndices[0];
          if (first != null && points[first]) inspectPoint(points[first]);
          else {
            setInspectedPoint(null);
            useWorkspace.getState().setSelectedSample(null);
          }
        }}
      />
    );
  }, [colorBy, handlePoint, inspectPoint, points, projection, selectedIndices]);

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

  const deleteRun = async (run: RunRow) => {
    if (deletingRunId) return;
    setDeletingRunId(run.id);
    try {
      await ipc.request("result.remove", { run_id: run.id });
      message.success("Descriptor result deleted");
      await refresh();
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "RESULT"}: ${err.message ?? "delete failed"}`);
    } finally {
      setDeletingRunId(null);
    }
  };

  if (!dataset) return <Empty description="Register a dataset first" style={{ marginTop: 120 }} />;
  const selectedRunRow = runs.find((run) => run.id === selectedRun);
  const resultRuns = displayableDescriptorRuns(runs);
  const completedRuns = resultRuns.filter((run) => run.status === "COMPLETED");
  const descriptorColumnWidth = Math.max(
    112,
    resultRuns.reduce(
      (max, run) => Math.max(max, run.descriptor_name.length * 8 + 24),
      "Descriptor".length * 8 + 24,
    ),
  );

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
            disabled={busy}
            onChange={setSelectedRun}
            options={completedRuns.map((run) => ({ value: run.id, label: `${run.descriptor_name} · ${run.shape ?? "unknown shape"}` }))}
          />
          <Tag color={selectedRunRow?.status === "COMPLETED" ? "green" : "orange"}>{selectedRunRow?.status ?? "No run"}</Tag>
          <Button size="small" icon={<ArrowSync16Regular />} onClick={() => void refresh()}>Refresh</Button>
        </Space>
        {busy && <Progress percent={Math.round((lastJobProgress ?? 0) * 100)} size="small" style={{ width: 180, marginLeft: "auto" }} />}
      </section>

      <section className="analysis-card analysis-runs-card">
        <SectionHeading title="RUN" meta={`${resultRuns.length} result${resultRuns.length === 1 ? "" : "s"}`} />
        {resultRuns.length ? (
          <Table
            className="analysis-runs-table"
            size="small"
            tableLayout="fixed"
            pagination={resultRuns.length > 5 ? { pageSize: 5 } : false}
            rowKey="id"
            dataSource={resultRuns}
            onRow={(run) => ({
              onClick: () => { if (run.status === "COMPLETED") setSelectedRun(run.id); },
              style: {
                cursor: run.status === "COMPLETED" ? "pointer" : "default",
                background: run.id === selectedRun ? "#EBF3FC" : undefined,
              },
            })}
            columns={[
              { title: "Descriptor", dataIndex: "descriptor_name", key: "descriptor", width: descriptorColumnWidth },
              { title: "Scope", dataIndex: "scope", key: "scope", width: 84 },
              {
                title: "Shape",
                dataIndex: "shape",
                key: "shape",
                width: 130,
                align: "center" as const,
                render: (value: string | null | undefined) => value ? <Typography.Text code style={{ fontSize: 11 }} title={value}>{value}</Typography.Text> : <Typography.Text type="secondary">—</Typography.Text>,
              },
              {
                title: "Status",
                dataIndex: "status",
                key: "status",
                width: 100,
                render: (value: string) => <Typography.Text style={{ color: RUN_STATUS_COLOR[value] ?? "#616161", fontWeight: 600, fontSize: 12 }}>{value}</Typography.Text>,
              },
              { title: "Created", dataIndex: "created_at", key: "created", width: 168, render: (value: string) => new Date(value).toLocaleString() },
              {
                title: "操作",
                key: "actions",
                width: 52,
                render: (_value: unknown, run: RunRow) => {
                  const active = run.status === "QUEUED" || run.status === "RUNNING";
                  return (
                    <span onClick={(event) => event.stopPropagation()}>
                      <Popconfirm
                        title="Delete this descriptor result?"
                        description="This also removes linked analysis history and stored files."
                        okText="Delete"
                        cancelText="Cancel"
                        okButtonProps={{ danger: true }}
                        disabled={active}
                        onConfirm={() => void deleteRun(run)}
                      >
                        <Button
                          size="small"
                          type="text"
                          aria-label={`Delete ${run.descriptor_name} result`}
                          title={active ? "Cancel the running job first" : "Delete descriptor result"}
                          icon={<Delete16Regular />}
                          loading={deletingRunId === run.id}
                          disabled={active || deletingRunId !== null}
                        />
                      </Popconfirm>
                    </span>
                  );
                },
              },
            ]}
          />
        ) : (
          <Empty description="No descriptor results yet — compute a descriptor first" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </section>

      <Tabs
        activeKey={tab}
        onChange={(value) => setTab(value as TabKey)}
        items={(Object.keys(TAB_LABELS) as TabKey[]).map((key) => ({ key, label: TAB_LABELS[key] }))}
      />

      <div className="analysis-workspace">
        <main className="analysis-main">
          <section className="analysis-card analysis-controls">
            {tab === "projection" && <ProjectionControls projection={projection} setProjection={setProjection} mode={mode} setMode={setMode} preprocess={preprocess} setPreprocess={setPreprocess} tsnePerplexity={tsnePerplexity} setTsnePerplexity={setTsnePerplexity} />}
            {tab === "similarity" && <Typography.Text type="secondary">Find the nearest descriptor points to a selected query index.</Typography.Text>}
            {tab === "clusters" && <Space wrap><Typography.Text>Algorithm</Typography.Text><Select value={clusterAlgorithm} onChange={setClusterAlgorithm} options={["kmeans", "dbscan", "hdbscan", "agglomerative"].map((value) => ({ value, label: value.toUpperCase() }))} /><Typography.Text>Clusters</Typography.Text><InputNumber min={2} value={nClusters} onChange={(value) => setNClusters(value ?? 6)} /></Space>}
            {tab === "outliers" && <Space wrap><Typography.Text>Algorithm</Typography.Text><Select value={outlierAlgorithm} onChange={setOutlierAlgorithm} options={["lof", "knn", "isolation_forest", "mahalanobis"].map((value) => ({ value, label: value.toUpperCase() }))} /><Typography.Text>Contamination</Typography.Text><InputNumber min={0.001} max={0.5} step={0.001} value={contamination} onChange={(value) => setContamination(value ?? 0.01)} /></Space>}
            {tab === "sampling" && <Space wrap><Typography.Text>Method</Typography.Text><Select value={samplingAlgorithm} onChange={setSamplingAlgorithm} options={["fps", "random", "stratified", "cluster_representative", "per_element"].map((value) => ({ value, label: value.replaceAll("_", " ") }))} /><Typography.Text>Target</Typography.Text><InputNumber min={1} value={nSamples} onChange={(value) => setNSamples(value ?? 1000)} /><Select value={mode} onChange={setMode} options={[{ value: "structure", label: "Structure" }, { value: "atom", label: "Atom" }]} /></Space>}
            {(tab === "coverage" || tab === "compare" || (tab === "overview" && (overviewAnalysis === "drift" || overviewAnalysis === "sensitivity"))) && <Space wrap><Typography.Text>{tab === "compare" ? "Left" : "Reference"}</Typography.Text><Select value={selectedRun ?? undefined} style={{ width: 220 }} disabled={busy} options={completedRuns.map((run) => ({ value: run.id, label: run.descriptor_name + " · " + run.id }))} onChange={setSelectedRun} /><Typography.Text>{tab === "compare" ? "Right" : "Query"}</Typography.Text><Select value={secondRun ?? undefined} style={{ width: 220 }} disabled={busy} options={completedRuns.filter((run) => run.id !== selectedRun).map((run) => ({ value: run.id, label: run.descriptor_name + " · " + run.id }))} onChange={setSecondRun} /></Space>}
            {tab === "similarity" && <Space wrap><Typography.Text>Query index</Typography.Text><InputNumber min={0} value={queryIndex} onChange={(value) => setQueryIndex(value ?? 0)} /><Typography.Text>k</Typography.Text><InputNumber min={1} value={k} onChange={(value) => setK(value ?? 10)} /></Space>}
            {tab === "overview" && <Space wrap><Typography.Text>Module</Typography.Text><Select value={overviewAnalysis} onChange={setOverviewAnalysis} options={[{ value: "feature_variance", label: "Feature variance" }, { value: "feature_correlation", label: "Feature correlation" }, { value: "effective_dimension", label: "Effective dimension" }, { value: "trajectory", label: "Trajectory" }, { value: "drift", label: "Dataset drift" }, { value: "sensitivity", label: "Parameter sensitivity" }]} /><Typography.Text type="secondary">All results stay on the backend as bounded artifacts.</Typography.Text></Space>}
            {tab === "overview" && overviewAnalysis === "trajectory" && <Space wrap><Typography.Text>Frame step</Typography.Text><InputNumber min={1} value={trajectoryStep} onChange={(value) => setTrajectoryStep(value ?? 1)} /></Space>}
            <Space wrap style={{ marginTop: 10 }}>
              <Button type="primary" icon={<CheckmarkCircle16Regular />} loading={busy} disabled={!selectedRun} onClick={() => void runTabAnalysis()}>{tab === "projection" ? `Run ${projection.toUpperCase()}` : tab === "overview" ? `Run ${overviewAnalysis.replaceAll("_", " ")}` : `Run ${TAB_LABELS[tab]}`}</Button>
              {points.length > 0 && <Select size="small" value={colorBy} onChange={setColorBy} options={[{ value: "none", label: "No color" }, { value: "energy", label: "Energy" }, { value: "force_max", label: "Max |F|" }, { value: "volume", label: "Volume" }]} />}
            </Space>
          </section>

          {tab === "projection" && <section className="analysis-card analysis-plot-card"><SectionHeading title="DESCRIPTOR SPACE" meta={`${points.length.toLocaleString()} preview points${selectedIndices.length ? ` · ${selectedIndices.length} selected` : ""}`} />{points.length ? <div className="analysis-plot-frame">{plot}</div> : <Empty description="Run PCA, UMAP, or t-SNE to populate the Plotly canvas." />}</section>}
          {tab === "overview" && <OverviewResultVisualization preview={preview} arrays={overviewArrays} loading={overviewArraysBusy} />}
          {tab !== "projection" && <ResultPanel preview={preview} points={points} onSelect={(row) => {
            if (row.i == null && row.sample_index == null && row.frame == null) return;
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

function OverviewResultVisualization({ preview, arrays, loading }: { preview: AnalysisPreview | null; arrays: NumericArrays; loading: boolean }) {
  if (!preview) return null;
  const kind = String(preview.kind ?? "");
  const title = OVERVIEW_KIND_LABELS[kind] ?? "ANALYSIS RESULT";
  if (loading) {
    return <section className="analysis-card analysis-visual-card"><SectionHeading title={title} meta="Loading chart data" /><div className="analysis-overview-empty"><Empty description="Loading the result arrays…" /></div></section>;
  }

  let content: ReactNode;
  if (kind === "feature_variance") content = <FeatureVarianceChart preview={preview} />;
  else if (kind === "feature_correlation") content = <FeatureCorrelationChart preview={preview} />;
  else if (kind === "effective_dimension") content = <EffectiveDimensionChart preview={preview} arrays={arrays} />;
  else if (kind === "trajectory") content = <TrajectoryChart preview={preview} arrays={arrays} />;
  else if (kind === "drift") content = <DriftChart preview={preview} />;
  else if (kind === "sensitivity") content = <SensitivityChart preview={preview} />;
  else return null;

  return <section className="analysis-card analysis-visual-card"><SectionHeading title={title} meta="Visual summary" />{content}</section>;
}

function FeatureVarianceChart({ preview }: { preview: AnalysisPreview }) {
  const indices = numericArray(preview.top_indices);
  const values = numericArray(preview.top_values);
  const count = Math.min(indices.length, values.length);
  if (!count) return <OverviewNoData message="No feature variance values were returned." />;
  const rows = Array.from({ length: count }, (_, index) => ({ feature: `Feature ${indices[index]}`, value: values[index] })).reverse();
  return <>
    <MetricStrip metrics={[{ label: "Features shown", value: String(count) }, { label: "Highest variance", value: formatNumber(values[0]) }]} />
    <OverviewPlot
      ariaLabel="Top descriptor feature variance"
      data={[{
        type: "bar",
        orientation: "h",
        x: rows.map((row) => row.value),
        y: rows.map((row) => row.feature),
        marker: { color: "#0F6CBD" },
        hovertemplate: "%{y}<br>variance=%{x:.5g}<extra></extra>",
      }]}
      layout={overviewLayout({ xaxis: { title: "Variance", zeroline: true }, yaxis: { automargin: true } })}
    />
    <ChartCaption>显示方差最大的 descriptor 维度；数值越大，跨样本变化越明显。</ChartCaption>
  </>;
}

function FeatureCorrelationChart({ preview }: { preview: AnalysisPreview }) {
  const pairs = recordArray(preview.pairs)
    .map((pair) => ({
      feature: `F${formatIndex(pair.feature_a)} ↔ F${formatIndex(pair.feature_b)}`,
      value: finiteNumber(pair.correlation),
    }))
    .filter((pair): pair is { feature: string; value: number } => pair.value !== null);
  if (!pairs.length) return <OverviewNoData message="No feature correlation pairs were returned." />;
  const rows = pairs.slice().reverse();
  const strongest = pairs.reduce((best, row) => Math.max(best, Math.abs(row.value)), 0);
  return <>
    <MetricStrip metrics={[{ label: "Pairs shown", value: String(pairs.length) }, { label: "Strongest |r|", value: strongest.toFixed(3) }]} />
    <OverviewPlot
      ariaLabel="Top descriptor feature correlations"
      data={[{
        type: "bar",
        orientation: "h",
        x: rows.map((row) => row.value),
        y: rows.map((row) => row.feature),
        marker: { color: rows.map((row) => row.value >= 0 ? "#0F6CBD" : "#D13438") },
        hovertemplate: "%{y}<br>correlation=%{x:.4f}<extra></extra>",
      }]}
      layout={overviewLayout({ xaxis: { title: "Pearson correlation", range: [-1, 1], zeroline: true }, yaxis: { automargin: true } })}
    />
    <ChartCaption>蓝色表示正相关，红色表示负相关；相关性绝对值越大，特征冗余越明显。</ChartCaption>
  </>;
}

function EffectiveDimensionChart({ preview, arrays }: { preview: AnalysisPreview; arrays: NumericArrays }) {
  const explained = arrays.explained_variance ?? [];
  if (!explained.length) return <OverviewNoData message="The explained-variance array is not available for visualization." />;
  const cumulative: number[] = [];
  let total = 0;
  for (const value of explained) {
    total += value;
    cumulative.push(total);
  }
  const indices = sampledIndices(explained.length, 320);
  const metrics: Metric[] = [
    { label: "Participation ratio", value: formatNumber(preview.participation_ratio) },
    { label: "90% components", value: formatCount(componentThreshold(preview, "0.9")) },
    { label: "95% components", value: formatCount(componentThreshold(preview, "0.95")) },
    { label: "99% components", value: formatCount(componentThreshold(preview, "0.99")) },
  ];
  return <>
    <MetricStrip metrics={metrics} />
    <OverviewPlot
      ariaLabel="Explained and cumulative descriptor variance by component"
      data={[
        {
          type: "bar",
          x: indices.map((index) => index + 1),
          y: indices.map((index) => explained[index] * 100),
          name: "Explained variance",
          marker: { color: "#0F6CBD" },
          hovertemplate: "PC %{x}<br>explained=%{y:.2f}%<extra></extra>",
        },
        {
          type: "scatter",
          mode: "lines",
          x: indices.map((index) => index + 1),
          y: indices.map((index) => cumulative[index] * 100),
          name: "Cumulative",
          line: { color: "#F7630C", width: 2 },
          hovertemplate: "PC %{x}<br>cumulative=%{y:.2f}%<extra></extra>",
        },
      ]}
      layout={overviewLayout({
        xaxis: { title: "Component", type: "linear" },
        yaxis: { title: "Variance (%)", range: [0, 100] },
        legend: { orientation: "h", y: 1.12, x: 0 },
      })}
    />
    <ChartCaption>柱状图是单个主成分贡献率，橙线是累计贡献率；横轴过长时会进行等距抽样显示。</ChartCaption>
  </>;
}

function TrajectoryChart({ preview, arrays }: { preview: AnalysisPreview; arrays: NumericArrays }) {
  const xValues = arrays.time?.length ? arrays.time : arrays.frames ?? [];
  const distances = arrays.step_distance ?? [];
  const count = Math.min(xValues.length, distances.length);
  if (count < 2) return <OverviewNoData message="At least two trajectory points are needed for visualization." />;
  const cumulative: number[] = [];
  let total = 0;
  for (let index = 0; index < count; index += 1) {
    total += distances[index];
    cumulative.push(total);
  }
  const indices = sampledIndices(count, 1000);
  return <>
    <MetricStrip metrics={[{ label: "Samples", value: formatCount(count) }, { label: "Total descriptor distance", value: formatNumber(preview.total_distance) }, { label: "Largest step", value: formatNumber(Math.max(...distances.slice(0, count))) }]} />
    <OverviewPlot
      ariaLabel="Descriptor trajectory step and cumulative distance"
      data={[
        {
          type: "scatter",
          mode: "lines",
          x: indices.map((index) => xValues[index]),
          y: indices.map((index) => distances[index]),
          name: "Step distance",
          line: { color: "#0F6CBD", width: 1.5 },
          hovertemplate: "t=%{x}<br>step=%{y:.5g}<extra></extra>",
        },
        {
          type: "scatter",
          mode: "lines",
          x: indices.map((index) => xValues[index]),
          y: indices.map((index) => cumulative[index]),
          name: "Cumulative distance",
          yaxis: "y2",
          line: { color: "#F7630C", width: 2 },
          hovertemplate: "t=%{x}<br>cumulative=%{y:.5g}<extra></extra>",
        },
      ]}
      layout={overviewLayout({
        xaxis: { title: String(preview.time_unit ?? "Frame") },
        yaxis: { title: "Step distance" },
        yaxis2: { title: "Cumulative distance", overlaying: "y", side: "right", showgrid: false },
        legend: { orientation: "h", y: 1.12, x: 0 },
      })}
    />
    <ChartCaption>蓝线显示相邻帧的 descriptor 变化，橙线显示从轨迹起点累计的变化距离。</ChartCaption>
  </>;
}

function DriftChart({ preview }: { preview: AnalysisPreview }) {
  const rows = recordArray(preview.rows)
    .map((row) => ({ label: finiteNumber(row.labels), distance: finiteNumber(row.distances) }))
    .filter((row): row is { label: number; distance: number } => row.label !== null && row.distance !== null);
  const categoryLabels = ["Covered", "Marginal", "Out of coverage"];
  const categoryColors = ["#107C10", "#F7630C", "#D13438"];
  const grouped = categoryLabels.map((_, category) => rows.filter((row) => row.label === category).map((row) => row.distance));
  const counts = [
    numberOr(preview.covered, grouped[0].length),
    numberOr(preview.marginal, grouped[1].length),
    numberOr(preview.out_of_coverage, grouped[2].length),
  ];
  const q95 = finiteNumber(preview.q95);
  const q99 = finiteNumber(preview.q99);
  const thresholdShapes = [q95, q99]
    .filter((value): value is number => value !== null)
    .map((value, index) => ({ type: "line" as const, x0: value, x1: value, y0: 0, y1: 1, yref: "paper" as const, line: { color: index === 0 ? "#F7630C" : "#D13438", dash: "dash" as const, width: 1.5 } }));
  const data: Data[] = rows.length
    ? grouped.map((values, index) => ({ type: "histogram" as const, x: values, name: categoryLabels[index], opacity: 0.78, marker: { color: categoryColors[index] }, nbinsx: 28, hovertemplate: `${categoryLabels[index]}<br>distance=%{x:.5g}<br>count=%{y}<extra></extra>` }))
    : [{ type: "bar", x: categoryLabels, y: counts, marker: { color: categoryColors }, hovertemplate: "%{x}<br>count=%{y}<extra></extra>" }];
  return <>
    <MetricStrip metrics={[{ label: "Mean distance", value: formatNumber(preview.mean_distance) }, { label: "Median distance", value: formatNumber(preview.median_distance) }, { label: "Max distance", value: formatNumber(preview.max_distance) }, { label: "Out of coverage", value: formatCount(preview.out_of_coverage) }]} />
    <OverviewPlot
      ariaLabel="Distribution of distances from query samples to the reference descriptor set"
      data={data}
      layout={overviewLayout({
        barmode: rows.length ? "stack" : "group",
        xaxis: { title: rows.length ? "Nearest-reference distance" : "Coverage category" },
        yaxis: { title: "Samples" },
        shapes: thresholdShapes,
        legend: { orientation: "h", y: 1.12, x: 0 },
      })}
    />
    <ChartCaption>分布越靠右表示 query 样本离 reference descriptor space 越远；虚线分别对应 q95 和 q99 阈值。</ChartCaption>
  </>;
}

function SensitivityChart({ preview }: { preview: AnalysisPreview }) {
  const runs = recordArray(preview.runs)
    .map((run, index) => ({
      label: parameterLabel(run.parameters, index),
      value: finiteNumber(run.mean_delta_norm),
      detail: parameterText(run.parameters),
    }))
    .filter((run): run is { label: string; value: number; detail: string } => run.value !== null);
  if (!runs.length) return <OverviewNoData message="No completed runs were returned for sensitivity analysis." />;
  const rows = runs.slice().reverse();
  return <>
    <MetricStrip metrics={[{ label: "Runs compared", value: String(runs.length) }, { label: "Largest mean delta", value: formatNumber(Math.max(...runs.map((run) => run.value))) }]} />
    <OverviewPlot
      ariaLabel="Descriptor parameter sensitivity across completed runs"
      data={[{
        type: "bar",
        orientation: "h",
        x: rows.map((run) => run.value),
        y: rows.map((run) => run.label),
        customdata: rows.map((run) => run.detail),
        marker: { color: rows.map((_, index) => index === rows.length - 1 ? "#107C10" : "#0F6CBD") },
        hovertemplate: "%{y}<br>mean delta norm=%{x:.5g}<br>%{customdata}<extra></extra>",
      }]}
      layout={overviewLayout({ xaxis: { title: "Mean descriptor delta norm", zeroline: true }, yaxis: { automargin: true } })}
    />
    <ChartCaption>以第一个 Completed Run 为基准；柱越长，descriptor 参数变化带来的整体结果差异越大。</ChartCaption>
  </>;
}

function OverviewPlot({ data, layout, ariaLabel }: { data: Data[]; layout: Partial<Layout>; ariaLabel: string }) {
  return <div className="analysis-overview-chart-frame" aria-label={ariaLabel}><Plot data={data} layout={layout} config={{ responsive: true, displaylogo: false, modeBarButtonsToRemove: ["toImage"] }} style={{ width: "100%", height: "100%" }} /></div>;
}

function MetricStrip({ metrics }: { metrics: Metric[] }) {
  return <div className="analysis-metric-strip">{metrics.map((metric) => <div className="analysis-metric" key={metric.label}><Typography.Text type="secondary">{metric.label}</Typography.Text><Typography.Text strong>{metric.value}</Typography.Text></div>)}</div>;
}

function ChartCaption({ children }: { children: ReactNode }) {
  return <Typography.Text type="secondary" className="analysis-chart-caption">{children}</Typography.Text>;
}

function OverviewNoData({ message }: { message: string }) {
  return <div className="analysis-overview-empty"><Empty description={message} /></div>;
}

function overviewLayout(overrides: Partial<Layout> = {}): Partial<Layout> {
  return {
    autosize: true,
    margin: { l: 64, r: 28, t: 18, b: 52 },
    paper_bgcolor: "#FFFFFF",
    plot_bgcolor: "#FFFFFF",
    font: { family: "Segoe UI, sans-serif", size: 12, color: "#424242" },
    ...overrides,
  };
}

function numericArray(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return value.map(finiteNumber).filter((item): item is number => item !== null);
}

function recordArray(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null && !Array.isArray(item));
}

function finiteNumber(value: unknown): number | null {
  if (typeof value !== "number" && typeof value !== "string") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function sampledIndices(length: number, maxPoints: number): number[] {
  if (length <= 0) return [];
  if (length <= maxPoints) return Array.from({ length }, (_, index) => index);
  return Array.from({ length: maxPoints }, (_, index) => Math.round(index * (length - 1) / (maxPoints - 1)));
}

function formatNumber(value: unknown): string {
  const number = finiteNumber(value);
  if (number === null) return "—";
  return Math.abs(number) >= 1000 ? number.toLocaleString(undefined, { maximumFractionDigits: 2 }) : number.toPrecision(5);
}

function formatCount(value: unknown): string {
  const number = finiteNumber(value);
  return number === null ? "—" : Math.round(number).toLocaleString();
}

function formatIndex(value: unknown): string {
  const number = finiteNumber(value);
  return number === null ? "?" : String(Math.round(number));
}

function componentThreshold(preview: AnalysisPreview, key: string): number | null {
  const thresholds = preview.components_for_threshold;
  if (typeof thresholds !== "object" || thresholds === null || Array.isArray(thresholds)) return null;
  return finiteNumber((thresholds as Record<string, unknown>)[key]);
}

function numberOr(value: unknown, fallback: number): number {
  return finiteNumber(value) ?? fallback;
}

function parameterText(value: unknown): string {
  if (value == null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function parameterLabel(value: unknown, index: number): string {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>).slice(0, 2);
    if (entries.length) return entries.map(([key, item]) => `${key}=${String(item)}`).join(", ");
  }
  return `Run ${index + 1}`;
}

function ProjectionControls({ projection, setProjection, mode, setMode, preprocess, setPreprocess, tsnePerplexity, setTsnePerplexity }: { projection: ProjectionName; setProjection: (value: ProjectionName) => void; mode: PcaMode; setMode: (value: PcaMode) => void; preprocess: string; setPreprocess: (value: string) => void; tsnePerplexity: number; setTsnePerplexity: (value: number) => void }) {
  return <Space wrap><Typography.Text>Method</Typography.Text><Select value={projection} onChange={setProjection} options={[{ value: "pca", label: "PCA" }, { value: "umap", label: "UMAP" }, { value: "tsne", label: "t-SNE" }]} /><Typography.Text>Granularity</Typography.Text><Select value={mode} onChange={setMode} options={[{ value: "structure", label: "Structure" }, { value: "atom", label: "Atom / local" }]} /><Typography.Text>Preprocess</Typography.Text><Select value={preprocess} onChange={setPreprocess} options={[{ value: "raw", label: "Raw scale" }, { value: "center", label: "Centered" }, { value: "standardized", label: "Standardized" }]} />{projection === "tsne" && <><Typography.Text>Perplexity</Typography.Text><InputNumber min={2} step={1} value={tsnePerplexity} onChange={(value) => setTsnePerplexity(value ?? 30)} /></>}</Space>;
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
  if (rows.length) return <section className="analysis-card"><SectionHeading title={String(preview?.kind ?? "RESULT").toUpperCase()} meta={`${rows.length.toLocaleString()} rows`} /><Table size="small" pagination={{ pageSize: 12 }} rowKey={(row, index) => `${String(row.i ?? row.sample_id ?? row.run_id ?? index)}:${String(row.source_i ?? row.rank ?? index)}`} dataSource={rows} onRow={(row) => ({ onClick: () => onSelect?.(row) })} columns={Object.keys(rows[0]).slice(0, 7).map((key) => ({ title: key, dataIndex: key, key, render: (value: unknown) => typeof value === "number" ? value.toPrecision(6) : String(value ?? "—") }))} /></section>;
  return <section className="analysis-card"><SectionHeading title={String(preview?.kind ?? "RESULT").toUpperCase()} /><pre className="analysis-json-preview">{JSON.stringify(preview, null, 2)}</pre></section>;
}

function SectionHeading({ title, meta }: { title: string; meta?: string }) {
  return <div className="analysis-section-heading"><Typography.Text strong>{title}</Typography.Text>{meta && <Typography.Text type="secondary">{meta}</Typography.Text>}</div>;
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="analysis-inspector-row"><span>{k}</span><Typography.Text code>{v}</Typography.Text></div>;
}

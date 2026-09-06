/*
 * Analysis is the successor to the old Results page.  It owns one shared run
 * selector, one compact inspector, and a set of real backend-backed modules.
 * Descriptor run history lives on the separate Results page.
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
  Modal,
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
  Info16Regular,
} from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { open as openDialog, save as saveDialog } from "@tauri-apps/plugin-dialog";
import { activeDataset, useWorkspace, type PcaMode } from "../stores/workspace";
import { jobStatusLabel, trackJob, watchJob } from "../stores/jobs";
import {
  buildParamsKey,
  latestSlotForTab,
  slotForParams,
  slotKey,
  useAnalysisUi,
  type AnalysisParams,
  type OverviewAnalysis,
  type ProjectionName,
  type TabKey,
} from "../stores/analysisUi";
import { useT, type Pair } from "../i18n";
import StructurePreview from "../components/StructurePreview";
import { normalizePoints, selectedDisplayIndices, type AnalysisPoint } from "./analysisPreview";
import AnalysisResultVisualization, { type AnalysisArrays } from "./analysisVisualizations";
import { getAnalysisMethodGuide, type AnalysisMethodGuide } from "./analysisMethodGuides";
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

type CompareMode = "geometry" | "mantel";

type Point = AnalysisPoint;
type NumericArrays = AnalysisArrays;

type CachedAnalysis = {
  preview: AnalysisPreview | null;
  points: Point[];
  selectedIndices: number[];
  arrays: NumericArrays;
};

// Module-level so computed charts survive leaving the Analysis page. After an
// app restart the backend re-serves the artifacts (analysis.preview /
// result.get_pca), so nothing is recomputed either way.
const analysisCache = new Map<string, CachedAnalysis>();

type ProjectionOverrides = {
  mode?: PcaMode;
  preprocess?: string;
};

type Metric = {
  label: string;
  value: string;
};

function AnalysisRunLabel({ name, shape }: { name: string; shape: string }) {
  return (
    <span className="analysis-run-label">
      <span className="analysis-run-name">{name}</span>
      <span className="analysis-run-shape">{shape}</span>
    </span>
  );
}

// Green dot marking a parameter value whose analysis result is already
// computed (and can be re-displayed without rerunning).
function CacheDot() {
  return <span aria-hidden title="cached result available" style={{ display: "inline-block", width: 6, height: 6, borderRadius: "50%", background: "#107C10", marginInlineEnd: 6, flex: "none" }} />;
}

// Parameter label with the cache dot when its current value is computed.
function ParamLabel({ label, cached }: { label: string; cached: boolean }) {
  return <Typography.Text>{cached ? <CacheDot /> : null}{label}</Typography.Text>;
}

// Prepend the cache dot to every select option that has a computed result.
type CacheOption = { value: string; label: ReactNode };
function withCacheMarks(isCached: (value: string) => boolean, options: CacheOption[]): CacheOption[] {
  return options.map((option) => (isCached(option.value) ? { ...option, label: <><CacheDot />{option.label}</> } : option));
}

const TAB_LABELS: Record<TabKey, Pair> = {
  overview: { en: "Overview", zh: "总览" },
  projection: { en: "Projection", zh: "投影" },
  similarity: { en: "Similarity", zh: "相似度" },
  clusters: { en: "Clusters", zh: "聚类" },
  outliers: { en: "Outliers", zh: "离群点" },
  sampling: { en: "Sampling", zh: "采样" },
  coverage: { en: "Coverage", zh: "覆盖度" },
  compare: { en: "Compare", zh: "对比" },
  local: { en: "Local", zh: "局部" },
  kernel: { en: "Kernel", zh: "核函数" },
};

const OVERVIEW_KIND_LABELS: Record<string, Pair> = {
  feature_variance: { en: "FEATURE VARIANCE", zh: "特征方差" },
  feature_correlation: { en: "FEATURE CORRELATION", zh: "特征相关性" },
  effective_dimension: { en: "EFFECTIVE DIMENSION", zh: "有效维度" },
  trajectory: { en: "TRAJECTORY", zh: "轨迹" },
  drift: { en: "DATASET DRIFT", zh: "数据集漂移" },
  sensitivity: { en: "PARAMETER SENSITIVITY", zh: "参数敏感性" },
  perturbation_sensitivity: { en: "STRUCTURAL PERTURBATION SENSITIVITY", zh: "结构扰动敏感性" },
};

// Lowercase module names (built today via replaceAll("_", " ")) as explicit pairs.
const OVERVIEW_MODULE_LABELS: Record<OverviewAnalysis, Pair> = {
  feature_variance: { en: "feature variance", zh: "特征方差" },
  feature_correlation: { en: "feature correlation", zh: "特征相关性" },
  effective_dimension: { en: "effective dimension", zh: "有效维度" },
  property_correlation: { en: "property correlation", zh: "属性相关性" },
  trajectory: { en: "trajectory", zh: "轨迹" },
  drift: { en: "drift", zh: "漂移" },
  sensitivity: { en: "sensitivity", zh: "敏感性" },
  perturbation_sensitivity: { en: "perturbation sensitivity", zh: "扰动敏感性" },
};

// Sampling method option labels (lowercase, generated the same way as before).
const SAMPLING_LABELS: Record<string, Pair> = {
  fps: { en: "fps", zh: "FPS 最远点采样" },
  novelty_fps: { en: "novelty fps", zh: "新颖性 FPS 采样" },
  uncertainty_diversity: { en: "uncertainty + diversity", zh: "不确定性 + 多样性" },
  random: { en: "random", zh: "随机采样" },
  stratified: { en: "stratified", zh: "分层采样" },
  cluster_representative: { en: "cluster representative", zh: "簇代表采样" },
  per_element: { en: "per element", zh: "按元素采样" },
};

const ARTIFACT_ARRAYS: Record<string, string[]> = {
  pairwise_similarity: ["similarity_matrix", "distance_matrix", "sample_indices"],
  compare: ["left_coords", "right_coords", "left_pair_distances", "right_pair_distances", "neighbor_overlap", "sample_indices"],
  mantel: ["left_pair_distances", "right_pair_distances", "null_distribution", "sample_indices"],
  feature_correlation: ["correlation_matrix", "correlation_feature_indices"],
  effective_dimension: ["explained_variance"],
  property_correlation: ["targets", "predictions", "residuals", "pair_distance", "pair_property_delta"],
  coverage: ["projection_coords", "projection_source", "projection_sample_indices", "labels", "distances"],
  overlap: ["projection_coords", "projection_source", "projection_sample_indices", "labels", "distances"],
  drift: ["projection_coords", "projection_source", "projection_sample_indices", "labels", "distances"],
  trajectory: ["time", "frames", "step_distance", "reference_distance", "cumulative_distance", "event_indices"],
  perturbation_sensitivity: ["amplitudes", "mean_response", "median_response", "p95_response", "max_response", "response_matrix", "sample_indices"],
  local_diversity: ["coords", "sample_indices", "labels", "scores", "cluster_labels", "elements", "coordination", "neighbor_offsets", "neighbor_indices", "neighbor_distances"],
  kernel: ["kernel_matrix", "eigenvalues", "sample_indices"],
};

function pcaPayloadPoints(payload: PcaPayload): Point[] {
  return payload.points.map((point) => ({
    i: point.i,
    frame: point.frame,
    row: point.atom,
    x: point.pc1,
    y: point.pc2,
    energy: point.energy,
    force_max: point.force_max,
    volume: point.volume,
  }));
}

function selectedIndicesFromPreview(result: AnalysisPreview): number[] {
  return (Array.isArray(result.selected) ? result.selected : [])
    .map((row) => Number(row.i ?? row.sample_index))
    .filter((index) => Number.isInteger(index) && index >= 0);
}

function tabForAnalysisType(analysisType: string): TabKey {
  if (["pca", "umap", "tsne"].includes(analysisType)) return "projection";
  if (["feature_variance", "feature_correlation", "effective_dimension", "property_correlation", "trajectory", "drift", "sensitivity", "perturbation_sensitivity"].includes(analysisType)) return "overview";
  if (["similarity", "neighbors", "pairwise", "pairwise_similarity"].includes(analysisType)) return "similarity";
  if (["kmeans", "dbscan", "hdbscan", "agglomerative", "hierarchical"].includes(analysisType)) return "clusters";
  if (["lof", "knn", "isolation_forest", "isolation-forest", "iforest", "mahalanobis", "mahalanobis_distance"].includes(analysisType)) return "outliers";
  if (["fps", "random", "stratified", "cluster_representative", "per_element", "acquisition", "novelty_fps"].includes(analysisType)) return "sampling";
  if (["coverage", "overlap"].includes(analysisType)) return "coverage";
  if (["compare", "mantel"].includes(analysisType)) return "compare";
  if (analysisType === "local_diversity") return "local";
  if (analysisType === "kernel") return "kernel";
  return "overview";
}

export default function Analysis() {
  const { message } = AntApp.useApp();
  const st = useWorkspace();
  const dataset = activeDataset(st);
  const { t, tr, locale } = useT();
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [analyses, setAnalyses] = useState<AnalysisRow[]>([]);
  // Tab / module selection lives in a module-level store: it survives leaving
  // the page, and remounting (or restarting the app) restores it together with
  // the last displayed analysis.
  const view = useAnalysisUi((s) => s.view);
  const slots = useAnalysisUi((s) => s.slots);
  const { tab, projection, overviewAnalysis, mode, preprocess, colorBy } = view;
  const setTab = useAnalysisUi((s) => s.setTab);
  const setProjection = useAnalysisUi((s) => s.setProjection);
  const setOverviewAnalysis = useAnalysisUi((s) => s.setOverviewAnalysis);
  const setMode = useAnalysisUi((s) => s.setMode);
  const setPreprocess = useAnalysisUi((s) => s.setPreprocess);
  const setColorBy = useAnalysisUi((s) => s.setColorBy);
  const [points, setPoints] = useState<Point[]>([]);
  const [preview, setPreview] = useState<AnalysisPreview | null>(null);
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // What is actually running: module label + originating tab + method. The
  // toolbar names it explicitly and Run buttons only spin for their own
  // module — a background t-SNE must not read as "PCA is running" after the
  // user switches methods or tabs mid-job.
  const [runningInfo, setRunningInfo] = useState<{ label: string; tab: TabKey; method: string | null } | null>(null);
  const [lastJobProgress, setLastJobProgress] = useState<number | null>(null);
  const [selectedIndices, setSelectedIndices] = useState<number[]>([]);
  const [inspectedPoint, setInspectedPoint] = useState<Point | null>(null);
  const [selectedFrame, setSelectedFrame] = useState<FramePayload | null>(null);
  const [selectedFrameBusy, setSelectedFrameBusy] = useState(false);
  const [clusterAlgorithm, setClusterAlgorithm] = useState("kmeans");
  const [outlierAlgorithm, setOutlierAlgorithm] = useState("lof");
  const [samplingAlgorithm, setSamplingAlgorithm] = useState("fps");
  const [similarityMode, setSimilarityMode] = useState<"query" | "all_neighbors" | "pairwise">("query");
  const [compareMode, setCompareMode] = useState<CompareMode>("geometry");
  const [mantelMethod, setMantelMethod] = useState<"pearson" | "spearman">("pearson");
  const [mantelPermutations, setMantelPermutations] = useState(999);
  const [coverageMode, setCoverageMode] = useState<"coverage" | "overlap">("coverage");
  const [propertyName, setPropertyName] = useState("energy_per_atom");
  const [kernelName, setKernelName] = useState("rbf");
  const [localCutoff, setLocalCutoff] = useState(3.0);
  const [secondRun, setSecondRun] = useState<string | null>(null);
  const [exportFormat, setExportFormat] = useState("csv");
  const [exportPath, setExportPath] = useState("");
  const [k, setK] = useState(10);
  const [nClusters, setNClusters] = useState(6);
  const [nSamples, setNSamples] = useState(1000);
  const [uncertaintyK, setUncertaintyK] = useState(8);
  const [contamination, setContamination] = useState(0.01);
  const [queryIndex, setQueryIndex] = useState(0);
  const [methodGuideOpen, setMethodGuideOpen] = useState(false);
  const [trajectoryStep, setTrajectoryStep] = useState(1);
  const [perturbationType, setPerturbationType] = useState<"jitter" | "strain">("jitter");
  const [perturbationCount, setPerturbationCount] = useState(8);
  const [perturbationMaximum, setPerturbationMaximum] = useState(0.2);
  const [perturbationMetric, setPerturbationMetric] = useState("euclidean");
  const [tsnePerplexity, setTsnePerplexity] = useState(30);
  const [overviewArrays, setOverviewArrays] = useState<NumericArrays>({});
  const [overviewArraysBusy, setOverviewArraysBusy] = useState(false);
  const [loadingAnalysisId, setLoadingAnalysisId] = useState<string | null>(null);
  const operationRef = useRef(0);
  // Analysis ids already auto-restored in this mount/run window, so a
  // persistent fetch error cannot loop the restore effect.
  const restoreAttemptedRef = useRef<string | null>(null);
  // Tab the restore effect last processed, so a tab switch can fall back to
  // the tab's most recent result while a same-tab parameter change cannot.
  const lastLookedTabRef = useRef<TabKey | null>(null);

  // Stable key of every parameter that changes what the current tab computes.
  const analysisParams: AnalysisParams = {
    projection, mode, preprocess, tsnePerplexity, similarityMode, k, queryIndex,
    clusterAlgorithm, nClusters, outlierAlgorithm, contamination, samplingAlgorithm,
    nSamples, uncertaintyK, coverageMode, compareMode, mantelMethod, mantelPermutations,
    localCutoff, kernelName, overviewAnalysis, trajectoryStep, propertyName,
    perturbationType, perturbationCount, perturbationMaximum, perturbationMetric,
  };
  const paramsKey = buildParamsKey(tab, analysisParams);
  // {tab, paramsKey, params} as of the latest render. Runs capture this when
  // they start so their slots always record the context the run belongs to,
  // never whatever the user has navigated to by completion time.
  const runContextRef = useRef<{ tab: TabKey; paramsKey: string; params: AnalysisParams }>({ tab, paramsKey, params: analysisParams });
  runContextRef.current = { tab, paramsKey, params: analysisParams };

  const clearDisplayedAnalysis = useCallback(() => {
    setAnalysisId(null);
    setPreview(null);
    setPoints([]);
    setSelectedIndices([]);
    setInspectedPoint(null);
    setOverviewArrays({});
  }, []);

  const selectedRun = st.activeDescriptorRunId;
  const setSelectedRun = st.setActiveRun;
  const selectedPoint = inspectedPoint ?? points.find((point) => point.i === selectedIndices[0]) ?? null;

  // Whether the (tab, run, parameter combination) result is already computed
  // and can be re-displayed without rerunning. One parameter can be probed
  // with a candidate value; the others stay at their current value.
  const isCached = useCallback(
    (param: string, value: string | number) =>
      !!selectedRun && slotForParams(slots, tab, selectedRun, buildParamsKey(tab, { ...analysisParams, [param]: value } as AnalysisParams)) !== null,
    [analysisParams, selectedRun, slots, tab],
  );
  // Cache dot helpers: select options marked per value, numeric labels per current value.
  const markOptions = (param: string, options: CacheOption[]) => withCacheMarks((value) => isCached(param, value), options);
  const cachedParam = (param: keyof AnalysisParams) => isCached(param, analysisParams[param]);

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
      const completedRuns = resultRows.filter((row) => row.status === "COMPLETED");
      setSelectedRun(current && completedRuns.some((row) => row.id === current) ? current : completedRuns[0]?.id ?? null);
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "ANALYSIS"}: ${err.message ?? t("Could not load analysis runs")}`);
    }
  }, [dataset, message, setSelectedRun, t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    operationRef.current += 1;
    restoreAttemptedRef.current = null;
    setBusy(false);
    setRunningInfo(null);
    setLastJobProgress(null);
    setPoints([]);
    setPreview(null);
    setAnalysisId(null);
    setSelectedIndices([]);
    setInspectedPoint(null);
    setSelectedFrame(null);
    setSecondRun(null);
    setOverviewArrays({});
    setLoadingAnalysisId(null);
  }, [dataset?.id, selectedRun]);

  useEffect(() => {
    const selectedRow = runs.find((run) => run.id === selectedRun);
    const secondRow = secondRun ? runs.find((run) => run.id === secondRun) : undefined;
    const sensitivityPair = tab === "overview" && overviewAnalysis === "sensitivity";
    const sensitivityDescriptorMismatch = sensitivityPair
      && selectedRow
      && secondRow
      && selectedRow.descriptor_name !== secondRow.descriptor_name;
    if (secondRun && (secondRun === selectedRun || !secondRow || secondRow.status !== "COMPLETED" || sensitivityDescriptorMismatch)) {
      setSecondRun(null);
    }
  }, [overviewAnalysis, runs, secondRun, selectedRun, tab]);

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
    const localShellActive = preview?.kind === "local_diversity" && selectedPoint.row != null;
    const displayCutoff = 2.4;
    const requestedCutoff = Math.min(10, Math.max(displayCutoff, localShellActive ? localCutoff : displayCutoff));
    void ipc.request<FramePayload>("dataset.frame", { id: dataset.id, index: selectedPoint.frame, bond_cutoff: requestedCutoff })
      .then((frame) => { if (!disposed) setSelectedFrame({ ...frame, bond_cutoff: displayCutoff }); })
      .catch(() => { if (!disposed) setSelectedFrame(null); })
      .finally(() => { if (!disposed) setSelectedFrameBusy(false); });
    return () => { disposed = true; };
  }, [dataset, localCutoff, preview?.kind, selectedPoint]);

  useEffect(() => {
    const kind = String(preview?.kind ?? "");
    const arrayNames = ARTIFACT_ARRAYS[kind] ?? [];
    if (!analysisId || !arrayNames.length) {
      setOverviewArrays({});
      setOverviewArraysBusy(false);
      return;
    }

    const cached = analysisCache.get(analysisId);
    const cachedArrays = cached?.arrays ?? {};
    const missingArrays = arrayNames.filter((name) => !Object.prototype.hasOwnProperty.call(cachedArrays, name));
    if (!missingArrays.length) {
      setOverviewArrays(cachedArrays);
      setOverviewArraysBusy(false);
      return;
    }

    let disposed = false;
    setOverviewArraysBusy(true);
    void Promise.all(missingArrays.map(async (name) => {
      try {
        const chunk = await ipc.request<AnalysisChunk>("analysis.chunk", {
          analysis_id: analysisId,
          array: name,
          offset: 0,
          limit: 20_000,
          column_end: 2_000,
        });
        return [name, chunk.data] as const;
      } catch {
        return [name, []] as const;
      }
    })).then((entries) => {
      if (disposed) return;
      const arrays = { ...cachedArrays, ...Object.fromEntries(entries) };
      const current = analysisCache.get(analysisId);
      analysisCache.set(analysisId, {
        preview: current?.preview ?? cached?.preview ?? preview,
        points: current?.points ?? cached?.points ?? normalizePoints(preview ?? { analysis_id: analysisId }),
        selectedIndices: current?.selectedIndices ?? cached?.selectedIndices ?? selectedIndicesFromPreview(preview ?? { analysis_id: analysisId }),
        arrays,
      });
      setOverviewArrays(arrays);
    }).finally(() => {
      if (!disposed) setOverviewArraysBusy(false);
    });

    return () => { disposed = true; };
  }, [analysisId, preview]);

  const runRequest = useCallback(async (method: string, params: Record<string, unknown>, label: string) => {
    if (!selectedRun) {
      message.warning(t("Select a completed descriptor run first"));
      return null;
    }
    const requestRunId = selectedRun;
    const requestDatasetId = dataset?.id;
    // The (tab, parameters) context the run was started under: the result and
    // its cache slot belong there even if the user navigates while the job is
    // in flight.
    const requestContext = { ...runContextRef.current };
    const operation = ++operationRef.current;
    const isCurrent = () => operationRef.current === operation
      && useWorkspace.getState().activeDescriptorRunId === requestRunId
      && useWorkspace.getState().activeDatasetId === requestDatasetId
      && useAnalysisUi.getState().view.tab === requestContext.tab;
    setLoadingAnalysisId(null);
    setBusy(true);
    setRunningInfo({ label, tab: requestContext.tab, method });
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
          message.error(`${label} ${jobStatusLabel(tr, done.status)}: ${done.error?.message ?? ""}`);
          return null;
        }
        if (typeof done.result?.analysis_id === "string") id = done.result.analysis_id;
      }
      if (!id) throw new Error(`${method} returned no analysis_id`);
      // Record the slot under the request context before the display checks:
      // the computed artifacts stay restorable even when the user has moved to
      // another tab and the result will not land on screen.
      useAnalysisUi.getState().rememberResult({ runId: requestRunId, analysisId: id, tab: requestContext.tab, paramsKey: requestContext.paramsKey });
      if (!isCurrent()) return null;
      const frontendCached = response.job_id ? undefined : analysisCache.get(id);
      if (frontendCached) {
        setAnalysisId(id);
        setPreview(frontendCached.preview);
        setPoints(frontendCached.points);
        setSelectedIndices(frontendCached.selectedIndices);
        setOverviewArrays(frontendCached.arrays);
        setLastJobProgress(1);
        message.success(t("{label} loaded from cache", { label }));
        return id;
      }
      const result = await ipc.request<AnalysisPreview>("analysis.preview", { analysis_id: id, limit: 20_000 });
      if (!isCurrent()) return null;
      const nextPoints = normalizePoints(result);
      const nextSelectedIndices = selectedIndicesFromPreview(result);
      const cached = analysisCache.get(id);
      analysisCache.set(id, {
        preview: result,
        points: nextPoints,
        selectedIndices: nextSelectedIndices,
        arrays: cached?.arrays ?? {},
      });
      setAnalysisId(id);
      setPreview(result);
      setPoints(nextPoints);
      setSelectedIndices(nextSelectedIndices);
      setLastJobProgress(1);
      message.success(response.job_id ? t("{label} complete", { label }) : t("{label} loaded from cache", { label }));
      return id;
    } catch (error) {
      const err = error as { code?: string; message?: string };
      if (isCurrent()) message.error(`${err.code ?? label}: ${err.message ?? t("analysis failed")}`);
      return null;
    } finally {
      if (operationRef.current === operation) {
        setBusy(false);
        setRunningInfo(null);
      }
    }
  }, [dataset?.id, message, selectedRun, t, tr]);

  const runProjection = useCallback(async ({ mode: requestedMode, preprocess: requestedPreprocess }: ProjectionOverrides = {}) => {
    if (!selectedRun) return;
    const requestRunId = selectedRun;
    const requestDatasetId = dataset?.id;
    const activeMode = requestedMode ?? mode;
    const activePreprocess = requestedPreprocess ?? preprocess;
    setLoadingAnalysisId(null);
    if (projection === "pca") {
      const operation = ++operationRef.current;
      // PCA is the one run that can start before a re-render (the preprocess
      // select reruns it directly), so build the context from the effective
      // mode/preprocess instead of the captured render state.
      const requestContext = {
        tab: "projection" as TabKey,
        paramsKey: buildParamsKey("projection", { ...runContextRef.current.params, projection: "pca", mode: activeMode, preprocess: activePreprocess }),
      };
      const isCurrent = () => operationRef.current === operation
        && useWorkspace.getState().activeDescriptorRunId === requestRunId
        && useWorkspace.getState().activeDatasetId === requestDatasetId
        && useAnalysisUi.getState().view.tab === requestContext.tab;
      setBusy(true);
      setRunningInfo({ label: "PCA", tab: "projection", method: "analysis.pca" });
      setLastJobProgress(0);
      setPoints([]);
      setPreview(null);
      setAnalysisId(null);
      setOverviewArrays({});
      setSelectedIndices([]);
      setInspectedPoint(null);
      useWorkspace.getState().setSelectedSample(null);
      try {
        const response = await ipc.request<PcaAnalysisResponse>("analysis.pca", { run_id: requestRunId, mode: activeMode, seed: 42, preprocess: activePreprocess });
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
            message.error(`PCA ${jobStatusLabel(tr, done.status)}: ${done.error?.message ?? ""}`);
            return;
          }
          if (typeof done.result?.analysis_id === "string") id = done.result.analysis_id;
        }
        if (!isCurrent()) return;
        useAnalysisUi.getState().rememberResult({ runId: requestRunId, analysisId: id, tab: requestContext.tab, paramsKey: requestContext.paramsKey });
        const frontendCached = response.job_id ? undefined : analysisCache.get(id);
        if (frontendCached) {
          setAnalysisId(id);
          setPreview(frontendCached.preview);
          setPoints(frontendCached.points);
          setSelectedIndices(frontendCached.selectedIndices);
          setOverviewArrays(frontendCached.arrays);
          setLastJobProgress(1);
          message.success(t("PCA loaded from cache"));
          return;
        }
        const payload = await ipc.request<PcaPayload>("result.get_pca", { analysis_id: id });
        if (!isCurrent()) return;
        const nextPoints = pcaPayloadPoints(payload);
        const cached = analysisCache.get(id);
        analysisCache.set(id, {
          preview: null,
          points: nextPoints,
          selectedIndices: [],
          arrays: cached?.arrays ?? {},
        });
        setAnalysisId(id);
        setPreview(null);
        setPoints(nextPoints);
        setSelectedIndices([]);
        setLastJobProgress(1);
        message.success(response.job_id ? t("PCA complete") : t("PCA loaded from cache"));
      } catch (error) {
        const err = error as { code?: string; message?: string };
        if (isCurrent()) message.error(`${err.code ?? "PCA"}: ${err.message ?? t("analysis failed")}`);
      } finally {
        if (operationRef.current === operation) {
          setBusy(false);
          setRunningInfo(null);
        }
      }
      return;
    }
    const projectionParams = projection === "umap"
      ? { mode: activeMode, preprocess: activePreprocess, n_neighbors: 15, min_dist: 0.1 }
      : { mode: activeMode, preprocess: activePreprocess, perplexity: tsnePerplexity === 30 ? undefined : tsnePerplexity, max_iter: 1000 };
    await runRequest(`analysis.${projection}`, projectionParams, projection.toUpperCase());
  }, [dataset?.id, message, mode, preprocess, projection, runRequest, selectedRun, tsnePerplexity, t, tr]);

  const handlePreprocessChange = useCallback((value: string) => {
    setPreprocess(value);
    if (projection === "pca" && selectedRun && value !== preprocess) {
      void runProjection({ preprocess: value });
    }
  }, [preprocess, projection, runProjection, selectedRun]);

  const loadAnalysis = useCallback(async (row: AnalysisRow, opts?: { silent?: boolean }) => {
    if (!dataset || !selectedRun || row.status !== "COMPLETED") return;
    const inputRunIds = row.input_run_ids?.length ? row.input_run_ids : [row.descriptor_run_id];
    if (!inputRunIds.includes(selectedRun)) {
      message.warning(t("Select the source descriptor run before loading this analysis"));
      return;
    }

    const operation = ++operationRef.current;
    const requestRunId = selectedRun;
    const requestDatasetId = dataset.id;
    // `tab` guards against a tab switch while a slow restore fetch is in
    // flight — the stale result must not land on (or yank back) another tab.
    const isCurrent = () => operationRef.current === operation
      && useWorkspace.getState().activeDescriptorRunId === requestRunId
      && useWorkspace.getState().activeDatasetId === requestDatasetId
      && useAnalysisUi.getState().view.tab === analysisTab;
    const analysisType = row.analysis_type.toLowerCase();
    const analysisTab = tabForAnalysisType(analysisType);
    // Slot context for the loaded analysis, derived from the row itself. The
    // cached path below records before React re-renders, so the live
    // runContextRef would still describe the tab the user is leaving and
    // would file this analysis under another tab's parameters.
    const loadedParams: AnalysisParams = { ...runContextRef.current.params };
    setTab(analysisTab);
    if (analysisTab === "overview" && ["feature_variance", "feature_correlation", "effective_dimension", "property_correlation", "trajectory", "drift", "sensitivity", "perturbation_sensitivity"].includes(analysisType)) {
      setOverviewAnalysis(analysisType as OverviewAnalysis);
      loadedParams.overviewAnalysis = analysisType as OverviewAnalysis;
    }
    if (analysisTab === "projection") {
      setProjection(analysisType as ProjectionName);
      loadedParams.projection = analysisType as ProjectionName;
      if (analysisType === "pca") {
        const savedMode: PcaMode = row.parameters?.mode === "atom" ? "atom" : "structure";
        const savedPreprocess = row.parameters?.preprocess;
        const savedPreprocessValue = savedPreprocess === "raw" || savedPreprocess === "standardized" ? savedPreprocess : "center";
        setMode(savedMode);
        setPreprocess(savedPreprocessValue);
        loadedParams.mode = savedMode;
        loadedParams.preprocess = savedPreprocessValue;
      }
    }
    const loadedContext = { tab: analysisTab, paramsKey: buildParamsKey(analysisTab, loadedParams) };
    if (analysisType === "pairwise" || analysisType === "pairwise_similarity") setSimilarityMode("pairwise");
    if (analysisType === "overlap") setCoverageMode("overlap");
    if (analysisType === "acquisition") setSamplingAlgorithm(row.parameters?.acquisition_method === "uncertainty_diversity" ? "uncertainty_diversity" : "novelty_fps");
    if (analysisType === "mantel") setCompareMode("mantel");

    setLoadingAnalysisId(row.id);
    setBusy(true);
    setRunningInfo({ label: analysisType.toUpperCase(), tab: analysisTab, method: null });
    setLastJobProgress(null);
    setPoints([]);
    setPreview(null);
    setAnalysisId(null);
    setOverviewArrays({});
    setSelectedIndices([]);
    setInspectedPoint(null);
    setSelectedFrame(null);
    useWorkspace.getState().setSelectedSample(null);

    try {
      const cached = analysisCache.get(row.id);
      if (cached) {
        if (!isCurrent()) return;
        setAnalysisId(row.id);
        setPreview(cached.preview);
        setPoints(cached.points);
        setSelectedIndices(cached.selectedIndices);
        setOverviewArrays(cached.arrays);
        useAnalysisUi.getState().rememberResult({ runId: requestRunId, analysisId: row.id, ...loadedContext });
        setLastJobProgress(1);
        if (!opts?.silent) message.success(t("Loaded cached {name}", { name: analysisType.toUpperCase() }));
        return;
      }

      if (analysisType === "pca") {
        const payload = await ipc.request<PcaPayload>("result.get_pca", { analysis_id: row.id });
        if (!isCurrent()) return;
        const nextPoints = pcaPayloadPoints(payload);
        const nextCached: CachedAnalysis = { preview: null, points: nextPoints, selectedIndices: [], arrays: {} };
        analysisCache.set(row.id, nextCached);
        setAnalysisId(row.id);
        setPreview(null);
        setPoints(nextPoints);
        setSelectedIndices([]);
        setOverviewArrays({});
      } else {
        const result = await ipc.request<AnalysisPreview>("analysis.preview", { analysis_id: row.id, limit: 20_000 });
        if (!isCurrent()) return;
        const nextPoints = normalizePoints(result);
        const nextSelectedIndices = selectedIndicesFromPreview(result);
        const nextCached: CachedAnalysis = { preview: result, points: nextPoints, selectedIndices: nextSelectedIndices, arrays: {} };
        analysisCache.set(row.id, nextCached);
        setAnalysisId(row.id);
        setPreview(result);
        setPoints(nextPoints);
        setSelectedIndices(nextSelectedIndices);
        setOverviewArrays({});
      }
      useAnalysisUi.getState().rememberResult({ runId: requestRunId, analysisId: row.id, ...loadedContext });
      setLastJobProgress(1);
      if (!opts?.silent) message.success(t("Loaded cached {name}", { name: analysisType.toUpperCase() }));
    } catch (error) {
      const err = error as { code?: string; message?: string };
      if (isCurrent()) message.error(`${err.code ?? "ANALYSIS"}: ${err.message ?? t("could not load analysis")}`);
    } finally {
      if (operationRef.current === operation) {
        setBusy(false);
        setRunningInfo(null);
        setLoadingAnalysisId(null);
      }
    }
  }, [dataset, message, selectedRun, t]);

  // Keep the displayed result in step with the current tab + parameters: an
  // exact slot match (same tab, run, parameters) is re-displayed from the
  // backend artifacts instead of recomputing; switching tabs falls back to
  // that tab's most recent result; anything else clears the stale display.
  // One load attempt per analysis id (reset when the dataset/run changes) so
  // a persistent fetch error cannot loop.
  useEffect(() => {
    if (busy || loadingAnalysisId) return;
    const slotMap = useAnalysisUi.getState().slots;
    const exact = slotForParams(slotMap, tab, selectedRun, paramsKey);
    const tabChanged = lastLookedTabRef.current !== tab;
    lastLookedTabRef.current = tab;
    const slot = exact ?? (tabChanged ? latestSlotForTab(slotMap, tab, selectedRun) : null);
    const wantedId = slot?.analysisId ?? null;
    if (analysisId === wantedId) return;
    if (!wantedId) {
      restoreAttemptedRef.current = null;
      clearDisplayedAnalysis();
      return;
    }
    if (restoreAttemptedRef.current === wantedId) return;
    if (!analyses.length) return;
    restoreAttemptedRef.current = wantedId;
    const row = analyses.find((item) => item.id === wantedId);
    if (!row) {
      // The history listing lost this analysis (filtered out or not listed
      // yet), but the in-memory cache still holds the computed chart —
      // restore from it instead of dropping the result.
      const cached = selectedRun ? analysisCache.get(wantedId) : undefined;
      if (cached) {
        setAnalysisId(wantedId);
        setPreview(cached.preview);
        setPoints(cached.points);
        setSelectedIndices(cached.selectedIndices);
        setOverviewArrays(cached.arrays);
        return;
      }
    }
    const inputRunIds = row?.input_run_ids?.length ? row.input_run_ids : row ? [row.descriptor_run_id] : [];
    if (!row || row.status !== "COMPLETED" || !inputRunIds.includes(selectedRun ?? "")) {
      restoreAttemptedRef.current = null;
      clearDisplayedAnalysis();
      return;
    }
    // A slot whose row belongs to another tab was recorded under the wrong
    // context; restoring it would navigate the page. Forget the bad slot and
    // stay put instead.
    if (tabForAnalysisType(row.analysis_type.toLowerCase()) !== tab) {
      if (slot) useAnalysisUi.getState().forgetSlot(slotKey(slot));
      restoreAttemptedRef.current = null;
      clearDisplayedAnalysis();
      return;
    }
    void loadAnalysis(row, { silent: true });
  }, [analyses, analysisId, busy, clearDisplayedAnalysis, loadAnalysis, loadingAnalysisId, paramsKey, selectedRun, tab]);

  const runTabAnalysis = useCallback(async () => {
    if (tab === "projection") return runProjection();
    if (tab === "similarity") {
      const method = similarityMode === "pairwise" ? "analysis.pairwise" : similarityMode === "all_neighbors" ? "analysis.neighbors" : "analysis.similarity";
      const parameters = similarityMode === "pairwise"
        ? { metric: "cosine", preprocess: "raw", mode, max_samples: 400 }
        : { k, query_index: queryIndex, metric: "cosine", preprocess: "raw", mode };
      await runRequest(method, parameters, similarityMode === "pairwise" ? t("Pairwise similarity") : similarityMode === "all_neighbors" ? t("Neighbor graph") : t("Similarity"));
    } else if (tab === "clusters") {
      await runRequest("analysis.cluster", { algorithm: clusterAlgorithm, n_clusters: nClusters, preprocess: "standardized", mode }, clusterAlgorithm.toUpperCase());
    } else if (tab === "outliers") {
      await runRequest("analysis.outlier", { algorithm: outlierAlgorithm, k, contamination, preprocess: "standardized", mode }, outlierAlgorithm.toUpperCase());
    } else if (tab === "sampling") {
      if (samplingAlgorithm === "novelty_fps" || samplingAlgorithm === "uncertainty_diversity") {
        if (!secondRun || !selectedRun) {
          message.warning(t("Select a reference run for acquisition"));
          return;
        }
        const uncertainty = samplingAlgorithm === "uncertainty_diversity";
        await runRequest("analysis.acquisition", { reference_run_id: secondRun, query_run_id: selectedRun, n_samples: nSamples, mode, acquisition_method: samplingAlgorithm, novelty_weight: 0.65, uncertainty_weight: 0.65, uncertainty_k: uncertaintyK }, uncertainty ? t("Uncertainty acquisition") : t("Novelty acquisition"));
      } else {
        await runRequest("analysis.sampling", { algorithm: samplingAlgorithm, n_samples: nSamples, mode }, t("Sampling"));
      }
    } else if (tab === "coverage") {
      if (!secondRun || !selectedRun) {
        message.warning(t("Select a reference/query run pair"));
        return;
      }
      await runRequest(`analysis.${coverageMode}`, { reference_run_id: selectedRun, query_run_id: secondRun, metric: "euclidean", mode }, coverageMode === "coverage" ? t("Coverage") : t("Overlap"));
    } else if (tab === "compare") {
      if (!secondRun || !selectedRun) {
        message.warning(t("Select a descriptor run pair"));
        return;
      }
      const method = compareMode === "mantel" ? "analysis.mantel" : "analysis.compare";
      const parameters = compareMode === "mantel"
        ? { left_run_id: selectedRun, right_run_id: secondRun, mode, method: mantelMethod, permutations: mantelPermutations, max_samples: 600 }
        : { left_run_id: selectedRun, right_run_id: secondRun, mode };
      await runRequest(method, parameters, compareMode === "mantel" ? t("Mantel test") : t("Compare"));
    } else if (tab === "local") {
      await runRequest("analysis.local_diversity", { mode: "atom", n_clusters: nClusters, k, cutoff: localCutoff, max_neighbors: 128 }, t("Local diversity"));
    } else if (tab === "kernel") {
      await runRequest("analysis.kernel", { kernel: kernelName, mode, max_samples: 400 }, t("Kernel diagnostics"));
    } else if (tab === "overview") {
      if ((overviewAnalysis === "drift" || overviewAnalysis === "sensitivity") && (!secondRun || !selectedRun)) {
        message.warning(t("Select a reference/query run pair"));
        return;
      }
      if (overviewAnalysis === "sensitivity" && selectedRun && secondRun) {
        const referenceRow = runs.find((run) => run.id === selectedRun);
        const queryRow = runs.find((run) => run.id === secondRun);
        if (!referenceRow || !queryRow || referenceRow.descriptor_name !== queryRow.descriptor_name) {
          message.warning(t("Parameter sensitivity requires the same descriptor; use Compare for different descriptors"));
          return;
        }
      }
      const overviewParams: Record<string, unknown> = overviewAnalysis === "drift"
        ? { reference_run_id: selectedRun, query_run_id: secondRun, metric: "euclidean" }
        : overviewAnalysis === "sensitivity"
          ? { run_ids: [selectedRun, secondRun] }
          : overviewAnalysis === "perturbation_sensitivity"
            ? { perturbation: perturbationType, n_amplitudes: perturbationCount, max_amplitude: perturbationMaximum, metric: perturbationMetric, max_structures: 64, preprocess: "standardized" }
          : overviewAnalysis === "trajectory"
            ? { frame_step: trajectoryStep }
            : overviewAnalysis === "property_correlation"
              ? { property: propertyName, folds: 5, top_k: 20, mode }
            : overviewAnalysis === "feature_correlation"
              ? { top_k: 20 }
              : overviewAnalysis === "effective_dimension"
                ? { preprocess: "center" }
                : { top_k: 20 };
      await runRequest(`analysis.${overviewAnalysis}`, overviewParams, tr(OVERVIEW_MODULE_LABELS[overviewAnalysis]));
    }
  }, [clusterAlgorithm, contamination, coverageMode, k, kernelName, localCutoff, mantelMethod, mantelPermutations, mode, nClusters, nSamples, overviewAnalysis, perturbationCount, perturbationMaximum, perturbationMetric, perturbationType, propertyName, queryIndex, runProjection, runRequest, runs, samplingAlgorithm, secondRun, selectedRun, similarityMode, tab, t, tr, trajectoryStep, uncertaintyK, message, compareMode]);

  const inspectPoint = useCallback((point: Point) => {
    setInspectedPoint(point);
    if (dataset && selectedRun) {
      useWorkspace.getState().setSelectedSample({ datasetId: dataset.id, runId: selectedRun, mode: point.row == null ? mode : "atom", frame: point.frame, atom: point.row });
      useWorkspace.getState().setActiveFrame(point.frame);
    }
  }, [dataset, mode, selectedRun]);

  // Keep the cached entry in sync with the on-chart selection so leaving the
  // page and coming back restores it together with the chart.
  const updateCachedSelection = useCallback(
    (indices: number[]) => {
      if (!analysisId) return;
      const entry = analysisCache.get(analysisId);
      if (entry) analysisCache.set(analysisId, { ...entry, selectedIndices: indices });
    },
    [analysisId],
  );

  const handlePoint = useCallback((point: Point) => {
    setSelectedIndices([point.i]);
    updateCachedSelection([point.i]);
    inspectPoint(point);
  }, [inspectPoint, updateCachedSelection]);

  const plot = useMemo(() => {
    if (!points.length) return null;
    const values = points.map((point) => colorBy === "energy" ? point.energy : colorBy === "force_max" ? point.force_max : colorBy === "volume" ? point.volume : undefined);
    const hasColor = values.some((value) => value != null && Number.isFinite(value));
    const displayedSelected = selectedDisplayIndices(points, selectedIndices);
    const colorByLabel = colorBy === "energy" ? t("energy") : colorBy === "force_max" ? t("force_max") : colorBy === "volume" ? t("volume") : colorBy;
    return (
      <Plot
        key={`${analysisId ?? "pending"}:${projection}:${mode}:${preprocess}`}
        data={[{
          type: "scattergl",
          mode: "markers",
          x: points.map((point) => point.x),
          y: points.map((point) => point.y),
          text: points.map((point) => `${t("frame {frame}", { frame: point.frame })}${point.row == null ? "" : t(" · row {row}", { row: point.row })}`),
          customdata: points.map((point) => [point.i, point.frame, point.row ?? -1]),
          marker: hasColor ? { size: 7, color: values as number[], colorscale: "Viridis", showscale: true, colorbar: { title: { text: colorByLabel } } } : { size: 7, color: "#0F6CBD" },
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
          updateCachedSelection(indices);
          const first = displayIndices[0];
          if (first != null && points[first]) inspectPoint(points[first]);
          else {
            setInspectedPoint(null);
            useWorkspace.getState().setSelectedSample(null);
          }
        }}
      />
    );
  }, [analysisId, colorBy, handlePoint, inspectPoint, mode, points, preprocess, projection, selectedIndices, t, updateCachedSelection]);

  const exportSelection = async () => {
    if (!selectedRun || !exportPath.trim()) {
      message.warning(t("Choose an output path first"));
      return;
    }
    const indices = selectedIndices.length ? selectedIndices : points.map((point) => point.i);
    try {
      const response = await ipc.request<AnalysisJobResponse>("analysis.export", { run_id: selectedRun, indices, mode, format: exportFormat, output_path: exportPath.trim() });
      if (!response.job_id) {
        message.success(t("Export loaded from cache"));
        return;
      }
      trackJob(response.job_id, "analysis.export");
      const done = await watchJob(response.job_id);
      if (done.status === "COMPLETED") message.success(t("Export written to {path}", { path: String(done.result?.output_path ?? exportPath) }));
      else message.error(`Export ${jobStatusLabel(tr, done.status)}: ${done.error?.message ?? ""}`);
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "EXPORT"}: ${err.message ?? t("export failed")}`);
    }
  };

  const chooseExportPath = async () => {
    try {
      const selected =
        exportFormat === "deepmd"
          ? await openDialog({ title: t("Choose export directory"), directory: true, multiple: false })
          : await saveDialog({
              title: t("Choose export file"),
              defaultPath: `analysis_subset.${exportFormat}`,
              filters: [{ name: exportFormat.toUpperCase(), extensions: [exportFormat] }],
            });
      if (typeof selected === "string") setExportPath(selected);
    } catch {
      message.error(t("Could not open the export chooser"));
    }
  };

  const deleteAnalysis = async (row: AnalysisRow) => {
    try {
      await ipc.request("analysis.delete", { analysis_id: row.id });
      analysisCache.delete(row.id);
      if (analysisId === row.id) {
        setAnalysisId(null);
        setPreview(null);
        setPoints([]);
        setSelectedIndices([]);
        setOverviewArrays({});
        useAnalysisUi.getState().clearResult(row.id);
      }
      await refresh();
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "ANALYSIS"}: ${err.message ?? t("delete failed")}`);
    }
  };

  if (!dataset) return <Empty description={t("Register a dataset first")} style={{ marginTop: 120 }} />;
  const selectedRunRow = runs.find((run) => run.id === selectedRun);
  const completedRuns = runs.filter((run) => run.status === "COMPLETED");
  const sensitivityRuns = completedRuns.filter((run) => run.descriptor_name === selectedRunRow?.descriptor_name);
  const sensitivityPair = tab === "overview" && overviewAnalysis === "sensitivity";
  const pairRuns = sensitivityPair ? sensitivityRuns : completedRuns;
  const visibleAnalyses = analyses.filter((row) => {
    const inputRunIds = row.input_run_ids?.length ? row.input_run_ids : [row.descriptor_run_id];
    return !selectedRun || inputRunIds.includes(selectedRun);
  });
  const methodGuideKey = tab === "projection"
    ? `projection.${projection}`
    : tab === "similarity"
      ? `similarity.${similarityMode}`
      : tab === "clusters"
        ? `cluster.${clusterAlgorithm}`
        : tab === "outliers"
          ? `outlier.${outlierAlgorithm}`
          : tab === "sampling"
            ? `sampling.${samplingAlgorithm}`
            : tab === "coverage"
              ? `coverage.${coverageMode}`
              : tab === "compare"
                ? `compare.${compareMode}`
                : tab === "local"
                  ? "local.local_diversity"
                  : tab === "kernel"
                    ? `kernel.${kernelName}`
                    : `overview.${overviewAnalysis}`;
  const methodGuide = getAnalysisMethodGuide(methodGuideKey);
  const overviewModuleControl = <Space wrap><Typography.Text>{t("Module")}</Typography.Text><Select value={overviewAnalysis} style={{ width: 220 }} onChange={setOverviewAnalysis} options={markOptions("overviewAnalysis", [{ value: "feature_variance", label: t("Feature variance") }, { value: "feature_correlation", label: t("Feature correlation") }, { value: "effective_dimension", label: t("Effective dimension") }, { value: "property_correlation", label: t("Property correlation") }, { value: "trajectory", label: t("Trajectory") }, { value: "drift", label: t("Dataset drift") }, { value: "sensitivity", label: t("Parameter sensitivity") }, { value: "perturbation_sensitivity", label: t("Structural perturbation") }])} /></Space>;
  const overviewModuleHint = tab === "overview" && (overviewAnalysis === "sensitivity" || overviewAnalysis === "perturbation_sensitivity") && <Typography.Text type="secondary">{overviewAnalysis === "sensitivity" ? t("Compare parameter variants of the same descriptor; use Compare for different descriptors.") : t("Recompute the selected descriptor after controlled atomic jitter or strain.")}</Typography.Text>;
  const legacyOverview = tab === "overview" && (preview?.kind === "feature_variance" || preview?.kind === "effective_dimension");

  return (
    <div className="analysis-page">
      <section className="analysis-toolbar">
        <Space wrap>
          <Typography.Text strong>{t("Run")}</Typography.Text>
          <Select
            aria-label={t("Analysis descriptor run")}
            value={selectedRun ?? undefined}
            placeholder={t("Select completed run")}
            style={{ width: 250 }}
            disabled={busy}
            onChange={setSelectedRun}
            options={completedRuns.map((run) => ({
              value: run.id,
              label: <AnalysisRunLabel name={run.descriptor_name} shape={run.shape ?? t("unknown shape")} />,
            }))}
          />
          <Tag color={selectedRunRow?.status === "COMPLETED" ? "green" : "orange"}>{selectedRunRow ? jobStatusLabel(tr, selectedRunRow.status) : t("No run")}</Tag>
          <Button size="small" icon={<ArrowSync16Regular />} onClick={() => void refresh()}>{t("Refresh")}</Button>
        </Space>
        {busy && runningInfo && (
          <Space size={8} style={{ marginLeft: "auto", flexWrap: "wrap" }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>{t("{label} running", { label: runningInfo.label })}</Typography.Text>
            <Progress percent={Math.round((lastJobProgress ?? 0) * 100)} size="small" style={{ width: 140 }} />
          </Space>
        )}
      </section>

      <Tabs
        activeKey={tab}
        onChange={(value) => setTab(value as TabKey)}
        items={(Object.keys(TAB_LABELS) as TabKey[]).map((key) => ({ key, label: tr(TAB_LABELS[key]) }))}
      />

      <div className="analysis-workspace">
        <main className="analysis-main">
          <section className="analysis-card analysis-controls">
            {tab === "projection" && <ProjectionControls projection={projection} setProjection={setProjection} mode={mode} setMode={setMode} preprocess={preprocess} onPreprocessChange={handlePreprocessChange} tsnePerplexity={tsnePerplexity} setTsnePerplexity={setTsnePerplexity} markOptions={markOptions} cachedParam={cachedParam} />}
            {tab === "similarity" && <Space wrap><Typography.Text>{t("View")}</Typography.Text><Select value={similarityMode} onChange={setSimilarityMode} options={markOptions("similarityMode", [{ value: "query", label: t("Query neighbors") }, { value: "all_neighbors", label: t("All-neighbor graph") }, { value: "pairwise", label: t("Pairwise matrix") }])} /><Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "clusters" && <Space wrap><Typography.Text>{t("Algorithm")}</Typography.Text><Select value={clusterAlgorithm} onChange={setClusterAlgorithm} options={markOptions("clusterAlgorithm", ["kmeans", "dbscan", "hdbscan", "agglomerative"].map((value) => ({ value, label: value.toUpperCase() })))} /><ParamLabel label={t("Clusters")} cached={cachedParam("nClusters")} /><InputNumber min={2} value={nClusters} onChange={(value) => setNClusters(value ?? 6)} /><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "outliers" && <Space wrap><Typography.Text>{t("Algorithm")}</Typography.Text><Select value={outlierAlgorithm} onChange={setOutlierAlgorithm} options={markOptions("outlierAlgorithm", ["lof", "knn", "isolation_forest", "mahalanobis"].map((value) => ({ value, label: value.toUpperCase() })))} /><ParamLabel label={t("Contamination")} cached={cachedParam("contamination")} /><InputNumber min={0.001} max={0.5} step={0.001} value={contamination} onChange={(value) => setContamination(value ?? 0.01)} /><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "sampling" && <Space wrap><Typography.Text>{t("Method")}</Typography.Text><Select value={samplingAlgorithm} onChange={setSamplingAlgorithm} options={markOptions("samplingAlgorithm", ["fps", "novelty_fps", "uncertainty_diversity", "random", "stratified", "cluster_representative", "per_element"].map((value) => ({ value, label: tr(SAMPLING_LABELS[value] ?? { en: value, zh: value }) })))} /><ParamLabel label={t("Target")} cached={cachedParam("nSamples")} /><InputNumber min={1} value={nSamples} onChange={(value) => setNSamples(value ?? 1000)} /><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom") }])} />{samplingAlgorithm === "uncertainty_diversity" && <><ParamLabel label="kNN" cached={cachedParam("uncertaintyK")} /><InputNumber min={2} value={uncertaintyK} onChange={(value) => setUncertaintyK(value ?? 8)} /></>}</Space>}
            {tab === "coverage" && <Space wrap><Typography.Text>{t("Analysis")}</Typography.Text><Select value={coverageMode} onChange={setCoverageMode} options={markOptions("coverageMode", [{ value: "coverage", label: t("Coverage") }, { value: "overlap", label: t("Train / test overlap") }])} /><Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "local" && <Space wrap><ParamLabel label={t("Clusters / element")} cached={cachedParam("nClusters")} /><InputNumber min={2} value={nClusters} onChange={(value) => setNClusters(value ?? 6)} /><ParamLabel label={t("Descriptor kNN")} cached={cachedParam("k")} /><InputNumber min={1} value={k} onChange={(value) => setK(value ?? 10)} /><ParamLabel label={t("Neighbor cutoff")} cached={cachedParam("localCutoff")} /><InputNumber min={0.1} max={10} step={0.1} precision={2} value={localCutoff} onChange={(value) => setLocalCutoff(value == null ? 3 : Math.max(0.1, Math.min(10, value)))} addonAfter="Å" /><Typography.Text type="secondary">{t("Coordinates and periodic images determine coordination.")}</Typography.Text></Space>}
            {tab === "kernel" && <Space wrap><Typography.Text>{t("Kernel")}</Typography.Text><Select value={kernelName} onChange={setKernelName} options={markOptions("kernelName", ["rbf", "linear", "cosine", "polynomial"].map((value) => ({ value, label: value.toUpperCase() })))} /><Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "overview" && overviewModuleControl}
            {tab === "overview" && overviewAnalysis === "perturbation_sensitivity" && <Space wrap><Typography.Text>{t("Perturbation")}</Typography.Text><Select value={perturbationType} onChange={setPerturbationType} options={markOptions("perturbationType", [{ value: "jitter", label: t("Atomic jitter (Å)") }, { value: "strain", label: t("Isotropic strain") }])} /><ParamLabel label={t("Steps")} cached={cachedParam("perturbationCount")} /><InputNumber min={2} max={32} value={perturbationCount} onChange={(value) => setPerturbationCount(value ?? 8)} /><ParamLabel label={t("Maximum")} cached={cachedParam("perturbationMaximum")} /><InputNumber min={0.001} step={0.01} precision={3} value={perturbationMaximum} onChange={(value) => setPerturbationMaximum(value ?? 0.2)} /><Typography.Text>{t("Metric")}</Typography.Text><Select value={perturbationMetric} onChange={setPerturbationMetric} options={markOptions("perturbationMetric", ["euclidean", "cosine", "manhattan"].map((value) => ({ value, label: value })))} /></Space>}
            {(tab === "coverage" || tab === "compare" || (tab === "sampling" && (samplingAlgorithm === "novelty_fps" || samplingAlgorithm === "uncertainty_diversity")) || (tab === "overview" && (overviewAnalysis === "drift" || overviewAnalysis === "sensitivity"))) && <Space wrap><Typography.Text>{tab === "compare" ? t("Left") : tab === "sampling" ? t("Query") : t("Reference")}</Typography.Text><Select value={selectedRun ?? undefined} style={{ width: 220 }} disabled={busy} options={completedRuns.map((run) => ({ value: run.id, label: run.descriptor_name + " · " + run.id }))} onChange={setSelectedRun} /><Typography.Text>{tab === "compare" ? t("Right") : tab === "sampling" ? t("Reference") : t("Query")}</Typography.Text><Select value={secondRun ?? undefined} style={{ width: 220 }} disabled={busy} notFoundContent={sensitivityPair ? t("No other completed run for this descriptor") : undefined} options={pairRuns.filter((run) => run.id !== selectedRun).map((run) => ({ value: run.id, label: run.descriptor_name + " · " + run.id }))} onChange={setSecondRun} /></Space>}
            {tab === "compare" && <Space wrap><Typography.Text>{t("Test")}</Typography.Text><Select value={compareMode} onChange={setCompareMode} options={markOptions("compareMode", [{ value: "geometry", label: t("Geometry comparison") }, { value: "mantel", label: t("Mantel permutation test") }])} />{compareMode === "mantel" && <><Typography.Text>{t("Statistic")}</Typography.Text><Select value={mantelMethod} onChange={setMantelMethod} options={markOptions("mantelMethod", [{ value: "pearson", label: "Pearson" }, { value: "spearman", label: "Spearman" }])} /><ParamLabel label={t("Permutations")} cached={cachedParam("mantelPermutations")} /><InputNumber min={1} max={5000} value={mantelPermutations} onChange={(value) => setMantelPermutations(value ?? 999)} /></>}</Space>}
            {tab === "similarity" && similarityMode !== "pairwise" && <Space wrap>{similarityMode === "query" && <><ParamLabel label={t("Query index")} cached={cachedParam("queryIndex")} /><InputNumber min={0} value={queryIndex} onChange={(value) => setQueryIndex(value ?? 0)} /></>}<ParamLabel label="k" cached={cachedParam("k")} /><InputNumber min={1} value={k} onChange={(value) => setK(value ?? 10)} /></Space>}
            {tab === "overview" && overviewAnalysis === "trajectory" && <Space wrap><ParamLabel label={t("Frame step")} cached={cachedParam("trajectoryStep")} /><InputNumber min={1} value={trajectoryStep} onChange={(value) => setTrajectoryStep(value ?? 1)} /></Space>}
            {tab === "overview" && overviewAnalysis === "property_correlation" && <Space wrap><Typography.Text>{t("Property")}</Typography.Text><Select value={propertyName} onChange={(value) => { setPropertyName(value); if (value === "force_magnitude") setMode("atom"); }} options={markOptions("propertyName", [{ value: "energy_per_atom", label: t("Energy / atom") }, { value: "energy", label: t("Energy") }, { value: "force_max", label: t("Max |F|") }, { value: "force_magnitude", label: t("Atom |F|") }, { value: "volume", label: t("Volume") }])} /><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            <div className="analysis-controls-actions">
              <Space wrap>
                <Button type="primary" icon={<CheckmarkCircle16Regular />} loading={busy && runningInfo !== null && runningInfo.tab === tab && (tab !== "projection" || runningInfo.method === `analysis.${projection}`)} disabled={!selectedRun} onClick={() => void runTabAnalysis()}>{tab === "projection" ? t("Run {name}", { name: projection.toUpperCase() }) : tab === "overview" ? t("Run {name}", { name: tr(OVERVIEW_MODULE_LABELS[overviewAnalysis]) }) : t("Run {name}", { name: tr(TAB_LABELS[tab]) })}</Button>
                {points.length > 0 && <Select size="small" value={colorBy} onChange={setColorBy} options={[{ value: "none", label: t("No color") }, { value: "energy", label: t("Energy") }, { value: "force_max", label: t("Max |F|") }, { value: "volume", label: t("Volume") }]} />}
                {overviewModuleHint}
              </Space>
              <Button
                className="analysis-method-guide-button"
                size="small"
                icon={<Info16Regular />}
                aria-haspopup="dialog"
                aria-label={t("Open method guide")}
                onClick={() => setMethodGuideOpen(true)}
              >
                {t("Method guide")}
              </Button>
            </div>
          </section>

          <AnalysisMethodGuideModal guide={methodGuide} open={methodGuideOpen} onClose={() => setMethodGuideOpen(false)} />

          {tab === "projection" && <section className="analysis-card analysis-plot-card"><SectionHeading title={t("DESCRIPTOR SPACE")} meta={`${t("{n} preview points", { n: points.length.toLocaleString() })}${selectedIndices.length ? t(" · {n} selected", { n: selectedIndices.length }) : ""}`} />{points.length ? <div className="analysis-plot-frame">{plot}</div> : <Empty description={t("Run PCA, UMAP, or t-SNE to populate the Plotly canvas.")} />}</section>}
          {legacyOverview && <OverviewResultVisualization preview={preview} arrays={overviewArrays} loading={overviewArraysBusy} />}
          {tab !== "projection" && !legacyOverview && <AnalysisResultVisualization preview={preview} arrays={overviewArrays} points={points} loading={overviewArraysBusy} selectedIndices={selectedIndices} onSelect={handlePoint} />}
          {tab !== "projection" && <ResultPanel preview={preview} points={points} onSelect={(row) => {
            if (row.i == null && row.sample_index == null && row.frame == null) return;
            const index = Number(row.i ?? row.sample_index ?? 0);
            const frame = Number(row.frame ?? index);
            handlePoint({ i: index, frame, row: row.row == null ? undefined : Number(row.row), sample_id: row.sample_id == null ? undefined : String(row.sample_id), x: 0, y: 0, label: row.labels == null ? undefined : Number(row.labels), score: row.scores == null ? undefined : Number(row.scores), distance: row.distances == null ? undefined : Number(row.distances), element: row.element == null ? undefined : Number(row.element), cluster: row.cluster_labels == null ? undefined : Number(row.cluster_labels), coordination: row.coordination == null ? undefined : Number(row.coordination), novelty: row.novelty == null ? undefined : Number(row.novelty), uncertainty: row.uncertainty == null ? undefined : Number(row.uncertainty), diversity: row.diversity == null ? undefined : Number(row.diversity) });
          }} />}

          {tab === "sampling" && <section className="analysis-card"><SectionHeading title={t("EXPORT SELECTED SET")} meta={t("Source data is never modified")} /><Space.Compact style={{ width: "100%" }}><Select value={exportFormat} onChange={setExportFormat} options={["json", "csv", "extxyz", "deepmd"].map((value) => ({ value, label: value.toUpperCase() }))} style={{ width: 120 }} /><Input readOnly placeholder={t("Choose an export destination")} value={exportPath} aria-label={t("Export destination")} /><Button onClick={() => void chooseExportPath()}>{t("Choose…")}</Button><Button icon={<ArrowDownload16Regular />} onClick={() => void exportSelection()}>{t("Export")}</Button></Space.Compact></section>}
        </main>

        <aside className="analysis-inspector">
          <section className="analysis-card"><SectionHeading title={t("INSPECTOR")} meta={selectedPoint ? t("Frame {index}", { index: selectedPoint.frame }) : undefined} />{selectedPoint ? <><Row k={t("Sample")} v={selectedPoint.sample_id ?? String(selectedPoint.i)} /><Row k={t("Frame")} v={String(selectedPoint.frame)} />{selectedPoint.row != null && <Row k={t("Row")} v={String(selectedPoint.row)} />}<Button size="small" icon={<ArrowRight16Regular />} onClick={() => { st.setActiveFrame(selectedPoint.frame); st.setPage("explore"); }}>{t("Open in Explore")}</Button></> : <Typography.Text type="secondary">{t("Click a point, or use box/lasso selection, to inspect a structure.")}</Typography.Text>}</section>
          <section className="analysis-card"><SectionHeading title={t("STRUCTURE PREVIEW")} meta={selectedFrame ? t("Frame {index}", { index: selectedFrame.index }) : undefined} />{selectedFrame ? <StructurePreview frame={selectedFrame} selectedAtom={selectedPoint?.row} localCutoff={preview?.kind === "local_diversity" && selectedPoint?.row != null ? localCutoff : undefined} onOpen={() => { st.setActiveFrame(selectedFrame.index); st.setPage("explore"); }} /> : <div className="analysis-empty-small">{selectedFrameBusy ? t("Loading structure…") : t("Select a sample to preview it.")}</div>}</section>
          <section className="analysis-card"><SectionHeading title={t("ANALYSIS HISTORY")} meta={`${visibleAnalyses.length}`} />{visibleAnalyses.length ? <div className="analysis-history-list">{visibleAnalyses.slice(0, 10).map((row) => <div className="analysis-history-row" key={row.id}><div><Typography.Text strong>{row.analysis_type}</Typography.Text><Typography.Text type="secondary" style={{ display: "block", fontSize: 11 }}>{new Date(row.created_at).toLocaleString(locale)}</Typography.Text></div><Space size={4}><Tag color={row.status === "COMPLETED" ? "green" : row.status === "STALE" ? "orange" : undefined}>{jobStatusLabel(tr, row.status)}</Tag><Button size="small" type="text" icon={<ArrowRight16Regular />} aria-label={t("Load {name} analysis", { name: row.analysis_type })} title={t("Load cached analysis")} loading={loadingAnalysisId === row.id} disabled={row.status !== "COMPLETED" || (loadingAnalysisId !== null && loadingAnalysisId !== row.id)} onClick={() => void loadAnalysis(row)} /><Button size="small" type="text" icon={<Delete16Regular />} aria-label={t("Delete {name} analysis", { name: row.analysis_type })} disabled={row.status === "RUNNING" || row.status === "QUEUED"} onClick={() => void deleteAnalysis(row)} /></Space></div>)}</div> : <Typography.Text type="secondary">{t("No analysis artifacts for this descriptor run yet.")}</Typography.Text>}</section>
        </aside>
      </div>
    </div>
  );
}

function OverviewResultVisualization({ preview, arrays, loading }: { preview: AnalysisPreview | null; arrays: NumericArrays; loading: boolean }) {
  const { t, tr } = useT();
  if (!preview) return null;
  const kind = String(preview.kind ?? "");
  const kindLabel = OVERVIEW_KIND_LABELS[kind];
  const title = kindLabel ? tr(kindLabel) : t("ANALYSIS RESULT");
  if (loading) {
    return <section className="analysis-card analysis-visual-card"><SectionHeading title={title} meta={t("Loading chart data")} /><div className="analysis-overview-empty"><Empty description={t("Loading the result arrays…")} /></div></section>;
  }

  let content: ReactNode;
  if (kind === "feature_variance") content = <FeatureVarianceChart preview={preview} />;
  else if (kind === "feature_correlation") content = <FeatureCorrelationChart preview={preview} />;
  else if (kind === "effective_dimension") content = <EffectiveDimensionChart preview={preview} arrays={arrays} />;
  else if (kind === "trajectory") content = <TrajectoryChart preview={preview} arrays={arrays} />;
  else if (kind === "drift") content = <DriftChart preview={preview} />;
  else if (kind === "sensitivity") content = <SensitivityChart preview={preview} />;
  else return null;

  return <section className="analysis-card analysis-visual-card"><SectionHeading title={title} meta={t("Visual summary")} />{content}</section>;
}

function FeatureVarianceChart({ preview }: { preview: AnalysisPreview }) {
  const { t } = useT();
  const indices = numericArray(preview.top_indices);
  const values = numericArray(preview.top_values);
  const count = Math.min(indices.length, values.length);
  if (!count) return <OverviewNoData message={t("No feature variance values were returned.")} />;
  const rows = Array.from({ length: count }, (_, index) => ({ feature: t("Feature {index}", { index: indices[index] }), value: values[index] })).reverse();
  return <>
    <MetricStrip metrics={[{ label: t("Features shown"), value: String(count) }, { label: t("Highest variance"), value: formatNumber(values[0]) }]} />
    <OverviewPlot
      ariaLabel={t("Top descriptor feature variance")}
      data={[{
        type: "bar",
        orientation: "h",
        x: rows.map((row) => row.value),
        y: rows.map((row) => row.feature),
        marker: { color: "#0F6CBD" },
        hovertemplate: `%{y}<br>${t("variance")}=%{x:.5g}<extra></extra>`,
      }]}
      layout={overviewLayout({ xaxis: { title: t("Variance"), zeroline: true }, yaxis: { automargin: true } })}
    />
    <ChartCaption>{t("Bars show the descriptor dimensions with the largest variance; the larger the value, the more the dimension varies across samples.")}</ChartCaption>
  </>;
}

function FeatureCorrelationChart({ preview }: { preview: AnalysisPreview }) {
  const { t } = useT();
  const pairs = recordArray(preview.pairs)
    .map((pair) => ({
      feature: `F${formatIndex(pair.feature_a)} ↔ F${formatIndex(pair.feature_b)}`,
      value: finiteNumber(pair.correlation),
    }))
    .filter((pair): pair is { feature: string; value: number } => pair.value !== null);
  if (!pairs.length) return <OverviewNoData message={t("No feature correlation pairs were returned.")} />;
  const rows = pairs.slice().reverse();
  const strongest = pairs.reduce((best, row) => Math.max(best, Math.abs(row.value)), 0);
  return <>
    <MetricStrip metrics={[{ label: t("Pairs shown"), value: String(pairs.length) }, { label: t("Strongest |r|"), value: strongest.toFixed(3) }]} />
    <OverviewPlot
      ariaLabel={t("Top descriptor feature correlations")}
      data={[{
        type: "bar",
        orientation: "h",
        x: rows.map((row) => row.value),
        y: rows.map((row) => row.feature),
        marker: { color: rows.map((row) => row.value >= 0 ? "#0F6CBD" : "#D13438") },
        hovertemplate: `%{y}<br>${t("correlation")}=%{x:.4f}<extra></extra>`,
      }]}
      layout={overviewLayout({ xaxis: { title: t("Pearson correlation"), range: [-1, 1], zeroline: true }, yaxis: { automargin: true } })}
    />
    <ChartCaption>{t("Blue means positive correlation and red means negative correlation; the larger the absolute correlation, the more redundant the features.")}</ChartCaption>
  </>;
}

function EffectiveDimensionChart({ preview, arrays }: { preview: AnalysisPreview; arrays: NumericArrays }) {
  const { t } = useT();
  const explained = numericArray(arrays.explained_variance);
  if (!explained.length) return <OverviewNoData message={t("The explained-variance array is not available for visualization.")} />;
  const cumulative: number[] = [];
  let total = 0;
  for (const value of explained) {
    total += value;
    cumulative.push(total);
  }
  const indices = sampledIndices(explained.length, 320);
  const metrics: Metric[] = [
    { label: t("Participation ratio"), value: formatNumber(preview.participation_ratio) },
    { label: t("90% components"), value: formatCount(componentThreshold(preview, "0.9")) },
    { label: t("95% components"), value: formatCount(componentThreshold(preview, "0.95")) },
    { label: t("99% components"), value: formatCount(componentThreshold(preview, "0.99")) },
  ];
  return <>
    <MetricStrip metrics={metrics} />
    <OverviewPlot
      ariaLabel={t("Explained and cumulative descriptor variance by component")}
      data={[
        {
          type: "bar",
          x: indices.map((index) => index + 1),
          y: indices.map((index) => explained[index] * 100),
          name: t("Explained variance"),
          marker: { color: "#0F6CBD" },
          hovertemplate: `PC %{x}<br>${t("explained")}=%{y:.2f}%<extra></extra>`,
        },
        {
          type: "scatter",
          mode: "lines",
          x: indices.map((index) => index + 1),
          y: indices.map((index) => cumulative[index] * 100),
          name: t("Cumulative"),
          line: { color: "#F7630C", width: 2 },
          hovertemplate: `PC %{x}<br>${t("cumulative")}=%{y:.2f}%<extra></extra>`,
        },
      ]}
      layout={overviewLayout({
        xaxis: { title: t("Component"), type: "linear" },
        yaxis: { title: t("Variance (%)"), range: [0, 100] },
        legend: { orientation: "h", y: 1.12, x: 0 },
      })}
    />
    <ChartCaption>{t("Bars show the contribution of each principal component and the orange line shows the cumulative contribution; the x axis is evenly subsampled when too long.")}</ChartCaption>
  </>;
}

function TrajectoryChart({ preview, arrays }: { preview: AnalysisPreview; arrays: NumericArrays }) {
  const { t } = useT();
  const time = numericArray(arrays.time);
  const xValues = time.length ? time : numericArray(arrays.frames);
  const distances = numericArray(arrays.step_distance);
  const count = Math.min(xValues.length, distances.length);
  if (count < 2) return <OverviewNoData message={t("At least two trajectory points are needed for visualization.")} />;
  const cumulative: number[] = [];
  let total = 0;
  for (let index = 0; index < count; index += 1) {
    total += distances[index];
    cumulative.push(total);
  }
  const indices = sampledIndices(count, 1000);
  return <>
    <MetricStrip metrics={[{ label: t("Samples"), value: formatCount(count) }, { label: t("Total descriptor distance"), value: formatNumber(preview.total_distance) }, { label: t("Largest step"), value: formatNumber(Math.max(...distances.slice(0, count))) }]} />
    <OverviewPlot
      ariaLabel={t("Descriptor trajectory step and cumulative distance")}
      data={[
        {
          type: "scatter",
          mode: "lines",
          x: indices.map((index) => xValues[index]),
          y: indices.map((index) => distances[index]),
          name: t("Step distance"),
          line: { color: "#0F6CBD", width: 1.5 },
          hovertemplate: `t=%{x}<br>${t("step")}=%{y:.5g}<extra></extra>`,
        },
        {
          type: "scatter",
          mode: "lines",
          x: indices.map((index) => xValues[index]),
          y: indices.map((index) => cumulative[index]),
          name: t("Cumulative distance"),
          yaxis: "y2",
          line: { color: "#F7630C", width: 2 },
          hovertemplate: `t=%{x}<br>${t("cumulative")}=%{y:.5g}<extra></extra>`,
        },
      ]}
      layout={overviewLayout({
        xaxis: { title: String(preview.time_unit ?? t("Frame")) },
        yaxis: { title: t("Step distance") },
        yaxis2: { title: t("Cumulative distance"), overlaying: "y", side: "right", showgrid: false },
        legend: { orientation: "h", y: 1.12, x: 0 },
      })}
    />
    <ChartCaption>{t("The blue line shows the descriptor change between neighboring frames and the orange line shows the accumulated change from the start of the trajectory.")}</ChartCaption>
  </>;
}

function DriftChart({ preview }: { preview: AnalysisPreview }) {
  const { t } = useT();
  const rows = recordArray(preview.rows)
    .map((row) => ({ label: finiteNumber(row.labels), distance: finiteNumber(row.distances) }))
    .filter((row): row is { label: number; distance: number } => row.label !== null && row.distance !== null);
  const categoryLabels = [t("Covered"), t("Marginal"), t("Out of coverage")];
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
    ? grouped.map((values, index) => ({ type: "histogram" as const, x: values, name: categoryLabels[index], opacity: 0.78, marker: { color: categoryColors[index] }, nbinsx: 28, hovertemplate: `${categoryLabels[index]}<br>${t("distance")}=%{x:.5g}<br>${t("count")}=%{y}<extra></extra>` }))
    : [{ type: "bar", x: categoryLabels, y: counts, marker: { color: categoryColors }, hovertemplate: `%{x}<br>${t("count")}=%{y}<extra></extra>` }];
  return <>
    <MetricStrip metrics={[{ label: t("Mean distance"), value: formatNumber(preview.mean_distance) }, { label: t("Median distance"), value: formatNumber(preview.median_distance) }, { label: t("Max distance"), value: formatNumber(preview.max_distance) }, { label: t("Out of coverage"), value: formatCount(preview.out_of_coverage) }]} />
    <OverviewPlot
      ariaLabel={t("Distribution of distances from query samples to the reference descriptor set")}
      data={data}
      layout={overviewLayout({
        barmode: rows.length ? "stack" : "group",
        xaxis: { title: rows.length ? t("Nearest-reference distance") : t("Coverage category") },
        yaxis: { title: t("Samples") },
        shapes: thresholdShapes,
        legend: { orientation: "h", y: 1.12, x: 0 },
      })}
    />
    <ChartCaption>{t("The farther right the distribution, the farther the query samples are from the reference descriptor space; the dashed lines correspond to the q95 and q99 thresholds.")}</ChartCaption>
  </>;
}

function SensitivityChart({ preview }: { preview: AnalysisPreview }) {
  const { t } = useT();
  const records = recordArray(preview.runs);
  const geometryMode = records.some((run) => finiteNumber(run.mean_delta_norm) === null);
  const metricKey = geometryMode ? "pairwise_distance_spearman" : "mean_delta_norm";
  const metricLabel = geometryMode ? t("Pairwise distance Spearman") : t("Mean descriptor delta norm");
  const runs = records
    .map((run, index) => ({
      label: parameterLabel(run.parameters, index),
      value: finiteNumber(run[metricKey]),
      detail: parameterText(run.parameters),
    }))
    .filter((run): run is { label: string; value: number; detail: string } => run.value !== null);
  if (!runs.length) return <OverviewNoData message={t("No completed runs were returned for sensitivity analysis.")} />;
  const rows = runs.slice().reverse();
  const extremeValue = geometryMode ? Math.min(...runs.map((run) => run.value)) : Math.max(...runs.map((run) => run.value));
  return <>
    <MetricStrip metrics={[{ label: t("Runs compared"), value: String(runs.length) }, { label: geometryMode ? t("Lowest geometry correlation") : t("Largest mean delta"), value: formatNumber(extremeValue) }]} />
    <OverviewPlot
      ariaLabel={t("Descriptor parameter sensitivity across completed runs")}
      data={[{
        type: "bar",
        orientation: "h",
        x: rows.map((run) => run.value),
        y: rows.map((run) => run.label),
        customdata: rows.map((run) => run.detail),
        marker: { color: rows.map((_, index) => index === rows.length - 1 ? "#107C10" : "#0F6CBD") },
        hovertemplate: `%{y}<br>${metricLabel.toLowerCase()}=%{x:.5g}<br>%{customdata}<extra></extra>`,
      }]}
      layout={overviewLayout({ xaxis: { title: metricLabel, zeroline: true }, yaxis: { automargin: true } })}
    />
    <ChartCaption>{geometryMode ? t("Descriptors with different feature dimensions are compared by sample geometric consistency; the closer the Spearman is to 1, the more consistent the sample ordering.") : t("The first completed run is the baseline; the longer the bar, the larger the overall result difference caused by the descriptor parameter change.")}</ChartCaption>
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

function AnalysisMethodGuideModal({ guide, open, onClose }: { guide: AnalysisMethodGuide; open: boolean; onClose: () => void }) {
  const { t, tr } = useT();
  return (
    <Modal
      className="analysis-method-guide-modal"
      title={`${t("Method guide")} · ${tr(guide.title)}`}
      open={open}
      onCancel={onClose}
      footer={null}
      width={720}
      destroyOnHidden
    >
      <div className="analysis-method-guide-content">
        <section className="analysis-method-guide-section">
          <Typography.Title level={5}>{t("Theory")}</Typography.Title>
          <Typography.Paragraph>{tr(guide.theory)}</Typography.Paragraph>
        </section>
        <section className="analysis-method-guide-section">
          <Typography.Title level={5}>{t("Applications")}</Typography.Title>
          <Typography.Paragraph>{tr(guide.application)}</Typography.Paragraph>
        </section>
      </div>
    </Modal>
  );
}

function ProjectionControls({ projection, setProjection, mode, setMode, preprocess, onPreprocessChange, tsnePerplexity, setTsnePerplexity, markOptions, cachedParam }: { projection: ProjectionName; setProjection: (value: ProjectionName) => void; mode: PcaMode; setMode: (value: PcaMode) => void; preprocess: string; onPreprocessChange: (value: string) => void; tsnePerplexity: number; setTsnePerplexity: (value: number) => void; markOptions: (param: string, options: CacheOption[]) => CacheOption[]; cachedParam: (param: keyof AnalysisParams) => boolean }) {
  const { t } = useT();
  return <Space wrap><Typography.Text>{t("Method")}</Typography.Text><Select value={projection} onChange={setProjection} options={markOptions("projection", [{ value: "pca", label: "PCA" }, { value: "umap", label: "UMAP" }, { value: "tsne", label: "t-SNE" }])} /><Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /><Typography.Text>{t("Preprocess")}</Typography.Text><Select value={preprocess} onChange={onPreprocessChange} options={markOptions("preprocess", [{ value: "raw", label: t("Raw scale") }, { value: "center", label: t("Centered") }, { value: "standardized", label: t("Standardized") }])} />{projection === "tsne" && <><ParamLabel label={t("Perplexity")} cached={cachedParam("tsnePerplexity")} /><InputNumber min={2} step={1} value={tsnePerplexity} onChange={(value) => setTsnePerplexity(value ?? 30)} /></>}</Space>;
}

function ResultPanel({ preview, points, onSelect }: { preview: AnalysisPreview | null; points: Point[]; onSelect?: (row: Record<string, unknown>) => void }) {
  const { t } = useT();
  if (!preview && !points.length) return <section className="analysis-card"><Empty description={t("Run an analysis module to see its bounded result preview.")} /></section>;
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
  if (rows.length) return <section className="analysis-card"><SectionHeading title={String(preview?.kind ?? "RESULT").toUpperCase()} meta={t("{n} rows", { n: rows.length.toLocaleString() })} /><Table size="small" pagination={{ pageSize: 12 }} rowKey={(row, index) => `${String(row.i ?? row.sample_id ?? row.run_id ?? index)}:${String(row.source_i ?? row.rank ?? index)}`} dataSource={rows} onRow={(row) => ({ onClick: () => onSelect?.(row) })} columns={Object.keys(rows[0]).slice(0, 7).map((key) => ({ title: key, dataIndex: key, key, render: (value: unknown) => typeof value === "number" ? value.toPrecision(6) : String(value ?? "—") }))} /></section>;
  return <section className="analysis-card"><SectionHeading title={String(preview?.kind ?? "RESULT").toUpperCase()} /><pre className="analysis-json-preview">{JSON.stringify(preview, null, 2)}</pre></section>;
}

function SectionHeading({ title, meta }: { title: string; meta?: string }) {
  return <div className="analysis-section-heading"><Typography.Text strong>{title}</Typography.Text>{meta && <Typography.Text type="secondary">{meta}</Typography.Text>}</div>;
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="analysis-inspector-row"><span>{k}</span><Typography.Text code>{v}</Typography.Text></div>;
}

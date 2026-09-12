/*
 * Analysis is the successor to the old Results page.  It owns one shared run
 * selector, one compact inspector, and a set of real backend-backed modules.
 * Descriptor run history lives on the separate Results page.
 * Plotly is deliberately scoped to this page; Overview remains ECharts and
 * Explore remains 3Dmol.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import Plot from "react-plotly.js";
import type { Data, Layout, PlotMouseEvent } from "plotly.js";
import {
  App as AntApp,
  Button,
  Collapse,
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
  Tooltip,
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
  type EffectiveDimensionPreprocess,
  type OverviewAnalysis,
  type ProjectionName,
  type TabKey,
} from "../stores/analysisUi";
import { useT, type Pair } from "../i18n";
import StructurePreview from "../components/StructurePreview";
import SaveViewModal from "../components/SaveViewModal";
import { normalizePoints, selectedDisplayIndices, type AnalysisPoint } from "./analysisPreview";
import AnalysisResultVisualization, { type AnalysisArrays } from "./analysisVisualizations";
import { getAnalysisMethodGuide, type AnalysisMethodGuide } from "./analysisMethodGuides";
import { NoData as OverviewNoData, formatCount, num as finiteNumber, nums as numericArray, records as recordArray } from "./analysisChartKit";
import type {
  AnalysisJobResponse,
  AnalysisChunk,
  AnalysisPreview,
  AnalysisRow,
  DatasetMeta,
  DatasetView,
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
  label: ReactNode;
  value: string;
};

type FeatureVarianceMetric = "variance" | "relative_variance" | "std" | "iqr" | "mad";
type FeatureVarianceSort = "variance_desc" | "variance_asc" | "index";
type FeatureVarianceDisplay = "all" | "top" | "bottom" | "near_zero" | "low_variation" | "constant";
type FeatureVariancePane = "chart" | "stats";
type FeatureVarianceStatus = "constant" | "near_zero" | "low_variation" | "active" | "invalid";

type FeatureVarianceRow = {
  index: number;
  mean: number | null;
  variance: number | null;
  relative_variance: number | null;
  std: number | null;
  min: number | null;
  max: number | null;
  p05: number | null;
  p25: number | null;
  median: number | null;
  p75: number | null;
  p95: number | null;
  iqr: number | null;
  mad: number | null;
  robust_sigma: number | null;
  std_robust_ratio: number | null;
  whisker_min: number | null;
  whisker_max: number | null;
  outlier_count: number;
  finite_count: number;
  invalid_count: number;
  distribution_sample_count: number;
  status: FeatureVarianceStatus;
};

type FeatureVarianceDistribution = {
  loading: boolean;
  edges: number[];
  counts: number[];
  samples: number[];
};

const FEATURE_VARIANCE_STATUS_LABELS: Record<FeatureVarianceStatus, Pair> = {
  constant: { en: "Constant", zh: "常量" },
  near_zero: { en: "Near-zero", zh: "近零方差" },
  low_variation: { en: "Low variation", zh: "低变化" },
  active: { en: "Active", zh: "活跃" },
  invalid: { en: "Invalid", zh: "无效数据" },
};

const FEATURE_VARIANCE_STATUS_COLORS: Record<FeatureVarianceStatus, string> = {
  constant: "#8A8886",
  near_zero: "#8764B8",
  low_variation: "#F7630C",
  active: "#0F6CBD",
  invalid: "#D13438",
};

function AnalysisRunLabel({ name, shape }: { name: string; shape: string }) {
  return (
    <span className="analysis-run-label">
      <span className="analysis-run-name">{name}</span>
      <span className="analysis-run-shape">{shape}</span>
    </span>
  );
}

function CrossDatasetPicker({
  datasets,
  referenceDatasetId,
  queryDatasetId,
  referenceRunId,
  queryRunId,
  referenceViewId,
  queryViewId,
  referenceRuns,
  queryRuns,
  referenceViews,
  queryViews,
  compatible,
  disabled,
  onReferenceDataset,
  onQueryDataset,
  onReferenceRun,
  onQueryRun,
  onReferenceView,
  onQueryView,
  onSwap,
}: {
  datasets: DatasetMeta[];
  referenceDatasetId: string | null;
  queryDatasetId: string | null;
  referenceRunId: string | null;
  queryRunId: string | null;
  referenceViewId: string | null;
  queryViewId: string | null;
  referenceRuns: RunRow[];
  queryRuns: RunRow[];
  referenceViews: DatasetView[];
  queryViews: DatasetView[];
  compatible: boolean;
  disabled: boolean;
  onReferenceDataset: (value: string) => void;
  onQueryDataset: (value: string) => void;
  onReferenceRun: (value: string) => void;
  onQueryRun: (value: string) => void;
  onReferenceView: (value: string | null) => void;
  onQueryView: (value: string | null) => void;
  onSwap: () => void;
}) {
  const { t } = useT();
  const datasetOptions = datasets.map((item) => ({ value: item.id, label: item.name }));
  const scopeOptions = (views: DatasetView[]) => [
    { value: "__full__", label: t("Full dataset") },
    ...views.map((view) => ({ value: view.id, label: `${view.name} · ${view.number_of_frames.toLocaleString()}` })),
  ];
  const runOptions = (rows: RunRow[]) => rows.map((run) => ({
    value: run.id,
    label: <AnalysisRunLabel name={run.descriptor_name} shape={run.shape ?? t("unknown shape")} />,
  }));
  const row = (
    label: string,
    datasetId: string | null,
    runId: string | null,
    viewId: string | null,
    runs: RunRow[],
    views: DatasetView[],
    onDataset: (value: string) => void,
    onRun: (value: string) => void,
    onView: (value: string | null) => void,
  ) => <div className="analysis-cross-input-row">
    <Typography.Text strong className="analysis-cross-input-label">{label}</Typography.Text>
    <Select aria-label={`${label} ${t("Dataset")}`} value={datasetId ?? undefined} disabled={disabled} options={datasetOptions} onChange={onDataset} />
    <Select aria-label={`${label} ${t("Scope")}`} value={viewId ?? "__full__"} disabled={disabled || !datasetId} options={scopeOptions(views)} onChange={(value) => onView(value === "__full__" ? null : value)} />
    <Select aria-label={`${label} ${t("Descriptor run")}`} value={runId ?? undefined} disabled={disabled || !datasetId} placeholder={runs.length ? t("Select completed run") : t("No compatible run")} options={runOptions(runs)} onChange={onRun} />
  </div>;
  return <div className="analysis-cross-inputs">
    {row(t("Reference"), referenceDatasetId, referenceRunId, referenceViewId, referenceRuns, referenceViews, onReferenceDataset, onReferenceRun, onReferenceView)}
    <Button className="analysis-cross-swap" size="small" icon={<ArrowSync16Regular />} disabled={disabled || !referenceDatasetId || !queryDatasetId} onClick={onSwap}>{t("Swap")}</Button>
    {row(t("Query"), queryDatasetId, queryRunId, queryViewId, queryRuns, queryViews, onQueryDataset, onQueryRun, onQueryView)}
    <div className="analysis-cross-status">
      <Tag color={compatible ? "green" : "orange"}>{compatible ? t("Compatible feature space") : t("Select a compatible feature space")}</Tag>
    </div>
  </div>;
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
  property_correlation: ["sample_indices", "sample_frames", "sample_rows", "targets", "predictions", "residuals", "absolute_errors", "feature_indices", "pearson_correlations", "spearman_correlations", "mutual_information", "oof_distances", "reliability_bin_center", "reliability_bin_median", "reliability_bin_p90", "reliability_bin_p95"],
  coverage: ["projection_coords", "projection_source", "projection_sample_indices", "labels", "distances"],
  overlap: ["projection_coords", "projection_source", "projection_sample_indices", "labels", "distances"],
  drift: ["projection_coords", "projection_source", "projection_sample_indices", "labels", "distances"],
  trajectory: ["time", "frames", "sample_indices", "step_distance", "reference_distance", "cumulative_distance", "coords", "pc_explained_variance", "event_indices"],
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
  const [allRuns, setAllRuns] = useState<RunRow[]>([]);
  const [datasetViews, setDatasetViews] = useState<DatasetView[]>([]);
  const [analyses, setAnalyses] = useState<AnalysisRow[]>([]);
  // Tab / module selection lives in a module-level store: it survives leaving
  // the page, and remounting (or restarting the app) restores it together with
  // the last displayed analysis.
  const view = useAnalysisUi((s) => s.view);
  const slots = useAnalysisUi((s) => s.slots);
  const { tab, projection, overviewAnalysis, mode, preprocess, effectiveDimensionPreprocess, colorBy, nearZeroThreshold, lowVariationThreshold, featureCorrelationMethod, featureCorrelationThreshold } = view;
  const setTab = useAnalysisUi((s) => s.setTab);
  const setProjection = useAnalysisUi((s) => s.setProjection);
  const setOverviewAnalysis = useAnalysisUi((s) => s.setOverviewAnalysis);
  const setMode = useAnalysisUi((s) => s.setMode);
  const setPreprocess = useAnalysisUi((s) => s.setPreprocess);
  const setEffectiveDimensionPreprocess = useAnalysisUi((s) => s.setEffectiveDimensionPreprocess);
  const setColorBy = useAnalysisUi((s) => s.setColorBy);
  const setNearZeroThreshold = useAnalysisUi((s) => s.setNearZeroThreshold);
  const setLowVariationThreshold = useAnalysisUi((s) => s.setLowVariationThreshold);
  const setFeatureCorrelationMethod = useAnalysisUi((s) => s.setFeatureCorrelationMethod);
  const setFeatureCorrelationThreshold = useAnalysisUi((s) => s.setFeatureCorrelationThreshold);
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
  const [saveViewOpen, setSaveViewOpen] = useState(false);
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
  const [propertyFolds, setPropertyFolds] = useState(5);
  const [propertyReliabilityK, setPropertyReliabilityK] = useState(5);
  const [propertyDistanceMetric, setPropertyDistanceMetric] = useState<"euclidean" | "cosine">("euclidean");
  const [propertySparsePercentile, setPropertySparsePercentile] = useState(90);
  const [propertyOodPercentile, setPropertyOodPercentile] = useState(99);
  const [kernelName, setKernelName] = useState("rbf");
  const [localCutoff, setLocalCutoff] = useState(3.0);
  const [secondRun, setSecondRun] = useState<string | null>(null);
  const [referenceDatasetId, setReferenceDatasetId] = useState<string | null>(null);
  const [queryDatasetId, setQueryDatasetId] = useState<string | null>(null);
  const [referenceRunId, setReferenceRunId] = useState<string | null>(null);
  const [queryRunId, setQueryRunId] = useState<string | null>(null);
  const [referenceViewId, setReferenceViewId] = useState<string | null>(null);
  const [queryViewId, setQueryViewId] = useState<string | null>(null);
  const [exportFormat, setExportFormat] = useState("csv");
  const [exportPath, setExportPath] = useState("");
  const [k, setK] = useState(10);
  const [nClusters, setNClusters] = useState(6);
  const [nSamples, setNSamples] = useState(1000);
  const [uncertaintyK, setUncertaintyK] = useState(8);
  const [contamination, setContamination] = useState(0.01);
  const [queryIndex, setQueryIndex] = useState(0);
  const [methodGuideOpen, setMethodGuideOpen] = useState(false);
  const [perturbationType, setPerturbationType] = useState<"jitter" | "strain">("jitter");
  const [perturbationCount, setPerturbationCount] = useState(8);
  const [perturbationMaximum, setPerturbationMaximum] = useState(0.2);
  const [perturbationStructures, setPerturbationStructures] = useState(64);
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
    projection, mode, preprocess, effectiveDimensionPreprocess, tsnePerplexity, similarityMode, k, queryIndex,
    clusterAlgorithm, nClusters, outlierAlgorithm, contamination, samplingAlgorithm,
    nSamples, uncertaintyK, coverageMode, compareMode, mantelMethod, mantelPermutations,
    localCutoff, kernelName, overviewAnalysis, propertyName,
    propertyFolds, propertyReliabilityK, propertyDistanceMetric, propertySparsePercentile, propertyOodPercentile,
    perturbationType, perturbationCount, perturbationMaximum, perturbationStructures, perturbationMetric,
    nearZeroThreshold, lowVariationThreshold, featureCorrelationMethod, featureCorrelationThreshold,
    referenceRunId, queryRunId, referenceViewId, queryViewId,
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
  const crossDatasetModule = tab === "coverage"
    || (tab === "sampling" && (samplingAlgorithm === "novelty_fps" || samplingAlgorithm === "uncertainty_diversity"))
    || (tab === "overview" && overviewAnalysis === "drift");
  const completedAllRuns = useMemo(() => allRuns.filter((run) => run.status === "COMPLETED"), [allRuns]);
  const referenceRuns = useMemo(() => completedAllRuns.filter((run) => run.dataset_id === referenceDatasetId), [completedAllRuns, referenceDatasetId]);
  const referenceRunRow = referenceRuns.find((run) => run.id === referenceRunId) ?? null;
  const queryRuns = useMemo(() => completedAllRuns.filter(
    (run) => run.dataset_id === queryDatasetId
      && !!referenceRunRow?.feature_space_signature
      && run.feature_space_signature === referenceRunRow.feature_space_signature,
  ), [completedAllRuns, queryDatasetId, referenceRunRow?.feature_space_signature]);
  const queryRunRow = queryRuns.find((run) => run.id === queryRunId) ?? null;
  const referenceViews = useMemo(() => datasetViews.filter((view) => view.dataset_id === referenceDatasetId && !view.stale), [datasetViews, referenceDatasetId]);
  const queryViews = useMemo(() => datasetViews.filter((view) => view.dataset_id === queryDatasetId && !view.stale), [datasetViews, queryDatasetId]);
  const crossInputsReady = !!referenceRunRow && !!queryRunRow;
  const pointDataset = crossDatasetModule
    ? st.datasets.find((item) => item.id === queryDatasetId) ?? dataset
    : dataset;
  const pointRunId = crossDatasetModule ? queryRunId : selectedRun;
  const analysisContextRunId = crossDatasetModule ? referenceRunId : selectedRun;
  const selectedPoint = inspectedPoint ?? points.find((point) => point.i === selectedIndices[0]) ?? null;
  // Dataset frames behind the current selection, for saving a purification
  // view: sample i maps to its frame via the preview rows (exact in atom mode;
  // structure-mode rows fall back to i === frame).
  const selectedFrames = useMemo(() => {
    if (tab === "projection") return [];
    const frameOf = new Map<number, number>();
    (preview?.selected ?? []).forEach((row) => {
      const index = Number(row.i ?? row.sample_index);
      if (Number.isInteger(index) && index >= 0) frameOf.set(index, Number(row.frame ?? index));
    });
    return [...new Set(selectedIndices.map((index) => frameOf.get(index) ?? index))].sort((a, b) => a - b);
  }, [preview, selectedIndices, tab]);

  // Whether the (tab, run, parameter combination) result is already computed
  // and can be re-displayed without rerunning. One parameter can be probed
  // with a candidate value; the others stay at their current value.
  const isCached = useCallback(
    (param: string, value: string | number | null) =>
      !!analysisContextRunId && slotForParams(slots, tab, analysisContextRunId, buildParamsKey(tab, { ...analysisParams, [param]: value } as AnalysisParams)) !== null,
    [analysisContextRunId, analysisParams, slots, tab],
  );
  // Cache dot helpers: select options marked per value, numeric labels per current value.
  const markOptions = (param: string, options: CacheOption[]) => withCacheMarks((value) => isCached(param, value), options);
  const cachedParam = (param: keyof AnalysisParams) => isCached(param, analysisParams[param]);

  const refresh = useCallback(async () => {
    if (!dataset) {
      setRuns([]);
      setAllRuns([]);
      setDatasetViews([]);
      setAnalyses([]);
      return;
    }
    try {
      const [allResultRows, analysisRows, viewRows] = await Promise.all([
        ipc.request<RunRow[]>("result.list", {}),
        ipc.request<AnalysisRow[]>("analysis.list", { }),
        ipc.request<DatasetView[]>("dataset.view.list", {}),
      ]);
      const resultRows = allResultRows.filter((row) => row.dataset_id === dataset.id);
      setRuns(resultRows);
      setAllRuns(allResultRows);
      setDatasetViews(viewRows);
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
    if (!dataset) return;
    setReferenceDatasetId((current) => st.datasets.some((item) => item.id === current) ? current : dataset.id);
  }, [dataset, st.datasets]);

  useEffect(() => {
    if (!referenceDatasetId) return;
    setReferenceRunId((current) => {
      if (referenceRuns.some((run) => run.id === current)) return current;
      if (selectedRun && referenceRuns.some((run) => run.id === selectedRun)) return selectedRun;
      return referenceRuns[0]?.id ?? null;
    });
    setReferenceViewId((current) => referenceViews.some((view) => view.id === current) ? current : null);
  }, [referenceDatasetId, referenceRuns, referenceViews, selectedRun]);

  useEffect(() => {
    if (!referenceRunRow) return;
    const compatibleDatasetIds = new Set(
      completedAllRuns
        .filter((run) => run.feature_space_signature === referenceRunRow.feature_space_signature)
        .map((run) => run.dataset_id),
    );
    setQueryDatasetId((current) => {
      if (current && compatibleDatasetIds.has(current)) return current;
      return st.datasets.find((item) => item.id !== referenceDatasetId && compatibleDatasetIds.has(item.id))?.id
        ?? (compatibleDatasetIds.has(referenceDatasetId ?? "") ? referenceDatasetId : null);
    });
  }, [completedAllRuns, referenceDatasetId, referenceRunRow, st.datasets]);

  useEffect(() => {
    setQueryRunId((current) => queryRuns.some((run) => run.id === current) ? current : queryRuns[0]?.id ?? null);
    setQueryViewId((current) => queryViews.some((view) => view.id === current) ? current : null);
  }, [queryRuns, queryViews]);

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
    if (!pointDataset || !selectedPoint) {
      setSelectedFrame(null);
      return;
    }
    let disposed = false;
    setSelectedFrameBusy(true);
    const localShellActive = preview?.kind === "local_diversity" && selectedPoint.row != null;
    const displayCutoff = 2.4;
    const requestedCutoff = Math.min(10, Math.max(displayCutoff, localShellActive ? localCutoff : displayCutoff));
    void ipc.request<FramePayload>("dataset.frame", { id: pointDataset.id, index: selectedPoint.frame, bond_cutoff: requestedCutoff })
      .then((frame) => { if (!disposed) setSelectedFrame({ ...frame, bond_cutoff: displayCutoff }); })
      .catch(() => { if (!disposed) setSelectedFrame(null); })
      .finally(() => { if (!disposed) setSelectedFrameBusy(false); });
    return () => { disposed = true; };
  }, [localCutoff, pointDataset, preview?.kind, selectedPoint]);

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
        // One frame per row: these arrays can legitimately exceed a single
        // chunk, so every trajectory series is stitched back together.
        const loadAll = (kind === "effective_dimension" && name === "explained_variance") || kind === "trajectory";
        const values: unknown[] = [];
        let offset = 0;
        while (true) {
          const chunk = await ipc.request<AnalysisChunk>("analysis.chunk", {
            analysis_id: analysisId,
            array: name,
            offset,
            limit: 20_000,
            column_end: 2_000,
          });
          values.push(...chunk.data);
          if (!loadAll) break;
          const nextOffset = Number(chunk.next_offset);
          const total = Number(chunk.shape?.[0]);
          if (!chunk.data.length || !Number.isFinite(nextOffset) || nextOffset <= offset || (Number.isFinite(total) && nextOffset >= total)) break;
          offset = nextOffset;
        }
        return [name, values] as const;
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

  // The two points-fetch variants behind an analysis: bounded preview arrays
  // (with normalized points) and the PCA payload (points only).
  const fetchAnalysisPoints = useCallback(async (id: string, source: "preview" | "pca"): Promise<CachedAnalysis> => {
    if (source === "pca") {
      const payload = await ipc.request<PcaPayload>("result.get_pca", { analysis_id: id });
      return { preview: null, points: pcaPayloadPoints(payload), selectedIndices: [], arrays: {} };
    }
    const result = await ipc.request<AnalysisPreview>("analysis.preview", { analysis_id: id, limit: 20_000 });
    return { preview: result, points: normalizePoints(result), selectedIndices: selectedIndicesFromPreview(result), arrays: {} };
  }, []);

  // Cache + display: the single place a resolved analysis lands on screen.
  const commitAnalysis = useCallback((id: string, next: CachedAnalysis) => {
    const cached = analysisCache.get(id);
    analysisCache.set(id, { ...next, arrays: cached?.arrays ?? next.arrays });
    setAnalysisId(id);
    setPreview(next.preview);
    setPoints(next.points);
    setSelectedIndices(next.selectedIndices);
    setOverviewArrays(next.arrays);
  }, []);

  const watchAnalysisJob = useCallback(async (jobId: string, method: string, label: string, isCurrent: () => boolean): Promise<{ failed: true } | { failed: false; analysisId: string | null }> => {
    trackJob(jobId, method);
    const offProgress = ipc.on("job.progress", (data) => {
      const event = data as { job_id: string; progress: number };
      if (event.job_id === jobId) setLastJobProgress(event.progress);
    });
    const done = await watchJob(jobId);
    offProgress();
    if (done.status !== "COMPLETED") {
      if (isCurrent()) message.error(`${label} ${jobStatusLabel(tr, done.status)}: ${done.error?.message ?? ""}`);
      return { failed: true };
    }
    return { failed: false, analysisId: typeof done.result?.analysis_id === "string" ? done.result.analysis_id : null };
  }, [message, tr]);

  const runRequest = useCallback(async (
    method: string,
    params: Record<string, unknown>,
    label: string,
    options?: { contextRunId?: string | null; followActiveRun?: boolean },
  ) => {
    const contextRunId = options?.contextRunId ?? selectedRun;
    if (!contextRunId) {
      message.warning(t("Select a completed descriptor run first"));
      return null;
    }
    const requestRunId = contextRunId;
    const requestDatasetId = dataset?.id;
    // The (tab, parameters) context the run was started under: the result and
    // its cache slot belong there even if the user navigates while the job is
    // in flight.
    const requestContext = { ...runContextRef.current };
    const operation = ++operationRef.current;
    const isCurrent = () => operationRef.current === operation
      && (options?.followActiveRun === false || useWorkspace.getState().activeDescriptorRunId === requestRunId)
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
        const watched = await watchAnalysisJob(response.job_id, method, label, isCurrent);
        if (watched.failed) return null;
        if (watched.analysisId !== null) id = watched.analysisId;
      }
      if (!id) throw new Error(`${method} returned no analysis_id`);
      // Record the slot under the request context before the display checks:
      // the computed artifacts stay restorable even when the user has moved to
      // another tab and the result will not land on screen.
      useAnalysisUi.getState().rememberResult({ runId: requestRunId, analysisId: id, tab: requestContext.tab, paramsKey: requestContext.paramsKey });
      if (!isCurrent()) return null;
      const frontendCached = response.job_id ? undefined : analysisCache.get(id);
      if (frontendCached) {
        commitAnalysis(id, frontendCached);
        setLastJobProgress(1);
        message.success(t("{label} loaded from cache", { label }));
        return id;
      }
      const fetched = await fetchAnalysisPoints(id, "preview");
      if (!isCurrent()) return null;
      commitAnalysis(id, fetched);
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
  }, [commitAnalysis, dataset?.id, fetchAnalysisPoints, message, selectedRun, t, tr, watchAnalysisJob]);

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
          const watched = await watchAnalysisJob(response.job_id, "analysis.pca", "PCA", isCurrent);
          if (watched.failed) return;
          if (watched.analysisId !== null) id = watched.analysisId;
        }
        if (!isCurrent()) return;
        useAnalysisUi.getState().rememberResult({ runId: requestRunId, analysisId: id, tab: requestContext.tab, paramsKey: requestContext.paramsKey });
        const frontendCached = response.job_id ? undefined : analysisCache.get(id);
        if (frontendCached) {
          commitAnalysis(id, frontendCached);
          setLastJobProgress(1);
          message.success(t("PCA loaded from cache"));
          return;
        }
        const fetched = await fetchAnalysisPoints(id, "pca");
        if (!isCurrent()) return;
        commitAnalysis(id, fetched);
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
  }, [commitAnalysis, dataset?.id, fetchAnalysisPoints, message, mode, preprocess, projection, runRequest, selectedRun, tsnePerplexity, t, tr, watchAnalysisJob]);

  const handlePreprocessChange = useCallback((value: string) => {
    setPreprocess(value);
    if (projection === "pca" && selectedRun && value !== preprocess) {
      void runProjection({ preprocess: value });
    }
  }, [preprocess, projection, runProjection, selectedRun]);

  const loadAnalysis = useCallback(async (row: AnalysisRow, opts?: { silent?: boolean }) => {
    if (!dataset || row.status !== "COMPLETED") return;
    const analysisType = row.analysis_type.toLowerCase();
    const analysisTab = tabForAnalysisType(analysisType);
    const inputRunIds = row.input_run_ids?.length ? row.input_run_ids : [row.descriptor_run_id];
    const crossDatasetAnalysis = ["coverage", "overlap", "acquisition", "drift"].includes(analysisType);
    if (!crossDatasetAnalysis && (!selectedRun || !inputRunIds.includes(selectedRun))) {
      message.warning(t("Select the source descriptor run before loading this analysis"));
      return;
    }
    const requestRunId = crossDatasetAnalysis ? inputRunIds[0] : selectedRun;
    if (!requestRunId) return;
    if (crossDatasetAnalysis) {
      const reference = allRuns.find((run) => run.id === inputRunIds[0]);
      const query = allRuns.find((run) => run.id === inputRunIds[1]);
      if (!reference || !query) {
        message.warning(t("The source descriptor runs are no longer available"));
        return;
      }
      setReferenceDatasetId(reference.dataset_id);
      setReferenceRunId(reference.id);
      setReferenceViewId(typeof row.parameters?.reference_view_id === "string" ? row.parameters.reference_view_id : null);
      setQueryDatasetId(query.dataset_id);
      setQueryRunId(query.id);
      setQueryViewId(typeof row.parameters?.query_view_id === "string" ? row.parameters.query_view_id : null);
    }

    const operation = ++operationRef.current;
    const requestDatasetId = dataset.id;
    // `tab` guards against a tab switch while a slow restore fetch is in
    // flight — the stale result must not land on (or yank back) another tab.
    const isCurrent = () => operationRef.current === operation
      && (crossDatasetAnalysis || useWorkspace.getState().activeDescriptorRunId === requestRunId)
      && useWorkspace.getState().activeDatasetId === requestDatasetId
      && useAnalysisUi.getState().view.tab === analysisTab;
    // Slot context for the loaded analysis, derived from the row itself. The
    // cached path below records before React re-renders, so the live
    // runContextRef would still describe the tab the user is leaving and
    // would file this analysis under another tab's parameters.
    const loadedParams: AnalysisParams = { ...runContextRef.current.params };
    if (crossDatasetAnalysis) {
      loadedParams.referenceRunId = inputRunIds[0] ?? null;
      loadedParams.queryRunId = inputRunIds[1] ?? null;
      loadedParams.referenceViewId = typeof row.parameters?.reference_view_id === "string" ? row.parameters.reference_view_id : null;
      loadedParams.queryViewId = typeof row.parameters?.query_view_id === "string" ? row.parameters.query_view_id : null;
    }
    setTab(analysisTab);
    if (analysisTab === "overview" && ["feature_variance", "feature_correlation", "effective_dimension", "property_correlation", "trajectory", "drift", "sensitivity", "perturbation_sensitivity"].includes(analysisType)) {
      setOverviewAnalysis(analysisType as OverviewAnalysis);
      loadedParams.overviewAnalysis = analysisType as OverviewAnalysis;
      if (analysisType === "feature_variance") {
        const savedNear = finiteNumber(row.parameters?.near_zero_relative_threshold);
        const savedLow = finiteNumber(row.parameters?.low_variance_relative_threshold);
        const nextNear = savedNear == null ? nearZeroThreshold : Math.min(1, Math.max(0, savedNear));
        const nextLow = savedLow == null
          ? Math.max(nextNear, lowVariationThreshold)
          : Math.min(1, Math.max(nextNear, savedLow));
        setNearZeroThreshold(nextNear);
        setLowVariationThreshold(nextLow);
        loadedParams.nearZeroThreshold = nextNear;
        loadedParams.lowVariationThreshold = nextLow;
      } else if (analysisType === "feature_correlation") {
        const nextMethod = row.parameters?.method === "spearman" ? "spearman" : "pearson";
        const savedThreshold = finiteNumber(row.parameters?.correlation_threshold ?? row.parameters?.redundancy_threshold);
        const nextThreshold = savedThreshold == null ? featureCorrelationThreshold : Math.min(1, Math.max(0, savedThreshold));
        setFeatureCorrelationMethod(nextMethod);
        setFeatureCorrelationThreshold(nextThreshold);
        loadedParams.featureCorrelationMethod = nextMethod;
        loadedParams.featureCorrelationThreshold = nextThreshold;
      } else if (analysisType === "effective_dimension") {
        // Analyses created before the preprocessing setting was exposed used
        // centered data; keep their cache key and control faithful on restore.
        const nextPreprocess: EffectiveDimensionPreprocess = row.parameters?.preprocess === "standardized" ? "standardized" : "center";
        setEffectiveDimensionPreprocess(nextPreprocess);
        loadedParams.effectiveDimensionPreprocess = nextPreprocess;
      } else if (analysisType === "property_correlation") {
        const nextFolds = Math.max(2, Math.round(finiteNumber(row.parameters?.folds) ?? 5));
        const nextK = Math.max(1, Math.round(finiteNumber(row.parameters?.reliability_k) ?? 5));
        const nextMetric = row.parameters?.distance_metric === "cosine" ? "cosine" : "euclidean";
        const nextSparse = Math.round((finiteNumber(row.parameters?.sparse_quantile) ?? 0.90) * 100);
        const nextOod = Math.round((finiteNumber(row.parameters?.ood_quantile) ?? 0.99) * 100);
        setPropertyName(String(row.parameters?.property ?? "energy_per_atom"));
        setPropertyFolds(nextFolds);
        setPropertyReliabilityK(nextK);
        setPropertyDistanceMetric(nextMetric);
        setPropertySparsePercentile(nextSparse);
        setPropertyOodPercentile(nextOod);
        loadedParams.propertyName = String(row.parameters?.property ?? "energy_per_atom");
        loadedParams.propertyFolds = nextFolds;
        loadedParams.propertyReliabilityK = nextK;
        loadedParams.propertyDistanceMetric = nextMetric;
        loadedParams.propertySparsePercentile = nextSparse;
        loadedParams.propertyOodPercentile = nextOod;
      }
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
        commitAnalysis(row.id, cached);
        useAnalysisUi.getState().rememberResult({ runId: requestRunId, analysisId: row.id, ...loadedContext });
        setLastJobProgress(1);
        if (!opts?.silent) message.success(t("Loaded cached {name}", { name: analysisType.toUpperCase() }));
        return;
      }

      const fetched = await fetchAnalysisPoints(row.id, analysisType === "pca" ? "pca" : "preview");
      if (!isCurrent()) return;
      commitAnalysis(row.id, fetched);
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
  }, [allRuns, commitAnalysis, dataset, featureCorrelationThreshold, fetchAnalysisPoints, lowVariationThreshold, message, nearZeroThreshold, selectedRun, setEffectiveDimensionPreprocess, setFeatureCorrelationMethod, setFeatureCorrelationThreshold, setLowVariationThreshold, setNearZeroThreshold, t]);

  // Keep the displayed result in step with the current tab + parameters: an
  // exact slot match (same tab, run, parameters) is re-displayed from the
  // backend artifacts instead of recomputing; switching tabs falls back to
  // that tab's most recent result; anything else clears the stale display.
  // One load attempt per analysis id (reset when the dataset/run changes) so
  // a persistent fetch error cannot loop.
  useEffect(() => {
    if (busy || loadingAnalysisId) return;
    const slotMap = useAnalysisUi.getState().slots;
    const exact = slotForParams(slotMap, tab, analysisContextRunId, paramsKey);
    const tabChanged = lastLookedTabRef.current !== tab;
    lastLookedTabRef.current = tab;
    const slot = exact ?? (tabChanged ? latestSlotForTab(slotMap, tab, analysisContextRunId) : null);
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
      const cached = analysisContextRunId ? analysisCache.get(wantedId) : undefined;
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
    if (!row || row.status !== "COMPLETED" || !inputRunIds.includes(analysisContextRunId ?? "")) {
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
  }, [analyses, analysisContextRunId, analysisId, busy, clearDisplayedAnalysis, loadAnalysis, loadingAnalysisId, paramsKey, tab]);

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
        if (!crossInputsReady || !referenceRunId || !queryRunId) {
          message.warning(t("Select compatible reference and query runs"));
          return;
        }
        const uncertainty = samplingAlgorithm === "uncertainty_diversity";
        await runRequest("analysis.acquisition", { reference_run_id: referenceRunId, query_run_id: queryRunId, ...(referenceViewId ? { reference_view_id: referenceViewId } : {}), ...(queryViewId ? { query_view_id: queryViewId } : {}), n_samples: nSamples, mode, acquisition_method: samplingAlgorithm, novelty_weight: 0.65, uncertainty_weight: 0.65, uncertainty_k: uncertaintyK }, uncertainty ? t("Uncertainty acquisition") : t("Novelty acquisition"), { contextRunId: referenceRunId, followActiveRun: false });
      } else {
        await runRequest("analysis.sampling", { algorithm: samplingAlgorithm, n_samples: nSamples, mode }, t("Sampling"));
      }
    } else if (tab === "coverage") {
      if (!crossInputsReady || !referenceRunId || !queryRunId) {
        message.warning(t("Select compatible reference and query runs"));
        return;
      }
      await runRequest(`analysis.${coverageMode}`, { reference_run_id: referenceRunId, query_run_id: queryRunId, ...(referenceViewId ? { reference_view_id: referenceViewId } : {}), ...(queryViewId ? { query_view_id: queryViewId } : {}), metric: "euclidean", mode }, coverageMode === "coverage" ? t("Coverage") : t("Overlap"), { contextRunId: referenceRunId, followActiveRun: false });
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
      if (overviewAnalysis === "drift" && (!crossInputsReady || !referenceRunId || !queryRunId)) {
        message.warning(t("Select compatible reference and query runs"));
        return;
      }
      if (overviewAnalysis === "sensitivity" && (!secondRun || !selectedRun)) {
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
        ? { reference_run_id: referenceRunId, query_run_id: queryRunId, ...(referenceViewId ? { reference_view_id: referenceViewId } : {}), ...(queryViewId ? { query_view_id: queryViewId } : {}), metric: "euclidean", mode }
        : overviewAnalysis === "sensitivity"
          ? { run_ids: [selectedRun, secondRun] }
          : overviewAnalysis === "perturbation_sensitivity"
            ? { perturbation: perturbationType, n_amplitudes: perturbationCount, max_amplitude: perturbationMaximum, metric: perturbationMetric, max_structures: perturbationStructures, preprocess: "standardized" }
          : overviewAnalysis === "trajectory"
            ? {}
          : overviewAnalysis === "property_correlation"
              ? { property: propertyName, folds: propertyFolds, top_k: 50, mode, reliability_k: propertyReliabilityK, distance_metric: propertyDistanceMetric, sparse_quantile: propertySparsePercentile / 100, ood_quantile: propertyOodPercentile / 100 }
            : overviewAnalysis === "feature_correlation"
              ? { top_k: 20, method: featureCorrelationMethod, correlation_threshold: featureCorrelationThreshold }
              : overviewAnalysis === "effective_dimension"
                ? { preprocess: effectiveDimensionPreprocess }
                : overviewAnalysis === "feature_variance"
                  ? { top_k: 20, near_zero_relative_threshold: nearZeroThreshold, low_variance_relative_threshold: lowVariationThreshold }
                  : { top_k: 20 };
      await runRequest(`analysis.${overviewAnalysis}`, overviewParams, tr(OVERVIEW_MODULE_LABELS[overviewAnalysis]), overviewAnalysis === "drift" ? { contextRunId: referenceRunId, followActiveRun: false } : undefined);
    }
  }, [clusterAlgorithm, contamination, coverageMode, crossInputsReady, effectiveDimensionPreprocess, featureCorrelationMethod, featureCorrelationThreshold, k, kernelName, localCutoff, lowVariationThreshold, mantelMethod, mantelPermutations, mode, nClusters, nSamples, nearZeroThreshold, overviewAnalysis, perturbationCount, perturbationMaximum, perturbationMetric, perturbationStructures, perturbationType, propertyDistanceMetric, propertyFolds, propertyName, propertyOodPercentile, propertyReliabilityK, propertySparsePercentile, queryIndex, queryRunId, queryViewId, referenceRunId, referenceViewId, runProjection, runRequest, runs, samplingAlgorithm, secondRun, selectedRun, setLowVariationThreshold, setNearZeroThreshold, similarityMode, tab, t, tr, uncertaintyK, message, compareMode]);

  const inspectPoint = useCallback((point: Point) => {
    setInspectedPoint(point);
    if (pointDataset && pointRunId) {
      useWorkspace.getState().setSelectedSample({ datasetId: pointDataset.id, runId: pointRunId, mode: point.row == null ? mode : "atom", frame: point.frame, atom: point.row });
      useWorkspace.getState().setActiveFrame(point.frame);
    }
  }, [mode, pointDataset, pointRunId]);

  const openPointInExplore = useCallback(() => {
    if (!pointDataset || !selectedPoint) return;
    const workspace = useWorkspace.getState();
    workspace.setActiveDataset(pointDataset.id);
    const next = useWorkspace.getState();
    next.setActiveFrame(selectedPoint.frame);
    next.setSelectedSample({ datasetId: pointDataset.id, ...(pointRunId ? { runId: pointRunId } : {}), mode: selectedPoint.row == null ? mode : "atom", frame: selectedPoint.frame, atom: selectedPoint.row });
    next.setPage("explore");
  }, [mode, pointDataset, pointRunId, selectedPoint]);

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

  // Structure Preview click-to-select: mirror the chart's point pipeline so
  // the preview highlight, inspector, and workspace sample stay in sync. A
  // synthetic point (like the result-table path) covers atoms with no
  // counterpart among the current projection's points.
  const handlePreviewAtomSelect = useCallback((atom: number) => {
    if (!pointDataset || !selectedFrame) return;
    if (selectedPoint?.row === atom && selectedPoint.frame === selectedFrame.index) {
      setInspectedPoint(null);
      setSelectedIndices([]);
      updateCachedSelection([]);
      useWorkspace.getState().setSelectedSample(null);
      return;
    }
    const existing = points.find((point) => point.frame === selectedFrame.index && point.row === atom);
    if (existing) {
      handlePoint(existing);
      return;
    }
    handlePoint({ i: selectedPoint?.i ?? selectedIndices[0] ?? points.length, frame: selectedFrame.index, row: atom, x: 0, y: 0 });
  }, [handlePoint, pointDataset, points, selectedFrame, selectedPoint, selectedIndices, updateCachedSelection]);

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
    return crossDatasetModule
      ? (row.dataset_ids ?? []).includes(dataset.id)
      : !selectedRun || inputRunIds.includes(selectedRun);
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
  const overviewModuleControl = <Space className="analysis-overview-module-control" wrap><Typography.Text>{t("Module")}</Typography.Text><Select className="analysis-overview-module-select" value={overviewAnalysis} onChange={setOverviewAnalysis} options={markOptions("overviewAnalysis", [{ value: "feature_variance", label: t("Feature variance") }, { value: "feature_correlation", label: t("Feature correlation") }, { value: "effective_dimension", label: t("Effective dimension") }, { value: "property_correlation", label: t("Property correlation") }, { value: "trajectory", label: t("Trajectory") }, { value: "drift", label: t("Dataset drift") }, { value: "sensitivity", label: t("Parameter sensitivity") }, { value: "perturbation_sensitivity", label: t("Structural perturbation") }])} /></Space>;
  const overviewModuleHint = tab === "overview" && overviewAnalysis === "sensitivity" && <Typography.Text type="secondary">{t("Compare parameter variants of the same descriptor; use Compare for different descriptors.")}</Typography.Text>;
  const legacyOverview = tab === "overview" && (preview?.kind === "feature_variance" || preview?.kind === "effective_dimension");

  return (
    <div className="analysis-page">
      <section className="analysis-toolbar">
        <Space wrap>
          {crossDatasetModule ? <>
            <Typography.Text strong>{t("Cross-dataset analysis")}</Typography.Text>
            <Tag>{dataset.name}</Tag>
          </> : <>
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
          </>}
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
          <section className={`analysis-card analysis-controls${tab === "overview" && overviewAnalysis === "property_correlation" ? " analysis-controls-property" : ""}`}>
            {tab === "projection" && <ProjectionControls projection={projection} setProjection={setProjection} mode={mode} setMode={setMode} preprocess={preprocess} onPreprocessChange={handlePreprocessChange} tsnePerplexity={tsnePerplexity} setTsnePerplexity={setTsnePerplexity} markOptions={markOptions} cachedParam={cachedParam} />}
            {tab === "similarity" && <Space wrap><Typography.Text>{t("View")}</Typography.Text><Select value={similarityMode} onChange={setSimilarityMode} options={markOptions("similarityMode", [{ value: "query", label: t("Query neighbors") }, { value: "all_neighbors", label: t("All-neighbor graph") }, { value: "pairwise", label: t("Pairwise matrix") }])} /><Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "clusters" && <Space wrap><Typography.Text>{t("Algorithm")}</Typography.Text><Select value={clusterAlgorithm} onChange={setClusterAlgorithm} options={markOptions("clusterAlgorithm", ["kmeans", "dbscan", "hdbscan", "agglomerative"].map((value) => ({ value, label: value.toUpperCase() })))} /><ParamLabel label={t("Clusters")} cached={cachedParam("nClusters")} /><InputNumber min={2} value={nClusters} onChange={(value) => setNClusters(value ?? 6)} /><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "outliers" && <Space wrap><Typography.Text>{t("Algorithm")}</Typography.Text><Select value={outlierAlgorithm} onChange={setOutlierAlgorithm} options={markOptions("outlierAlgorithm", ["lof", "knn", "isolation_forest", "mahalanobis"].map((value) => ({ value, label: value.toUpperCase() })))} /><ParamLabel label={t("Contamination")} cached={cachedParam("contamination")} /><InputNumber min={0.001} max={0.5} step={0.001} value={contamination} onChange={(value) => setContamination(value ?? 0.01)} /><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "sampling" && <Space wrap><Typography.Text>{t("Method")}</Typography.Text><Select value={samplingAlgorithm} onChange={setSamplingAlgorithm} options={markOptions("samplingAlgorithm", ["fps", "novelty_fps", "uncertainty_diversity", "random", "stratified", "cluster_representative", "per_element"].map((value) => ({ value, label: tr(SAMPLING_LABELS[value] ?? { en: value, zh: value }) })))} /><ParamLabel label={t("Target")} cached={cachedParam("nSamples")} /><InputNumber min={1} value={nSamples} onChange={(value) => setNSamples(value ?? 1000)} /><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom") }])} />{samplingAlgorithm === "uncertainty_diversity" && <><ParamLabel label="kNN" cached={cachedParam("uncertaintyK")} /><InputNumber min={2} value={uncertaintyK} onChange={(value) => setUncertaintyK(value ?? 8)} /></>}</Space>}
            {tab === "coverage" && <Space wrap><Typography.Text>{t("Analysis")}</Typography.Text><Select value={coverageMode} onChange={setCoverageMode} options={markOptions("coverageMode", [{ value: "coverage", label: t("Coverage") }, { value: "overlap", label: t("Train / test overlap") }])} /><Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "local" && <Space wrap><ParamLabel label={t("Clusters / element")} cached={cachedParam("nClusters")} /><InputNumber min={2} value={nClusters} onChange={(value) => setNClusters(value ?? 6)} /><ParamLabel label={t("Descriptor kNN")} cached={cachedParam("k")} /><InputNumber min={1} value={k} onChange={(value) => setK(value ?? 10)} /><ParamLabel label={t("Neighbor cutoff")} cached={cachedParam("localCutoff")} /><InputNumber min={0.1} max={10} step={0.1} precision={2} value={localCutoff} onChange={(value) => setLocalCutoff(value == null ? 3 : Math.max(0.1, Math.min(10, value)))} addonAfter="Å" /><Typography.Text type="secondary">{t("Coordinates and periodic images determine coordination.")}</Typography.Text></Space>}
            {tab === "kernel" && <Space wrap><Typography.Text>{t("Kernel")}</Typography.Text><Select value={kernelName} onChange={setKernelName} options={markOptions("kernelName", ["rbf", "linear", "cosine", "polynomial"].map((value) => ({ value, label: value.toUpperCase() })))} /><Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "overview" && overviewModuleControl}
            {crossDatasetModule && <CrossDatasetPicker
              datasets={st.datasets}
              referenceDatasetId={referenceDatasetId}
              queryDatasetId={queryDatasetId}
              referenceRunId={referenceRunId}
              queryRunId={queryRunId}
              referenceViewId={referenceViewId}
              queryViewId={queryViewId}
              referenceRuns={referenceRuns}
              queryRuns={queryRuns}
              referenceViews={referenceViews}
              queryViews={queryViews}
              compatible={crossInputsReady}
              disabled={busy}
              onReferenceDataset={(value) => { setReferenceDatasetId(value); setReferenceRunId(null); setReferenceViewId(null); }}
              onQueryDataset={(value) => { setQueryDatasetId(value); setQueryRunId(null); setQueryViewId(null); }}
              onReferenceRun={setReferenceRunId}
              onQueryRun={setQueryRunId}
              onReferenceView={setReferenceViewId}
              onQueryView={setQueryViewId}
              onSwap={() => {
                const nextReferenceDataset = queryDatasetId;
                const nextReferenceRun = queryRunId;
                const nextReferenceView = queryViewId;
                setQueryDatasetId(referenceDatasetId);
                setQueryRunId(referenceRunId);
                setQueryViewId(referenceViewId);
                setReferenceDatasetId(nextReferenceDataset);
                setReferenceRunId(nextReferenceRun);
                setReferenceViewId(nextReferenceView);
              }}
            />}
            {tab === "overview" && overviewAnalysis === "feature_variance" && <Space wrap>
              <ParamLabel label={t("Near-zero threshold")} cached={cachedParam("nearZeroThreshold")} />
              <InputNumber min={0} max={1} step={0.0001} precision={6} value={nearZeroThreshold} onChange={(value) => setNearZeroThreshold(Math.min(lowVariationThreshold, Math.max(0, value ?? 1e-4)))} />
              <ParamLabel label={t("Low variation threshold")} cached={cachedParam("lowVariationThreshold")} />
              <InputNumber min={0} max={1} step={0.0001} precision={6} value={lowVariationThreshold} onChange={(value) => setLowVariationThreshold(Math.min(1, Math.max(nearZeroThreshold, value ?? 1e-2)))} />
            </Space>}
            {tab === "overview" && overviewAnalysis === "feature_correlation" && <Space wrap>
              <ParamLabel label={t("Correlation method")} cached={cachedParam("featureCorrelationMethod")} />
              <Select aria-label={t("Correlation method")} value={featureCorrelationMethod} onChange={setFeatureCorrelationMethod} options={markOptions("featureCorrelationMethod", [{ value: "pearson", label: "Pearson" }, { value: "spearman", label: "Spearman" }])} />
              <ParamLabel label={t("High-correlation threshold")} cached={cachedParam("featureCorrelationThreshold")} />
              <InputNumber min={0.8} max={0.999} step={0.01} precision={2} value={featureCorrelationThreshold} onChange={(value) => setFeatureCorrelationThreshold(Math.min(0.999, Math.max(0.8, value ?? 0.95)))} />
              <Typography.Text type="secondary">{t("High when |correlation| ≥ threshold")}</Typography.Text>
            </Space>}
            {tab === "overview" && overviewAnalysis === "effective_dimension" && <Space wrap>
              <ParamLabel label={t("PCA preprocessing")} cached={cachedParam("effectiveDimensionPreprocess")} />
              <Select
                aria-label={t("PCA preprocessing")}
                value={effectiveDimensionPreprocess}
                onChange={setEffectiveDimensionPreprocess}
                options={markOptions("effectiveDimensionPreprocess", [
                  { value: "standardized", label: t("Standardized") },
                  { value: "center", label: t("Centered") },
                ])}
              />
              <Typography.Text type="secondary">{effectiveDimensionPreprocess === "standardized" ? t("Correlation basis") : t("Covariance basis")}</Typography.Text>
            </Space>}
            {tab === "overview" && overviewAnalysis === "perturbation_sensitivity" && <Space wrap><Typography.Text>{t("Perturbation")}</Typography.Text><Select value={perturbationType} onChange={setPerturbationType} options={markOptions("perturbationType", [{ value: "jitter", label: t("Atomic jitter (Å)") }, { value: "strain", label: t("Isotropic strain") }])} /><ParamLabel label={t("Steps")} cached={cachedParam("perturbationCount")} /><InputNumber min={2} max={32} value={perturbationCount} onChange={(value) => setPerturbationCount(value ?? 8)} /><ParamLabel label={t("Maximum")} cached={cachedParam("perturbationMaximum")} /><InputNumber min={0.001} step={0.01} precision={3} value={perturbationMaximum} onChange={(value) => setPerturbationMaximum(value ?? 0.2)} /><ParamLabel label={t("Max structures")} cached={cachedParam("perturbationStructures")} /><Tooltip title={t("Structures sampled evenly across the run; every one is recomputed per amplitude.")} placement="top"><InputNumber aria-label={t("Max structures")} min={1} max={2048} step={8} value={perturbationStructures} onChange={(value) => setPerturbationStructures(Math.max(1, Math.min(2048, Math.round(value ?? 64))))} /></Tooltip><Typography.Text>{t("Metric")}</Typography.Text><Select value={perturbationMetric} onChange={setPerturbationMetric} options={markOptions("perturbationMetric", ["euclidean", "cosine", "manhattan"].map((value) => ({ value, label: value })))} /></Space>}
            {(tab === "compare" || (tab === "overview" && overviewAnalysis === "sensitivity")) && <Space wrap><Typography.Text>{tab === "compare" ? t("Left") : t("Reference")}</Typography.Text><Select value={selectedRun ?? undefined} style={{ width: 220 }} disabled={busy} options={completedRuns.map((run) => ({ value: run.id, label: run.descriptor_name + " · " + run.id }))} onChange={setSelectedRun} /><Typography.Text>{tab === "compare" ? t("Right") : t("Query")}</Typography.Text><Select value={secondRun ?? undefined} style={{ width: 220 }} disabled={busy} notFoundContent={sensitivityPair ? t("No other completed run for this descriptor") : undefined} options={pairRuns.filter((run) => run.id !== selectedRun).map((run) => ({ value: run.id, label: run.descriptor_name + " · " + run.id }))} onChange={setSecondRun} /></Space>}
            {tab === "compare" && <Space wrap><Typography.Text>{t("Test")}</Typography.Text><Select value={compareMode} onChange={setCompareMode} options={markOptions("compareMode", [{ value: "geometry", label: t("Geometry comparison") }, { value: "mantel", label: t("Mantel permutation test") }])} />{compareMode === "mantel" && <><Typography.Text>{t("Statistic")}</Typography.Text><Select value={mantelMethod} onChange={setMantelMethod} options={markOptions("mantelMethod", [{ value: "pearson", label: "Pearson" }, { value: "spearman", label: "Spearman" }])} /><ParamLabel label={t("Permutations")} cached={cachedParam("mantelPermutations")} /><InputNumber min={1} max={5000} value={mantelPermutations} onChange={(value) => setMantelPermutations(value ?? 999)} /></>}</Space>}
            {tab === "similarity" && similarityMode !== "pairwise" && <Space wrap>{similarityMode === "query" && <><ParamLabel label={t("Query index")} cached={cachedParam("queryIndex")} /><InputNumber min={0} value={queryIndex} onChange={(value) => setQueryIndex(value ?? 0)} /></>}<ParamLabel label="k" cached={cachedParam("k")} /><InputNumber min={1} value={k} onChange={(value) => setK(value ?? 10)} /></Space>}
            {tab === "overview" && overviewAnalysis === "trajectory" && <Typography.Text type="secondary">{t("Frame range, trajectory sampling interval, event method, sensitivity, and coloring live in the trajectory result itself.")}</Typography.Text>}
            {tab === "overview" && overviewAnalysis === "property_correlation" && <Space className="analysis-property-controls" wrap>
              <Typography.Text>{t("Property")}</Typography.Text><Select value={propertyName} onChange={(value) => { setPropertyName(value); if (value === "force_magnitude") setMode("atom"); }} options={markOptions("propertyName", [{ value: "energy_per_atom", label: t("Energy / atom") }, { value: "energy", label: t("Energy") }, { value: "force_max", label: t("Max |F|") }, { value: "force_magnitude", label: t("Atom |F|") }, { value: "volume", label: t("Volume") }])} />
              <Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} />
              <Typography.Text>{t("Model")}</Typography.Text><Tag>Ridge</Tag>
              <Typography.Text>{t("CV folds")}</Typography.Text><InputNumber aria-label={t("CV folds")} min={2} max={20} value={propertyFolds} onChange={(value) => setPropertyFolds(value ?? 5)} />
              <Typography.Text>kNN k</Typography.Text><InputNumber aria-label="kNN k" min={1} max={50} value={propertyReliabilityK} onChange={(value) => setPropertyReliabilityK(value ?? 5)} />
              <Typography.Text>{t("Distance metric")}</Typography.Text><Select aria-label={t("Distance metric")} value={propertyDistanceMetric} onChange={setPropertyDistanceMetric} options={[{ value: "euclidean", label: "Euclidean" }, { value: "cosine", label: "Cosine" }]} />
              <Typography.Text>{t("Sparse threshold (%)")}</Typography.Text><InputNumber aria-label={t("Sparse threshold")} min={50} max={98} value={propertySparsePercentile} onChange={(value) => setPropertySparsePercentile(Math.min(propertyOodPercentile - 1, value ?? 90))} />
              <Typography.Text>{t("OOD-like threshold (%)")}</Typography.Text><InputNumber aria-label={t("OOD-like threshold")} min={propertySparsePercentile + 1} max={99} value={propertyOodPercentile} onChange={(value) => setPropertyOodPercentile(Math.max(propertySparsePercentile + 1, value ?? 99))} />
            </Space>}
            <div className="analysis-controls-actions">
              <Space wrap>
                <Button type="primary" icon={<CheckmarkCircle16Regular />} loading={busy && runningInfo !== null && runningInfo.tab === tab && (tab !== "projection" || runningInfo.method === `analysis.${projection}`)} disabled={crossDatasetModule ? !crossInputsReady : !selectedRun} onClick={() => void runTabAnalysis()}>{tab === "projection" ? t("Run {name}", { name: projection.toUpperCase() }) : tab === "overview" ? t("Run {name}", { name: tr(OVERVIEW_MODULE_LABELS[overviewAnalysis]) }) : t("Run {name}", { name: tr(TAB_LABELS[tab]) })}</Button>
                {/* Only the Projection canvas recolors by property; every
                    other module owns its coloring inside the result view. */}
                {tab === "projection" && points.length > 0 && <Select size="small" aria-label={t("Color by")} value={colorBy} onChange={setColorBy} options={[{ value: "none", label: t("No color") }, { value: "energy", label: t("Energy") }, { value: "force_max", label: t("Max |F|") }, { value: "volume", label: t("Volume") }]} />}
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

          {pointDataset && (
            <SaveViewModal
              open={saveViewOpen}
              onClose={() => setSaveViewOpen(false)}
              datasetId={pointDataset.id}
              frames={selectedFrames}
              totalFrames={pointDataset.number_of_frames}
              defaultName={`${tr(TAB_LABELS[tab])} ${selectedFrames.length}`}
              source={{ source: "analysis", analysis_type: String(preview?.kind ?? "") }}
              onSaved={(view) => setDatasetViews((prev) => [...prev, view])}
            />
          )}

          {tab === "projection" && <section className="analysis-card analysis-plot-card"><SectionHeading title={t("DESCRIPTOR SPACE")} meta={`${t("{n} preview points", { n: points.length.toLocaleString() })}${selectedIndices.length ? t(" · {n} selected", { n: selectedIndices.length }) : ""}`} />{points.length ? <div className="analysis-plot-frame">{plot}</div> : <Empty description={t("Run PCA, UMAP, or t-SNE to populate the Plotly canvas.")} />}</section>}
          {legacyOverview && <OverviewResultVisualization preview={preview} arrays={overviewArrays} loading={overviewArraysBusy} analysisId={analysisId} />}
          {tab !== "projection" && !legacyOverview && <AnalysisResultVisualization preview={preview} arrays={overviewArrays} points={points} loading={overviewArraysBusy} selectedIndices={selectedIndices} onSelect={handlePoint} />}
          {tab !== "projection" && selectedFrames.length > 0 && (
            <section className="analysis-card">
              <Space wrap>
                <Typography.Text type="secondary">{t("{n} frames selected", { n: selectedFrames.length.toLocaleString() })}</Typography.Text>
                <Button size="small" disabled={busy} onClick={() => setSaveViewOpen(true)}>{t("Save selection as view")}</Button>
              </Space>
            </section>
          )}
          {tab !== "projection" && <ResultPanel preview={preview} points={points} onSelect={(row) => {
            if (row.i == null && row.sample_index == null && row.frame == null) return;
            const index = Number(row.i ?? row.sample_index ?? 0);
            const frame = Number(row.frame ?? index);
            handlePoint({ i: index, frame, row: row.row == null ? undefined : Number(row.row), sample_id: row.sample_id == null ? undefined : String(row.sample_id), x: 0, y: 0, label: row.labels == null ? undefined : Number(row.labels), score: row.scores == null ? undefined : Number(row.scores), distance: row.distances == null ? undefined : Number(row.distances), element: row.element == null ? undefined : Number(row.element), cluster: row.cluster_labels == null ? undefined : Number(row.cluster_labels), coordination: row.coordination == null ? undefined : Number(row.coordination), novelty: row.novelty == null ? undefined : Number(row.novelty), uncertainty: row.uncertainty == null ? undefined : Number(row.uncertainty), diversity: row.diversity == null ? undefined : Number(row.diversity) });
          }} />}

          {tab === "sampling" && <section className="analysis-card"><SectionHeading title={t("EXPORT SELECTED SET")} meta={t("Source data is never modified")} /><Space.Compact style={{ width: "100%" }}><Select value={exportFormat} onChange={setExportFormat} options={["json", "csv", "extxyz", "deepmd"].map((value) => ({ value, label: value.toUpperCase() }))} style={{ width: 120 }} /><Input readOnly placeholder={t("Choose an export destination")} value={exportPath} aria-label={t("Export destination")} /><Button onClick={() => void chooseExportPath()}>{t("Choose…")}</Button><Button icon={<ArrowDownload16Regular />} onClick={() => void exportSelection()}>{t("Export")}</Button></Space.Compact></section>}
        </main>

        <aside className="analysis-inspector">
          <section className="analysis-card"><SectionHeading title={t("INSPECTOR")} meta={selectedPoint ? t("Frame {index}", { index: selectedPoint.frame }) : undefined} />{selectedPoint ? <><Row k={t("Sample")} v={selectedPoint.sample_id ?? String(selectedPoint.i)} /><Row k={t("Frame")} v={String(selectedPoint.frame)} />{selectedPoint.row != null && <Row k={t("Row")} v={String(selectedPoint.row)} />}<Button size="small" icon={<ArrowRight16Regular />} onClick={openPointInExplore}>{t("Open in Explore")}</Button></> : <Typography.Text type="secondary">{t("Click a point, or use box/lasso selection, to inspect a structure.")}</Typography.Text>}</section>
          <section className="analysis-card"><SectionHeading title={t("STRUCTURE PREVIEW")} meta={selectedFrame ? t("Frame {index}", { index: selectedFrame.index }) : undefined} />{selectedFrame ? <StructurePreview frame={selectedFrame} selectedAtom={selectedPoint?.row} localCutoff={preview?.kind === "local_diversity" && selectedPoint?.row != null ? localCutoff : undefined} onOpen={openPointInExplore} onSelectAtom={handlePreviewAtomSelect} /> : <div className="analysis-empty-small">{selectedFrameBusy ? t("Loading structure…") : t("Select a sample to preview it.")}</div>}</section>
          <section className="analysis-card"><SectionHeading title={t("ANALYSIS HISTORY")} meta={`${visibleAnalyses.length}`} />{visibleAnalyses.length ? <div className="analysis-history-list">{visibleAnalyses.slice(0, 10).map((row) => <div className="analysis-history-row" key={row.id}><div><Typography.Text strong>{row.analysis_type}</Typography.Text><Typography.Text type="secondary" style={{ display: "block", fontSize: 11 }}>{new Date(row.created_at).toLocaleString(locale)}</Typography.Text></div><Space size={4}><Tag color={row.status === "COMPLETED" ? "green" : row.status === "STALE" ? "orange" : undefined}>{jobStatusLabel(tr, row.status)}</Tag><Button size="small" type="text" icon={<ArrowRight16Regular />} aria-label={t("Load {name} analysis", { name: row.analysis_type })} title={t("Load cached analysis")} loading={loadingAnalysisId === row.id} disabled={row.status !== "COMPLETED" || (loadingAnalysisId !== null && loadingAnalysisId !== row.id)} onClick={() => void loadAnalysis(row)} /><Button size="small" type="text" icon={<Delete16Regular />} aria-label={t("Delete {name} analysis", { name: row.analysis_type })} disabled={row.status === "RUNNING" || row.status === "QUEUED"} onClick={() => void deleteAnalysis(row)} /></Space></div>)}</div> : <Typography.Text type="secondary">{t("No analysis artifacts for this descriptor run yet.")}</Typography.Text>}</section>
        </aside>
      </div>
    </div>
  );
}

function OverviewResultVisualization({ preview, arrays, loading, analysisId }: { preview: AnalysisPreview | null; arrays: NumericArrays; loading: boolean; analysisId: string | null }) {
  const { t, tr } = useT();
  if (!preview) return null;
  const kind = String(preview.kind ?? "");
  const kindLabel = OVERVIEW_KIND_LABELS[kind];
  const title = kindLabel ? tr(kindLabel) : t("ANALYSIS RESULT");
  if (loading) {
    return <section className="analysis-card analysis-visual-card"><SectionHeading title={title} meta={t("Loading chart data")} /><div className="analysis-overview-empty"><Empty description={t("Loading the result arrays…")} /></div></section>;
  }

  let content: ReactNode;
  if (kind === "feature_variance") content = <FeatureVarianceChart preview={preview} analysisId={analysisId} />;
  else if (kind === "effective_dimension") content = <EffectiveDimensionChart preview={preview} arrays={arrays} />;
  else return null;

  return <section className="analysis-card analysis-visual-card"><SectionHeading title={title} meta={t("Visual summary")} />{content}</section>;
}

function FeatureVarianceChart({ preview, analysisId }: { preview: AnalysisPreview; analysisId: string | null }) {
  const { t, tr } = useT();
  const features = useMemo(() => parseFeatureVarianceRows(preview), [preview]);
  const positiveVariances = useMemo(
    () => features.map((feature) => feature.variance).filter((value): value is number => value !== null && value > 0),
    [features],
  );
  const varianceSpan = positiveVariances.length ? Math.max(...positiveVariances) / Math.min(...positiveVariances) : 0;
  const recommendedScale: "linear" | "log" = varianceSpan >= 100 ? "log" : "linear";
  const [metric, setMetric] = useState<FeatureVarianceMetric>("variance");
  const [sort, setSort] = useState<FeatureVarianceSort>("variance_desc");
  const [display, setDisplay] = useState<FeatureVarianceDisplay>(() => features.length <= 100 ? "all" : "top");
  const [topK, setTopK] = useState(20);
  const [scale, setScale] = useState<"linear" | "log">(() => recommendedScale);
  const [selectedFeatureIndex, setSelectedFeatureIndex] = useState<number | null>(null);
  const [distribution, setDistribution] = useState<FeatureVarianceDistribution | null>(null);
  const [activePane, setActivePane] = useState<FeatureVariancePane>("chart");

  useEffect(() => {
    setDisplay(features.length <= 100 ? "all" : "top");
    setScale(recommendedScale);
    setSelectedFeatureIndex(null);
    setDistribution(null);
    setActivePane("chart");
  }, [analysisId, features.length, recommendedScale]);

  useEffect(() => {
    if (!analysisId || selectedFeatureIndex == null) {
      setDistribution(null);
      return;
    }
    let disposed = false;
    setDistribution({ loading: true, edges: [], counts: [], samples: [] });
    const loadRow = async (array: string): Promise<number[]> => {
      const chunk = await ipc.request<AnalysisChunk>("analysis.chunk", {
        analysis_id: analysisId,
        array,
        offset: selectedFeatureIndex,
        limit: 1,
        column_end: 20_000,
      });
      return numericArray(chunk.data[0]);
    };
    void Promise.all([
      loadRow("histogram_edges"),
      loadRow("histogram_counts"),
      loadRow("distribution_samples"),
    ]).then(([edges, counts, samples]) => {
      if (!disposed) setDistribution({ loading: false, edges, counts, samples });
    }).catch(() => {
      if (!disposed) setDistribution({ loading: false, edges: [], counts: [], samples: [] });
    });
    return () => { disposed = true; };
  }, [analysisId, selectedFeatureIndex]);

  const summary = recordValue(preview.summary);
  const maxVariance = finiteNumber(summary?.max_variance) ?? Math.max(0, ...features.map((feature) => feature.variance ?? 0));
  const medianVariance = finiteNumber(summary?.median_variance) ?? medianOf(features.map((feature) => feature.variance).filter((value): value is number => value !== null));
  const varianceValues = features.map((feature) => feature.variance).filter((value): value is number => value !== null);
  const minVariance = finiteNumber(summary?.min_variance) ?? (varianceValues.length ? Math.min(...varianceValues) : 0);
  const nearZeroCount = finiteNumber(summary?.near_zero_count) ?? features.filter((feature) => feature.status === "near_zero").length;
  const constantCount = finiteNumber(summary?.constant_count) ?? features.filter((feature) => feature.status === "constant").length;
  const featureCount = finiteNumber(preview.feature_count) ?? features.length;
  const sampleCount = finiteNumber(preview.sample_count);
  const settings = recordValue(preview.settings);
  const nearZeroThreshold = finiteNumber(settings?.near_zero_relative_threshold) ?? 1e-4;
  const lowVariationThreshold = finiteNumber(settings?.low_variance_relative_threshold) ?? 1e-2;
  const constantTolerance = finiteNumber(settings?.constant_tolerance) ?? 1e-12;

  const rankedRows = useMemo(() => {
    const filtered = features.filter((feature) => {
      if (display === "near_zero") return feature.status === "near_zero";
      if (display === "low_variation") return feature.status === "low_variation";
      if (display === "constant") return feature.status === "constant";
      return true;
    });
    const compare = (left: FeatureVarianceRow, right: FeatureVarianceRow) => {
      if (sort === "index") return left.index - right.index;
      const leftValue = left.variance ?? -Infinity;
      const rightValue = right.variance ?? -Infinity;
      return sort === "variance_asc" ? leftValue - rightValue : rightValue - leftValue;
    };
    if (display === "top" || display === "bottom") {
      const varianceRanked = filtered
        .filter((feature) => feature.variance !== null)
        .sort((left, right) => (right.variance ?? -Infinity) - (left.variance ?? -Infinity));
      const selected = display === "top"
        ? varianceRanked.slice(0, Math.max(1, topK))
        : varianceRanked.slice(Math.max(0, varianceRanked.length - Math.max(1, topK)));
      return selected.sort(compare);
    }
    return filtered.slice().sort(compare);
  }, [display, features, sort, topK]);

  const plotRows = useMemo(
    () => rankedRows.filter((row) => featureVarianceMetricValue(row, metric) !== null),
    [metric, rankedRows],
  );
  const chartRows = plotRows.slice().reverse();
  const selectedFeature = selectedFeatureIndex == null ? null : features.find((feature) => feature.index === selectedFeatureIndex) ?? null;
  const selectedSamples = selectedFeature && distribution
    ? distribution.samples.slice(0, Math.max(0, selectedFeature.distribution_sample_count || distribution.samples.length))
    : [];
  const selectedHistogram = useMemo(
    () => buildFeatureHistogram(distribution?.edges ?? [], distribution?.counts ?? [], selectedSamples),
    [distribution?.counts, distribution?.edges, selectedSamples],
  );
  const selectedKde = useMemo(
    () => buildKde(selectedSamples, selectedHistogram.edges, selectedHistogram.countScale),
    [selectedHistogram.countScale, selectedHistogram.edges, selectedSamples],
  );
  if (!features.length) return <OverviewNoData message={t("No feature variance values were returned.")} />;

  const chooseFilter = (next: FeatureVarianceDisplay) => {
    setDisplay((current) => current === next ? "all" : next);
  };
  const statusText = (status: FeatureVarianceStatus) => tr(FEATURE_VARIANCE_STATUS_LABELS[status]);
  const topKEnabled = display === "top" || display === "bottom";
  const hasZeroVariance = features.some((feature) => feature.variance === 0);
  const featureOptions = plotRows.map((row) => ({ value: row.index, label: `${t("Feature {index}", { index: row.index })} · ${formatNumber(featureVarianceMetricValue(row, metric))}` }));
  const chartLabels = chartRows.map((row) => t("Feature {index}", { index: row.index }));
  const chartPositions = chartRows.map((_, index) => index);
  const chartHeight = Math.max(360, chartRows.length * 22 + 96);

  return <>
    <div className="analysis-metric-strip feature-variance-summary">
      <div className="analysis-metric"><Typography.Text type="secondary">{t("Features")}</Typography.Text><Typography.Text strong>{formatCount(featureCount)}</Typography.Text></div>
      <div className="analysis-metric"><Typography.Text type="secondary">{t("Max absolute variance")}</Typography.Text><Typography.Text strong>{formatNumber(maxVariance)}</Typography.Text></div>
      <div className="analysis-metric"><Typography.Text type="secondary">{t("Median absolute variance")}</Typography.Text><Typography.Text strong>{formatNumber(medianVariance)}</Typography.Text></div>
      <div className="analysis-metric"><Typography.Text type="secondary">{t("Min absolute variance")}</Typography.Text><Typography.Text strong>{formatNumber(minVariance)}</Typography.Text></div>
      <button type="button" className={`analysis-metric analysis-metric-action${display === "near_zero" ? " is-selected" : ""}`} aria-pressed={display === "near_zero"} onClick={() => chooseFilter("near_zero")}>
        <Typography.Text type="secondary">{t("Near-zero")}</Typography.Text><Typography.Text strong>{formatCount(nearZeroCount)}</Typography.Text>
      </button>
      <button type="button" className={`analysis-metric analysis-metric-action${display === "constant" ? " is-selected" : ""}`} aria-pressed={display === "constant"} onClick={() => chooseFilter("constant")}>
        <Typography.Text type="secondary">{t("Constant")}</Typography.Text><Typography.Text strong>{formatCount(constantCount)}</Typography.Text>
      </button>
    </div>
    <Typography.Text type="secondary" className="feature-variance-summary-note">{t("Summary cards always show absolute variance, independent of the selected metric.")}</Typography.Text>

    <div className="feature-variance-toolbar" role="group" aria-label={t("Feature variance controls")}>
      <label className="feature-variance-control"><span>{t("Metric")}</span><Select aria-label={t("Variance metric")} value={metric} onChange={setMetric} options={[{ value: "variance", label: t("Absolute variance") }, { value: "relative_variance", label: t("Normalized variance") }, { value: "std", label: t("Standard deviation") }, { value: "iqr", label: t("IQR") }, { value: "mad", label: t("MAD") }]} /></label>
      <label className="feature-variance-control"><span>{t("Sort")}</span><Select aria-label={t("Variance sort order")} value={sort} onChange={setSort} options={[{ value: "variance_desc", label: t("Variance descending") }, { value: "variance_asc", label: t("Variance ascending") }, { value: "index", label: t("Feature index") }]} /></label>
      <label className="feature-variance-control"><span>{t("Display")}</span><Select aria-label={t("Feature display filter")} value={display} onChange={setDisplay} options={[{ value: "all", label: t("All features") }, { value: "top", label: t("Highest variation") }, { value: "bottom", label: t("Lowest variation") }, { value: "near_zero", label: t("Near-zero") }, { value: "low_variation", label: t("Low variation") }, { value: "constant", label: t("Constant") }]} /></label>
      <label className={`feature-variance-control feature-variance-k-control${topKEnabled ? "" : " is-disabled"}`} title={topKEnabled ? undefined : t("Top/Bottom K applies only to highest/lowest variation views.")}><span>{t("Top/Bottom K")}</span><InputNumber aria-label={t("Top/Bottom K")} disabled={!topKEnabled} min={1} max={Math.max(1, features.length)} value={topK} onChange={(value) => setTopK(Math.min(Math.max(1, value ?? 20), Math.max(1, features.length)))} /></label>
      <label className="feature-variance-control"><span>{t("Coordinate scale")}</span><Select aria-label={t("Coordinate scale")} value={scale} onChange={setScale} options={[{ value: "linear", label: t("Linear") }, { value: "log", label: t("Log") }]} /></label>
    </div>
    <div className="feature-variance-thresholds">
      <Typography.Text type="secondary">{t("{n} samples · Near-zero < {near} · Low variation < {low} · Constant tolerance ≤ {constant}", { n: sampleCount ?? 0, near: nearZeroThreshold, low: lowVariationThreshold, constant: constantTolerance })}</Typography.Text>
      <Typography.Text type="secondary">{t("Near-zero and Low variation thresholds use normalized variance.")}</Typography.Text>
      {varianceSpan >= 100 && <Typography.Text type="warning">{t("Variance spans multiple orders of magnitude; log scale is recommended by default.")}</Typography.Text>}
      {scale === "log" && hasZeroVariance && <Typography.Text type="secondary">{t("Zero-variance features are omitted from the log axis.")}</Typography.Text>}
      {Array.isArray(preview.warnings) && preview.warnings.filter((warning): warning is string => typeof warning === "string").map((warning, index) => <Typography.Text type="warning" key={`${index}-${warning}`}>{warning}</Typography.Text>)}
    </div>

    <div className="feature-variance-layout">
      <div className="feature-variance-overview">
        <Tabs
          className="feature-variance-pane-tabs"
          type="card"
          activeKey={activePane}
          onChange={(key) => setActivePane(key as FeatureVariancePane)}
          items={[
            {
              key: "chart",
              label: t("Variance distribution"),
              children: <>
                {chartRows.length ? <div className="feature-variance-chart-scroll">
                  <OverviewPlot
                    className="feature-variance-overview-chart"
                    style={{ height: `${chartHeight}px` }}
                    ariaLabel={t("Descriptor feature variance distribution")}
                    data={[{
                      type: "bar",
                      orientation: "h",
                      x: chartRows.map((row) => {
                        const value = featureVarianceMetricValue(row, metric);
                        return scale === "log" && value !== null && value <= 0 ? null : value;
                      }),
                      y: chartPositions,
                      customdata: chartRows.map((row) => [
                        row.index,
                        formatNumber(row.variance),
                        formatNumber(row.std),
                        formatNumber(row.mean),
                        formatNumber(row.median),
                        formatNumber(row.p05),
                        formatNumber(row.p95),
                        formatNumber(row.relative_variance),
                        statusText(row.status),
                      ]),
                      marker: { color: chartRows.map((row) => FEATURE_VARIANCE_STATUS_COLORS[row.status]) },
                      hovertemplate: `${t("Feature")} %{customdata[0]}<br>${t("Absolute variance")}=%{customdata[1]}<br>${t("Standard deviation")}=%{customdata[2]}<br>${t("Mean")}=%{customdata[3]}<br>${t("Median")}=%{customdata[4]}<br>${t("P05–P95")}=%{customdata[5]} – %{customdata[6]}<br>${t("Normalized variance")}=%{customdata[7]}<br>${t("Status")}=%{customdata[8]}<extra></extra>`,
                    }]}
                    layout={overviewLayout({
                      xaxis: { title: t(metricLabel(metric)), type: scale === "log" ? "log" : "linear", zeroline: true },
                      yaxis: {
                        automargin: true,
                        range: [-0.5, Math.max(0.5, chartRows.length - 0.5)],
                        tickmode: "array",
                        tickvals: chartPositions,
                        ticktext: chartLabels,
                      },
                    })}
                    onClick={(event: Readonly<PlotMouseEvent>) => {
                      const index = event.points?.[0]?.pointIndex;
                      if (typeof index === "number") {
                        const nextFeatureIndex = chartRows[index]?.index ?? null;
                        if (nextFeatureIndex !== null) {
                          setSelectedFeatureIndex(nextFeatureIndex);
                          setActivePane("stats");
                        }
                      }
                    }}
                  />
                </div> : <OverviewNoData message={t("No feature values are available for this metric.")} />}
              </>,
            },
            {
              key: "stats",
              label: t("Feature statistics"),
              children: selectedFeature ? <FeatureVarianceStats feature={selectedFeature} /> : <OverviewNoData message={t("Select a feature for detail")} />,
            },
          ]}
        />
      </div>
      <div className="feature-variance-detail-column">
        <div className="feature-variance-feature-picker feature-variance-detail-picker">
          <Typography.Text type="secondary">{t("Feature detail")}</Typography.Text>
          <Select
            allowClear
            aria-label={t("Select feature for detail")}
            placeholder={t("Select a feature for detail")}
            value={selectedFeatureIndex ?? undefined}
            options={featureOptions}
            onChange={(value) => setSelectedFeatureIndex(value ?? null)}
          />
        </div>
        {selectedFeature ? <FeatureVarianceDetail feature={selectedFeature} distribution={distribution} histogram={selectedHistogram} kde={selectedKde} statusText={statusText(selectedFeature.status)} /> : <div className="feature-variance-detail feature-variance-detail-empty"><div className="analysis-section-heading"><Typography.Text strong>{t("Feature detail")}</Typography.Text></div><OverviewNoData message={t("Select a feature for detail")} /></div>}
      </div>
    </div>
  </>;
}

function FeatureVarianceDetail({ feature, distribution, histogram, kde, statusText }: { feature: FeatureVarianceRow; distribution: FeatureVarianceDistribution | null; histogram: { edges: number[]; counts: number[] }; kde: { x: number[]; y: number[] }; statusText: string }) {
  const { t } = useT();
  const histogramCenters = histogram.counts.map((_, index) => (histogram.edges[index] + histogram.edges[index + 1]) / 2);
  const histogramWidths = histogram.counts.map((_, index) => histogram.edges[index + 1] - histogram.edges[index]);
  const whiskerMin = feature.whisker_min ?? feature.min;
  const whiskerMax = feature.whisker_max ?? feature.max;
  const boxData = feature.p25 !== null && feature.median !== null && feature.p75 !== null && whiskerMin !== null && whiskerMax !== null
    ? [{ type: "box", orientation: "h", q1: [feature.p25], median: [feature.median], q3: [feature.p75], lowerfence: [whiskerMin], upperfence: [whiskerMax], y: [t("Feature {index}", { index: feature.index })], name: t("Tukey whiskers"), boxpoints: false, marker: { color: "#8764B8" } } as Data]
    : null;
  const sampledOutliers = distribution && !distribution.loading && whiskerMin !== null && whiskerMax !== null
    ? distribution.samples.filter((value) => value < whiskerMin || value > whiskerMax)
    : [];
  const robustRatio = feature.std_robust_ratio;
  const robustDiagnostic = robustRatio === null
    ? null
    : robustRatio > 2
      ? { label: t("Long-tail / outlier-sensitive"), color: "red" }
      : robustRatio >= 1.5
        ? { label: t("Possible skew / long-tail"), color: "orange" }
        : { label: t("Within robust-scale baseline"), color: "green" };
  return <div className="feature-variance-detail">
    <div className="analysis-section-heading"><Typography.Text strong>{t("Feature detail")}</Typography.Text><Typography.Text type="secondary">{t("Feature {index}", { index: feature.index })}</Typography.Text></div>
    <div className="feature-variance-detail-meta"><Tag color={FEATURE_VARIANCE_STATUS_COLORS[feature.status]}>{statusText}</Tag>{robustDiagnostic && <Tag color={robustDiagnostic.color}>{robustDiagnostic.label}</Tag>}{feature.invalid_count > 0 && <Typography.Text type="warning">{t("{n} invalid values were excluded.", { n: feature.invalid_count })}</Typography.Text>}</div>
    {feature.status === "invalid" ? <OverviewNoData message={t("This feature has no finite values.")} /> : <div className="feature-variance-detail-charts">
      {histogram.counts.length ? <OverviewPlot compact ariaLabel={t("Feature value histogram and KDE")} data={[{ type: "bar", x: histogramCenters, y: histogram.counts, width: histogramWidths, marker: { color: "#0F6CBD", opacity: 0.7 }, hovertemplate: `${t("Value")}=%{x:.5g}<br>${t("Samples")}=%{y}<extra></extra>` }, ...(kde.x.length ? [{ type: "scatter", mode: "lines", x: kde.x, y: kde.y, name: "KDE", line: { color: "#D13438", width: 2 }, hovertemplate: `${t("KDE")}=%{y:.5g}<extra></extra>` }] : [])] as Data[]} layout={overviewLayout({ xaxis: { title: t("Value") }, yaxis: { title: t("Samples") }, legend: { orientation: "h" } })} /> : <OverviewNoData message={t("Distribution data is unavailable.")} />}
      {boxData ? <OverviewPlot compact ariaLabel={t("Feature value box plot")} data={[...boxData, ...(sampledOutliers.length ? [{ type: "scatter", mode: "markers", x: sampledOutliers, y: sampledOutliers.map(() => t("Feature {index}", { index: feature.index })), name: t("Outliers"), marker: { color: "#D13438", size: 7, symbol: "circle-open" }, hovertemplate: `${t("Outliers")}=%{x:.5g}<extra></extra>` } as Data] : [])]} layout={overviewLayout({ xaxis: { title: t("Value") }, yaxis: { automargin: true }, legend: { orientation: "h" } })} /> : <OverviewNoData message={t("Box plot data is unavailable.")} />}
    </div>}
    {distribution?.loading && <Typography.Text type="secondary">{t("Loading feature distribution…")}</Typography.Text>}
    {feature.status === "constant" && <ChartCaption>{t("Constant feature; KDE omitted.")}</ChartCaption>}
  </div>;
}

function FeatureVarianceStats({ feature }: { feature: FeatureVarianceRow }) {
  const { t } = useT();
  const whiskerMin = feature.whisker_min ?? feature.min;
  const whiskerMax = feature.whisker_max ?? feature.max;
  const stats: [string, string][] = [
    [t("Samples"), formatCount(feature.finite_count)],
    [t("Invalid values"), formatCount(feature.invalid_count)],
    [t("Mean"), formatNumber(feature.mean)],
    [t("Absolute variance"), formatNumber(feature.variance)],
    [t("Normalized variance"), formatNumber(feature.relative_variance)],
    [t("Standard deviation"), formatNumber(feature.std)],
    [t("Min"), formatNumber(feature.min)],
    [t("Whisker min"), formatNumber(whiskerMin)],
    [t("P05"), formatNumber(feature.p05)],
    [t("P25"), formatNumber(feature.p25)],
    [t("Median"), formatNumber(feature.median)],
    [t("P75"), formatNumber(feature.p75)],
    [t("P95"), formatNumber(feature.p95)],
    [t("Whisker max"), formatNumber(whiskerMax)],
    [t("Max"), formatNumber(feature.max)],
    [t("Outliers"), formatCount(feature.outlier_count)],
    [t("IQR"), formatNumber(feature.iqr)],
    [t("MAD"), formatNumber(feature.mad)],
    [t("Robust sigma"), formatNumber(feature.robust_sigma)],
    [t("Std / Robust sigma"), formatNumber(feature.std_robust_ratio)],
  ];
  return <div className="feature-variance-stat-grid">{stats.map(([label, value]) => <div className="feature-variance-stat" key={label}><span>{label}</span><Typography.Text code>{value}</Typography.Text></div>)}</div>;
}

function parseFeatureVarianceRows(preview: AnalysisPreview): FeatureVarianceRow[] {
  const records = recordArray(preview.features);
  if (records.length) {
    return records.map((record) => {
      const rawStatus = String(record.status ?? "active");
      const status: FeatureVarianceStatus = rawStatus === "constant" || rawStatus === "near_zero" || rawStatus === "low_variation" || rawStatus === "invalid" ? rawStatus : "active";
      return {
        index: Math.round(finiteNumber(record.index) ?? 0),
        mean: finiteNumber(record.mean),
        variance: finiteNumber(record.variance),
        relative_variance: finiteNumber(record.relative_variance),
        std: finiteNumber(record.std),
        min: finiteNumber(record.min),
        max: finiteNumber(record.max),
        p05: finiteNumber(record.p05),
        p25: finiteNumber(record.p25),
        median: finiteNumber(record.median),
        p75: finiteNumber(record.p75),
        p95: finiteNumber(record.p95),
        iqr: finiteNumber(record.iqr),
        mad: finiteNumber(record.mad),
        robust_sigma: finiteNumber(record.robust_sigma),
        std_robust_ratio: finiteNumber(record.std_robust_ratio),
        whisker_min: finiteNumber(record.whisker_min),
        whisker_max: finiteNumber(record.whisker_max),
        outlier_count: Math.max(0, Math.round(finiteNumber(record.outlier_count) ?? 0)),
        finite_count: Math.max(0, Math.round(finiteNumber(record.finite_count) ?? 0)),
        invalid_count: Math.max(0, Math.round(finiteNumber(record.invalid_count ?? record.missing_count) ?? 0)),
        distribution_sample_count: Math.max(0, Math.round(finiteNumber(record.distribution_sample_count) ?? 0)),
        status,
      };
    }).filter((row, index) => row.index >= 0 && index === records.findIndex((record) => Math.round(finiteNumber(record.index) ?? 0) === row.index));
  }

  // Legacy artifacts remain displayable while users transition to the full
  // schema. They do not contain enough information for the detail panel.
  const indices = Array.isArray(preview.top_indices) ? preview.top_indices.map(finiteNumber) : [];
  const values = Array.isArray(preview.top_values) ? preview.top_values.map(finiteNumber) : [];
  return indices.map((index, position) => {
    const variance = values[position] ?? null;
    return {
      index: Math.round(index ?? position),
      mean: null,
      variance,
      relative_variance: null,
      std: variance === null ? null : Math.sqrt(Math.max(variance, 0)),
      min: null,
      max: null,
      p05: null,
      p25: null,
      median: null,
      p75: null,
      p95: null,
      iqr: null,
      mad: null,
      robust_sigma: null,
      std_robust_ratio: null,
      whisker_min: null,
      whisker_max: null,
      outlier_count: 0,
      finite_count: Math.round(finiteNumber(preview.sample_count) ?? 0),
      invalid_count: 0,
      distribution_sample_count: 0,
      status: "active" as const,
    };
  }).filter((row) => row.variance !== null);
}

function featureVarianceMetricValue(row: FeatureVarianceRow, metric: FeatureVarianceMetric): number | null {
  if (metric === "relative_variance") return row.relative_variance;
  if (metric === "std") return row.std;
  if (metric === "iqr") return row.iqr;
  if (metric === "mad") return row.mad;
  return row.variance;
}

function metricLabel(metric: FeatureVarianceMetric): string {
  if (metric === "relative_variance") return "Normalized variance";
  if (metric === "std") return "Standard deviation";
  if (metric === "iqr") return "IQR";
  if (metric === "mad") return "MAD";
  return "Absolute variance";
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function medianOf(values: number[]): number {
  if (!values.length) return 0;
  const sorted = values.slice().sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function buildFeatureHistogram(edges: number[], counts: number[], samples: number[]): { edges: number[]; counts: number[]; countScale: number } {
  if (edges.length === counts.length + 1 && counts.length > 0) {
    return { edges, counts, countScale: counts.reduce((sum, count) => sum + Math.max(0, count), 0) };
  }
  if (!samples.length) return { edges: [], counts: [], countScale: 0 };
  const bins = 32;
  const minimum = samples.reduce((value, sample) => Math.min(value, sample), samples[0]);
  const maximum = samples.reduce((value, sample) => Math.max(value, sample), samples[0]);
  const width = maximum > minimum ? (maximum - minimum) / bins : Math.max(Math.abs(minimum) * 0.01, 0.5);
  const start = maximum > minimum ? minimum : minimum - width / 2;
  const computedEdges = Array.from({ length: bins + 1 }, (_, index) => start + width * index);
  const computedCounts = Array.from({ length: bins }, () => 0);
  samples.forEach((sample) => {
    const index = Math.min(bins - 1, Math.max(0, Math.floor((sample - start) / width)));
    computedCounts[index] += 1;
  });
  return { edges: computedEdges, counts: computedCounts, countScale: samples.length };
}

function buildKde(samples: number[], edges: number[], totalCount: number): { x: number[]; y: number[] } {
  if (samples.length < 2) return { x: [], y: [] };
  const minimum = samples.reduce((value, sample) => Math.min(value, sample), samples[0]);
  const maximum = samples.reduce((value, sample) => Math.max(value, sample), samples[0]);
  const range = maximum - minimum;
  if (!(range > Number.EPSILON)) return { x: [], y: [] };
  const mean = samples.reduce((sum, sample) => sum + sample, 0) / samples.length;
  const variance = samples.reduce((sum, sample) => sum + (sample - mean) ** 2, 0) / samples.length;
  const standardDeviation = Math.sqrt(variance);
  const bandwidth = Math.max(1.06 * standardDeviation * samples.length ** -0.2, range / 80, Number.EPSILON);
  const gridCount = 80;
  const start = minimum - 3 * bandwidth;
  const end = maximum + 3 * bandwidth;
  const step = (end - start) / (gridCount - 1);
  const binWidth = edges.length > 1 ? Math.max(Math.abs(edges[1] - edges[0]), Number.EPSILON) : range / 32;
  const histogramCount = Number.isFinite(totalCount) && totalCount > 0 ? totalCount : samples.length;
  const normalizer = samples.length * bandwidth * Math.sqrt(2 * Math.PI);
  const x = Array.from({ length: gridCount }, (_, index) => start + index * step);
  const y = x.map((position) => {
    const density = samples.reduce((sum, sample) => {
      const z = (position - sample) / bandwidth;
      return sum + Math.exp(-0.5 * z * z);
    }, 0) / normalizer;
    return density * histogramCount * binWidth;
  });
  return { x, y };
}

function EffectiveDimensionChart({ preview, arrays }: { preview: AnalysisPreview; arrays: NumericArrays }) {
  const { t } = useT();
  const [spectrumRange, setSpectrumRange] = useState<SpectrumRange>("20");
  useEffect(() => setSpectrumRange("20"), [preview.analysis_id]);

  const explained = numericArray(arrays.explained_variance);
  if (!explained.length) return <OverviewNoData message={t("The explained-variance array is not available for visualization.")} />;
  const cumulative: number[] = [];
  let total = 0;
  for (const value of explained) {
    total += value;
    cumulative.push(total);
  }

  const declaredComponentCount = integerCount(preview.component_count) ?? explained.length;
  const availableComponentCount = Math.min(declaredComponentCount, explained.length);
  const shownComponentCount = spectrumRange === "all"
    ? availableComponentCount
    : Math.min(Number(spectrumRange), availableComponentCount);
  const indices = sampledIndices(shownComponentCount, 320);
  const pcaFeatureCount = integerCount(preview.pca_feature_count);
  const featureCount = integerCount(preview.feature_count) ?? pcaFeatureCount;
  const preprocess = preview.preprocess === "standardized" || preview.preprocess === "raw" ? preview.preprocess : "center";
  const scalingLabel = preprocess === "standardized" ? t("Standardized") : preprocess === "raw" ? t("Raw scale") : t("Centered");
  const basisLabel = preprocess === "standardized" ? t("Correlation") : preprocess === "raw" ? t("Uncentered second moment") : t("Covariance");
  const thresholdRows = [
    { key: "0.9", target: "90%", color: "#107C10" },
    { key: "0.95", target: "95%", color: "#8764B8" },
    { key: "0.99", target: "99%", color: "#D13438" },
  ]
    .map((row) => ({ ...row, component: componentThreshold(preview, row.key) }))
    .filter((row): row is typeof row & { component: number } => row.component != null && row.component >= 1 && row.component <= availableComponentCount)
    .map((row) => ({ ...row, cumulative: cumulative[row.component - 1] }));
  const thresholdShapes = thresholdRows.map((row) => ({
    type: "line" as const,
    x0: row.component,
    x1: row.component,
    y0: 0,
    y1: 1,
    yref: "paper" as const,
    line: { color: row.color, dash: "dash" as const, width: 1.5 },
  }));
  const thresholdAnnotations = thresholdRows.map((row) => ({
    x: row.component,
    y: 1,
    xref: "x" as const,
    yref: "paper" as const,
    text: `${row.target} · PC${row.component}`,
    showarrow: false,
    yshift: 16,
    font: { size: 10, color: row.color },
    bgcolor: "#FFFFFF",
    bordercolor: row.color,
    borderwidth: 1,
    borderpad: 2,
  }));
  const thresholdTrace: Data | null = thresholdRows.length
    ? {
        type: "scatter",
        mode: "markers",
        x: thresholdRows.map((row) => row.component),
        y: thresholdRows.map((row) => row.cumulative * 100),
        text: thresholdRows.map((row) => `${row.target} · PC${row.component}`),
        marker: { color: thresholdRows.map((row) => row.color), size: 8, line: { color: "#FFFFFF", width: 1 } },
        hovertemplate: `%{text}<br>${t("Cumulative explained variance ratio")}=%{y:.2f}%<extra></extra>`,
        showlegend: false,
      }
    : null;
  const thresholdMetric = (key: string) => {
    const component = componentThreshold(preview, key);
    if (component == null) return "—";
    return pcaFeatureCount == null ? formatCount(component) : `${formatCount(component)} / ${formatCount(pcaFeatureCount)}`;
  };
  const participationRatio = finiteNumber(preview.participation_ratio);
  const prLabel = (
    <Tooltip title={t("Participation ratio definition")} placement="top">
      <span className="analysis-metric-label" tabIndex={0}>
        {t("PR effective dimension")} <Info16Regular aria-hidden="true" />
      </span>
    </Tooltip>
  );
  const hiddenThresholds = ["0.9", "0.95", "0.99"]
    .map((key) => componentThreshold(preview, key))
    .filter((component): component is number => component != null && component >= 1 && component > shownComponentCount);
  const conclusion = participationRatio != null && featureCount != null && pcaFeatureCount != null
    && componentThreshold(preview, "0.9") != null && componentThreshold(preview, "0.95") != null && componentThreshold(preview, "0.99") != null
    ? t("Effective dimension conclusion", {
        featureCount: formatCount(featureCount),
        pcaFeatureCount: formatCount(pcaFeatureCount),
        pc90: formatCount(componentThreshold(preview, "0.9")),
        pc95: formatCount(componentThreshold(preview, "0.95")),
        pc99: formatCount(componentThreshold(preview, "0.99")),
        participationRatio: formatNumber(participationRatio),
        scaling: scalingLabel,
      })
    : null;
  const metrics: Metric[] = [
    { label: prLabel, value: formatNumber(participationRatio) },
    { label: t("90% effective dimension"), value: thresholdMetric("0.9") },
    { label: t("95% effective dimension"), value: thresholdMetric("0.95") },
    { label: t("99% effective dimension"), value: thresholdMetric("0.99") },
  ];
  return <>
    <MetricStrip metrics={metrics} />
    <div className="analysis-method-meta" aria-label={t("PCA method details")}>
      <span><Typography.Text type="secondary">{t("Scaling")}: </Typography.Text><Typography.Text strong>{scalingLabel}</Typography.Text></span>
      <span><Typography.Text type="secondary">{t("PCA basis")}: </Typography.Text><Typography.Text strong>{basisLabel}</Typography.Text></span>
      <span><Typography.Text type="secondary">{t("PCA features")}: </Typography.Text><Typography.Text strong>{pcaFeatureCount == null ? "—" : `${formatCount(pcaFeatureCount)} / ${formatCount(featureCount)}`}</Typography.Text></span>
      <span><Typography.Text type="secondary">{t("Components")}: </Typography.Text><Typography.Text strong>{formatCount(declaredComponentCount)}</Typography.Text></span>
    </div>
    <Typography.Text type="secondary" className="analysis-spectrum-note">{t("Threshold dimension explanation")}</Typography.Text>
    <div className="analysis-spectrum-toolbar">
      <Space wrap size={8}>
        <Typography.Text strong>{t("Spectrum range")}</Typography.Text>
        <Select
          aria-label={t("Spectrum range")}
          value={spectrumRange}
          onChange={(value) => setSpectrumRange(value as SpectrumRange)}
          options={[
            { value: "20", label: t("First 20") },
            { value: "50", label: t("First 50") },
            { value: "all", label: t("All components") },
          ]}
        />
        <Typography.Text type="secondary">{t("{shown} of {total} components shown", { shown: formatCount(shownComponentCount), total: formatCount(declaredComponentCount) })}</Typography.Text>
      </Space>
    </div>
    <OverviewPlot
      ariaLabel={t("Explained and cumulative descriptor variance by component")}
      data={[
        {
          type: "bar",
          x: indices.map((index) => index + 1),
          y: indices.map((index) => explained[index] * 100),
          name: t("Single-component explained variance ratio"),
          marker: { color: "#0F6CBD" },
          hovertemplate: `PC %{x}<br>${t("Single-component explained variance ratio")}=%{y:.2f}%<extra></extra>`,
        },
        {
          type: "scatter",
          mode: "lines",
          x: indices.map((index) => index + 1),
          y: indices.map((index) => cumulative[index] * 100),
          name: t("Cumulative explained variance ratio"),
          line: { color: "#F7630C", width: 2 },
          hovertemplate: `PC %{x}<br>${t("Cumulative explained variance ratio")}=%{y:.2f}%<extra></extra>`,
        },
        ...(thresholdTrace ? [thresholdTrace] : []),
      ]}
      layout={overviewLayout({
        margin: { l: 68, r: 28, t: 52, b: 52 },
        xaxis: { title: t("Principal component"), type: "linear" },
        yaxis: { title: t("Explained variance ratio (%)"), range: [0, 100] },
        shapes: thresholdShapes,
        annotations: thresholdAnnotations,
        legend: { orientation: "h", y: 1.18, x: 0 },
      })}
    />
    {conclusion && <Typography.Paragraph className="analysis-effective-conclusion">{conclusion}</Typography.Paragraph>}
    <ChartCaption>
      {t("Bars show the single-component explained variance ratio and the orange line shows the cumulative explained variance ratio.")}
      {shownComponentCount > 320 ? ` ${t("When more than 320 components are selected, the chart samples evenly for rendering while preserving the selected range.")}` : ""}
      {hiddenThresholds.length ? ` ${t("Some threshold markers are outside the selected range.")}` : ""}
    </ChartCaption>
  </>;
}

type SpectrumRange = "20" | "50" | "all";

function OverviewPlot({ data, layout, ariaLabel, compact = false, className, style, onClick }: { data: Data[]; layout: Partial<Layout>; ariaLabel: string; compact?: boolean; className?: string; style?: CSSProperties; onClick?: (event: Readonly<PlotMouseEvent>) => void }) {
  const classes = ["analysis-overview-chart-frame", compact ? "compact" : "", className ?? ""].filter(Boolean).join(" ");
  return <div className={classes} style={style} aria-label={ariaLabel}><Plot data={data} layout={layout} config={{ responsive: true, displaylogo: false, modeBarButtonsToRemove: ["toImage"] }} style={{ width: "100%", height: "100%" }} onClick={onClick} /></div>;
}

function MetricStrip({ metrics }: { metrics: Metric[] }) {
  return <div className="analysis-metric-strip">{metrics.map((metric, index) => <div className="analysis-metric" key={index}><Typography.Text type="secondary">{metric.label}</Typography.Text><Typography.Text strong>{metric.value}</Typography.Text></div>)}</div>;
}

function ChartCaption({ children }: { children: ReactNode }) {
  return <Typography.Text type="secondary" className="analysis-chart-caption">{children}</Typography.Text>;
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

function integerCount(value: unknown): number | null {
  const number = finiteNumber(value);
  return number !== null && Number.isInteger(number) && number >= 0 ? number : null;
}

function componentThreshold(preview: AnalysisPreview, key: string): number | null {
  const thresholds = preview.components_for_threshold;
  if (typeof thresholds !== "object" || thresholds === null || Array.isArray(thresholds)) return null;
  return finiteNumber((thresholds as Record<string, unknown>)[key]);
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
  if (preview?.kind === "feature_variance" || preview?.kind === "feature_correlation" || preview?.kind === "effective_dimension" || preview?.kind === "property_correlation") return null;
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
  return <section className="analysis-card"><SectionHeading title={String(preview?.kind ?? "RESULT").toUpperCase()} /><Collapse ghost size="small" items={[{ key: "raw", label: t("Raw result output"), children: <pre className="analysis-json-preview">{JSON.stringify(preview, null, 2)}</pre> }]} /></section>;
}

function SectionHeading({ title, meta }: { title: string; meta?: string }) {
  return <div className="analysis-section-heading"><Typography.Text strong>{title}</Typography.Text>{meta && <Typography.Text type="secondary">{meta}</Typography.Text>}</div>;
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="analysis-inspector-row"><span>{k}</span><Typography.Text code>{v}</Typography.Text></div>;
}

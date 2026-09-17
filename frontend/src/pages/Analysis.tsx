/*
 * Analysis is the successor to the old Results page.  It owns one shared run
 * selector, one compact inspector, and a set of real backend-backed modules.
 * Descriptor run history lives on the separate Results page.
 * Plotly is deliberately scoped to this page; Overview remains ECharts and
 * Explore remains 3Dmol.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Plot from "../plotlyBundle";
import type { Data, PlotDatum, PlotSelectionEvent } from "plotly.js";
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
  Spin,
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
  analysisSlotMatchesModule,
  latestSlotForModule,
  ANALYSIS_NAV_GROUPS,
  analysisNavModuleForAnalysisType,
  analysisNavModuleForKey,
  analysisNavModuleForView,
  slotForParams,
  slotKey,
  useAnalysisUi,
  type AnalysisModuleKey,
  type AnalysisParams,
  type EffectiveDimensionPreprocess,
  type ProjectionName,
  type TabKey,
} from "../stores/analysisUi";
import { useT, type Pair } from "../i18n";
import StructurePreview from "../components/StructurePreview";
import SaveViewModal from "../components/SaveViewModal";
import { normalizePoints, selectedDisplayIndices, type AnalysisPoint } from "./analysisPreview";
import AnalysisResultVisualization, { type AnalysisArrays } from "./analysisVisualizations";
import { getAnalysisMethodGuide, type AnalysisMethodGuide } from "./analysisMethodGuides";
import { ChartCaption, HIGH_CONTRAST_COLORSCALE, NoData as OverviewNoData, OverviewPlot, formatCount, num as finiteNumber, nums as numericArray, overviewLayout, plotData } from "./analysisChartKit";
import { FeatureVarianceChart } from "./featureVariance";
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

function analysisSourceContextKey(
  tab: TabKey,
  params: Pick<AnalysisParams, "overviewAnalysis" | "samplingAlgorithm" | "referenceRunId" | "queryRunId" | "referenceViewId" | "queryViewId" | "viewId">,
  selectedRun: string | null,
  secondRun: string | null,
): string {
  const crossDataset = tab === "coverage"
    || (tab === "sampling" && (params.samplingAlgorithm === "novelty_fps" || params.samplingAlgorithm === "uncertainty_diversity"))
    || (tab === "overview" && params.overviewAnalysis === "drift");
  if (crossDataset) {
    return [params.referenceRunId, params.referenceViewId ?? "full", params.queryRunId, params.queryViewId ?? "full"].join("|");
  }
  if (tab === "compare" || (tab === "overview" && params.overviewAnalysis === "sensitivity")) {
    return [selectedRun, secondRun].join("|");
  }
  return [selectedRun, params.viewId ?? "full"].join("|");
}

type Metric = {
  label: ReactNode;
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

const OVERVIEW_KIND_LABELS: Record<string, Pair> = {
  feature_variance: { en: "FEATURE VARIANCE", zh: "特征方差" },
  feature_correlation: { en: "FEATURE CORRELATION", zh: "特征相关性" },
  effective_dimension: { en: "EFFECTIVE DIMENSION", zh: "有效维度" },
  trajectory: { en: "TRAJECTORY", zh: "轨迹" },
  drift: { en: "DATASET DRIFT", zh: "数据集漂移" },
  sensitivity: { en: "PARAMETER SENSITIVITY", zh: "参数敏感性" },
  perturbation_sensitivity: { en: "STRUCTURAL PERTURBATION SENSITIVITY", zh: "结构扰动敏感性" },
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
  sampling: ["coverage_radius_curve", "coverage_mean_curve"],
};

function pcaPayloadPoints(payload: PcaPayload): Point[] {
  return payload.points.map((point) => ({
    i: point.i,
    frame: point.frame,
    row: point.atom,
    x: point.pc1,
    y: point.pc2,
    energy_per_atom: point.energy_per_atom,
    force_max: point.force_max,
    volume: point.volume,
  }));
}

function selectedIndicesFromPreview(result: AnalysisPreview): number[] {
  return (Array.isArray(result.selected) ? result.selected : [])
    .map((row) => Number(row.i ?? row.sample_index))
    .filter((index) => Number.isInteger(index) && index >= 0);
}

export default function Analysis() {
  const { message } = AntApp.useApp();
  const st = useWorkspace();
  const dataset = activeDataset(st);
  const selectedRun = st.activeDescriptorRunId;
  const setSelectedRun = st.setActiveRun;
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
  const recentModulesByGroup = useAnalysisUi((s) => s.recentModulesByGroup);
  const { tab, projection, overviewAnalysis, coverageMode, mode, preprocess, effectiveDimensionPreprocess, colorBy, nearZeroThreshold, lowVariationThreshold, featureCorrelationMethod, featureCorrelationThreshold } = view;
  const setProjection = useAnalysisUi((s) => s.setProjection);
  const setNavigationTarget = useAnalysisUi((s) => s.setNavigationTarget);
  const rememberNavigationModule = useAnalysisUi((s) => s.rememberNavigationModule);
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
  const [runningInfo, setRunningInfo] = useState<{ label: string; tab: TabKey; moduleKey: AnalysisModuleKey | null; method: string | null } | null>(null);
  const [lastJobProgress, setLastJobProgress] = useState<number | null>(null);
  const [selectedIndices, setSelectedIndices] = useState<number[]>([]);
  const [saveViewOpen, setSaveViewOpen] = useState(false);
  const [inspectedPoint, setInspectedPoint] = useState<Point | null>(null);
  const [selectedFrame, setSelectedFrame] = useState<FramePayload | null>(null);
  const [selectedFrameBusy, setSelectedFrameBusy] = useState(false);
  const [clusterAlgorithm, setClusterAlgorithm] = useState("kmeans");
  const [outlierAlgorithm, setOutlierAlgorithm] = useState("lof");
  const [samplingAlgorithm, setSamplingAlgorithm] = useState("fps");
  const [samplingStrategy, setSamplingStrategy] = useState("global");
  const [samplingScaling, setSamplingScaling] = useState("robust");
  const [samplingMinDistance, setSamplingMinDistance] = useState(0);
  const [samplingExistingRunId, setSamplingExistingRunId] = useState<string | null>(null);
  const [samplingBlocks, setSamplingBlocks] = useState<string[]>([]);
  const [samplingBudgetMode, setSamplingBudgetMode] = useState<"count" | "coverage">("count");
  const [samplingCoverage, setSamplingCoverage] = useState(95);
  const [samplingQuota, setSamplingQuota] = useState<{ groups: { group: string; structures: number; quota: number }[]; n_candidates: number } | null>(null);
  const [samplingQuotaBusy, setSamplingQuotaBusy] = useState(false);
  const [similarityMode, setSimilarityMode] = useState<"query" | "all_neighbors" | "pairwise">("query");
  const [compareMode, setCompareMode] = useState<CompareMode>("geometry");
  const [mantelMethod, setMantelMethod] = useState<"pearson" | "spearman">("pearson");
  const [mantelPermutations, setMantelPermutations] = useState(999);
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
  const [viewId, setViewId] = useState<string | null>(null);
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
  const activeNavModule = useMemo(() => analysisNavModuleForView(tab, overviewAnalysis, coverageMode), [coverageMode, overviewAnalysis, tab]);
  const activeNavGroup = useMemo(
    () => ANALYSIS_NAV_GROUPS.find((group) => group.modules.some((module) => module.key === activeNavModule?.key)) ?? ANALYSIS_NAV_GROUPS[0],
    [activeNavModule?.key],
  );
  const selectAnalysisModule = useCallback((moduleKey: AnalysisModuleKey) => {
    const module = analysisNavModuleForKey(moduleKey);
    if (!module) return;
    const group = ANALYSIS_NAV_GROUPS.find((candidate) => candidate.modules.some((item) => item.key === module.key));
    if (group) rememberNavigationModule(group.key, module.key);
    setNavigationTarget(module.target);
  }, [rememberNavigationModule, setNavigationTarget]);
  useEffect(() => {
    if (!activeNavModule || !activeNavGroup) return;
    rememberNavigationModule(activeNavGroup.key, activeNavModule.key);
  }, [activeNavGroup, activeNavModule, rememberNavigationModule]);
  const operationRef = useRef(0);
  // Analysis ids already auto-restored in this mount/run window, so a
  // persistent fetch error cannot loop the restore effect.
  const restoreAttemptedRef = useRef<string | null>(null);
  // Concrete module the restore effect last processed, so a module switch can
  // fall back to its own recent result while a same-module parameter change
  // remains exact-only.
  const lastLookedModuleRef = useRef<AnalysisModuleKey | null>(null);

  // Stable key of every parameter that changes what the current module computes.
  const analysisParams: AnalysisParams = {
    projection, mode, preprocess, effectiveDimensionPreprocess, tsnePerplexity, similarityMode, k, queryIndex,
    clusterAlgorithm, nClusters, outlierAlgorithm, contamination, samplingAlgorithm,
    nSamples, uncertaintyK, samplingStrategy, samplingScaling, samplingMinDistance, samplingExistingRunId,
    samplingBlocks, samplingBudgetMode, samplingCoverage,
    coverageMode, compareMode, mantelMethod, mantelPermutations,
    localCutoff, kernelName, overviewAnalysis, propertyName,
    propertyFolds, propertyReliabilityK, propertyDistanceMetric, propertySparsePercentile, propertyOodPercentile,
    perturbationType, perturbationCount, perturbationMaximum, perturbationStructures, perturbationMetric,
    nearZeroThreshold, lowVariationThreshold, featureCorrelationMethod, featureCorrelationThreshold,
    referenceRunId, queryRunId, referenceViewId, queryViewId, viewId,
  };
  const paramsKey = buildParamsKey(tab, analysisParams);
  const sourceContextKey = analysisSourceContextKey(tab, analysisParams, selectedRun, secondRun);
  // {tab, moduleKey, paramsKey, params} as of the latest render. Runs capture this when
  // they start so their slots always record the context the run belongs to,
  // never whatever the user has navigated to by completion time.
  const runContextRef = useRef<{ tab: TabKey; moduleKey: AnalysisModuleKey | null; paramsKey: string; params: AnalysisParams; sourceContextKey: string }>({
    tab,
    moduleKey: activeNavModule?.key ?? null,
    paramsKey,
    params: analysisParams,
    sourceContextKey,
  });
  runContextRef.current = { tab, moduleKey: activeNavModule?.key ?? null, paramsKey, params: analysisParams, sourceContextKey };

  const clearDisplayedAnalysis = useCallback(() => {
    setAnalysisId(null);
    setPreview(null);
    setPoints([]);
    setSelectedIndices([]);
    setInspectedPoint(null);
    setOverviewArrays({});
  }, []);

  const crossDatasetModule = tab === "coverage"
    || (tab === "sampling" && (samplingAlgorithm === "novelty_fps" || samplingAlgorithm === "uncertainty_diversity"))
    || (tab === "overview" && overviewAnalysis === "drift");
  // Modules that consume the single-run Scope selector (dataset view slicing);
  // compare/sensitivity take two runs and cross-dataset modules carry their
  // own reference/query views.
  const singleRunModule = !crossDatasetModule && tab !== "compare" && !(tab === "overview" && overviewAnalysis === "sensitivity");
  const completedAllRuns = useMemo(() => allRuns.filter((run) => run.status === "COMPLETED"), [allRuns]);
  const referenceRuns = useMemo(() => completedAllRuns.filter((run) => run.dataset_id === referenceDatasetId), [completedAllRuns, referenceDatasetId]);
  const referenceRunRow = referenceRuns.find((run) => run.id === referenceRunId) ?? null;
  const queryRuns = useMemo(() => completedAllRuns.filter(
    (run) => run.dataset_id === queryDatasetId
      && !!referenceRunRow?.feature_space_signature
      && run.feature_space_signature === referenceRunRow.feature_space_signature,
  ), [completedAllRuns, queryDatasetId, referenceRunRow?.feature_space_signature]);
  const queryRunRow = queryRuns.find((run) => run.id === queryRunId) ?? null;
  // Warm-start FPS: completed runs sharing the active run's feature space.
  // The existing training set may live in a different dataset file, so the
  // descriptor signature — not the dataset — decides compatibility here.
  const warmStartRuns = useMemo(() => {
    const active = completedAllRuns.find((run) => run.id === selectedRun);
    if (!active?.feature_space_signature) return [];
    return completedAllRuns.filter((run) => run.id !== selectedRun && run.feature_space_signature === active.feature_space_signature);
  }, [completedAllRuns, selectedRun]);
  const referenceViews = useMemo(() => datasetViews.filter((view) => view.dataset_id === referenceDatasetId && !view.stale), [datasetViews, referenceDatasetId]);
  const queryViews = useMemo(() => datasetViews.filter((view) => view.dataset_id === queryDatasetId && !view.stale), [datasetViews, queryDatasetId]);
  // Views of the active dataset, for the single-run modules' Scope selector.
  const activeViews = useMemo(() => datasetViews.filter((view) => view.dataset_id === dataset?.id && !view.stale), [datasetViews, dataset?.id]);
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
  const isCached = (param: string, value: string | number | string[] | null) =>
    !!analysisContextRunId && slotForParams(slots, tab, analysisContextRunId, buildParamsKey(tab, { ...analysisParams, [param]: value } as AnalysisParams)) !== null;
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
    setViewId((current) => activeViews.some((view) => view.id === current) ? current : null);
  }, [activeViews]);

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

  // Grouped-FPS budget preview: how the requested sample count splits across
  // element sets, fetched before running so the allocation is never a surprise.
  // Debounced because the preview resolves element metadata from the dataset,
  // and typing a sample count should not trigger one request per keystroke.
  useEffect(() => {
    if (tab !== "sampling" || samplingAlgorithm !== "fps" || samplingStrategy !== "grouped" || !selectedRun) {
      setSamplingQuota(null);
      setSamplingQuotaBusy(false);
      return;
    }
    let disposed = false;
    setSamplingQuotaBusy(true);
    const timer = window.setTimeout(() => {
      void ipc.request<{ groups: { group: string; structures: number; quota: number }[]; n_candidates: number }>("analysis.fps_quota", {
        run_id: selectedRun,
        mode,
        n_samples: nSamples,
        ...(viewId ? { view_id: viewId } : {}),
      }).then((payload) => {
        if (!disposed) setSamplingQuota(payload);
      }).catch(() => {
        if (!disposed) setSamplingQuota(null);
      }).finally(() => {
        if (!disposed) setSamplingQuotaBusy(false);
      });
    }, 400);
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [tab, samplingAlgorithm, samplingStrategy, selectedRun, mode, nSamples, viewId]);

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
    // The page-level history normally refreshes from job.finished. The
    // watcher also recovers terminal state by polling, so refresh explicitly
    // to cover a missed finished event.
    void refresh();
    if (done.status !== "COMPLETED") {
      if (isCurrent()) message.error(`${label} ${jobStatusLabel(tr, done.status)}: ${done.error?.message ?? ""}`);
      return { failed: true };
    }
    return { failed: false, analysisId: typeof done.result?.analysis_id === "string" ? done.result.analysis_id : null };
  }, [message, refresh, tr]);

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
      && analysisNavModuleForView(
        useAnalysisUi.getState().view.tab,
        useAnalysisUi.getState().view.overviewAnalysis,
        useAnalysisUi.getState().view.coverageMode,
      )?.key === requestContext.moduleKey
      && runContextRef.current.paramsKey === requestContext.paramsKey
      && runContextRef.current.sourceContextKey === requestContext.sourceContextKey;
    setLoadingAnalysisId(null);
    setBusy(true);
    setRunningInfo({ label, tab: requestContext.tab, moduleKey: requestContext.moduleKey, method });
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
  }, [commitAnalysis, dataset?.id, fetchAnalysisPoints, message, selectedRun, t, watchAnalysisJob]);

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
        moduleKey: "descriptor_space" as AnalysisModuleKey,
        paramsKey: buildParamsKey("projection", { ...runContextRef.current.params, projection: "pca", mode: activeMode, preprocess: activePreprocess }),
        sourceContextKey: sourceContextKey,
      };
      const isCurrent = () => operationRef.current === operation
        && useWorkspace.getState().activeDescriptorRunId === requestRunId
        && useWorkspace.getState().activeDatasetId === requestDatasetId
        && analysisNavModuleForView(
          useAnalysisUi.getState().view.tab,
          useAnalysisUi.getState().view.overviewAnalysis,
          useAnalysisUi.getState().view.coverageMode,
        )?.key === requestContext.moduleKey
        && useAnalysisUi.getState().view.projection === "pca"
        && useAnalysisUi.getState().view.mode === activeMode
        && useAnalysisUi.getState().view.preprocess === activePreprocess
        && runContextRef.current.sourceContextKey === requestContext.sourceContextKey;
      setBusy(true);
      setRunningInfo({ label: "PCA", tab: "projection", moduleKey: requestContext.moduleKey, method: "analysis.pca" });
      setLastJobProgress(0);
      setPoints([]);
      setPreview(null);
      setAnalysisId(null);
      setOverviewArrays({});
      setSelectedIndices([]);
      setInspectedPoint(null);
      useWorkspace.getState().setSelectedSample(null);
      try {
        const response = await ipc.request<PcaAnalysisResponse>("analysis.pca", { run_id: requestRunId, mode: activeMode, seed: 42, preprocess: activePreprocess, ...(viewId ? { view_id: viewId } : {}) });
        let id = response.analysis_id;
        if (response.job_id) {
          const watched = await watchAnalysisJob(response.job_id, "analysis.pca", "PCA", isCurrent);
          if (watched.failed) return;
          if (watched.analysisId !== null) id = watched.analysisId;
        }
        useAnalysisUi.getState().rememberResult({ runId: requestRunId, analysisId: id, tab: requestContext.tab, paramsKey: requestContext.paramsKey });
        if (!isCurrent()) return;
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
      ? { mode: activeMode, preprocess: activePreprocess, n_neighbors: 15, min_dist: 0.1, ...(viewId ? { view_id: viewId } : {}) }
      : { mode: activeMode, preprocess: activePreprocess, perplexity: tsnePerplexity === 30 ? undefined : tsnePerplexity, max_iter: 1000, ...(viewId ? { view_id: viewId } : {}) };
    await runRequest(`analysis.${projection}`, projectionParams, projection.toUpperCase());
  }, [commitAnalysis, dataset?.id, fetchAnalysisPoints, message, mode, preprocess, projection, runRequest, selectedRun, sourceContextKey, tsnePerplexity, t, viewId, watchAnalysisJob]);

  const handlePreprocessChange = useCallback((value: string) => {
    setPreprocess(value);
    if (projection === "pca" && selectedRun && value !== preprocess) {
      void runProjection({ preprocess: value });
    }
  }, [preprocess, projection, runProjection, selectedRun, setPreprocess]);

  const loadAnalysis = useCallback(async (row: AnalysisRow, opts?: { silent?: boolean }) => {
    if (!dataset || row.status !== "COMPLETED") return;
    const analysisType = row.analysis_type.toLowerCase();
    const analysisModule = analysisNavModuleForAnalysisType(analysisType);
    if (!analysisModule) {
      message.warning(t("The selected analysis module is no longer available"));
      return;
    }
    const analysisTarget = analysisModule.target;
    const analysisTab = analysisTarget.tab;
    const parameters = row.parameters ?? {};
    const inputRunIds = row.input_run_ids?.length ? row.input_run_ids : [row.descriptor_run_id];
    const crossDatasetAnalysis = ["coverage", "overlap", "acquisition", "drift"].includes(analysisType);
    if (!crossDatasetAnalysis && (!selectedRun || !inputRunIds.includes(selectedRun))) {
      message.warning(t("Select the source descriptor run before loading this analysis"));
      return;
    }
    const requestRunId = crossDatasetAnalysis ? inputRunIds[0] : selectedRun;
    if (!requestRunId) return;

    const referenceView = typeof parameters.reference_view_id === "string" ? parameters.reference_view_id : null;
    const queryView = typeof parameters.query_view_id === "string" ? parameters.query_view_id : null;
    if (crossDatasetAnalysis) {
      const reference = allRuns.find((run) => run.id === inputRunIds[0]);
      const query = allRuns.find((run) => run.id === inputRunIds[1]);
      if (!reference || !query) {
        message.warning(t("The source descriptor runs are no longer available"));
        return;
      }
      setReferenceDatasetId(reference.dataset_id);
      setReferenceRunId(reference.id);
      setReferenceViewId(referenceView);
      setQueryDatasetId(query.dataset_id);
      setQueryRunId(query.id);
      setQueryViewId(queryView);
    } else {
      setViewId(typeof parameters.view_id === "string" ? parameters.view_id : null);
    }

    const loadedParams: AnalysisParams = { ...runContextRef.current.params };
    loadedParams.overviewAnalysis = analysisTarget.overviewAnalysis ?? loadedParams.overviewAnalysis;
    loadedParams.coverageMode = analysisTarget.coverageMode ?? loadedParams.coverageMode;
    if (crossDatasetAnalysis) {
      loadedParams.referenceRunId = inputRunIds[0] ?? null;
      loadedParams.queryRunId = inputRunIds[1] ?? null;
      loadedParams.referenceViewId = referenceView;
      loadedParams.queryViewId = queryView;
    } else {
      loadedParams.viewId = typeof parameters.view_id === "string" ? parameters.view_id : null;
    }

    let loadedSecondRun = secondRun;
    if (analysisType === "compare" || analysisType === "mantel" || analysisType === "sensitivity") {
      loadedSecondRun = inputRunIds.find((runId) => runId !== requestRunId) ?? inputRunIds[1] ?? null;
      setSecondRun(loadedSecondRun);
    }

    // Navigation is one store update and one persisted settings write. All
    // module controls below are restored from the row before the slot key is
    // built, so the loaded result cannot be filed under a partial context.
    setNavigationTarget(analysisTarget);
    const navGroup = ANALYSIS_NAV_GROUPS.find((group) => group.modules.some((module) => module.key === analysisModule.key));
    if (navGroup) rememberNavigationModule(navGroup.key, analysisModule.key);

    if (analysisTab === "overview") {
      if (analysisType === "feature_variance") {
        const savedNear = finiteNumber(parameters.near_zero_relative_threshold);
        const savedLow = finiteNumber(parameters.low_variance_relative_threshold);
        const nextNear = savedNear == null ? nearZeroThreshold : Math.min(1, Math.max(0, savedNear));
        const nextLow = savedLow == null
          ? Math.max(nextNear, lowVariationThreshold)
          : Math.min(1, Math.max(nextNear, savedLow));
        setNearZeroThreshold(nextNear);
        setLowVariationThreshold(nextLow);
        loadedParams.nearZeroThreshold = nextNear;
        loadedParams.lowVariationThreshold = nextLow;
      } else if (analysisType === "feature_correlation") {
        const nextMethod = parameters.method === "spearman" ? "spearman" : "pearson";
        const savedThreshold = finiteNumber(parameters.correlation_threshold ?? parameters.redundancy_threshold);
        const nextThreshold = savedThreshold == null ? featureCorrelationThreshold : Math.min(1, Math.max(0, savedThreshold));
        setFeatureCorrelationMethod(nextMethod);
        setFeatureCorrelationThreshold(nextThreshold);
        loadedParams.featureCorrelationMethod = nextMethod;
        loadedParams.featureCorrelationThreshold = nextThreshold;
      } else if (analysisType === "effective_dimension") {
        // Analyses created before the preprocessing setting was exposed used
        // centered data; keep their cache key and control faithful on restore.
        const nextPreprocess: EffectiveDimensionPreprocess = parameters.preprocess === "standardized" ? "standardized" : "center";
        setEffectiveDimensionPreprocess(nextPreprocess);
        loadedParams.effectiveDimensionPreprocess = nextPreprocess;
      } else if (analysisType === "property_correlation") {
        const savedMode: PcaMode = parameters.mode === "atom" ? "atom" : "structure";
        const nextFolds = Math.max(2, Math.round(finiteNumber(parameters.folds) ?? 5));
        const nextK = Math.max(1, Math.round(finiteNumber(parameters.reliability_k) ?? 5));
        const nextMetric = parameters.distance_metric === "cosine" ? "cosine" : "euclidean";
        const nextSparse = Math.round((finiteNumber(parameters.sparse_quantile) ?? 0.90) * 100);
        const nextOod = Math.round((finiteNumber(parameters.ood_quantile) ?? 0.99) * 100);
        setMode(savedMode);
        setPropertyName(String(parameters.property ?? "energy_per_atom"));
        setPropertyFolds(nextFolds);
        setPropertyReliabilityK(nextK);
        setPropertyDistanceMetric(nextMetric);
        setPropertySparsePercentile(nextSparse);
        setPropertyOodPercentile(nextOod);
        loadedParams.propertyName = String(parameters.property ?? "energy_per_atom");
        loadedParams.propertyFolds = nextFolds;
        loadedParams.propertyReliabilityK = nextK;
        loadedParams.propertyDistanceMetric = nextMetric;
        loadedParams.propertySparsePercentile = nextSparse;
        loadedParams.propertyOodPercentile = nextOod;
        loadedParams.mode = savedMode;
      } else if (analysisType === "perturbation_sensitivity") {
        const nextType = parameters.perturbation === "strain" ? "strain" : "jitter";
        const nextCount = Math.max(2, Math.round(finiteNumber(parameters.n_amplitudes) ?? 8));
        const nextMaximum = Math.max(0.001, finiteNumber(parameters.max_amplitude) ?? 0.2);
        const nextStructures = Math.max(1, Math.round(finiteNumber(parameters.max_structures) ?? 64));
        const nextMetric = typeof parameters.metric === "string" && parameters.metric ? parameters.metric : "euclidean";
        setPerturbationType(nextType);
        setPerturbationCount(nextCount);
        setPerturbationMaximum(nextMaximum);
        setPerturbationStructures(nextStructures);
        setPerturbationMetric(nextMetric);
        loadedParams.perturbationType = nextType;
        loadedParams.perturbationCount = nextCount;
        loadedParams.perturbationMaximum = nextMaximum;
        loadedParams.perturbationStructures = nextStructures;
        loadedParams.perturbationMetric = nextMetric;
      } else if (analysisType === "sensitivity") {
        loadedParams.overviewAnalysis = "sensitivity";
      }
    }

    if (analysisTab === "projection") {
      const nextProjection: ProjectionName = analysisType === "umap" || analysisType === "tsne" ? analysisType : "pca";
      const savedMode: PcaMode = parameters.mode === "atom" ? "atom" : "structure";
      const savedPreprocess = parameters.preprocess;
      const savedPreprocessValue = savedPreprocess === "raw" || savedPreprocess === "center" || savedPreprocess === "standardized" ? savedPreprocess : "center";
      setProjection(nextProjection);
      setMode(savedMode);
      setPreprocess(savedPreprocessValue);
      loadedParams.projection = nextProjection;
      loadedParams.mode = savedMode;
      loadedParams.preprocess = savedPreprocessValue;
      loadedParams.tsnePerplexity = Math.max(2, Math.round(finiteNumber(parameters.perplexity) ?? tsnePerplexity));
      if (nextProjection === "tsne") setTsnePerplexity(loadedParams.tsnePerplexity);
    }

    if (analysisTab === "similarity") {
      const requestedMode = typeof parameters.similarity_mode === "string" ? parameters.similarity_mode : "";
      const nextSimilarityMode: "query" | "all_neighbors" | "pairwise" = analysisType === "neighbors" || requestedMode === "all_neighbors"
        ? "all_neighbors"
        : analysisType === "pairwise" || analysisType === "pairwise_similarity" || requestedMode === "pairwise"
          ? "pairwise"
          : "query";
      const nextMode: PcaMode = parameters.mode === "atom" ? "atom" : "structure";
      const nextK = Math.max(1, Math.round(finiteNumber(parameters.k ?? parameters.n_neighbors) ?? k));
      const nextQueryIndex = Math.max(0, Math.round(finiteNumber(parameters.query_index) ?? queryIndex));
      setSimilarityMode(nextSimilarityMode);
      setMode(nextMode);
      setK(nextK);
      setQueryIndex(nextQueryIndex);
      loadedParams.similarityMode = nextSimilarityMode;
      loadedParams.mode = nextMode;
      loadedParams.k = nextK;
      loadedParams.queryIndex = nextQueryIndex;
    }

    if (analysisTab === "clusters") {
      const requestedAlgorithm = String(parameters.algorithm ?? analysisType).toLowerCase();
      const nextAlgorithm = requestedAlgorithm === "hierarchical"
        ? "agglomerative"
        : ["kmeans", "dbscan", "hdbscan", "agglomerative"].includes(requestedAlgorithm)
          ? requestedAlgorithm
        : clusterAlgorithm;
      const nextClusters = Math.max(2, Math.round(finiteNumber(parameters.n_clusters ?? parameters.nClusters) ?? nClusters));
      const nextMode: PcaMode = parameters.mode === "atom" ? "atom" : "structure";
      setClusterAlgorithm(nextAlgorithm);
      setNClusters(nextClusters);
      setMode(nextMode);
      loadedParams.clusterAlgorithm = nextAlgorithm;
      loadedParams.nClusters = nextClusters;
      loadedParams.mode = nextMode;
    }

    if (analysisTab === "outliers") {
      const requestedAlgorithm = String(parameters.algorithm ?? analysisType).toLowerCase();
      const nextAlgorithm = requestedAlgorithm === "isolation-forest" || requestedAlgorithm === "iforest"
        ? "isolation_forest"
        : requestedAlgorithm === "mahalanobis_distance"
          ? "mahalanobis"
          : ["lof", "knn", "isolation_forest", "mahalanobis"].includes(requestedAlgorithm)
            ? requestedAlgorithm
        : outlierAlgorithm;
      const nextK = Math.max(1, Math.round(finiteNumber(parameters.k) ?? k));
      const nextContamination = Math.min(0.5, Math.max(0.001, finiteNumber(parameters.contamination) ?? contamination));
      const nextMode: PcaMode = parameters.mode === "atom" ? "atom" : "structure";
      setOutlierAlgorithm(nextAlgorithm);
      setK(nextK);
      setContamination(nextContamination);
      setMode(nextMode);
      loadedParams.outlierAlgorithm = nextAlgorithm;
      loadedParams.k = nextK;
      loadedParams.contamination = nextContamination;
      loadedParams.mode = nextMode;
    }

    if (analysisTab === "sampling") {
      const requestedAlgorithm = String(parameters.algorithm ?? analysisType).toLowerCase();
      const nextAlgorithm = analysisType === "acquisition"
        ? (parameters.acquisition_method === "uncertainty_diversity" ? "uncertainty_diversity" : "novelty_fps")
        : requestedAlgorithm === "cluster" ? "cluster_representative"
          : requestedAlgorithm === "element" ? "per_element"
            : ["fps", "novelty_fps", "uncertainty_diversity", "random", "stratified", "cluster_representative", "per_element"].includes(requestedAlgorithm)
              ? requestedAlgorithm
              : samplingAlgorithm;
      const nextSamples = Math.max(1, Math.round(finiteNumber(parameters.n_samples) ?? nSamples));
      const nextMode: PcaMode = parameters.mode === "atom" ? "atom" : "structure";
      setSamplingAlgorithm(nextAlgorithm);
      setNSamples(nextSamples);
      setMode(nextMode);
      loadedParams.samplingAlgorithm = nextAlgorithm;
      loadedParams.nSamples = nextSamples;
      loadedParams.mode = nextMode;
      const nextUncertaintyK = Math.max(2, Math.round(finiteNumber(parameters.uncertainty_k) ?? uncertaintyK));
      setUncertaintyK(nextUncertaintyK);
      loadedParams.uncertaintyK = nextUncertaintyK;
      if (nextAlgorithm === "fps") {
        const savedStrategy = parameters.strategy === "grouped" ? "grouped" : "global";
        const savedScaling = parameters.scaling === "raw" || parameters.scaling === "standardized" ? parameters.scaling : "robust";
        const savedMinDistance = Math.max(0, finiteNumber(parameters.min_distance) ?? 0);
        const savedExisting = typeof parameters.existing_run_id === "string" && parameters.existing_run_id ? parameters.existing_run_id : null;
        const savedBlocks = Array.isArray(parameters.blocks) ? parameters.blocks.map(String) : [];
        const savedCoverage = finiteNumber(parameters.target_coverage);
        const savedBudgetMode = savedCoverage != null && savedCoverage > 0 ? "coverage" : "count";
        const savedCoveragePercent = savedCoverage != null && savedCoverage > 0 ? Math.round(savedCoverage * 100) : 95;
        setSamplingStrategy(savedStrategy);
        setSamplingScaling(savedScaling);
        setSamplingMinDistance(savedMinDistance);
        setSamplingExistingRunId(savedExisting);
        setSamplingBlocks(savedBlocks);
        setSamplingBudgetMode(savedBudgetMode);
        setSamplingCoverage(savedCoveragePercent);
        loadedParams.samplingStrategy = savedStrategy;
        loadedParams.samplingScaling = savedScaling;
        loadedParams.samplingMinDistance = savedMinDistance;
        loadedParams.samplingExistingRunId = savedExisting;
        loadedParams.samplingBlocks = savedBlocks;
        loadedParams.samplingBudgetMode = savedBudgetMode;
        loadedParams.samplingCoverage = savedCoveragePercent;
      }
    }

    if (analysisTab === "coverage") {
      // The module target is authoritative, so loading coverage always resets
      // an old overlap selection and vice versa.
      loadedParams.coverageMode = analysisTarget.coverageMode ?? "coverage";
      const nextMode: PcaMode = parameters.mode === "atom" ? "atom" : "structure";
      setMode(nextMode);
      loadedParams.mode = nextMode;
    }

    if (analysisTab === "compare") {
      const nextCompareMode: CompareMode = analysisType === "mantel" || parameters.compare_mode === "mantel" ? "mantel" : "geometry";
      const nextMode: PcaMode = parameters.mode === "atom" ? "atom" : "structure";
      const nextMantelMethod = parameters.method === "spearman" || parameters.mantel_method === "spearman" ? "spearman" : "pearson";
      const nextPermutations = Math.max(1, Math.round(finiteNumber(parameters.permutations ?? parameters.mantel_permutations) ?? mantelPermutations));
      setCompareMode(nextCompareMode);
      setMode(nextMode);
      setMantelMethod(nextMantelMethod);
      setMantelPermutations(nextPermutations);
      loadedParams.compareMode = nextCompareMode;
      loadedParams.mode = nextMode;
      loadedParams.mantelMethod = nextMantelMethod;
      loadedParams.mantelPermutations = nextPermutations;
    }

    if (analysisTab === "local") {
      const nextK = Math.max(1, Math.round(finiteNumber(parameters.k) ?? k));
      const nextClusters = Math.max(2, Math.round(finiteNumber(parameters.n_clusters) ?? nClusters));
      const nextCutoff = Math.max(0.1, Math.min(10, finiteNumber(parameters.cutoff) ?? localCutoff));
      setMode("atom");
      setK(nextK);
      setNClusters(nextClusters);
      setLocalCutoff(nextCutoff);
      loadedParams.mode = "atom";
      loadedParams.k = nextK;
      loadedParams.nClusters = nextClusters;
      loadedParams.localCutoff = nextCutoff;
    }

    if (analysisTab === "kernel") {
      const requestedKernel = String(parameters.kernel ?? parameters.kernel_name ?? kernelName).toLowerCase();
      const nextKernel = ["rbf", "linear", "cosine", "polynomial"].includes(requestedKernel) ? requestedKernel : kernelName;
      const nextMode: PcaMode = parameters.mode === "atom" ? "atom" : "structure";
      setKernelName(nextKernel);
      setMode(nextMode);
      loadedParams.kernelName = nextKernel;
      loadedParams.mode = nextMode;
    }

    const loadedContext = { tab: analysisTab, paramsKey: buildParamsKey(analysisTab, loadedParams) };
    const loadedSourceContextKey = analysisSourceContextKey(analysisTab, loadedParams, requestRunId, loadedSecondRun);
    // Make the synchronous cached path observe the row-derived source while
    // React schedules the control updates above. The next render replaces it
    // with the live context as usual.
    runContextRef.current = {
      tab: analysisTab,
      moduleKey: analysisModule.key,
      paramsKey: loadedContext.paramsKey,
      params: loadedParams,
      sourceContextKey: loadedSourceContextKey,
    };
    const operation = ++operationRef.current;
    const requestDatasetId = dataset.id;
    const isCurrent = () => operationRef.current === operation
      && (crossDatasetAnalysis || useWorkspace.getState().activeDescriptorRunId === requestRunId)
      && useWorkspace.getState().activeDatasetId === requestDatasetId
      && analysisNavModuleForView(
        useAnalysisUi.getState().view.tab,
        useAnalysisUi.getState().view.overviewAnalysis,
        useAnalysisUi.getState().view.coverageMode,
      )?.key === analysisModule.key
      && runContextRef.current.paramsKey === loadedContext.paramsKey
      && runContextRef.current.sourceContextKey === loadedSourceContextKey;

    setLoadingAnalysisId(row.id);
    setBusy(true);
    setRunningInfo({ label: analysisType.toUpperCase(), tab: analysisTab, moduleKey: analysisModule.key, method: null });
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
  }, [allRuns, clusterAlgorithm, commitAnalysis, contamination, dataset, featureCorrelationThreshold, fetchAnalysisPoints, k, kernelName, localCutoff, lowVariationThreshold, mantelPermutations, message, nClusters, nSamples, nearZeroThreshold, outlierAlgorithm, queryIndex, rememberNavigationModule, secondRun, selectedRun, samplingAlgorithm, setEffectiveDimensionPreprocess, setFeatureCorrelationMethod, setFeatureCorrelationThreshold, setLowVariationThreshold, setMode, setNearZeroThreshold, setNavigationTarget, setPreprocess, setProjection, t, tsnePerplexity, uncertaintyK]);

  // Keep the displayed result in step with the current module + parameters:
  // an exact slot match is re-displayed from the backend artifacts instead of
  // recomputing; switching modules falls back only to that module's recent
  // result; a same-module parameter change remains exact-only.
  // One load attempt per analysis id (reset when the dataset/run changes) so
  // a persistent fetch error cannot loop.
  useEffect(() => {
    if (busy || loadingAnalysisId) return;
    const slotMap = useAnalysisUi.getState().slots;
    const moduleKey = activeNavModule?.key ?? null;
    const exact = slotForParams(slotMap, tab, analysisContextRunId, paramsKey);
    const exactForModule = exact && analysisSlotMatchesModule(exact, moduleKey) ? exact : null;
    const moduleChanged = lastLookedModuleRef.current !== moduleKey;
    lastLookedModuleRef.current = moduleKey;
    const slot = exactForModule ?? (moduleChanged ? latestSlotForModule(slotMap, moduleKey, analysisContextRunId) : null);
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
    // A slot whose row belongs to another concrete module was recorded under
    // the wrong context; restoring it would cross modules. Forget the bad
    // slot and stay put instead.
    if (analysisNavModuleForAnalysisType(row.analysis_type.toLowerCase())?.key !== moduleKey) {
      if (slot) useAnalysisUi.getState().forgetSlot(slotKey(slot));
      restoreAttemptedRef.current = null;
      clearDisplayedAnalysis();
      return;
    }
    void loadAnalysis(row, { silent: true });
  }, [activeNavModule?.key, analyses, analysisContextRunId, analysisId, busy, clearDisplayedAnalysis, loadAnalysis, loadingAnalysisId, paramsKey, tab]);

  const runTabAnalysis = useCallback(async () => {
    if (tab === "projection") return runProjection();
    if (tab === "similarity") {
      const method = similarityMode === "pairwise" ? "analysis.pairwise" : similarityMode === "all_neighbors" ? "analysis.neighbors" : "analysis.similarity";
      const parameters = similarityMode === "pairwise"
        ? { metric: "cosine", preprocess: "raw", mode, max_samples: 400, ...(viewId ? { view_id: viewId } : {}) }
        : { k, query_index: queryIndex, metric: "cosine", preprocess: "raw", mode, ...(viewId ? { view_id: viewId } : {}) };
      await runRequest(method, parameters, similarityMode === "pairwise" ? t("Pairwise similarity") : similarityMode === "all_neighbors" ? t("Neighbor graph") : t("Similarity"));
    } else if (tab === "clusters") {
      await runRequest("analysis.cluster", { algorithm: clusterAlgorithm, n_clusters: nClusters, preprocess: "standardized", mode, ...(viewId ? { view_id: viewId } : {}) }, clusterAlgorithm.toUpperCase());
    } else if (tab === "outliers") {
      await runRequest("analysis.outlier", { algorithm: outlierAlgorithm, k, contamination, preprocess: "standardized", mode, ...(viewId ? { view_id: viewId } : {}) }, outlierAlgorithm.toUpperCase());
    } else if (tab === "sampling") {
      if (samplingAlgorithm === "novelty_fps" || samplingAlgorithm === "uncertainty_diversity") {
        if (!crossInputsReady || !referenceRunId || !queryRunId) {
          message.warning(t("Select compatible reference and query runs"));
          return;
        }
        const uncertainty = samplingAlgorithm === "uncertainty_diversity";
        await runRequest("analysis.acquisition", { reference_run_id: referenceRunId, query_run_id: queryRunId, ...(referenceViewId ? { reference_view_id: referenceViewId } : {}), ...(queryViewId ? { query_view_id: queryViewId } : {}), n_samples: nSamples, mode, acquisition_method: samplingAlgorithm, novelty_weight: 0.65, uncertainty_weight: 0.65, uncertainty_k: uncertaintyK }, uncertainty ? t("Uncertainty acquisition") : t("Novelty acquisition"), { contextRunId: referenceRunId, followActiveRun: false });
      } else {
        const fpsParams = samplingAlgorithm === "fps"
          ? {
              strategy: samplingStrategy,
              scaling: samplingScaling,
              min_distance: samplingMinDistance,
              ...(samplingExistingRunId ? { existing_run_id: samplingExistingRunId } : {}),
              ...(samplingBlocks.length ? { blocks: samplingBlocks } : {}),
              ...(samplingBudgetMode === "coverage" ? { target_coverage: samplingCoverage / 100 } : {}),
            }
          : {};
        await runRequest("analysis.sampling", { algorithm: samplingAlgorithm, n_samples: nSamples, mode, ...fpsParams, ...(viewId ? { view_id: viewId } : {}) }, t("Sampling"));
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
      await runRequest("analysis.local_diversity", { mode: "atom", n_clusters: nClusters, k, cutoff: localCutoff, max_neighbors: 128, ...(viewId ? { view_id: viewId } : {}) }, t("Local diversity"));
    } else if (tab === "kernel") {
      await runRequest("analysis.kernel", { kernel: kernelName, mode, max_samples: 400, ...(viewId ? { view_id: viewId } : {}) }, t("Kernel diagnostics"));
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
      if (viewId && overviewAnalysis !== "drift" && overviewAnalysis !== "sensitivity") overviewParams.view_id = viewId;
      await runRequest(`analysis.${overviewAnalysis}`, overviewParams, activeNavModule ? tr(activeNavModule.label) : t("Analysis"), overviewAnalysis === "drift" ? { contextRunId: referenceRunId, followActiveRun: false } : undefined);
    }
  }, [activeNavModule, clusterAlgorithm, contamination, coverageMode, crossInputsReady, effectiveDimensionPreprocess, featureCorrelationMethod, featureCorrelationThreshold, k, kernelName, localCutoff, lowVariationThreshold, mantelMethod, mantelPermutations, mode, nClusters, nSamples, nearZeroThreshold, outlierAlgorithm, overviewAnalysis, perturbationCount, perturbationMaximum, perturbationMetric, perturbationStructures, perturbationType, propertyDistanceMetric, propertyFolds, propertyName, propertyOodPercentile, propertyReliabilityK, propertySparsePercentile, queryIndex, queryRunId, queryViewId, referenceRunId, referenceViewId, runProjection, runRequest, runs, samplingAlgorithm, samplingBlocks, samplingBudgetMode, samplingCoverage, samplingExistingRunId, samplingMinDistance, samplingScaling, samplingStrategy, secondRun, selectedRun, similarityMode, tab, t, tr, uncertaintyK, viewId, message, compareMode]);

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
    const values = points.map((point) => colorBy === "energy" ? point.energy_per_atom : colorBy === "force_max" ? point.force_max : colorBy === "volume" ? point.volume : undefined);
    const hasColor = values.some((value) => value != null && Number.isFinite(value));
    const displayedSelected = selectedDisplayIndices(points, selectedIndices);
    const colorByLabel = colorBy === "energy" ? t("Energy / atom") : colorBy === "force_max" ? t("force_max") : colorBy === "volume" ? t("volume") : colorBy;
    return (
      <Plot
        key={`${analysisId ?? "pending"}:${projection}:${mode}:${preprocess}`}
        data={plotData([{
          type: "scattergl",
          mode: "markers",
          x: points.map((point) => point.x),
          y: points.map((point) => point.y),
          text: points.map((point) => `${t("frame {frame}", { frame: point.frame })}${point.row == null ? "" : t(" · row {row}", { row: point.row })}`),
          customdata: points.map((point) => [point.i, point.frame, point.row ?? -1]),
          marker: hasColor ? { size: 7, color: values as number[], colorscale: HIGH_CONTRAST_COLORSCALE, showscale: true, colorbar: { title: { text: colorByLabel } } } : { size: 7, color: "#0F6CBD" },
           selectedpoints: displayedSelected,
          hovertemplate: "%{text}<br>x=%{x:.5g}<br>y=%{y:.5g}<extra></extra>",
        }])}
        layout={overviewLayout({ autosize: true, margin: { l: 56, r: 24, t: 16, b: 48 }, paper_bgcolor: "#FFFFFF", plot_bgcolor: "#FFFFFF", dragmode: "lasso", hovermode: "closest", xaxis: { title: { text: projection === "pca" ? "PC1" : `${projection.toUpperCase()}-1` }, gridcolor: "#F0F1F3" }, yaxis: { title: { text: projection === "pca" ? "PC2" : `${projection.toUpperCase()}-2` }, gridcolor: "#F0F1F3" }, showlegend: false })}
        config={{ responsive: true, displaylogo: false, showSendToCloud: false, modeBarButtonsToAdd: ["select2d", "lasso2d"], modeBarButtonsToRemove: ["toImage"] }}
        style={{ width: "100%", height: "100%" }}
        onClick={(event) => {
          const index = event.points?.[0]?.pointIndex;
          if (typeof index === "number" && points[index]) handlePoint(points[index]);
        }}
        onSelected={(event: Readonly<PlotSelectionEvent>) => {
          const displayIndices = (event?.points ?? []).map((point: PlotDatum) => point.pointIndex).filter((index): index is number => typeof index === "number");
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
    if (exportFormat === "report" && !analysisId) {
      message.warning(t("Run a sampling analysis first"));
      return;
    }
    const indices = selectedIndices.length ? selectedIndices : points.map((point) => point.i);
    try {
      const response = await ipc.request<AnalysisJobResponse>("analysis.export", { run_id: selectedRun, indices, mode, format: exportFormat, output_path: exportPath.trim(), ...(exportFormat === "report" && analysisId ? { analysis_id: analysisId } : {}) });
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
            ? `sampling.${samplingAlgorithm !== "fps"
                ? samplingAlgorithm
                : samplingBudgetMode === "coverage"
                  ? "coverage_target_fps"
                  : samplingBlocks.length
                    ? "composite_fps"
                    : samplingStrategy === "grouped"
                      ? "grouped_fps"
                      : "fps"}`
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
  const overviewModuleHint = tab === "overview" && overviewAnalysis === "sensitivity" && <Typography.Text type="secondary">{t("Compare parameter variants of the same descriptor; use Compare for different descriptors.")}</Typography.Text>;
  const activeModuleLabel = activeNavModule ? tr(activeNavModule.label) : t("Analysis");
  const legacyOverview = tab === "overview" && (preview?.kind === "feature_variance" || preview?.kind === "effective_dimension");

  return (
    <div className="analysis-page">
      <div className="analysis-navigation" aria-label={t("Analysis navigation")}>
        <Tabs
          className="analysis-group-tabs"
          aria-label={t("Analysis categories")}
          activeKey={activeNavGroup?.key}
          onChange={(key) => {
            const group = ANALYSIS_NAV_GROUPS.find((item) => item.key === key);
            if (!group) return;
            const remembered = recentModulesByGroup[group.key];
            selectAnalysisModule(remembered && group.modules.some((module) => module.key === remembered) ? remembered : group.modules[0].key);
          }}
          items={ANALYSIS_NAV_GROUPS.map((group) => ({ key: group.key, label: tr(group.label) }))}
        />
        <Tabs
          className="analysis-module-tabs"
          aria-label={t("Analysis modules")}
          size="small"
          activeKey={activeNavModule?.key}
          onChange={(key) => selectAnalysisModule(key as AnalysisModuleKey)}
          items={(activeNavGroup?.modules ?? []).map((module) => ({ key: module.key, label: tr(module.label) }))}
        />
      </div>
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
            {singleRunModule && activeViews.length > 0 && <>
              <Typography.Text>{t("Scope")}</Typography.Text>
              <Select
                aria-label={t("Scope")}
                value={viewId ?? "__full__"}
                style={{ width: 200 }}
                disabled={busy}
                options={[
                  { value: "__full__", label: t("Full dataset") },
                  ...activeViews.map((view) => ({ value: view.id, label: `${view.name} · ${view.number_of_frames.toLocaleString()}` })),
                ]}
                onChange={(value) => setViewId(value === "__full__" ? null : value)}
              />
            </>}
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

      <div className="analysis-workspace">
        <main className="analysis-main">
          <section className={`analysis-card analysis-controls${tab === "overview" && overviewAnalysis === "property_correlation" ? " analysis-controls-property" : ""}`}>
            {tab === "projection" && <ProjectionControls projection={projection} setProjection={setProjection} mode={mode} setMode={setMode} preprocess={preprocess} onPreprocessChange={handlePreprocessChange} tsnePerplexity={tsnePerplexity} setTsnePerplexity={setTsnePerplexity} markOptions={markOptions} cachedParam={cachedParam} />}
            {tab === "similarity" && <Space wrap><Typography.Text>{t("View")}</Typography.Text><Select value={similarityMode} onChange={setSimilarityMode} options={markOptions("similarityMode", [{ value: "query", label: t("Query neighbors") }, { value: "all_neighbors", label: t("All-neighbor graph") }, { value: "pairwise", label: t("Pairwise matrix") }])} /><Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "clusters" && <Space wrap><Typography.Text>{t("Algorithm")}</Typography.Text><Select value={clusterAlgorithm} onChange={setClusterAlgorithm} options={markOptions("clusterAlgorithm", ["kmeans", "dbscan", "hdbscan", "agglomerative"].map((value) => ({ value, label: value.toUpperCase() })))} /><ParamLabel label={t("Clusters")} cached={cachedParam("nClusters")} /><InputNumber min={2} value={nClusters} onChange={(value) => setNClusters(value ?? 6)} /><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "outliers" && <Space wrap><Typography.Text>{t("Algorithm")}</Typography.Text><Select value={outlierAlgorithm} onChange={setOutlierAlgorithm} options={markOptions("outlierAlgorithm", ["lof", "knn", "isolation_forest", "mahalanobis"].map((value) => ({ value, label: value.toUpperCase() })))} /><ParamLabel label={t("Contamination")} cached={cachedParam("contamination")} /><InputNumber min={0.001} max={0.5} step={0.001} value={contamination} onChange={(value) => setContamination(value ?? 0.01)} /><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "sampling" && <><Space wrap><Typography.Text>{t("Method")}</Typography.Text><Select value={samplingAlgorithm} onChange={setSamplingAlgorithm} options={markOptions("samplingAlgorithm", ["fps", "novelty_fps", "uncertainty_diversity", "random", "stratified", "cluster_representative", "per_element"].map((value) => ({ value, label: tr(SAMPLING_LABELS[value] ?? { en: value, zh: value }) })))} /><ParamLabel label={t("Maximum samples")} cached={cachedParam("nSamples")} /><InputNumber min={1} value={nSamples} onChange={(value) => setNSamples(value ?? 1000)} /><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom") }])} />{samplingAlgorithm === "uncertainty_diversity" && <><ParamLabel label="kNN" cached={cachedParam("uncertaintyK")} /><InputNumber min={2} value={uncertaintyK} onChange={(value) => setUncertaintyK(value ?? 8)} /></>}{samplingAlgorithm === "fps" && <><ParamLabel label={t("Strategy")} cached={cachedParam("samplingStrategy")} /><Select value={samplingStrategy} onChange={setSamplingStrategy} options={markOptions("samplingStrategy", [{ value: "global", label: t("Global FPS") }, { value: "grouped", label: t("Grouped FPS") }])} /><ParamLabel label={t("Feature scaling")} cached={cachedParam("samplingScaling")} /><Select value={samplingScaling} onChange={setSamplingScaling} options={markOptions("samplingScaling", [{ value: "robust", label: t("Robust") }, { value: "standardized", label: t("Standardized") }, { value: "raw", label: t("Raw") }])} /><ParamLabel label={t("Sampling space")} cached={cachedParam("samplingBlocks")} /><Select mode="multiple" allowClear value={samplingBlocks} onChange={(value) => setSamplingBlocks(value ?? [])} style={{ minWidth: 240 }} placeholder={t("Descriptor only")} options={[{"value":"descriptor","label":t("Descriptor")},{"value":"descriptor_summary","label":t("Descriptor summary")},{"value":"lattice","label":t("Lattice")},{"value":"composition","label":t("Composition")},{"value":"energy","label":t("Energy")},{"value":"force","label":t("Force statistics")}]} /><ParamLabel label={t("Sample budget")} cached={cachedParam("samplingBudgetMode")} /><Select value={samplingBudgetMode} onChange={setSamplingBudgetMode} options={markOptions("samplingBudgetMode", [{ value: "count", label: t("Fixed sample count") }, { value: "coverage", label: t("Target coverage") }])} />{samplingBudgetMode === "coverage" && <><ParamLabel label={t("Target coverage")} cached={cachedParam("samplingCoverage")} /><InputNumber min={1} max={99} value={samplingCoverage} onChange={(value) => setSamplingCoverage(Math.min(99, Math.max(1, Math.round(value ?? 95))))} addonAfter="%" /></>}<ParamLabel label={t("Min descriptor distance")} cached={cachedParam("samplingMinDistance")} /><InputNumber min={0} step={0.001} value={samplingMinDistance} onChange={(value) => setSamplingMinDistance(Math.max(0, value ?? 0))} /><Typography.Text type="secondary">{t("Initialization: Center")}</Typography.Text><ParamLabel label={t("Existing dataset")} cached={cachedParam("samplingExistingRunId")} /><Select allowClear placeholder={t("None")} value={samplingExistingRunId} onChange={(value) => setSamplingExistingRunId(value ?? null)} style={{ minWidth: 180 }} options={warmStartRuns.map((run) => ({ value: run.id, label: `${run.descriptor_name} · ${run.dataset_name ?? run.dataset_id}` }))} /></>}</Space>
            {samplingAlgorithm === "fps" && samplingStrategy === "grouped" && (
              <div style={{ marginTop: 12 }} aria-busy={samplingQuotaBusy}>
                <Typography.Text type="secondary">{t("Sampling quota preview (√N per element set)")}</Typography.Text>
                {samplingQuotaBusy ? <Spin size="small" style={{ marginLeft: 8 }} /> : samplingQuota && samplingQuota.groups.length ? (
                  <Table
                    size="small"
                    style={{ marginTop: 8, maxWidth: 480 }}
                    rowKey="group"
                    pagination={false}
                    dataSource={samplingQuota.groups}
                    columns={[
                      { title: t("Element set"), dataIndex: "group" },
                      { title: t("Structures"), dataIndex: "structures", align: "right" as const, render: (value: number) => value.toLocaleString() },
                      { title: t("Sampling quota"), dataIndex: "quota", align: "right" as const },
                    ]}
                  />
                ) : !samplingQuotaBusy ? <Typography.Text type="secondary" style={{ marginLeft: 8 }}>{t("Element metadata is unavailable for this run")}</Typography.Text> : null}
              </div>
            )}</>}
            {tab === "coverage" && <Space wrap><Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "local" && <Space wrap><ParamLabel label={t("Clusters / element")} cached={cachedParam("nClusters")} /><InputNumber min={2} value={nClusters} onChange={(value) => setNClusters(value ?? 6)} /><ParamLabel label={t("Descriptor kNN")} cached={cachedParam("k")} /><InputNumber min={1} value={k} onChange={(value) => setK(value ?? 10)} /><ParamLabel label={t("Neighbor cutoff")} cached={cachedParam("localCutoff")} /><InputNumber min={0.1} max={10} step={0.1} precision={2} value={localCutoff} onChange={(value) => setLocalCutoff(value == null ? 3 : Math.max(0.1, Math.min(10, value)))} addonAfter="Å" /><Typography.Text type="secondary">{t("Coordinates and periodic images determine coordination.")}</Typography.Text></Space>}
            {tab === "kernel" && <Space wrap><Typography.Text>{t("Kernel")}</Typography.Text><Select value={kernelName} onChange={setKernelName} options={markOptions("kernelName", ["rbf", "linear", "cosine", "polynomial"].map((value) => ({ value, label: value.toUpperCase() })))} /><Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
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
                <Button type="primary" icon={<CheckmarkCircle16Regular />} loading={busy && runningInfo !== null && runningInfo.moduleKey === activeNavModule?.key && (tab !== "projection" || runningInfo.method === `analysis.${projection}`)} disabled={crossDatasetModule ? !crossInputsReady : !selectedRun} onClick={() => void runTabAnalysis()}>{t("Run {name}", { name: tab === "projection" ? projection.toUpperCase() : activeModuleLabel })}</Button>
                {/* Only the Projection canvas recolors by property; every
                    other module owns its coloring inside the result view. */}
                {tab === "projection" && points.length > 0 && <Select size="small" style={{ width: 132 }} aria-label={t("Color by")} value={colorBy} onChange={setColorBy} options={[{ value: "none", label: t("No color") }, { value: "energy", label: t("Energy / atom") }, { value: "force_max", label: t("Max |F|") }, { value: "volume", label: t("Volume") }]} />}
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
              defaultName={`${activeModuleLabel} ${selectedFrames.length}`}
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

          {tab === "sampling" && <section className="analysis-card"><SectionHeading title={t("EXPORT SELECTED SET")} meta={t("Source data is never modified")} /><Space.Compact style={{ width: "100%" }}><Select value={exportFormat} onChange={setExportFormat} options={["json", "csv", "extxyz", "deepmd", "indices", "report"].map((value) => ({ value, label: value.toUpperCase() }))} style={{ width: 120 }} /><Input readOnly placeholder={t("Choose an export destination")} value={exportPath} aria-label={t("Export destination")} /><Button onClick={() => void chooseExportPath()}>{t("Choose…")}</Button><Button icon={<ArrowDownload16Regular />} onClick={() => void exportSelection()}>{t("Export")}</Button></Space.Compact></section>}
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
        xaxis: { title: { text: t("Principal component") }, type: "linear" },
        yaxis: { title: { text: t("Explained variance ratio (%)") }, range: [0, 100] },
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

function MetricStrip({ metrics }: { metrics: Metric[] }) {
  return <div className="analysis-metric-strip">{metrics.map((metric, index) => <div className="analysis-metric" key={index}><Typography.Text type="secondary">{metric.label}</Typography.Text><Typography.Text strong>{metric.value}</Typography.Text></div>)}</div>;
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

/*
 * Analysis is the successor to the old Results page.  It owns one shared run
 * selector, one compact inspector, and a set of real backend-backed modules.
 * Descriptor run history lives on the separate Results page.
 * Plotly is deliberately scoped to this page; Overview remains ECharts and
 * Explore remains 3Dmol.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Plot from "../viz/ScientificPlot";
import type { PlotDatum, PlotSelectionEvent } from "plotly.js";
import {
  App as AntApp,
  Button,
  Empty,
  InputNumber,
  Progress,
  Select,
  Space,
  Tabs,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  ArrowRight16Regular,
  ArrowSync16Regular,
  CheckmarkCircle16Regular,
  Delete16Regular,
  Info16Regular,
} from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { open as openDialog, save as saveDialog } from "@tauri-apps/plugin-dialog";
import { useWorkspace } from "../stores/workspace";
import { jobStatusLabel, trackJob, watchJob } from "../stores/jobs";
import {
  buildAnalysisInputKey,
  buildParamsKey,
  buildSubmission,
  restoreAnalysisParams,
  analysisSlotMatchesModule,
  latestSlotForModule,
  ANALYSIS_NAV_GROUPS,
  analysisNavModuleForAnalysisType,
  analysisNavModuleForKey,
  analysisNavModuleForView,
  ARTIFACT_ARRAYS,
  slotForParams,
  slotKey,
  useAnalysisUi,
  type AnalysisModuleKey,
  type AnalysisParams,
  type TabKey,
} from "../features/analysis";
import { useT } from "../i18n";
import StructurePreview from "../components/StructurePreview";
import SaveViewModal from "../components/SaveViewModal";
import { hasColorByData, narrowedArrays, normalizePoints, previewRowFields, selectedDisplayIndices, stalenessNote } from "./analysisPreview";
import AnalysisResultVisualization from "./analysisVisualizations";
import { getAnalysisMethodGuide } from "./analysisMethodGuides";
import { HIGH_CONTRAST_COLORSCALE, overviewLayout, plotData } from "./analysisChartKit";
import { analysisCache, type CachedAnalysis } from "./analysisCache";
import type {
  AnalysisJobResponse,
  AnalysisChunk,
  AnalysisPreview,
  AnalysisRow,
  DatasetView,
  FramePayload,
  PcaPayload,
  RunRow,
} from "../types/protocol";

import {
  AnalysisMethodGuideModal,
  AnalysisRunLabel,
  CrossDatasetPicker,
  OverviewResultVisualization,
  ParamLabel,
  ProjectionControls,
  ResultPanel,
  Row,
  SectionHeading,
  pcaPayloadPoints,
  selectedIndicesFromPreview,
  SamplingControls,
  SamplingExportCard,
  withCacheMarks,
  type CacheOption,
  type CompareMode,
  type NumericArrays,
  type Point,
  type ProjectionOverrides,
} from "./analysisShared";
import { describeError } from "../util/errors";


export default function Analysis() {
  const { message } = AntApp.useApp();
  // Subscribed field by field: job.progress writes runningJobs on every tick,
  // and a whole-store subscription re-runs this component body each time.
  const datasets = useWorkspace((state) => state.datasets);
  const activeDatasetId = useWorkspace((state) => state.activeDatasetId);
  const selectedRun = useWorkspace((state) => state.activeDescriptorRunId);
  const setSelectedRun = useWorkspace((state) => state.setActiveRun);
  const dataset = datasets.find((item) => item.id === activeDatasetId);
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
  const [overviewArraysNarrowed, setOverviewArraysNarrowed] = useState<string[]>([]);
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
  const analysisParams = useMemo<AnalysisParams>(() => ({
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
  }), [
    clusterAlgorithm, compareMode, contamination, coverageMode, effectiveDimensionPreprocess, featureCorrelationMethod, featureCorrelationThreshold, k, kernelName, localCutoff, lowVariationThreshold, mantelMethod, mantelPermutations, mode, nClusters, nSamples, nearZeroThreshold, outlierAlgorithm, overviewAnalysis, perturbationCount, perturbationMaximum, perturbationMetric, perturbationStructures, perturbationType, preprocess, projection, propertyDistanceMetric, propertyFolds, propertyName, propertyOodPercentile, propertyReliabilityK, propertySparsePercentile, queryIndex, queryRunId, queryViewId, referenceRunId, referenceViewId, samplingAlgorithm, samplingBlocks, samplingBudgetMode, samplingCoverage, samplingExistingRunId, samplingMinDistance, samplingScaling, samplingStrategy, similarityMode, tsnePerplexity, uncertaintyK, viewId,
  ]);
  const paramsKey = buildParamsKey(tab, analysisParams);
  const inputKey = buildAnalysisInputKey(tab, analysisParams, selectedRun, secondRun);
  // {tab, moduleKey, paramsKey, inputKey, params} as of the latest render. Runs capture this when
  // they start so their slots always record the context the run belongs to,
  // never whatever the user has navigated to by completion time.
  const runContextRef = useRef<{ tab: TabKey; moduleKey: AnalysisModuleKey | null; paramsKey: string; inputKey: string; params: AnalysisParams }>({
    tab,
    moduleKey: activeNavModule?.key ?? null,
    paramsKey,
    inputKey,
    params: analysisParams,
  });
  runContextRef.current = { tab, moduleKey: activeNavModule?.key ?? null, paramsKey, inputKey, params: analysisParams };

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
    ? datasets.find((item) => item.id === queryDatasetId) ?? dataset
    : dataset;
  const pointRunId = crossDatasetModule ? queryRunId : selectedRun;
  const analysisContextRunId = crossDatasetModule ? referenceRunId : selectedRun;
  const selectedPoint = inspectedPoint ?? points.find((point) => point.i === selectedIndices[0]) ?? null;
  // Dataset frames behind the current selection, for saving a purification
  // view. The scatter and the preview rows both name the frame of a sample
  // index; an index neither names has no frame we may claim, so it is dropped
  // rather than saved as its own number — under a Scope, or in atom mode, the
  // two are different things and the wrong one silently changes the dataset.
  const selectedFrames = useMemo(() => {
    if (tab === "projection") return [];
    const frameOf = new Map<number, number>();
    for (const point of points) frameOf.set(point.i, point.frame);
    for (const row of preview?.selected ?? []) {
      const index = Number(row.i ?? row.sample_index);
      const frame = Number(row.frame);
      if (Number.isInteger(index) && index >= 0 && Number.isInteger(frame)) frameOf.set(index, frame);
    }
    const unmapped = selectedIndices.filter((index) => !frameOf.has(index));
    if (unmapped.length) console.warn("saving a view without a frame for", unmapped.length, "selected sample(s)");
    return [...new Set(selectedIndices.map((index) => frameOf.get(index)).filter((frame): frame is number => frame !== undefined))].sort((a, b) => a - b);
  }, [points, preview, selectedIndices, tab]);

  // Whether the (module, input, parameter combination) result is already computed
  // and can be re-displayed without rerunning. One parameter can be probed
  // with a candidate value; the others stay at their current value.
  const isCached = (param: string, value: string | number | string[] | null) =>
    !!activeNavModule && slotForParams(slots, activeNavModule.key, inputKey, buildParamsKey(tab, { ...analysisParams, [param]: value } as AnalysisParams)) !== null;
  // Cache dot helpers: select options marked per value, numeric labels per current value.
  const markOptions = (param: string, options: CacheOption[]) => withCacheMarks((value) => isCached(param, value), options);
  const cachedParam = (param: keyof AnalysisParams) => isCached(param, analysisParams[param]);

  // The four lists below are read together and written together, so a response
  // that arrives after a newer one must be dropped whole: `dataset` is a new
  // object after every refetch, which rebuilds this callback and puts another
  // request in flight, and the RPC pool answers out of order. An abandoned
  // answer used to bounce the selection back to the first completed run and
  // persist a run from the dataset the user had already left (pass 5, 5-B3).
  const refreshGeneration = useRef(0);
  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
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
      if (generation !== refreshGeneration.current) return;
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
      message.error(describeError(err, "ANALYSIS", t("Could not load analysis runs")));
    }
  }, [dataset, message, setSelectedRun, t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!dataset) return;
    setReferenceDatasetId((current) => datasets.some((item) => item.id === current) ? current : dataset.id);
  }, [dataset, datasets]);

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
      return datasets.find((item) => item.id !== referenceDatasetId && compatibleDatasetIds.has(item.id))?.id
        ?? (compatibleDatasetIds.has(referenceDatasetId ?? "") ? referenceDatasetId : null);
    });
  }, [completedAllRuns, referenceDatasetId, referenceRunRow, datasets]);

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
    setSelectedFrameBusy(false);
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
      // A request still in flight when the selection disappears will not report
      // its result (its cleanup has already set `disposed`), so nothing else
      // ever clears this - and the inspector then shows a loading line for a
      // structure that is no longer pending at all.
      setSelectedFrame(null);
      setSelectedFrameBusy(false);
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
      setOverviewArraysNarrowed([]);
      setOverviewArraysBusy(false);
      return;
    }

    const cached = analysisCache.get(analysisId);
    const cachedArrays = cached?.arrays ?? {};
    const missingArrays = arrayNames.filter((name) => !Object.prototype.hasOwnProperty.call(cachedArrays, name));
    if (!missingArrays.length) {
      setOverviewArrays(cachedArrays);
      setOverviewArraysNarrowed(cached?.narrowed ?? []);
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
        let truncated = false;
        let rowsTotal = Number.NaN;
        while (true) {
          const chunk = await ipc.request<AnalysisChunk>("analysis.chunk", {
            analysis_id: analysisId,
            array: name,
            offset,
            limit: 20_000,
            column_end: 2_000,
          });
          values.push(...chunk.data);
          truncated = truncated || chunk.truncated;
          rowsTotal = Number(chunk.shape?.[0]);
          if (!loadAll) break;
          const nextOffset = Number(chunk.next_offset);
          if (!chunk.data.length || !Number.isFinite(nextOffset) || nextOffset <= offset || (Number.isFinite(rowsTotal) && nextOffset >= rowsTotal)) break;
          offset = nextOffset;
        }
        return { array: name, values, truncated, rows: values.length, total: rowsTotal } as const;
      } catch {
        // Stay "missing" rather than caching an empty array: hasOwnProperty is
        // what counts as loaded, so [] told every later visit that a result
        // that merely failed once had been fetched — the panel then reported
        // the matrix as unavailable until the app was restarted.
        return null;
      }
    })).then((entries) => {
      if (disposed) return;
      const loaded = entries.filter(
        (entry): entry is { array: string; values: unknown[]; truncated: boolean; rows: number; total: number } => entry !== null,
      );
      const arrays = { ...cachedArrays, ...Object.fromEntries(loaded.map((entry) => [entry.array, entry.values] as const)) };
      const current = analysisCache.get(analysisId);
      const narrowed = narrowedArrays([
        ...loaded,
        ...Array.from(new Set([...(current?.narrowed ?? []), ...(cached?.narrowed ?? [])])).map((array) => ({ array, truncated: true })),
      ]);
      analysisCache.set(analysisId, {
        preview: current?.preview ?? cached?.preview ?? preview,
        points: current?.points ?? cached?.points ?? normalizePoints(preview ?? { analysis_id: analysisId }),
        selectedIndices: current?.selectedIndices ?? cached?.selectedIndices ?? selectedIndicesFromPreview(preview ?? { analysis_id: analysisId }),
        arrays,
        narrowed,
      });
      setOverviewArrays(arrays);
      setOverviewArraysNarrowed(narrowed);
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
      return { preview: null, points: pcaPayloadPoints(payload), selectedIndices: [], arrays: {}, narrowed: [] };
    }
    const result = await ipc.request<AnalysisPreview>("analysis.preview", { analysis_id: id, limit: 20_000 });
    return { preview: result, points: normalizePoints(result), selectedIndices: selectedIndicesFromPreview(result), arrays: {}, narrowed: [] };
  }, []);

  // Cache + display: the single place a resolved analysis lands on screen.
  const commitAnalysis = useCallback((id: string, next: CachedAnalysis) => {
    const cached = analysisCache.get(id);
    // Whatever arrays are kept are the ones `narrowed` describes, so the two
    // travel together: a history load must not lose the notice that the cached
    // matrix arrived column-truncated.
    analysisCache.set(id, { ...next, arrays: cached?.arrays ?? next.arrays, narrowed: cached ? cached.narrowed : next.narrowed });
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
    options?: {
      contextRunId?: string | null;
      followActiveRun?: boolean;
      /** Start under a context this render does not have yet — the PCA
       * preprocess select reruns before React has re-rendered with it. */
      context?: { moduleKey: AnalysisModuleKey; paramsKey: string };
      /** Replaces the captured-paramsKey freshness test for an overridden
       * context, which that comparison cannot express. */
      isFresh?: () => boolean;
    },
  ) => {
    const contextRunId = options?.contextRunId ?? selectedRun;
    if (!contextRunId) {
      message.warning(t("Select a completed descriptor run first"));
      return null;
    }
    const requestRunId = contextRunId;
    const requestDatasetId = dataset?.id;
  // The (module, input, parameters) context the run was started under: the result and
    // its cache slot belong there even if the user navigates while the job is
    // in flight.
    const requestContext = { ...runContextRef.current, ...(options?.context ?? {}) };
    const operation = ++operationRef.current;
    const isCurrent = () => operationRef.current === operation
      && (options?.followActiveRun === false || useWorkspace.getState().activeDescriptorRunId === requestRunId)
      && useWorkspace.getState().activeDatasetId === requestDatasetId
      && analysisNavModuleForView(
        useAnalysisUi.getState().view.tab,
        useAnalysisUi.getState().view.overviewAnalysis,
        useAnalysisUi.getState().view.coverageMode,
      )?.key === requestContext.moduleKey
      && (options?.isFresh ? options.isFresh() : runContextRef.current.paramsKey === requestContext.paramsKey)
      && runContextRef.current.inputKey === requestContext.inputKey;
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
      if (!requestContext.moduleKey) throw new Error(`${method} has no analysis module`);
      useAnalysisUi.getState().rememberResult({ analysisId: id, moduleKey: requestContext.moduleKey, inputKey: requestContext.inputKey, parameterKey: requestContext.paramsKey });
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
      if (isCurrent()) message.error(describeError(err, label, t("analysis failed")));
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
    const activeMode = requestedMode ?? mode;
    const activePreprocess = requestedPreprocess ?? preprocess;
    setLoadingAnalysisId(null);
    if (projection === "pca") {
      // PCA is the one run that can start before a re-render (the preprocess
      // select reruns it directly), so its context comes from the effective
      // mode/preprocess rather than the captured render state.
      return runRequest("analysis.pca", {
        mode: activeMode,
        preprocess: activePreprocess,
        ...(viewId ? { view_id: viewId } : {}),
      }, "PCA", {
        context: {
          moduleKey: "descriptor_space",
          paramsKey: buildParamsKey("projection", {
            ...runContextRef.current.params, projection: "pca", mode: activeMode, preprocess: activePreprocess,
          }),
        },
        isFresh: () => {
          const view = useAnalysisUi.getState().view;
          return view.projection === "pca" && view.mode === activeMode && view.preprocess === activePreprocess;
        },
      });
    }
    const projectionParams = projection === "umap"
      ? { mode: activeMode, preprocess: activePreprocess, n_neighbors: 15, min_dist: 0.1, ...(viewId ? { view_id: viewId } : {}) }
      : { mode: activeMode, preprocess: activePreprocess, perplexity: tsnePerplexity === 30 ? undefined : tsnePerplexity, max_iter: 1000, ...(viewId ? { view_id: viewId } : {}) };
    await runRequest(`analysis.${projection}`, projectionParams, projection.toUpperCase());
  }, [mode, preprocess, projection, runRequest, selectedRun, tsnePerplexity, viewId]);

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

    // One source for what this row was computed with: the restored parameters
    // drive both the controls and the run context, so the two cannot drift.
    const loadedParams: AnalysisParams = {
      ...runContextRef.current.params,
      ...restoreAnalysisParams({ tab: analysisTab, analysisType, parameters, current: runContextRef.current.params }),
      overviewAnalysis: analysisTarget.overviewAnalysis ?? runContextRef.current.params.overviewAnalysis,
      coverageMode: analysisTarget.coverageMode ?? runContextRef.current.params.coverageMode,
      ...(crossDatasetAnalysis
        ? { referenceRunId: inputRunIds[0] ?? null, queryRunId: inputRunIds[1] ?? null, referenceViewId: referenceView, queryViewId: queryView }
        : { viewId: typeof parameters.view_id === "string" ? parameters.view_id : null }),
    };

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

    // Every control the row can speak to, written once from the merged
    // parameters. A parameter the row does not carry keeps its current value,
    // so these assignments are no-ops in that case.
    setProjection(loadedParams.projection);
    setMode(loadedParams.mode);
    setPreprocess(loadedParams.preprocess);
    setTsnePerplexity(loadedParams.tsnePerplexity);
    setSimilarityMode(loadedParams.similarityMode as "query" | "all_neighbors" | "pairwise");
    setK(loadedParams.k);
    setQueryIndex(loadedParams.queryIndex);
    setClusterAlgorithm(loadedParams.clusterAlgorithm);
    setNClusters(loadedParams.nClusters);
    setOutlierAlgorithm(loadedParams.outlierAlgorithm);
    setContamination(loadedParams.contamination);
    setSamplingAlgorithm(loadedParams.samplingAlgorithm);
    setNSamples(loadedParams.nSamples);
    setUncertaintyK(loadedParams.uncertaintyK);
    setSamplingStrategy(loadedParams.samplingStrategy);
    setSamplingScaling(loadedParams.samplingScaling);
    setSamplingMinDistance(loadedParams.samplingMinDistance);
    setSamplingExistingRunId(loadedParams.samplingExistingRunId);
    setSamplingBlocks(loadedParams.samplingBlocks);
    setSamplingBudgetMode(loadedParams.samplingBudgetMode as "count" | "coverage");
    setSamplingCoverage(loadedParams.samplingCoverage);
    setCompareMode(loadedParams.compareMode as CompareMode);
    setMantelMethod(loadedParams.mantelMethod as "pearson" | "spearman");
    setMantelPermutations(loadedParams.mantelPermutations);
    setLocalCutoff(loadedParams.localCutoff);
    setKernelName(loadedParams.kernelName);
    setNearZeroThreshold(loadedParams.nearZeroThreshold);
    setLowVariationThreshold(loadedParams.lowVariationThreshold);
    setFeatureCorrelationMethod(loadedParams.featureCorrelationMethod);
    setFeatureCorrelationThreshold(loadedParams.featureCorrelationThreshold);
    setEffectiveDimensionPreprocess(loadedParams.effectiveDimensionPreprocess);
    setPropertyName(loadedParams.propertyName);
    setPropertyFolds(loadedParams.propertyFolds);
    setPropertyReliabilityK(loadedParams.propertyReliabilityK);
    setPropertyDistanceMetric(loadedParams.propertyDistanceMetric as "euclidean" | "cosine");
    setPropertySparsePercentile(loadedParams.propertySparsePercentile);
    setPropertyOodPercentile(loadedParams.propertyOodPercentile);
    setPerturbationType(loadedParams.perturbationType as "jitter" | "strain");
    setPerturbationCount(loadedParams.perturbationCount);
    setPerturbationMaximum(loadedParams.perturbationMaximum);
    setPerturbationStructures(loadedParams.perturbationStructures);
    setPerturbationMetric(loadedParams.perturbationMetric);

    const loadedContext = {
      moduleKey: analysisModule.key,
      parameterKey: buildParamsKey(analysisTab, loadedParams),
      inputKey: buildAnalysisInputKey(analysisTab, loadedParams, requestRunId, loadedSecondRun),
    };
    // Make the synchronous cached path observe the row-derived source while
    // React schedules the control updates above. The next render replaces it
    // with the live context as usual.
    runContextRef.current = {
      tab: analysisTab,
      moduleKey: analysisModule.key,
      paramsKey: loadedContext.parameterKey,
      inputKey: loadedContext.inputKey,
      params: loadedParams,
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
      && runContextRef.current.paramsKey === loadedContext.parameterKey
      && runContextRef.current.inputKey === loadedContext.inputKey;

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
        useAnalysisUi.getState().rememberResult({ analysisId: row.id, ...loadedContext });
        setLastJobProgress(1);
        if (!opts?.silent) message.success(t("Loaded cached {name}", { name: analysisType.toUpperCase() }));
        return;
      }

      const legacyPca = analysisType === "pca" && !row.artifact_manifest?.files;
      const fetched = await fetchAnalysisPoints(row.id, legacyPca ? "pca" : "preview");
      if (!isCurrent()) return;
      commitAnalysis(row.id, fetched);
      useAnalysisUi.getState().rememberResult({ analysisId: row.id, ...loadedContext });
      setLastJobProgress(1);
      if (!opts?.silent) message.success(t("Loaded cached {name}", { name: analysisType.toUpperCase() }));
    } catch (error) {
      const err = error as { code?: string; message?: string };
      if (isCurrent()) message.error(describeError(err, "ANALYSIS", t("could not load analysis")));
    } finally {
      if (operationRef.current === operation) {
        setBusy(false);
        setRunningInfo(null);
        setLoadingAnalysisId(null);
      }
    }
  }, [allRuns, commitAnalysis, dataset, fetchAnalysisPoints, message, rememberNavigationModule, secondRun, selectedRun, setEffectiveDimensionPreprocess, setFeatureCorrelationMethod, setFeatureCorrelationThreshold, setLowVariationThreshold, setMode, setNearZeroThreshold, setNavigationTarget, setPreprocess, setProjection, t]);

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
    const exact = slotForParams(slotMap, moduleKey, inputKey, paramsKey);
    const exactForModule = exact && analysisSlotMatchesModule(exact, moduleKey) ? exact : null;
    const moduleChanged = lastLookedModuleRef.current !== moduleKey;
    lastLookedModuleRef.current = moduleKey;
    const slot = exactForModule ?? (moduleChanged ? latestSlotForModule(slotMap, moduleKey, inputKey) : null);
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
        // The inspector belongs to the previous analysis, not this one.
        setInspectedPoint(null);
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
  }, [activeNavModule?.key, analyses, analysisContextRunId, analysisId, busy, clearDisplayedAnalysis, inputKey, loadAnalysis, loadingAnalysisId, paramsKey, tab]);

  const runTabAnalysis = useCallback(async () => {
    const submission = buildSubmission(tab, analysisParams, {
      selectedRun,
      secondRun,
      crossInputsReady,
      referenceDescriptor: runs.find((run) => run.id === selectedRun)?.descriptor_name ?? null,
      queryDescriptor: runs.find((run) => run.id === secondRun)?.descriptor_name ?? null,
    });
    if (submission.kind === "projection") return runProjection();
    if (submission.kind === "warning") {
      message.warning(t(submission.message));
      return;
    }
    // Overview modules name themselves through the navigation registry; the
    // rest carry their own label, and the clustering/outlier variants are just
    // the algorithm in caps.
    const label = submission.labelFromModule
      ? (activeNavModule ? tr(activeNavModule.label) : t("Analysis"))
      : t(submission.label);
    await runRequest(
      submission.method,
      submission.params,
      label,
      submission.anchoredToReference ? { contextRunId: referenceRunId, followActiveRun: false } : undefined,
    );
  }, [activeNavModule, analysisParams, crossInputsReady, message, referenceRunId, runProjection, runRequest, runs, secondRun, selectedRun, tab, t, tr]);

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
      analysisCache.setSelectedIndices(analysisId, indices);
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
      const response = await ipc.request<AnalysisJobResponse>("analysis.export", { run_id: selectedRun, indices, mode, format: exportFormat, output_path: exportPath.trim(), ...(viewId ? { view_id: viewId } : {}), ...(exportFormat === "report" && analysisId ? { analysis_id: analysisId } : {}) });
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
      message.error(describeError(err, "EXPORT", t("export failed")));
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
      message.error(describeError(err, "ANALYSIS", t("delete failed")));
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
            {tab === "outliers" && <Space wrap><Typography.Text>{t("Algorithm")}</Typography.Text><Select value={outlierAlgorithm} onChange={setOutlierAlgorithm} options={markOptions("outlierAlgorithm", ["lof", "knn", "isolation_forest", "mahalanobis"].map((value) => ({ value, label: value.toUpperCase() })))} />{(outlierAlgorithm === "lof" || outlierAlgorithm === "knn") && <><ParamLabel label="k" cached={cachedParam("k")} /><InputNumber min={1} value={k} onChange={(value) => setK(value ?? 10)} /></>}<ParamLabel label={t("Contamination")} cached={cachedParam("contamination")} /><InputNumber min={0.001} max={0.5} step={0.001} value={contamination} onChange={(value) => setContamination(value ?? 0.01)} /><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "sampling" && (
              <SamplingControls
                params={analysisParams}
                setters={{
                  samplingAlgorithm: setSamplingAlgorithm,
                  nSamples: setNSamples,
                  mode: setMode,
                  uncertaintyK: setUncertaintyK,
                  samplingStrategy: setSamplingStrategy,
                  samplingScaling: setSamplingScaling,
                  samplingBlocks: setSamplingBlocks,
                  samplingBudgetMode: (value: string) => setSamplingBudgetMode(value as "count" | "coverage"),
                  samplingCoverage: setSamplingCoverage,
                  samplingMinDistance: setSamplingMinDistance,
                  samplingExistingRunId: setSamplingExistingRunId,
                }}
                warmStartRuns={warmStartRuns}
                quota={samplingQuota}
                quotaBusy={samplingQuotaBusy}
                markOptions={markOptions}
                cachedParam={cachedParam}
              />
            )}
           {tab === "coverage" && <Space wrap><Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {tab === "local" && <Space wrap><ParamLabel label={t("Clusters / element")} cached={cachedParam("nClusters")} /><InputNumber min={2} value={nClusters} onChange={(value) => setNClusters(value ?? 6)} /><ParamLabel label={t("Descriptor kNN")} cached={cachedParam("k")} /><InputNumber min={1} value={k} onChange={(value) => setK(value ?? 10)} /><ParamLabel label={t("Neighbor cutoff")} cached={cachedParam("localCutoff")} /><InputNumber min={0.1} max={10} step={0.1} precision={2} value={localCutoff} onChange={(value) => setLocalCutoff(value == null ? 3 : Math.max(0.1, Math.min(10, value)))} addonAfter="Å" /><Typography.Text type="secondary">{t("Coordinates and periodic images determine coordination.")}</Typography.Text></Space>}
            {tab === "kernel" && <Space wrap><Typography.Text>{t("Kernel")}</Typography.Text><Select value={kernelName} onChange={setKernelName} options={markOptions("kernelName", ["rbf", "linear", "cosine", "polynomial"].map((value) => ({ value, label: value.toUpperCase() })))} /><Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
            {crossDatasetModule && <CrossDatasetPicker
              datasets={datasets}
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
              <InputNumber min={0} max={1} step={0.0001} precision={6} value={nearZeroThreshold} onChange={(value) => setNearZeroThreshold(value ?? 1e-4)} />
              <ParamLabel label={t("Low variation threshold")} cached={cachedParam("lowVariationThreshold")} />
              <InputNumber min={0} max={1} step={0.0001} precision={6} value={lowVariationThreshold} onChange={(value) => setLowVariationThreshold(value ?? 1e-2)} />
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
            {/* Drift measures whichever matrix its two runs share, so granularity
                is one of its inputs and its identity key already carries it. With
                no control here, changing the mode elsewhere dropped the reference
                points with nothing on screen to explain or undo it. */}
            {tab === "overview" && overviewAnalysis === "drift" && <Space wrap><ParamLabel label={t("Granularity")} cached={cachedParam("mode")} /><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /></Space>}
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
                {tab === "projection" && points.length > 0 && hasColorByData(points) && <Select size="small" style={{ width: 132 }} aria-label={t("Color by")} value={colorBy} onChange={setColorBy} options={[{ value: "none", label: t("No color") }, { value: "energy", label: t("Energy / atom") }, { value: "force_max", label: t("Max |F|") }, { value: "volume", label: t("Volume") }]} />}
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
          {tab !== "projection" && !legacyOverview && <AnalysisResultVisualization preview={preview} arrays={overviewArrays} narrowed={overviewArraysNarrowed} points={points} loading={overviewArraysBusy} selectedIndices={selectedIndices} onSelect={handlePoint} />}
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
            const numeric = (key: string) => (row[key] == null ? undefined : Number(row[key]));
            handlePoint({ i: index, frame, row: numeric("row"), sample_id: row.sample_id == null ? undefined : String(row.sample_id), x: 0, y: 0, element: numeric("element"), coordination: numeric("coordination"), novelty: numeric("novelty"), uncertainty: numeric("uncertainty"), diversity: numeric("diversity"), ...previewRowFields(row) });
          }} />}

          {tab === "sampling" && <SamplingExportCard format={exportFormat} onFormat={setExportFormat} destination={exportPath} onChoose={() => void chooseExportPath()} onExport={() => void exportSelection()} />}
        </main>

        <aside className="analysis-inspector">
          <section className="analysis-card"><SectionHeading title={t("INSPECTOR")} meta={selectedPoint ? t("Frame {index}", { index: selectedPoint.frame }) : undefined} />{selectedPoint ? <><Row k={t("Sample")} v={selectedPoint.sample_id ?? String(selectedPoint.i)} /><Row k={t("Frame")} v={String(selectedPoint.frame)} />{selectedPoint.row != null && <Row k={t("Row")} v={String(selectedPoint.row)} />}<Button size="small" icon={<ArrowRight16Regular />} onClick={openPointInExplore}>{t("Open in Explore")}</Button></> : <Typography.Text type="secondary">{t("Click a point, or use box/lasso selection, to inspect a structure.")}</Typography.Text>}</section>
          <section className="analysis-card"><SectionHeading title={t("STRUCTURE PREVIEW")} meta={selectedFrame ? t("Frame {index}", { index: selectedFrame.index }) : undefined} />{selectedFrame ? <StructurePreview frame={selectedFrame} selectedAtom={selectedPoint?.row} localCutoff={preview?.kind === "local_diversity" && selectedPoint?.row != null ? localCutoff : undefined} onOpen={openPointInExplore} onSelectAtom={handlePreviewAtomSelect} /> : <div className="analysis-empty-small">{selectedFrameBusy ? t("Loading structure…") : t("Select a sample to preview it.")}</div>}</section>
          <section className="analysis-card"><SectionHeading title={t("ANALYSIS HISTORY")} meta={`${visibleAnalyses.length}`} />{visibleAnalyses.length ? <div className="analysis-history-list">{visibleAnalyses.slice(0, 10).map((row) => { const note = stalenessNote(row.status, row.stale_reason); return <div className="analysis-history-row" key={row.id}><div><Typography.Text strong>{row.analysis_type}</Typography.Text><Typography.Text type="secondary" style={{ display: "block", fontSize: 11 }}>{new Date(row.created_at).toLocaleString(locale)}</Typography.Text>{note && <Typography.Text type="secondary" style={{ display: "block", fontSize: 11 }}>{note}</Typography.Text>}</div><Space size={4}><Tag color={row.status === "COMPLETED" ? "green" : row.status === "STALE" ? "orange" : undefined}>{jobStatusLabel(tr, row.status)}</Tag><Button size="small" type="text" icon={<ArrowRight16Regular />} aria-label={t("Load {name} analysis", { name: row.analysis_type })} title={t("Load cached analysis")} loading={loadingAnalysisId === row.id} disabled={row.status !== "COMPLETED" || (loadingAnalysisId !== null && loadingAnalysisId !== row.id)} onClick={() => void loadAnalysis(row)} /><Button size="small" type="text" icon={<Delete16Regular />} aria-label={t("Delete {name} analysis", { name: row.analysis_type })} disabled={row.status === "RUNNING" || row.status === "QUEUED"} onClick={() => void deleteAnalysis(row)} /></Space></div>; })}</div> : <Typography.Text type="secondary">{t("No analysis artifacts for this descriptor run yet.")}</Typography.Text>}</section>
        </aside>
      </div>
    </div>
  );
}


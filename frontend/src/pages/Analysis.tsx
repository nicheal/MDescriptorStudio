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
  ArrowSync16Regular,
  CheckmarkCircle16Regular,
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
  slotForParams,
  slotKey,
  useAnalysisUi,
  type AnalysisModuleKey,
  type AnalysisParams,
} from "../features/analysis";
import { useT } from "../i18n";
import SaveViewModal from "../components/SaveViewModal";
import { hasColorByData, previewRowFields, selectedDisplayIndices } from "./analysisPreview";
import AnalysisResultVisualization from "./analysisVisualizations";
import { getAnalysisMethodGuide } from "./analysisMethodGuides";
import { HIGH_CONTRAST_COLORSCALE, overviewLayout, plotData } from "./analysisChartKit";
import { analysisCache } from "./analysisCache";
import type {
  AnalysisJobResponse,
  AnalysisPreview,
  AnalysisRow,
  DatasetView,
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
  SectionHeading,
  SamplingControls,
  SamplingExportCard,
  withCacheMarks,
  type CacheOption,
  type Point,
} from "./analysisShared";
import { useAnalysisArtifactArrays } from "./useAnalysisArtifactArrays";
import { useAnalysisExecution, type AnalysisRunContext, type AnalysisRunningInfo } from "./useAnalysisExecution";
import { useAnalysisParameters } from "./useAnalysisParameters";
import { useAnalysisSelection } from "./useAnalysisSelection";
import AnalysisInspector from "./AnalysisInspector";
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
  const { t, tr } = useT();
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
  const setModeTransient = useAnalysisUi((s) => s.setModeTransient);
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
  const [runningInfo, setRunningInfo] = useState<AnalysisRunningInfo | null>(null);
  const [lastJobProgress, setLastJobProgress] = useState<number | null>(null);
  const [selectedIndices, setSelectedIndices] = useState<number[]>([]);
  const [saveViewOpen, setSaveViewOpen] = useState(false);
  const {
    analysisParams,
    restoreParameters,
    clusterAlgorithm, setClusterAlgorithm,
    outlierAlgorithm, setOutlierAlgorithm,
    samplingAlgorithm, setSamplingAlgorithm,
    samplingStrategy, setSamplingStrategy,
    setSamplingScaling,
    setSamplingMinDistance,
    setSamplingExistingRunId,
    samplingBlocks, setSamplingBlocks,
    samplingBudgetMode, setSamplingBudgetMode,
    setSamplingCoverage,
    samplingQuota, setSamplingQuota,
    samplingQuotaBusy, setSamplingQuotaBusy,
    similarityMode, setSimilarityMode,
    compareMode, setCompareMode,
    mantelMethod, setMantelMethod,
    mantelPermutations, setMantelPermutations,
    propertyName, setPropertyName,
    propertyFolds, setPropertyFolds,
    propertyReliabilityK, setPropertyReliabilityK,
    propertyDistanceMetric, setPropertyDistanceMetric,
    propertySparsePercentile, setPropertySparsePercentile,
    propertyOodPercentile, setPropertyOodPercentile,
    kernelName, setKernelName,
    localCutoff, setLocalCutoff,
    secondRun, setSecondRun,
    referenceDatasetId, setReferenceDatasetId,
    queryDatasetId, setQueryDatasetId,
    referenceRunId, setReferenceRunId,
    queryRunId, setQueryRunId,
    referenceViewId, setReferenceViewId,
    queryViewId, setQueryViewId,
    viewId, setViewId,
    exportFormat, setExportFormat,
    exportPath, setExportPath,
    k, setK,
    nClusters, setNClusters,
    nSamples, setNSamples,
    setUncertaintyK,
    contamination, setContamination,
    queryIndex, setQueryIndex,
    methodGuideOpen, setMethodGuideOpen,
    perturbationType, setPerturbationType,
    perturbationCount, setPerturbationCount,
    perturbationMaximum, setPerturbationMaximum,
    perturbationStructures, setPerturbationStructures,
    perturbationMetric, setPerturbationMetric,
    tsnePerplexity, setTsnePerplexity,
  } = useAnalysisParameters({
    projection,
    mode,
    preprocess,
    effectiveDimensionPreprocess,
    coverageMode,
    overviewAnalysis,
    nearZeroThreshold,
    lowVariationThreshold,
    featureCorrelationMethod,
    featureCorrelationThreshold,
  });
  const [overviewArraysRetry, setOverviewArraysRetry] = useState(0);
  const [loadingAnalysisId, setLoadingAnalysisId] = useState<string | null>(null);
  const {
    arrays: overviewArrays,
    setArrays: setOverviewArrays,
    narrowed: overviewArraysNarrowed,
    busy: overviewArraysBusy,
    error: overviewArraysError,
  } = useAnalysisArtifactArrays({ analysisId, preview, retry: overviewArraysRetry });
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

  const paramsKey = buildParamsKey(tab, analysisParams);
  const inputKey = buildAnalysisInputKey(tab, analysisParams, selectedRun, secondRun);
  // {tab, moduleKey, paramsKey, inputKey, params} as of the latest render. Runs capture this when
  // they start so their slots always record the context the run belongs to,
  // never whatever the user has navigated to by completion time.
  const runContextRef = useRef<AnalysisRunContext>({
    tab,
    moduleKey: activeNavModule?.key ?? null,
    paramsKey,
    inputKey,
    params: analysisParams,
  });
  runContextRef.current = { tab, moduleKey: activeNavModule?.key ?? null, paramsKey, inputKey, params: analysisParams };

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
  const {
    selectedPoint,
    selectedFrames,
    selectedFrame,
    selectedFrameBusy,
    inspectPoint,
    clearInspectedPoint,
    clearFrame,
    openPointInExplore,
    updateCachedSelection,
    handlePoint,
    handlePreviewAtomSelect,
  } = useAnalysisSelection({
    analysisId,
    pointDataset: pointDataset ?? null,
    pointRunId,
    preview,
    points,
    selectedIndices,
    setSelectedIndices,
    tab,
    mode,
    localCutoff,
  });

  const clearDisplayedAnalysis = useCallback(() => {
    setAnalysisId(null);
    setPreview(null);
    setPoints([]);
    setSelectedIndices([]);
    clearInspectedPoint();
    setOverviewArrays({});
  }, [clearInspectedPoint, setOverviewArrays]);

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
  }, [dataset, datasets, setReferenceDatasetId]);

  useEffect(() => {
    if (!referenceDatasetId) return;
    setReferenceRunId((current) => {
      if (referenceRuns.some((run) => run.id === current)) return current;
      if (selectedRun && referenceRuns.some((run) => run.id === selectedRun)) return selectedRun;
      return referenceRuns[0]?.id ?? null;
    });
    setReferenceViewId((current) => referenceViews.some((view) => view.id === current) ? current : null);
  }, [referenceDatasetId, referenceRuns, referenceViews, selectedRun, setReferenceRunId, setReferenceViewId]);

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
  }, [completedAllRuns, referenceDatasetId, referenceRunRow, datasets, setQueryDatasetId]);

  useEffect(() => {
    setQueryRunId((current) => queryRuns.some((run) => run.id === current) ? current : queryRuns[0]?.id ?? null);
    setQueryViewId((current) => queryViews.some((view) => view.id === current) ? current : null);
  }, [queryRuns, queryViews, setQueryRunId, setQueryViewId]);

  useEffect(() => {
    setViewId((current) => activeViews.some((view) => view.id === current) ? current : null);
  }, [activeViews, setViewId]);

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
    clearInspectedPoint();
    setSecondRun(null);
    setOverviewArrays({});
    setLoadingAnalysisId(null);
  }, [clearInspectedPoint, dataset?.id, selectedRun, setOverviewArrays, setSecondRun]);

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
  }, [overviewAnalysis, runs, secondRun, selectedRun, tab, setSecondRun]);

  useEffect(() => {
    const offFinished = ipc.on("job.finished", () => void refresh());
    return offFinished;
  }, [refresh]);

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
  }, [mode, nSamples, samplingAlgorithm, samplingStrategy, selectedRun, setSamplingQuota, setSamplingQuotaBusy, tab, viewId]);

  const { fetchAnalysisPoints, commitAnalysis, runRequest, runProjection, handlePreprocessChange } = useAnalysisExecution({
    datasetId: dataset?.id,
    selectedRun,
    projection,
    mode,
    preprocess,
    tsnePerplexity,
    viewId,
    setPreprocess,
    operationRef,
    runContextRef,
    refresh,
    message,
    t,
    tr,
    display: {
      clearInspectedPoint,
      setAnalysisId,
      setPreview,
      setPoints,
      setSelectedIndices,
      setOverviewArrays,
      setLoadingAnalysisId,
      setBusy,
      setRunningInfo,
      setLastJobProgress,
    },
  });

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
    setModeTransient(loadedParams.mode);
    setPreprocess(loadedParams.preprocess);
    restoreParameters(loadedParams);
    setNearZeroThreshold(loadedParams.nearZeroThreshold);
    setLowVariationThreshold(loadedParams.lowVariationThreshold);
    setFeatureCorrelationMethod(loadedParams.featureCorrelationMethod);
    setFeatureCorrelationThreshold(loadedParams.featureCorrelationThreshold);
    setEffectiveDimensionPreprocess(loadedParams.effectiveDimensionPreprocess);

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
    clearInspectedPoint();
    clearFrame();
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
  }, [allRuns, clearFrame, clearInspectedPoint, commitAnalysis, dataset, fetchAnalysisPoints, message, rememberNavigationModule, restoreParameters, secondRun, selectedRun, setEffectiveDimensionPreprocess, setFeatureCorrelationMethod, setFeatureCorrelationThreshold, setLowVariationThreshold, setModeTransient, setNearZeroThreshold, setNavigationTarget, setOverviewArrays, setPreprocess, setProjection, setQueryDatasetId, setQueryRunId, setQueryViewId, setReferenceDatasetId, setReferenceRunId, setReferenceViewId, setSecondRun, setViewId, t]);

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
        clearInspectedPoint();
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
  }, [activeNavModule?.key, analyses, analysisContextRunId, analysisId, busy, clearDisplayedAnalysis, clearInspectedPoint, inputKey, loadAnalysis, loadingAnalysisId, paramsKey, setOverviewArrays, tab]);

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
            clearInspectedPoint();
            useWorkspace.getState().setSelectedSample(null);
          }
        }}
      />
    );
  }, [analysisId, clearInspectedPoint, colorBy, handlePoint, inspectPoint, mode, points, preprocess, projection, selectedIndices, t, updateCachedSelection]);

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
          {overviewArraysError && <section className="analysis-card"><Space><Typography.Text type="warning">{t("Some analysis arrays failed to load.")}</Typography.Text><Button size="small" icon={<ArrowSync16Regular />} onClick={() => setOverviewArraysRetry((value) => value + 1)}>{t("Retry")}</Button></Space></section>}
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

        <AnalysisInspector
          selectedPoint={selectedPoint}
          selectedFrame={selectedFrame}
          selectedFrameBusy={selectedFrameBusy}
          preview={preview}
          localCutoff={localCutoff}
          visibleAnalyses={visibleAnalyses}
          loadingAnalysisId={loadingAnalysisId}
          onOpenPoint={openPointInExplore}
          onSelectAtom={handlePreviewAtomSelect}
          onLoadAnalysis={(row) => loadAnalysis(row)}
          onDeleteAnalysis={(row) => deleteAnalysis(row)}
        />
      </div>
    </div>
  );
}


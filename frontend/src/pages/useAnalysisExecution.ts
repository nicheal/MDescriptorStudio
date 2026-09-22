import { useCallback } from "react";
import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import { ipc } from "../ipc/client";
import { jobStatusLabel, trackJob, watchJob } from "../stores/jobs";
import { useWorkspace } from "../stores/workspace";
import {
  analysisNavModuleForView,
  buildParamsKey,
  useAnalysisUi,
  type AnalysisModuleKey,
  type AnalysisParams,
  type TabKey,
} from "../features/analysis";
import type { T } from "../i18n";
import type { AnalysisJobResponse, AnalysisPreview, PcaPayload } from "../types/protocol";
import { analysisCache, type CachedAnalysis } from "./analysisCache";
import { normalizePoints } from "./analysisPreview";
import { pcaPayloadPoints, selectedIndicesFromPreview, type NumericArrays, type Point, type ProjectionOverrides } from "./analysisShared";
import { describeError } from "../util/errors";

export interface AnalysisRunContext {
  tab: TabKey;
  moduleKey: AnalysisModuleKey | null;
  paramsKey: string;
  inputKey: string;
  params: AnalysisParams;
}

export interface AnalysisRunningInfo {
  label: string;
  tab: TabKey;
  moduleKey: AnalysisModuleKey | null;
  method: string | null;
}

interface MessageApi {
  warning(content: string): void;
  error(content: string): void;
  success(content: string): void;
}

interface DisplayState {
  clearInspectedPoint: () => void;
  setAnalysisId: Dispatch<SetStateAction<string | null>>;
  setPreview: Dispatch<SetStateAction<AnalysisPreview | null>>;
  setPoints: Dispatch<SetStateAction<Point[]>>;
  setSelectedIndices: Dispatch<SetStateAction<number[]>>;
  setOverviewArrays: Dispatch<SetStateAction<NumericArrays>>;
  setLoadingAnalysisId: Dispatch<SetStateAction<string | null>>;
  setBusy: Dispatch<SetStateAction<boolean>>;
  setRunningInfo: Dispatch<SetStateAction<AnalysisRunningInfo | null>>;
  setLastJobProgress: Dispatch<SetStateAction<number | null>>;
}

interface UseAnalysisExecutionOptions {
  datasetId?: string;
  selectedRun: string | null;
  projection: AnalysisParams["projection"];
  mode: AnalysisParams["mode"];
  preprocess: string;
  tsnePerplexity: number;
  viewId: string | null;
  setPreprocess: (value: string) => void;
  operationRef: MutableRefObject<number>;
  runContextRef: MutableRefObject<AnalysisRunContext>;
  refresh: () => Promise<void>;
  message: MessageApi;
  t: T["t"];
  tr: T["tr"];
  display: DisplayState;
}

export function useAnalysisExecution({
  datasetId,
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
  display,
}: UseAnalysisExecutionOptions) {
  const {
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
  } = display;

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
  }, [setAnalysisId, setOverviewArrays, setPoints, setPreview, setSelectedIndices]);

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
  }, [message, refresh, setLastJobProgress, tr]);

  const runRequest = useCallback(async (
    method: string,
    params: Record<string, unknown>,
    label: string,
    options?: {
      contextRunId?: string | null;
      followActiveRun?: boolean;
      /** Start under a context this render does not have yet — the PCA
       * preprocess select reruns it directly, so the effective context is
       * supplied by the caller. */
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
    const requestDatasetId = datasetId;
    // The (module, input, parameters) context the run was started under: the
    // result and its cache slot belong there even if the user navigates while
    // the job is in flight.
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
    clearInspectedPoint();
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
  }, [clearInspectedPoint, commitAnalysis, datasetId, fetchAnalysisPoints, message, operationRef, runContextRef, selectedRun, setAnalysisId, setBusy, setLastJobProgress, setLoadingAnalysisId, setOverviewArrays, setPoints, setPreview, setRunningInfo, setSelectedIndices, t, watchAnalysisJob]);

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
            ...runContextRef.current.params,
            projection: "pca",
            mode: activeMode,
            preprocess: activePreprocess,
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
  }, [mode, preprocess, projection, runContextRef, runRequest, selectedRun, setLoadingAnalysisId, tsnePerplexity, viewId]);

  const handlePreprocessChange = useCallback((value: string) => {
    setPreprocess(value);
    if (projection === "pca" && selectedRun && value !== preprocess) {
      void runProjection({ preprocess: value });
    }
  }, [preprocess, projection, runProjection, selectedRun, setPreprocess]);

  return { fetchAnalysisPoints, commitAnalysis, runRequest, runProjection, handlePreprocessChange };
}

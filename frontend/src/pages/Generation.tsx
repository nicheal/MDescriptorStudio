// Descriptor-guided dataset expansion: one page, three phases.
//   config  — the three-group workflow form
//   running — live metrics + convergence curves polled from generation.get
//   results — summary, curves, materialize/export actions
import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, App as AntApp, Button, Spin } from "antd";
import { ArrowLeft16Regular } from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { useWorkspace } from "../stores/workspace";
import { useGenerationStore } from "../features/generation/generationStore";
import { buildSubmitPayload, validateConfig } from "../features/generation/submission";
import type { GenerationCatalog, GenerationRow } from "../features/generation/types";
import type { DatasetView, RunRow } from "../types/protocol";
import { useT } from "../i18n";
import { describeError } from "../util/errors";
import GenerationConfigPanel from "./generationConfig";
import GenerationRunPanel from "./generationRun";
import GenerationResultsPanel from "./generationResults";

function messageFromError(error: unknown, fallback: string) {
  if (error && typeof error === "object" && "message" in error && typeof error.message === "string") {
    const frame = error as { code?: string; message: string; error_id?: string };
    return frame.code ? describeError(frame, "GENERATION", fallback) : frame.message;
  }
  return error instanceof Error ? error.message : fallback;
}

export default function Generation() {
  const { message } = AntApp.useApp();
  const { t } = useT();
  const translateRef = useRef(t);
  translateRef.current = t;
  const datasets = useWorkspace((s) => s.datasets);
  const activeDatasetId = useWorkspace((s) => s.activeDatasetId);
  const activeDatasetName = datasets.find((d) => d.id === activeDatasetId)?.name ?? null;
  const store = useGenerationStore();
  const [catalog, setCatalog] = useState<GenerationCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [allRuns, setAllRuns] = useState<RunRow[]>([]);
  const [runsDatasetId, setRunsDatasetId] = useState<string | null>(null);
  const [runsState, setRunsState] = useState<"loading" | "ready" | "failed">("loading");
  const [views, setViews] = useState<DatasetView[]>([]);
  const [viewsState, setViewsState] = useState<{ datasetId: string | null; status: "loading" | "ready" | "failed"; error: string | null }>({ datasetId: null, status: "loading", error: null });
  const [catalogState, setCatalogState] = useState<"loading" | "ready" | "failed">("loading");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [cancelPending, setCancelPending] = useState(false);
  const [runLoading, setRunLoading] = useState(false);
  const [runLoadError, setRunLoadError] = useState<string | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollVersionRef = useRef(0);
  const viewsRequestRef = useRef(0);
  const runsRequestRef = useRef(0);
  const catalogRequestRef = useRef(0);
  const openRequestRef = useRef(0);
  const phaseIntentRef = useRef(0);
  const restoreAttemptedRef = useRef(false);
  const historyRequestRef = useRef(0);
  const historyDatasetRef = useRef<string | null>(null);

  // Generation always follows the workspace's active dataset. Anchor indices
  // belong to that source dataset too, so never reuse them against another one.
  useEffect(() => {
    const { config, updateConfig, phase } = useGenerationStore.getState();
    if (config.source.datasetId !== activeDatasetId) {
      updateConfig((c) => ({
        ...c,
        source: { ...c.source, datasetId: activeDatasetId, descriptorRunId: null, seedViewId: null },
        searchTarget: { ...c.searchTarget, anchorFrames: [] },
      }));
      if (config.searchTarget.anchorFrames.length > 0 && phase !== "config") {
        message.info(t("Target anchor frames were cleared because they belong to the previous source dataset"));
      }
    }
  }, [activeDatasetId, message, t]);

  const refreshCatalog = useCallback(() => {
    const request = ++catalogRequestRef.current;
    setCatalogState("loading");
    setCatalogError(null);
    ipc.request<GenerationCatalog>("generation.catalog", {}).then((value) => {
      if (request !== catalogRequestRef.current) return;
      setCatalog(value);
      setCatalogState("ready");
    }).catch((error) => {
      if (request !== catalogRequestRef.current) return;
      setCatalog(null);
      setCatalogState("failed");
      setCatalogError(messageFromError(error, translateRef.current("Could not load the expansion catalog")));
    });
  }, []);

  useEffect(() => refreshCatalog(), [refreshCatalog]);

  const datasetId = activeDatasetId;

  const refreshViews = useCallback(() => {
    const request = ++viewsRequestRef.current;
    if (!datasetId) {
      setViews([]);
      setViewsState({ datasetId: null, status: "ready", error: null });
      return;
    }
    setViewsState({ datasetId, status: "loading", error: null });
    ipc
      .request<DatasetView[]>("dataset.view.list", { dataset_id: datasetId })
      .then((rows) => {
        if (request !== viewsRequestRef.current || useWorkspace.getState().activeDatasetId !== datasetId) return;
        setViews(rows);
        setViewsState({ datasetId, status: "ready", error: null });
      }).catch((error) => {
        if (request !== viewsRequestRef.current || useWorkspace.getState().activeDatasetId !== datasetId) return;
        setViewsState({ datasetId, status: "failed", error: messageFromError(error, translateRef.current("Could not load dataset views")) });
      });
  }, [datasetId]);

  useEffect(() => {
    void refreshViews();
    const onViewsChanged = () => refreshViews();
    window.addEventListener("dataset-views-changed", onViewsChanged);
    return () => {
      viewsRequestRef.current += 1;
      window.removeEventListener("dataset-views-changed", onViewsChanged);
    };
  }, [refreshViews]);

  useEffect(() => {
    if (!datasetId || viewsState.datasetId !== datasetId || viewsState.status !== "ready") return;
    const available = new Set(views.filter((view) => !view.stale).map((view) => view.id));
    const { config, updateConfig } = useGenerationStore.getState();
    if (config.source.seedViewId && !available.has(config.source.seedViewId)) {
      updateConfig((current) => ({ ...current, source: { ...current.source, seedViewId: null } }));
    }
  }, [datasetId, views, viewsState]);

  const refreshRuns = useCallback(() => {
    const request = ++runsRequestRef.current;
    if (!datasetId) {
      setRuns([]);
      setAllRuns([]);
      setRunsDatasetId(null);
      setRunsState("ready");
      return;
    }
    setRunsDatasetId(null);
    setRunsState("loading");
    setRuns([]);
    setAllRuns([]);
    ipc
      .request<RunRow[]>("result.list", { dataset_id: datasetId })
      .then((rows) => {
        if (request !== runsRequestRef.current || useWorkspace.getState().activeDatasetId !== datasetId) return;
        setAllRuns(rows);
        setRunsDatasetId(datasetId);
        const completed = rows.filter(
          (r) => r.status === "COMPLETED" && r.device !== "imported" && r.device !== "external",
        );
        setRuns(completed);
        setRunsState("ready");
        useGenerationStore.getState().updateConfig((c) =>
          c.source.descriptorRunId && completed.some((r) => r.id === c.source.descriptorRunId)
            ? c
            : { ...c, source: { ...c.source, descriptorRunId: completed[0]?.id ?? null } },
        );
      })
      .catch(() => {
        if (request !== runsRequestRef.current || useWorkspace.getState().activeDatasetId !== datasetId) return;
        setRuns([]);
        setAllRuns([]);
        setRunsDatasetId(datasetId);
        setRunsState("failed");
      });
  }, [datasetId]);

  useEffect(() => refreshRuns(), [refreshRuns]);

  const refreshHistory = useCallback(() => {
    const request = ++historyRequestRef.current;
    if (!datasetId) {
      useGenerationStore.getState().setHistory([]);
      useGenerationStore.getState().setHistoryStatus("ready");
      historyDatasetRef.current = null;
      return;
    }
    if (historyDatasetRef.current !== datasetId) {
      historyDatasetRef.current = datasetId;
      useGenerationStore.getState().setHistory([]);
    }
    useGenerationStore.getState().setHistoryStatus("loading");
    ipc
      .request<GenerationRow[]>("generation.list", { dataset_id: datasetId })
      .then((rows) => {
        if (request !== historyRequestRef.current || useWorkspace.getState().activeDatasetId !== datasetId) return;
        useGenerationStore.getState().setHistory(rows);
        useGenerationStore.getState().setHistoryStatus("ready");
      })
      .catch((error) => {
        if (request !== historyRequestRef.current || useWorkspace.getState().activeDatasetId !== datasetId) return;
        useGenerationStore.getState().setHistoryStatus("failed", messageFromError(error, translateRef.current("Could not refresh expansion history")));
      });
  }, [datasetId]);

  useEffect(refreshHistory, [refreshHistory]);

  const stopPolling = useCallback(() => {
    pollVersionRef.current += 1;
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (id: string) => {
      stopPolling();
      const pollVersion = pollVersionRef.current;
      let requestInFlight = false;
      pollRef.current = setInterval(() => {
        if (requestInFlight) return;
        requestInFlight = true;
        ipc
          .request<GenerationRow>("generation.get", { id })
          .then((row) => {
            const state = useGenerationStore.getState();
            if (pollVersionRef.current !== pollVersion || state.activeGenerationId !== id) return;
            state.setLiveRow(row);
            setLastUpdatedAt(Date.now());
            setConnectionError(null);
            setRunLoadError(null);
            if (row.status === "COMPLETED") {
              setCancelPending(false);
              stopPolling();
              if (state.phase !== "config") state.setPhase("results");
              refreshHistory();
            } else if (row.status === "FAILED" || row.status === "CANCELLED") {
              setCancelPending(false);
              stopPolling();
              if (state.phase !== "config") state.setPhase("results");
              refreshHistory();
            }
          })
          .catch((error) => {
            if (pollVersionRef.current !== pollVersion) return;
            setConnectionError(messageFromError(error, t("Could not refresh the expansion status")));
          })
          .finally(() => {
            requestInFlight = false;
          });
      }, 1000);
    },
    [refreshHistory, stopPolling, t],
  );

  useEffect(
    () => () => {
      openRequestRef.current += 1;
      stopPolling();
    },
    [stopPolling],
  );

  const runExpansion = useCallback(async () => {
    const config = useGenerationStore.getState().config;
    if (viewsState.datasetId !== activeDatasetId || viewsState.status !== "ready") {
      message.warning(t("Verify the seed scope before submitting"));
      return;
    }
    if (catalogState !== "ready") {
      message.warning(t("Wait for the expansion catalog to load"));
      return;
    }
    if (
      config.source.datasetId !== activeDatasetId ||
      !runs.some((run) => run.id === config.source.descriptorRunId)
    ) {
      message.warning(t("No completed descriptor runs for this dataset"));
      return;
    }
    const check = validateConfig(config);
    if (!check.ok) {
      message.warning(t(check.reason ?? "Configure the expansion first"));
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = await ipc.request<{ generation_id: string; job_id: string | null; cached: boolean }>(
        "generation.submit",
        buildSubmitPayload(config),
      );
      const state = useGenerationStore.getState();
      state.setActiveGeneration(result.generation_id);
      state.setLiveRow(null);
      setCancelPending(false);
      setConnectionError(null);
      setRunLoadError(null);
      setRunLoading(false);
      setLastUpdatedAt(null);
      state.setPhase("running");
      startPolling(result.generation_id);
    } catch (error) {
      console.error(error);
      const detail = messageFromError(error, t("Failed to submit the expansion run"));
      setSubmitError(detail);
      message.error(detail);
    } finally {
      setSubmitting(false);
    }
  }, [activeDatasetId, catalogState, runs, viewsState, startPolling, message, t]);

  const openRun = useCallback(
    (id: string) => {
      stopPolling();
      const request = ++openRequestRef.current;
      const phaseIntent = ++phaseIntentRef.current;
      setCancelPending(false);
      setRunLoading(true);
      setRunLoadError(null);
      setConnectionError(null);
      setLastUpdatedAt(null);
      useGenerationStore.getState().setActiveGeneration(id);
      useGenerationStore.getState().setLiveRow(null);
      useGenerationStore.getState().setPhase("running");
      ipc
        .request<GenerationRow>("generation.get", { id })
        .then((row) => {
          const state = useGenerationStore.getState();
          if (request !== openRequestRef.current || state.activeGenerationId !== id) return;
          state.setLiveRow(row);
          setRunLoading(false);
          setLastUpdatedAt(Date.now());
          if (phaseIntentRef.current === phaseIntent && state.phase !== "config") {
            state.setPhase(row.status === "RUNNING" || row.status === "QUEUED" ? "running" : "results");
          }
          if (row.status === "RUNNING" || row.status === "QUEUED") startPolling(id);
        })
        .catch((error) => {
          const state = useGenerationStore.getState();
          if (request !== openRequestRef.current || state.activeGenerationId !== id) return;
          setRunLoading(false);
          setRunLoadError(messageFromError(error, t("Could not load this expansion run")));
        });
    },
    [startPolling, stopPolling, t],
  );

  const returnToConfig = useCallback(() => {
    phaseIntentRef.current += 1;
    useGenerationStore.getState().setPhase("config");
  }, []);

  useEffect(() => {
    const onOpen = (event: Event) => {
      const id = (event as CustomEvent<string>).detail;
      if (id) openRun(id);
    };
    window.addEventListener("generation-open", onOpen);
    return () => window.removeEventListener("generation-open", onOpen);
  }, [openRun]);

  useEffect(() => {
    const onHistoryChanged = () => refreshHistory();
    window.addEventListener("generation-history-changed", onHistoryChanged);
    return () => window.removeEventListener("generation-history-changed", onHistoryChanged);
  }, [refreshHistory]);

  useEffect(() => {
    if (restoreAttemptedRef.current) return;
    restoreAttemptedRef.current = true;
    const state = useGenerationStore.getState();
    if (state.activeGenerationId && state.phase !== "config") openRun(state.activeGenerationId);
    // Restore once per page mount. Locale and workspace changes must not steal
    // navigation or restart polling for the active run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const cancelRun = useCallback(async () => {
    if (!store.activeGenerationId) return;
    try {
      const result = await ipc.request<{ already_finished?: boolean }>("generation.cancel", { id: store.activeGenerationId });
      if (!result.already_finished) setCancelPending(true);
    } catch (error) {
      console.error(error);
      setCancelPending(false);
      message.error(messageFromError(error, t("Failed to cancel the expansion run")));
    }
  }, [store.activeGenerationId, message, t]);

  const row = store.liveRow;
  const runProblem = runLoadError ?? connectionError;
  const descriptorState = !datasetId
    ? "noDataset"
    : runsDatasetId !== datasetId || runsState === "loading"
      ? "loading"
      : runsState === "failed"
        ? "failed"
        : runs.length > 0
          ? "ready"
          : allRuns.some((run) => run.status === "STALE")
            ? "stale"
            : "missing";
  const sourceDatasetName = row ? datasets.find((dataset) => dataset.id === row.dataset_id)?.name ?? row.dataset_id : null;
  const sourceDatasetMismatch = Boolean(row && activeDatasetId && row.dataset_id !== activeDatasetId);

  return (
    <div style={{ width: "100%", height: store.phase === "config" ? "100%" : "auto", minHeight: 0, paddingBottom: store.phase === "config" ? 0 : 32, display: store.phase === "config" ? "flex" : "block", flexDirection: "column" }}>
      <div style={{ marginBottom: 4, flex: "0 0 auto" }}>
        <div style={{ fontSize: 20, fontWeight: 600, color: "#242424" }}>{t("DATASET EXPANSION")}</div>
        <div style={{ fontSize: 13, color: "#616161" }}>
          {t("Explore structure space guided by descriptor diversity")}
        </div>
      </div>

      {store.phase !== "config" && (
        <Button
          icon={<ArrowLeft16Regular />}
          style={{ margin: "8px 0" }}
          onClick={() => {
            returnToConfig();
          }}
        >
          {t("Back to configuration")}
        </Button>
      )}

      {store.phase === "config" && (
        <>
          {store.activeGenerationId && (
            <Alert
              type={row?.status === "FAILED" ? "error" : row?.status === "CANCELLED" ? "warning" : row?.status === "COMPLETED" ? "success" : "info"}
              showIcon
              style={{ margin: "8px 0" }}
              message={row?.status === "FAILED"
                ? t("The current expansion failed while you were configuring")
                : row?.status === "CANCELLED"
                  ? t("The current expansion was cancelled while you were configuring")
                  : row?.status === "COMPLETED"
                    ? t("The current expansion finished while you were configuring")
                    : t("The current expansion continues in the background")}
              description={row
                ? `${t("Run {id} · source dataset {dataset}", { id: row.id, dataset: sourceDatasetName ?? row.dataset_id })}${runProblem ? ` · ${runProblem}` : ""}`
                : `${runProblem ?? t("Run {id} is being loaded or monitored", { id: store.activeGenerationId })}`}
              action={<Button size="small" onClick={() => {
                if (row) store.setPhase(row.status === "QUEUED" || row.status === "RUNNING" ? "running" : "results");
                else openRun(store.activeGenerationId!);
              }}>{t("Return to current run")}</Button>}
            />
          )}
          <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
          <GenerationConfigPanel
            datasetId={datasetId}
            activeDatasetName={activeDatasetName}
            datasetFrameCount={datasets.find((dataset) => dataset.id === datasetId)?.number_of_frames ?? null}
            runs={runsDatasetId === datasetId ? runs : []}
            views={views.filter((view) => view.dataset_id === datasetId)}
            viewsState={viewsState.datasetId === datasetId ? viewsState.status : "loading"}
            viewsError={viewsState.datasetId === datasetId ? viewsState.error : null}
            onRetryViews={refreshViews}
            catalog={catalog}
            descriptorState={descriptorState}
            catalogFailed={catalogState === "failed"}
            catalogLoading={catalogState === "loading"}
            catalogError={catalogError}
            onRetryCatalog={refreshCatalog}
            onRetryDescriptors={refreshRuns}
            submitError={submitError}
            onRun={runExpansion}
            running={submitting}
            onDismissSubmitError={() => setSubmitError(null)}
          />
          </div>
        </>
      )}
      {store.phase === "running" && !row && (
        <Alert
          type={runProblem ? "error" : "info"}
          showIcon
          style={{ marginTop: 8 }}
          message={runProblem ? t("Could not refresh the expansion status") : runLoading ? t("Loading expansion status") : t("Expansion submitted; waiting for queue and initialization status")}
          description={runProblem ?? (store.activeGenerationId ? t("Run {id}", { id: store.activeGenerationId }) : t("Waiting for the backend to create the run record"))}
          action={runProblem && store.activeGenerationId
            ? <Button size="small" onClick={() => openRun(store.activeGenerationId!)}>{t("Retry")}</Button>
            : <Spin size="small" />}
        />
      )}
      {store.phase === "running" && row && (
        <>
          {sourceDatasetMismatch && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={t("This run belongs to a different dataset")}
            description={t("Run {id} uses source dataset {dataset}; the active workspace dataset is {active}", {
              id: row.id, dataset: sourceDatasetName ?? row.dataset_id, active: activeDatasetName ?? t("No datasets"),
            })} />}
          <GenerationRunPanel row={row} onCancel={cancelRun} cancelPending={cancelPending}
            lastUpdatedAt={lastUpdatedAt} connectionError={connectionError} onRetry={() => openRun(row.id)} sourceDatasetName={sourceDatasetName} />
        </>
      )}
      {store.phase === "results" && row && (
        <>
          {sourceDatasetMismatch && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={t("This run belongs to a different dataset")}
            description={t("Run {id} uses source dataset {dataset}; the active workspace dataset is {active}", {
              id: row.id, dataset: sourceDatasetName ?? row.dataset_id, active: activeDatasetName ?? t("No datasets"),
            })} />}
          <GenerationResultsPanel row={row} onMaterialized={refreshHistory} onBackToConfig={returnToConfig} />
        </>
      )}
    </div>
  );
}

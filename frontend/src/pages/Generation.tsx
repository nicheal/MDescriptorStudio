// Descriptor-guided dataset expansion: one page, three phases.
//   config  — the six-section workflow form
//   running — live metrics + convergence curves polled from generation.get
//   results — summary, curves, materialize/export actions
import { useCallback, useEffect, useRef, useState } from "react";
import { App as AntApp, Button } from "antd";
import { ArrowLeft16Regular } from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { useWorkspace } from "../stores/workspace";
import { useGenerationStore } from "../features/generation/generationStore";
import { buildSubmitPayload, validateConfig } from "../features/generation/submission";
import type { GenerationCatalog, GenerationRow } from "../features/generation/types";
import type { DatasetView, RunRow } from "../types/protocol";
import { useT } from "../i18n";
import GenerationConfigPanel from "./generationConfig";
import GenerationRunPanel from "./generationRun";
import GenerationResultsPanel from "./generationResults";

export default function Generation() {
  const { message } = AntApp.useApp();
  const { t } = useT();
  const datasets = useWorkspace((s) => s.datasets);
  const activeDatasetId = useWorkspace((s) => s.activeDatasetId);
  const activeDatasetName = datasets.find((d) => d.id === activeDatasetId)?.name ?? null;
  const store = useGenerationStore();
  const [catalog, setCatalog] = useState<GenerationCatalog | null>(null);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [views, setViews] = useState<DatasetView[]>([]);
  const [viewsDatasetId, setViewsDatasetId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [cancelPending, setCancelPending] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollVersionRef = useRef(0);
  const viewsRequestRef = useRef(0);
  const openRequestRef = useRef(0);

  // Generation always follows the workspace's active dataset.
  useEffect(() => {
    const { config, updateConfig } = useGenerationStore.getState();
    if (config.source.datasetId !== activeDatasetId) {
      updateConfig((c) => ({
        ...c,
        source: { ...c.source, datasetId: activeDatasetId, descriptorRunId: null, seedViewId: null },
      }));
    }
  }, [activeDatasetId]);

  useEffect(() => {
    ipc.request<GenerationCatalog>("generation.catalog", {}).then(setCatalog).catch(() => setCatalog(null));
  }, []);

  const datasetId = activeDatasetId;

  const refreshViews = useCallback(() => {
    const request = ++viewsRequestRef.current;
    if (!datasetId) {
      setViews([]);
      setViewsDatasetId(null);
      return;
    }
    ipc
      .request<DatasetView[]>("dataset.view.list", { dataset_id: datasetId })
      .then((rows) => {
        if (request !== viewsRequestRef.current) return;
        setViews(rows);
        setViewsDatasetId(datasetId);
      })
      .catch(() => {
        if (request !== viewsRequestRef.current) return;
        setViews([]);
        setViewsDatasetId(datasetId);
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
    if (!datasetId || viewsDatasetId !== datasetId) return;
    const available = new Set(views.filter((view) => !view.stale).map((view) => view.id));
    const { config, updateConfig } = useGenerationStore.getState();
    if (config.source.seedViewId && !available.has(config.source.seedViewId)) {
      updateConfig((current) => ({ ...current, source: { ...current.source, seedViewId: null } }));
    }
  }, [datasetId, views, viewsDatasetId]);

  useEffect(() => {
    let cancelled = false;
    setRuns([]);
    if (!datasetId) {
      return;
    }
    ipc
      .request<RunRow[]>("result.list", { dataset_id: datasetId })
      .then((rows) => {
        if (cancelled) return;
        const completed = rows.filter(
          (r) => r.status === "COMPLETED" && r.device !== "imported" && r.device !== "external",
        );
        setRuns(completed);
        store.updateConfig((c) =>
          c.source.descriptorRunId && completed.some((r) => r.id === c.source.descriptorRunId)
            ? c
            : { ...c, source: { ...c.source, descriptorRunId: completed[0]?.id ?? null } },
        );
      })
      .catch(() => !cancelled && setRuns([]));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetId]);

  const refreshHistory = useCallback(() => {
    if (!datasetId) {
      useGenerationStore.getState().setHistory([]);
      return;
    }
    ipc
      .request<GenerationRow[]>("generation.list", { dataset_id: datasetId })
      .then((rows) => useGenerationStore.getState().setHistory(rows))
      .catch(() => undefined);
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
            if (row.status === "COMPLETED") {
              setCancelPending(false);
              stopPolling();
              state.setPhase("results");
              refreshHistory();
            } else if (row.status === "FAILED" || row.status === "CANCELLED") {
              setCancelPending(false);
              stopPolling();
              state.setPhase("results");
              refreshHistory();
            }
          })
          .catch(() => undefined)
          .finally(() => {
            requestInFlight = false;
          });
      }, 1000);
    },
    [refreshHistory, stopPolling],
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
    try {
      const result = await ipc.request<{ generation_id: string; job_id: string | null; cached: boolean }>(
        "generation.submit",
        buildSubmitPayload(config),
      );
      const state = useGenerationStore.getState();
      state.setActiveGeneration(result.generation_id);
      state.setLiveRow(null);
      setCancelPending(false);
      state.setPhase("running");
      startPolling(result.generation_id);
    } catch (error) {
      console.error(error);
      message.error(t("Failed to submit the expansion run"));
    } finally {
      setSubmitting(false);
    }
  }, [activeDatasetId, runs, startPolling, message, t]);

  const openRun = useCallback(
    (id: string) => {
      stopPolling();
      const request = ++openRequestRef.current;
      setCancelPending(false);
      useGenerationStore.getState().setActiveGeneration(id);
      useGenerationStore.getState().setLiveRow(null);
      ipc
        .request<GenerationRow>("generation.get", { id })
        .then((row) => {
          const state = useGenerationStore.getState();
          if (request !== openRequestRef.current || state.activeGenerationId !== id) return;
          state.setLiveRow(row);
          state.setPhase(row.status === "RUNNING" || row.status === "QUEUED" ? "running" : "results");
          if (row.status === "RUNNING" || row.status === "QUEUED") startPolling(id);
        })
        .catch(() => {
          const state = useGenerationStore.getState();
          if (request !== openRequestRef.current || state.activeGenerationId !== id) return;
          state.setPhase("config");
        });
    },
    [startPolling, stopPolling],
  );

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
    const state = useGenerationStore.getState();
    if (state.activeGenerationId && state.phase !== "config") openRun(state.activeGenerationId);
  }, [openRun]);

  const cancelRun = useCallback(async () => {
    if (!store.activeGenerationId) return;
    try {
      const result = await ipc.request<{ already_finished?: boolean }>("generation.cancel", { id: store.activeGenerationId });
      if (!result.already_finished) setCancelPending(true);
    } catch (error) {
      console.error(error);
      setCancelPending(false);
      message.error(t("Failed to cancel the expansion run"));
    }
  }, [store.activeGenerationId, message, t]);

  const row = store.liveRow;

  return (
    <div style={{ width: "100%", paddingBottom: 32 }}>
      <div style={{ marginBottom: 4 }}>
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
            stopPolling();
            openRequestRef.current += 1;
            store.setActiveGeneration(null);
            store.setLiveRow(null);
            store.setPhase("config");
            refreshHistory();
          }}
        >
          {t("Back to configuration")}
        </Button>
      )}

      {store.phase === "config" && (
        <>
          <GenerationConfigPanel
            activeDatasetName={activeDatasetName}
            runs={runs}
            views={views.filter((view) => view.dataset_id === datasetId && !view.stale)}
            catalog={catalog}
            onRun={runExpansion}
            running={submitting}
          />
        </>
      )}
      {store.phase === "running" && row && <GenerationRunPanel row={row} onCancel={cancelRun} cancelPending={cancelPending} />}
      {store.phase === "results" && row && (
        <GenerationResultsPanel row={row} onMaterialized={refreshHistory} />
      )}
    </div>
  );
}

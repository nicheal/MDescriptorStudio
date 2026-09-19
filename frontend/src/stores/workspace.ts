// Global workspace state (design doc §15, ADR-17 persistence rule).
import { create } from "zustand";
import { ipc } from "../ipc/client";
import type { DatasetMeta } from "../types/protocol";

export type BackendStatus = "starting" | "ready" | "error";
export type Page = "overview" | "explore" | "descriptors" | "results" | "analysis";
export type PcaMode = "structure" | "atom";

export interface SelectedSample {
  datasetId: string;
  /** Descriptor run the selection came from; absent for plain browse clicks. */
  runId?: string;
  mode: PcaMode;
  frame: number;
  atom?: number;
}

interface WorkspaceState {
  backendStatus: BackendStatus;
  engineVersion: string | null;
  cpuThreads: number | null;
  activeDatasetId: string | null;
  activeFrameIndex: number;
  activeDescriptorRunId: string | null;
  selectedSample: SelectedSample | null;
  datasets: DatasetMeta[];
  runningJobs: number;
  page: Page;
  // top-right Jobs drawer (the single jobs surface since the Jobs tab was folded into it)
  jobsDrawerOpen: boolean;
  // data-health findings drawer; findingsCheck selects the active check tab
  // (null = all checks) when opened from a health-rail row
  findingsDrawerOpen: boolean;
  findingsCheck: string | null;
  // bumped after a health rescan completes → pages refetch dataset statistics
  statsTick: number;

  setBackendReady: (engineVersion: string | null, cpuThreads?: number | null) => void;
  setBackendStarting: () => void;
  setBackendError: () => void;
  setDatasets: (datasets: DatasetMeta[]) => void;
  setActiveDataset: (id: string | null) => void;
  setActiveFrame: (index: number) => void;
  setActiveRun: (id: string | null) => void;
  setSelectedSample: (sample: SelectedSample | null) => void;
  setRunningJobs: (n: number) => void;
  setPage: (p: Page) => void;
  setJobsDrawerOpen: (open: boolean) => void;
  /** Opens the data-health findings drawer, optionally focused on one check. */
  openFindings: (check?: string | null) => void;
  closeFindings: () => void;
  bumpStatsTick: () => void;
}

export const useWorkspace = create<WorkspaceState>((set) => ({
  backendStatus: "starting",
  engineVersion: null,
  cpuThreads: null,
  activeDatasetId: null,
  activeFrameIndex: 0,
  activeDescriptorRunId: null,
  selectedSample: null,
  datasets: [],
  runningJobs: 0,
  page: "overview",
  jobsDrawerOpen: false,
  findingsDrawerOpen: false,
  findingsCheck: null,
  statsTick: 0,

  setBackendReady: (engineVersion, cpuThreads) =>
    set((st) => ({ backendStatus: "ready", engineVersion, cpuThreads: cpuThreads ?? st.cpuThreads })),
  setBackendStarting: () => set({ backendStatus: "starting", engineVersion: null }),
  setBackendError: () => set({ backendStatus: "error" }),
  setDatasets: (datasets) => set({ datasets }),
  setActiveDataset: (id) =>
    set((st) => {
      if (st.activeDatasetId !== id && id) {
        void ipc
          .request("settings.set", {
            key: "workspace.activeDatasetId",
            value: id,
          })
          // The write is a persistence nicety; with the backend down it must
          // not surface as an unhandled rejection (see setActiveRun above).
          .catch(() => {});
      }
      return { activeDatasetId: id, activeFrameIndex: 0, activeDescriptorRunId: null, selectedSample: null };
    }),
  setActiveFrame: (index) => set({ activeFrameIndex: index }),
  setActiveRun: (id) =>
    set((st) => {
      if (st.activeDescriptorRunId === id) return st;
      if (id) {
        void ipc.request("settings.set", { key: "workspace.activeDescriptorRunId", value: id }).catch(() => {});
      }
      return { activeDescriptorRunId: id, selectedSample: null };
    }),
  setSelectedSample: (sample) => set({ selectedSample: sample }),
  setRunningJobs: (n) => set({ runningJobs: n }),
  setPage: (p) => set({ page: p }),
  setJobsDrawerOpen: (open) => set({ jobsDrawerOpen: open }),
  openFindings: (check) => set({ findingsDrawerOpen: true, findingsCheck: check ?? null }),
  closeFindings: () => set({ findingsDrawerOpen: false }),
  bumpStatsTick: () => set((st) => ({ statsTick: st.statsTick + 1 })),
}));

/**
 * The active dataset, subscribed field by field.
 *
 * A selector that returns the dataset object itself invites reading the whole
 * store to find it, which makes the caller re-render on every unrelated write -
 * including the job.progress ticks that fire many times a second while a
 * descriptor run is going. Take the two fields instead.
 */
export function useActiveDataset(): DatasetMeta | undefined {
  const datasets = useWorkspace((st) => st.datasets);
  const activeDatasetId = useWorkspace((st) => st.activeDatasetId);
  return datasets.find((d) => d.id === activeDatasetId);
}

/**
 * Restore the persisted active descriptor run before the pages render
 * (backend.ready path). Analysis.refresh validates it against the completed
 * runs and falls back to the first one when it no longer exists.
 */
export async function hydrateActiveRun(): Promise<void> {
  try {
    const r = await ipc.request<{ value: string | null }>("settings.get", {
      key: "workspace.activeDescriptorRunId",
    });
    if (typeof r.value === "string" && r.value) {
      useWorkspace.setState({ activeDescriptorRunId: r.value });
    }
  } catch {
    /* backend not reachable yet — keep the default */
  }
}

/** Re-pull dataset.list; falls back to the first dataset when the active one is gone. */
export async function refetchDatasets(): Promise<void> {
  const list = await ipc.request<DatasetMeta[]>("dataset.list");
  const st = useWorkspace.getState();
  st.setDatasets(list);
  if (st.activeDatasetId && !list.some((d) => d.id === st.activeDatasetId)) {
    st.setActiveDataset(list[0]?.id ?? null);
  }
}

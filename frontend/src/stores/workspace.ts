// Global workspace state (design doc §15, ADR-17 persistence rule).
import { create } from "zustand";
import { ipc } from "../ipc/client";
import type { DatasetMeta } from "../types/protocol";

export type BackendStatus = "starting" | "ready" | "error";
export type Page = "overview" | "explore" | "descriptors" | "results";
export type PcaMode = "structure" | "atom";

export interface SelectedSample {
  datasetId: string;
  runId: string;
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
  statsTick: 0,

  setBackendReady: (engineVersion, cpuThreads) =>
    set((st) => ({ backendStatus: "ready", engineVersion, cpuThreads: cpuThreads ?? st.cpuThreads })),
  setBackendStarting: () => set({ backendStatus: "starting", engineVersion: null }),
  setBackendError: () => set({ backendStatus: "error" }),
  setDatasets: (datasets) => set({ datasets }),
  setActiveDataset: (id) =>
    set((st) => {
      if (st.activeDatasetId !== id && id) {
        void ipc.request("settings.set", {
          key: "workspace.activeDatasetId",
          value: id,
        });
      }
      return { activeDatasetId: id, activeFrameIndex: 0, activeDescriptorRunId: null, selectedSample: null };
    }),
  setActiveFrame: (index) => set({ activeFrameIndex: index }),
  setActiveRun: (id) =>
    set((st) =>
      st.activeDescriptorRunId === id ? st : { activeDescriptorRunId: id, selectedSample: null },
    ),
  setSelectedSample: (sample) => set({ selectedSample: sample }),
  setRunningJobs: (n) => set({ runningJobs: n }),
  setPage: (p) => set({ page: p }),
  setJobsDrawerOpen: (open) => set({ jobsDrawerOpen: open }),
  bumpStatsTick: () => set((st) => ({ statsTick: st.statsTick + 1 })),
}));

export const activeDataset = (st: WorkspaceState): DatasetMeta | undefined =>
  st.datasets.find((d) => d.id === st.activeDatasetId);

/** Re-pull dataset.list; falls back to the first dataset when the active one is gone. */
export async function refetchDatasets(): Promise<void> {
  const list = await ipc.request<DatasetMeta[]>("dataset.list");
  const st = useWorkspace.getState();
  st.setDatasets(list);
  if (st.activeDatasetId && !list.some((d) => d.id === st.activeDatasetId)) {
    st.setActiveDataset(list[0]?.id ?? null);
  }
}

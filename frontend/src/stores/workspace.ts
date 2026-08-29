// Global workspace state (design doc §15, ADR-17 persistence rule).
import { create } from "zustand";
import { ipc } from "../ipc/client";
import type { DatasetMeta } from "../types/protocol";

export type BackendStatus = "starting" | "ready" | "error";

interface WorkspaceState {
  backendStatus: BackendStatus;
  engineVersion: string | null;
  cpuThreads: number | null;
  activeDatasetId: string | null;
  activeFrameIndex: number;
  activeDescriptorRunId: string | null;
  datasets: DatasetMeta[];
  runningJobs: number;

  setBackendReady: (engineVersion: string | null) => void;
  setBackendError: () => void;
  setDatasets: (datasets: DatasetMeta[]) => void;
  setActiveDataset: (id: string | null) => void;
  setActiveFrame: (index: number) => void;
  setActiveRun: (id: string | null) => void;
  setRunningJobs: (n: number) => void;
}

export const useWorkspace = create<WorkspaceState>((set) => ({
  backendStatus: "starting",
  engineVersion: null,
  cpuThreads: null,
  activeDatasetId: null,
  activeFrameIndex: 0,
  activeDescriptorRunId: null,
  datasets: [],
  runningJobs: 0,

  setBackendReady: (engineVersion) => set({ backendStatus: "ready", engineVersion }),
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
      return { activeDatasetId: id, activeFrameIndex: 0, activeDescriptorRunId: null };
    }),
  setActiveFrame: (index) => set({ activeFrameIndex: index }),
  setActiveRun: (id) => set({ activeDescriptorRunId: id }),
  setRunningJobs: (n) => set({ runningJobs: n }),
}));

export const activeDataset = (st: WorkspaceState): DatasetMeta | undefined =>
  st.datasets.find((d) => d.id === st.activeDatasetId);

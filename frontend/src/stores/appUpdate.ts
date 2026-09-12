import { getVersion } from "@tauri-apps/api/app";
import { check, type DownloadEvent, type Update } from "@tauri-apps/plugin-updater";
import { create } from "zustand";

export type AppUpdateStatus =
  | "idle"
  | "checking"
  | "up_to_date"
  | "available"
  | "installing"
  | "installed"
  | "error";

export interface AppUpdateState {
  current: string;
  latest: string | null;
  notes: string | null;
  status: AppUpdateStatus;
  progress: number | null;
  error: string | null;
}

interface Store extends AppUpdateState {
  update: Update | null;
  refresh: () => Promise<void>;
  install: () => Promise<void>;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "Application update failed";
}

export const useAppUpdate = create<Store>((set, get) => ({
  current: "",
  latest: null,
  notes: null,
  status: "idle",
  progress: null,
  error: null,
  update: null,

  refresh: async () => {
    const status = get().status;
    if (status === "checking" || status === "installing") return;
    set({ status: "checking", error: null, progress: null });
    try {
      const current = await getVersion();
      const update = await check();
      if (!update) {
        set({ current, latest: null, notes: null, update: null, status: "up_to_date", progress: null });
        return;
      }
      set({
        current: update.currentVersion || current,
        latest: update.version,
        notes: update.body?.trim() || null,
        update,
        status: "available",
        progress: null,
      });
    } catch (error) {
      set({ status: "error", error: errorMessage(error), progress: null, update: null });
    }
  },

  install: async () => {
    const update = get().update;
    if (!update) return;
    set({ status: "installing", error: null, progress: 0 });
    let downloaded = 0;
    let contentLength: number | undefined;
    try {
      await update.downloadAndInstall((event: DownloadEvent) => {
        if (event.event === "Started") {
          contentLength = event.data.contentLength;
          set({ progress: contentLength ? 0 : null });
        } else if (event.event === "Progress") {
          downloaded += event.data.chunkLength;
          set({ progress: contentLength ? Math.min(100, Math.round((downloaded / contentLength) * 100)) : null });
        } else {
          set({ progress: 100 });
        }
      }, { restartAfterInstall: true });
      set({ status: "installed", progress: 100 });
    } catch (error) {
      set({ status: "error", error: errorMessage(error), progress: null });
    } finally {
      await update.close().catch(() => undefined);
      if (get().update === update) set({ update: null });
    }
  },
}));

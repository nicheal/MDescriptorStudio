// Engine update state (PyPI check at startup + in-venv upgrade; ADR-2 note).
import { create } from "zustand";
import { ipc } from "../ipc/client";
import { trackJob, watchJob } from "./jobs";

export type UpdateStatus =
  | "idle"
  | "checking"
  | "up_to_date"
  | "available"
  | "error"
  | "unsupported"
  | "updating"
  | "restart_required";

export interface EngineUpdateState {
  installed: string;
  latest: string | null;
  hasUpdate: boolean;
  status: UpdateStatus;
  error: string | null;
}

interface Store extends EngineUpdateState {
  refresh: () => Promise<void>;
  runUpdate: () => Promise<void>;
}

export const useEngineUpdate = create<Store>((set, get) => ({
  installed: "",
  latest: null,
  hasUpdate: false,
  status: "idle",
  error: null,

  refresh: async () => {
    const snap = await ipc.request<EngineUpdateState>("engine.check_update");
    set({ ...snap });
  },

  runUpdate: async () => {
    const target = get().latest;
    if (!target) return;
    set({ status: "updating", error: null });
    try {
      const r = await ipc.request<{ job_id: string }>("engine.update", { version: target });
      trackJob(r.job_id, "engine.update");
      const done = await watchJob(r.job_id);
      if (done.status === "COMPLETED") {
        set({ status: "restart_required", installed: target, hasUpdate: false });
      } else {
        set({ status: "error", error: done.error?.message ?? done.status });
      }
    } catch (e) {
      const err = e as { code: string; message: string };
      set({ status: "error", error: `${err.code}: ${err.message}` });
    }
  },
}));

let wired = false;
export function wireEngineUpdate() {
  if (wired) return;
  wired = true;
  ipc.on("engine.update.state", (data) => {
    const s = data as EngineUpdateState;
    const updating = useEngineUpdate.getState().status === "updating";
    useEngineUpdate.setState({
      ...s,
      status: updating && s.status !== "error" ? "updating" : s.status,
    });
  });
}

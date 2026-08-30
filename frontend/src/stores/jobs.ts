// Job registry fed by job.progress / job.finished events (docs/plan/02 §4).
import { create } from "zustand";
import { ipc } from "../ipc/client";
import type { JobRow } from "../types/protocol";

export interface JobState {
  id: string;
  job_type: string;
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
  progress: number;
  completed: number | null;
  total: number | null;
  message: string | null;
  error: { code: string; message: string } | null;
  created_at?: string;
}

export const JOB_TYPE_LABEL: Record<string, string> = {
  "dataset.register": "Dataset scan & statistics",
  "dataset.statistics": "Dataset statistics",
  "descriptor.compute": "Descriptor compute",
  "analysis.pca": "PCA",
  "analysis.umap": "UMAP",
  "analysis.tsne": "t-SNE",
  "analysis.neighbors": "Nearest neighbors",
  "analysis.similarity": "Similarity",
  "analysis.kmeans": "K-Means",
  "analysis.dbscan": "DBSCAN",
  "analysis.hdbscan": "HDBSCAN",
  "analysis.agglomerative": "Agglomerative",
  "analysis.analysis": "Analysis",
  "analysis.export": "Analysis export",
};

export const JOB_STATUS_COLOR: Record<JobState["status"], string> = {
  QUEUED: "#616161",
  RUNNING: "#0F6CBD",
  COMPLETED: "#107C10",
  FAILED: "#C42B1C",
  CANCELLED: "#8A8A8A",
};

interface JobsStore {
  jobs: Record<string, JobState>;
  order: string[]; // newest first
  setRunning: (n: number) => void;
}

export const useJobs = create<JobsStore>(() => ({ jobs: {}, order: [], setRunning: () => {} }));

export function trackJob(jobId: string, jobType: string) {
  useJobs.setState((st) => ({
    jobs: {
      ...st.jobs,
      [jobId]: {
        id: jobId,
        job_type: jobType,
        status: "QUEUED",
        progress: 0,
        completed: null,
        total: null,
        message: null,
        error: null,
        created_at: new Date().toISOString(),
      },
    },
    order: [jobId, ...st.order.filter((j) => j !== jobId)],
  }));
}

export function watchJob(jobId: string): Promise<{
  status: string;
  result: Record<string, unknown> | null;
  error: JobState["error"];
}> {
  return new Promise((resolve) => {
    const off = ipc.on("job.finished", (data) => {
      const d = data as { job_id: string; status: string; result: Record<string, unknown> | null; error: JobState["error"] };
      if (d.job_id !== jobId) return;
      off();
      resolve(d);
    });
  });
}

let wired = false;
export function wireJobEvents(setRunning: (n: number) => void) {
  if (wired) return;
  wired = true;
  ipc.on("job.progress", (data) => {
    const d = data as { job_id: string; progress: number; completed: number; total: number; message: string | null };
    useJobs.setState((st) => {
      const cur = st.jobs[d.job_id];
      if (!cur) return st;
      return {
        jobs: {
          ...st.jobs,
          [d.job_id]: { ...cur, status: "RUNNING", progress: d.progress, completed: d.completed, total: d.total, message: d.message },
        },
      };
    });
  });
  ipc.on("job.finished", (data) => {
    const d = data as { job_id: string; status: JobState["status"]; result: Record<string, unknown> | null; error: JobState["error"] };
    useJobs.setState((st) => {
      const cur = st.jobs[d.job_id];
      if (!cur) return st;
      return {
        jobs: {
          ...st.jobs,
          [d.job_id]: { ...cur, status: d.status, progress: d.status === "COMPLETED" ? 1 : cur.progress, error: d.error },
        },
      };
    });
  });
  ipc.on("job.progress", () => {
    const st = useJobs.getState();
    setRunning(Object.values(st.jobs).filter((j) => j.status === "RUNNING" || j.status === "QUEUED").length);
  });
  ipc.on("job.finished", () => {
    const st = useJobs.getState();
    setRunning(Object.values(st.jobs).filter((j) => j.status === "RUNNING" || j.status === "QUEUED").length);
  });
}

export function fromJobRow(row: JobRow): JobState {
  return {
    id: row.id,
    job_type: row.job_type,
    status: row.status,
    progress: row.progress,
    completed: row.completed,
    total: row.total,
    message: row.message,
    error: row.error ? { code: row.error, message: row.error } : null,
    created_at: row.created_at,
  };
}

// Live session jobs overlay the persisted job.list history (same id wins).
export function mergeJobRows(rows: JobRow[], live: JobState[]): JobState[] {
  const byId = new Map<string, JobState>();
  for (const row of rows) {
    const j = fromJobRow(row);
    byId.set(j.id, j);
  }
  for (const j of live) byId.set(j.id, { ...byId.get(j.id), ...j });
  return [...byId.values()].sort((a, b) =>
    (b.created_at ?? "").localeCompare(a.created_at ?? ""),
  );
}

// Job registry fed by job.progress / job.finished events (docs/plan/02 §4).
import { create } from "zustand";
import { ipc } from "../ipc/client";
import type { Pair } from "../i18n";
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

const JOB_TYPE_PAIRS: Record<string, Pair> = {
  "dataset.register": { en: "Dataset scan & statistics", zh: "数据集扫描与统计" },
  "dataset.statistics": { en: "Dataset statistics", zh: "数据集统计" },
  "descriptor.compute": { en: "Descriptor compute", zh: "描述符计算" },
  "analysis.pca": { en: "PCA", zh: "PCA" },
  "analysis.umap": { en: "UMAP", zh: "UMAP" },
  "analysis.tsne": { en: "t-SNE", zh: "t-SNE" },
  "analysis.neighbors": { en: "Nearest neighbors", zh: "最近邻" },
  "analysis.similarity": { en: "Similarity", zh: "相似度" },
  "analysis.pairwise": { en: "Pairwise similarity", zh: "两两相似度" },
  "analysis.cluster": { en: "Clustering", zh: "聚类" },
  "analysis.outlier": { en: "Outlier detection", zh: "离群点检测" },
  "analysis.sampling": { en: "Representative sampling", zh: "代表性采样" },
  "analysis.coverage": { en: "Dataset coverage", zh: "数据集覆盖度" },
  "analysis.overlap": { en: "Dataset overlap", zh: "数据集重叠" },
  "analysis.acquisition": { en: "Novelty acquisition", zh: "新颖性采样" },
  "analysis.compare": { en: "Descriptor comparison", zh: "描述符对比" },
  "analysis.feature_variance": { en: "Feature variance", zh: "特征方差" },
  "analysis.feature_correlation": { en: "Feature correlation", zh: "特征相关性" },
  "analysis.effective_dimension": { en: "Effective dimension", zh: "有效维度" },
  "analysis.property_correlation": { en: "Property correlation", zh: "属性相关性" },
  "analysis.local_diversity": { en: "Local diversity", zh: "局部多样性" },
  "analysis.kernel": { en: "Kernel diagnostics", zh: "核函数诊断" },
  "analysis.trajectory": { en: "Trajectory", zh: "轨迹" },
  "analysis.drift": { en: "Dataset drift", zh: "数据集漂移" },
  "analysis.sensitivity": { en: "Parameter sensitivity", zh: "参数敏感性" },
  "analysis.kmeans": { en: "K-Means", zh: "K-Means" },
  "analysis.dbscan": { en: "DBSCAN", zh: "DBSCAN" },
  "analysis.hdbscan": { en: "HDBSCAN", zh: "HDBSCAN" },
  "analysis.agglomerative": { en: "Agglomerative", zh: "层次聚类" },
  "analysis.analysis": { en: "Analysis", zh: "分析" },
  "analysis.export": { en: "Analysis export", zh: "分析导出" },
};

// Statuses render raw (uppercase) in English mode, exactly as before the
// bilingual layer; Chinese gets a readable label.
const JOB_STATUS_PAIRS: Record<string, Pair> = {
  QUEUED: { en: "QUEUED", zh: "排队中" },
  RUNNING: { en: "RUNNING", zh: "运行中" },
  COMPLETED: { en: "COMPLETED", zh: "已完成" },
  FAILED: { en: "FAILED", zh: "失败" },
  CANCELLED: { en: "CANCELLED", zh: "已取消" },
  STALE: { en: "STALE", zh: "已过期" },
};

/** Display label for a job type in the current UI language (falls back to the raw type). */
export function jobTypeLabel(tr: (p: Pair) => string, jobType: string): string {
  const pair = JOB_TYPE_PAIRS[jobType];
  return pair ? tr(pair) : jobType;
}

/** Display label for a job/run status (QUEUED/RUNNING/…) in the current UI language. */
export function jobStatusLabel(tr: (p: Pair) => string, status: string): string {
  const pair = JOB_STATUS_PAIRS[status];
  return pair ? tr(pair) : status;
}

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
    let settled = false;
    let off = () => {};
    let poller: ReturnType<typeof setInterval> | undefined;
    const finish = (data: {
      status: string;
      result: Record<string, unknown> | null;
      error: JobState["error"];
    }) => {
      if (settled) return;
      settled = true;
      off();
      if (poller) clearInterval(poller);
      resolve(data);
    };
    const terminal = (status: string) => status === "COMPLETED" || status === "FAILED" || status === "CANCELLED";
    const inspect = async () => {
      try {
        const row = await ipc.request<JobRow>("job.get", { id: jobId });
        if (!terminal(row.status)) return;
        finish({
          status: row.status,
          result: null,
          error: row.error ? { code: row.error, message: row.message ?? row.error } : null,
        });
      } catch (error) {
        const err = error as { code?: string; message?: string };
        finish({
          status: "FAILED",
          result: null,
          error: { code: err.code ?? "BACKEND_DOWN", message: err.message ?? "backend unavailable" },
        });
      }
    };

    // Attach the event listener before querying persisted state. A fast job can
    // finish between the submit response and this function call; the query
    // closes that race while the event handles the normal live path.
    off = ipc.on("job.finished", (data) => {
      const d = data as { job_id: string; status: string; result: Record<string, unknown> | null; error: JobState["error"] };
      if (d.job_id !== jobId) return;
      finish({ status: d.status, result: d.result ?? null, error: d.error ?? null });
    });
    void inspect();
    if (!settled) poller = setInterval(() => void inspect(), 500);
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

// Job registry fed by job.progress / job.finished events (docs/plan/02 §4).
import { create } from "zustand";
import { ipc } from "../ipc/client";
import type { Pair } from "../i18n";
import type { JobRow } from "../types/protocol";

export interface JobState {
  id: string;
  job_type: string;
  /** Dataset that owns the job; absent for jobs without a dataset. */
  dataset_id?: string | null;
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
  progress: number;
  completed: number | null;
  total: number | null;
  message: string | null;
  error: { code: string; message: string } | null;
  created_at?: string;
  /** 1-based position within its category pool; only known from persisted rows while QUEUED. */
  queue_position?: number;
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
}

export const useJobs = create<JobsStore>(() => ({ jobs: {}, order: [] }));

const isTerminalStatus = (status: string) => status === "COMPLETED" || status === "FAILED" || status === "CANCELLED";

export function trackJob(jobId: string, jobType: string, datasetId?: string | null) {
  useJobs.setState((st) => ({
    jobs: {
      ...st.jobs,
      [jobId]: {
        id: jobId,
        job_type: jobType,
        ...(datasetId != null ? { dataset_id: datasetId } : {}),
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

export function watchJob(jobId: string, onProgress?: (p: number) => void): Promise<{
  status: string;
  result: Record<string, unknown> | null;
  error: JobState["error"];
}> {
  return new Promise((resolve) => {
    let settled = false;
    let off = () => {};
    let offProgress = () => {};
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const finish = (data: {
      status: string;
      result: Record<string, unknown> | null;
      error: JobState["error"];
    }) => {
      if (settled) return;
      settled = true;
      off();
      offProgress();
      if (timer) clearTimeout(timer);
      resolve(data);
    };
    const schedule = (delay: number) => {
      if (settled) return;
      timer = setTimeout(() => void inspect(), delay);
    };
    const inspect = async () => {
      try {
        const row = await ipc.request<JobRow>("job.get", { id: jobId });
        if (settled) return;
        failures = 0;
        reconcileTrackedJob(row);
        if (!isTerminalStatus(row.status)) {
          schedule(500);
          return;
        }
        finish({
          status: row.status,
          result: row.result ?? null,
          error: row.error ? { code: row.error, message: row.message ?? row.error } : null,
        });
      } catch (error) {
        if (settled) return;
        const err = error as { code?: string; message?: string };
        // BUSY means the backend answered but its request queue is jammed (a
        // long native compute stalls the RPC workers until the poll slots
        // fill) — the job itself is alive, so back off and keep waiting
        // instead of failing the watch and popping a bogus error.
        if (err.code === "BUSY") {
          failures = 0;
          schedule(2000);
          return;
        }
        failures += 1;
        if (failures >= 20) {
          finish({
            status: "FAILED",
            result: null,
            error: { code: err.code ?? "BACKEND_DOWN", message: err.message ?? "backend unavailable" },
          });
          return;
        }
        schedule(Math.min(500 * 2 ** failures, 4000));
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
    if (onProgress) {
      offProgress = ipc.on("job.progress", (data) => {
        const d = data as { job_id: string; progress: number };
        if (d.job_id === jobId) onProgress(d.progress);
      });
    }
    void inspect();
  });
}

/** Keep the live registry consistent when polling recovers a missed event. */
function reconcileTrackedJob(row: JobRow): void {
  useJobs.setState((st) => {
    if (!st.jobs[row.id]) return st;
    return { jobs: { ...st.jobs, [row.id]: fromJobRow(row) } };
  });
}

/** Wait for successful completion and throw the backend's failure reason. */
export async function waitForSuccessfulJob(jobId: string, onProgress?: (p: number) => void): Promise<void> {
  const done = await watchJob(jobId, onProgress);
  if (done.status === "COMPLETED") return;
  throw done.error ?? {
    code: done.status,
    message: done.status === "CANCELLED" ? "The job was cancelled." : "The job failed.",
  };
}

let cleanupJobEvents: (() => void) | null = null;

/** Queued or running: what the status bar and the Jobs badge count. */
export function countRunning(jobs: Record<string, JobState>): number {
  return Object.values(jobs).filter((j) => j.status === "RUNNING" || j.status === "QUEUED").length;
}

export function wireJobEvents(setRunning: (n: number) => void): () => void {
  cleanupJobEvents?.();
  const offProgress = ipc.on("job.progress", (data) => {
    const d = data as { job_id: string; progress: number; completed: number; total: number; message: string | null };
    useJobs.setState((st) => {
      const cur = st.jobs[d.job_id];
      if (!cur || isTerminalStatus(cur.status)) return st;
      return {
        jobs: {
          ...st.jobs,
          [d.job_id]: { ...cur, status: "RUNNING", progress: d.progress, completed: d.completed, total: d.total, message: d.message },
        },
      };
    });
  });
  const offFinished = ipc.on("job.finished", (data) => {
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
  // Count the store, not the events: a tracked submission, a job.get recovery
  // after a missed event and a settled run all change `jobs`, and listening only
  // for the two events left the badge counting a job that had already finished
  // whenever the backend died before emitting one.
  const unsubscribe = useJobs.subscribe((st) => setRunning(countRunning(st.jobs)));
  const cleanup = () => {
    offProgress();
    offFinished();
    unsubscribe();
    if (cleanupJobEvents === cleanup) cleanupJobEvents = null;
  };
  cleanupJobEvents = cleanup;
  return cleanup;
}

export function fromJobRow(row: JobRow): JobState {
  return {
    id: row.id,
    job_type: row.job_type,
    ...(row.dataset_id != null ? { dataset_id: row.dataset_id } : {}),
    status: row.status,
    progress: row.progress,
    completed: row.completed,
    total: row.total,
    message: row.message,
    // job_service persists only the error *code* on the row (the human sentence
    // travels on the job.finished event), so `row.error` cannot also be the
    // message: a job that failed while the drawer was closed read
    // "INTERNAL_ERROR: INTERNAL_ERROR" while the same job watched live read the
    // real sentence. watchJob, two functions above, already falls back this way.
    error: row.error ? { code: row.error, message: row.message ?? row.error } : null,
    created_at: row.created_at,
    // live events never carry a position; dropping the key (vs. setting
    // undefined) keeps mergeJobRows from clobbering a persisted value
    ...(row.queue_position != null ? { queue_position: row.queue_position } : {}),
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

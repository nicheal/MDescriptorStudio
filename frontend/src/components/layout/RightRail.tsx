// Persistent right rail, page-aware:
//  • Overview/Explore — Data Health panel: missing values / invalid cells /
//    duplicate structures / extreme forces from the last scan, plus scan
//    status and a Rescan trigger.
//  • Analysis page — no right rail, so the analysis workspace stays focused.
//  • Descriptors/Results pages — Recent Jobs panel: latest descriptor compute jobs
//    (persisted history merged with the live session) with live progress;
//    "View all jobs" opens the top-right Jobs drawer (full history).
// Fits the viewport by design — no scrollbar at default window sizes; below
// 1280px window width it hides so pages keep their working area.
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { App as AntApp, Button, Progress, Tooltip, Typography } from "antd";
import {
  ArrowSync16Regular,
  BroadActivityFeed16Regular,
  CheckmarkCircle16Filled,
  CheckmarkCircle16Regular,
  Clock16Regular,
  Copy16Regular,
  Cube16Regular,
  GridDots16Regular,
  Info16Regular,
  Warning16Filled,
} from "@fluentui/react-icons";
import { ipc } from "../../ipc/client";
import { activeDataset, refetchDatasets, useWorkspace } from "../../stores/workspace";
import { useT, type T } from "../../i18n";
import {
  JOB_STATUS_COLOR as STATUS_COLOR,
  jobStatusLabel,
  jobTypeLabel,
  mergeJobRows,
  useJobs,
  type JobState,
} from "../../stores/jobs";
import type { DatasetHealth, JobRow } from "../../types/protocol";

const BLUE = "#0F6CBD";
const GREEN = "#107C10";
const ORANGE = "#F0A000";
const GRAY = "#8A8A8A";

const RECENT_JOB_LIMIT = 6;

const subscribeResize = (cb: () => void) => {
  window.addEventListener("resize", cb);
  return () => window.removeEventListener("resize", cb);
};

type StatisticsResponse = {
  recalculating: boolean;
  job_id: string | null;
  stats: { structures: number; health?: DatasetHealth } | null;
};

/** Resolves when the backend job finishes; reports progress along the way. */
function jobDone(jobId: string, onProgress?: (p: number) => void): Promise<void> {
  return new Promise((resolve) => {
    const offDone = ipc.on("job.finished", (data) => {
      const j = data as { job_id: string };
      if (j.job_id !== jobId) return;
      offDone();
      offProgress();
      resolve();
    });
    const offProgress = ipc.on("job.progress", (data) => {
      const j = data as { job_id: string; progress: number };
      if (j.job_id !== jobId) return;
      onProgress?.(j.progress);
    });
  });
}

export default function RightRail() {
  const page = useWorkspace().page;
  const wide = useSyncExternalStore(subscribeResize, () => window.innerWidth >= 1280);
  if (!wide || page === "analysis") return null;
  return page === "descriptors" || page === "results" ? <RecentJobsRail /> : <DataHealthRail />;
}

function DataHealthRail() {
  const { message } = AntApp.useApp();
  const { t } = useT();
  const st = useWorkspace();
  const d = activeDataset(st);
  const [health, setHealth] = useState<DatasetHealth | null>(null);
  const [structures, setStructures] = useState<number | null>(null);
  const [scanning, setScanning] = useState<number | null>(null); // job progress 0..1

  const applyStats = useCallback((r: StatisticsResponse) => {
    if (r.stats) {
      setHealth(r.stats.health ?? null);
      setStructures(r.stats.structures);
      setScanning(null);
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    setHealth(null);
    setScanning(null);
    if (!d) return;
    const dsId = d.id;
    (async () => {
      try {
        const r = await ipc.request<StatisticsResponse>("dataset.statistics", { id: dsId });
        if (disposed) return;
        if (r.stats) {
          applyStats(r);
        } else if (r.job_id) {
          // stale or pre-health cache: a recompute job is already running
          setScanning(0);
          await jobDone(r.job_id, (p) => !disposed && setScanning(p));
          if (disposed) return;
          await refetchDatasets();
          applyStats(await ipc.request<StatisticsResponse>("dataset.statistics", { id: dsId }));
        }
      } catch (e) {
        console.error("dataset.statistics failed", e);
      } finally {
        if (!disposed) setScanning(null);
      }
    })();
    return () => {
      disposed = true;
    };
  }, [d?.id, st.statsTick, applyStats]); // eslint-disable-line react-hooks/exhaustive-deps

  const rescan = useCallback(async () => {
    if (!d || scanning !== null) return;
    const dsId = d.id;
    try {
      setScanning(0);
      const r = await ipc.request<{ job_id: string }>("dataset.rescan", { id: dsId });
      await jobDone(r.job_id, (p) => setScanning(p));
      await refetchDatasets();
      const fresh = await ipc.request<StatisticsResponse>("dataset.statistics", { id: dsId });
      if (useWorkspace.getState().activeDatasetId !== dsId) return;
      applyStats(fresh);
      useWorkspace.getState().bumpStatsTick(); // Overview refetches too
      message.success(t("Rescan complete"));
    } catch (e) {
      const err = e as { code: string; message: string };
      message.error(`${err.code}: ${err.message}`);
    } finally {
      setScanning(null);
    }
  }, [d, scanning, applyStats, message]);

  const rows: {
    key: string;
    icon: React.ReactNode;
    title: string;
    subtitle: string;
    count: number | null;
  }[] = [
    {
      key: "missing",
      icon: <GridDots16Regular />,
      title: t("Missing values"),
      subtitle: t("Across all properties"),
      count: health ? health.missing_values : null,
    },
    {
      key: "cell",
      icon: <Cube16Regular />,
      title: t("Invalid cell"),
      subtitle: t("Non-positive or degenerate"),
      count: health ? health.invalid_cell : null,
    },
    {
      key: "dup",
      icon: <Copy16Regular />,
      title: t("Duplicate structures"),
      subtitle: t("Exact duplicates (hash)"),
      count: health ? health.duplicate_structures : null,
    },
    {
      key: "force",
      icon: <BroadActivityFeed16Regular />,
      title: t("Extreme force"),
      subtitle: t("|F| > {threshold} eV/Å", { threshold: health ? health.extreme_force_threshold : 50 }),
      count: health ? health.extreme_force : null,
    },
  ];

  return (
    <div
      style={{
        width: 264,
        flex: "0 0 264px",
        borderLeft: "1px solid #E1E4E8",
        background: "#F9FAFB",
        padding: 14,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minWidth: 0,
      }}
    >
      <div
        style={{
          background: "#FFFFFF",
          border: "1px solid #EAECF0",
          borderRadius: 10,
          boxShadow: "0 1px 2px rgba(16,24,40,0.06)",
          padding: "12px 14px 14px",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6, paddingBottom: 4 }}>
          <Typography.Text strong style={{ fontSize: 14, color: "#242424" }}>
            {t("Data Health")}
          </Typography.Text>
          <Tooltip title={t("Quality checks from the last full scan: property values missing on some structures, non-positive or degenerate cells, exact duplicate structures (content hash), and any atom force above the threshold.")}>
            <span style={{ color: GRAY, display: "inline-flex", cursor: "default" }}>
              <Info16Regular />
            </span>
          </Tooltip>
        </div>

        {!d ? (
          <Typography.Text type="secondary" style={{ fontSize: 12, padding: "10px 0 14px" }}>
            {t("Register a dataset to see its health.")}
          </Typography.Text>
        ) : (
          <>
            {rows.map((row, i) => (
              <HealthRow
                key={row.key}
                icon={row.icon}
                title={row.title}
                subtitle={row.subtitle}
                count={row.count}
                total={structures}
                first={i === 0}
                loading={health === null && scanning === null}
              />
            ))}
            <ScanRow scanning={scanning} lastScanAt={d.last_scan_at} />
            <Button
              block
              disabled={scanning !== null}
              onClick={() => void rescan()}
              icon={scanning === null ? <ArrowSync16Regular /> : <ArrowSync16Regular className="rail-spin" />}
              style={{
                marginTop: 12,
                borderRadius: 8,
                borderColor: BLUE,
                color: BLUE,
                background: "#FFFFFF",
                fontWeight: 600,
              }}
            >
              {scanning === null ? t("Rescan") : t("Scanning… {percent}%", { percent: Math.round(scanning * 100) })}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function RecentJobsRail() {
  const setJobsDrawerOpen = useWorkspace().setJobsDrawerOpen;
  const { t } = useT();
  const { jobs, order } = useJobs();
  const [rows, setRows] = useState<JobRow[]>([]);

  const load = useCallback(() => {
    ipc.request<JobRow[]>("job.list", {})
      .then((r) => setRows(r))
      .catch((e) => console.error("job.list failed", e));
  }, []);

  useEffect(() => {
    load();
    // persisted rows go stale as jobs settle — refetch when any job finishes
    return ipc.on("job.finished", load);
  }, [load]);

  const recent = useMemo(
    () =>
      mergeJobRows(rows, order.map((id) => jobs[id]).filter(Boolean))
        .filter((j) => j.job_type === "descriptor.compute")
        .slice(0, RECENT_JOB_LIMIT),
    [rows, jobs, order],
  );

  return (
    <div
      style={{
        width: 264,
        flex: "0 0 264px",
        borderLeft: "1px solid #E1E4E8",
        background: "#F9FAFB",
        padding: 14,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minWidth: 0,
      }}
    >
      <div
        style={{
          background: "#FFFFFF",
          border: "1px solid #EAECF0",
          borderRadius: 10,
          boxShadow: "0 1px 2px rgba(16,24,40,0.06)",
          padding: "12px 14px 14px",
          display: "flex",
          flexDirection: "column",
          flex: 1,
          minHeight: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6, paddingBottom: 4 }}>
          <Typography.Text strong style={{ fontSize: 14, color: "#242424" }}>
            {t("Recent Jobs")}
          </Typography.Text>
          <Tooltip title={t("Latest descriptor compute jobs. Live jobs update automatically — the top-right Jobs button has the full history.")}>
            <span style={{ color: GRAY, display: "inline-flex", cursor: "default" }}>
              <Info16Regular />
            </span>
          </Tooltip>
        </div>

        {recent.length === 0 ? (
          <Typography.Text type="secondary" style={{ fontSize: 12, padding: "10px 0 14px" }}>
            {t("No descriptor computes yet — submit one from this page.")}
          </Typography.Text>
        ) : (
          <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
            {recent.map((j, i) => (
              <JobRailRow key={j.id} job={j} first={i === 0} />
            ))}
          </div>
        )}

        <Button
          block
          onClick={() => setJobsDrawerOpen(true)}
          icon={<Clock16Regular />}
          style={{
            marginTop: 12,
            flex: "0 0 auto",
            borderRadius: 8,
            borderColor: BLUE,
            color: BLUE,
            background: "#FFFFFF",
            fontWeight: 600,
          }}
        >
          {t("View all jobs")}
        </Button>
      </div>
    </div>
  );
}

function JobRailRow({ job, first }: { job: JobState; first: boolean }) {
  const i18n = useT();
  const { tr } = i18n;
  const active = job.status === "RUNNING" || job.status === "QUEUED";
  const detail =
    job.completed != null && job.total != null
      ? `${job.completed.toLocaleString()} / ${job.total.toLocaleString()}`
      : job.error
        ? job.error.message
        : (job.message ?? "—");
  return (
    <div style={{ padding: "10px 0 9px", borderTop: first ? "none" : "1px solid #EAECF0" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: STATUS_COLOR[job.status], flex: "0 0 auto" }} />
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "#242424",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            minWidth: 0,
          }}
        >
          {jobTypeLabel(tr, job.job_type)}        </span>
        <span style={{ marginLeft: "auto", fontSize: 12, fontWeight: 600, color: STATUS_COLOR[job.status], flex: "0 0 auto" }}>
          {jobStatusLabel(tr, job.status)}
        </span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginTop: 2 }}>
        <span
          style={{
            fontSize: 11,
            color: "#616161",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            minWidth: 0,
          }}
        >
          {detail}
        </span>
        <span style={{ fontSize: 11, color: "#616161", fontVariantNumeric: "tabular-nums", flex: "0 0 auto" }}>
          {job.created_at ? formatRailTime(job.created_at, i18n) : "—"}
        </span>
      </div>
      {active && (
        <Progress percent={Math.round(job.progress * 100)} size="small" strokeColor={STATUS_COLOR[job.status]} style={{ margin: "6px 0 0" }} />
      )}
    </div>
  );
}

function HealthRow({
  icon,
  title,
  subtitle,
  count,
  total,
  first,
  loading,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
  count: number | null;
  total: number | null;
  first: boolean;
  loading: boolean;
}) {
  const pct = count !== null && total ? (count / total) * 100 : 0;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "11px 0",
        borderTop: first ? "none" : "1px solid #EAECF0",
      }}
    >
      <span style={{ color: BLUE, display: "inline-flex", flex: "0 0 auto" }}>{icon}</span>
      <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "#242424" }}>{title}</span>
        <span
          style={{
            fontSize: 11,
            color: "#616161",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {subtitle}
        </span>
      </span>
      <span
        style={{
          marginLeft: "auto",
          fontSize: 12.5,
          color: "#242424",
          fontVariantNumeric: "tabular-nums",
          whiteSpace: "nowrap",
          flex: "0 0 auto",
        }}
      >
        {loading ? "—" : `${(count ?? 0).toLocaleString()} (${pct.toFixed(2)}%)`}
      </span>
      <span
        style={{
          color: loading ? "#C9CDD4" : (count ?? 0) > 0 ? ORANGE : GREEN,
          display: "inline-flex",
          flex: "0 0 auto",
        }}
      >
        {loading ? <CheckmarkCircle16Regular /> : (count ?? 0) > 0 ? <Warning16Filled /> : <CheckmarkCircle16Filled />}
      </span>
    </div>
  );
}

function ScanRow({ scanning, lastScanAt }: { scanning: number | null; lastScanAt: string | null }) {
  const i18n = useT();
  const done = scanning === null && lastScanAt;
  const icon = scanning !== null ? (
    <span className="rail-spin" style={{ display: "inline-flex" }}>
      <ArrowSync16Regular />
    </span>
  ) : done ? (
    <CheckmarkCircle16Regular />
  ) : (
    <Clock16Regular />
  );
  const iconColor = scanning !== null ? BLUE : done ? GREEN : GRAY;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 0", borderTop: "1px solid #EAECF0" }}>
      <span style={{ color: iconColor, display: "inline-flex", flex: "0 0 auto" }}>{icon}</span>
      <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "#242424" }}>{i18n.t("Scan status")}</span>
        <span style={{ fontSize: 11, color: "#616161" }}>
          {scanning !== null ? i18n.t("Scanning…") : done ? i18n.t("Completed") : i18n.t("Pending")}
        </span>
      </span>
      <span
        style={{
          marginLeft: "auto",
          fontSize: 12.5,
          color: "#242424",
          whiteSpace: "nowrap",
          flex: "0 0 auto",
        }}
      >
        {scanning !== null
          ? `${Math.round(scanning * 100)}%`
          : done
            ? formatRailTime(lastScanAt!, i18n)
            : "—"}
      </span>
    </div>
  );
}

/** "Today 10:24 AM" style, from a UTC ISO timestamp. */
function formatRailTime(iso: string, i18n: T): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const time = date.toLocaleTimeString(i18n.locale, { hour: "numeric", minute: "2-digit" });
  const now = new Date();
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diffDays = Math.round((day(now) - day(date)) / 86_400_000);
  if (diffDays === 0) return i18n.t("Today {time}", { time });
  if (diffDays === 1) return i18n.t("Yesterday {time}", { time });
  return `${date.toLocaleDateString(i18n.locale, { month: "short", day: "numeric" })} ${time}`;
}

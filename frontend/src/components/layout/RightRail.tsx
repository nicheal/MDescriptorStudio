// Persistent right rail, page-aware:
//  • Overview/Explore — Data Health panel: missing values / non-negative
//    per-atom energy / invalid cells /
//    duplicate structures / extreme forces / non-physical structures /
//    net-force imbalance from the last scan, plus scan status and a Rescan
//    trigger.
//  • Analysis page — no right rail, so the analysis workspace stays focused.
//  • Descriptors/Results pages — Recent Jobs panel: latest descriptor compute jobs
//    (persisted history merged with the live session) with live progress;
//    "View all jobs" opens the top-right Jobs drawer (full history).
// Fits the viewport by design — no scrollbar at default window sizes; below
// 1280px window width it hides so pages keep their working area.
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { Alert, App as AntApp, Button, Popconfirm, Progress, Space, Spin, Tag, Tooltip, Typography } from "antd";
import {
  ArrowRight16Regular,
  ArrowSync16Regular,
  BroadActivityFeed16Regular,
  Calculator16Regular,
  CheckmarkCircle16Filled,
  CheckmarkCircle16Regular,
  Clock16Regular,
  Copy16Regular,
  Cube16Regular,
  Delete16Regular,
  Flash16Regular,
  GridDots16Regular,
  Info16Regular,
  Search16Regular,
  Warning16Filled,
} from "@fluentui/react-icons";
import { ipc } from "../../ipc/client";
import { refetchDatasets, useActiveDataset, useWorkspace } from "../../stores/workspace";
import { DATASET_PROPERTY_LABELS } from "../../util/properties";
import { useT, type T } from "../../i18n";
import { useGenerationStore } from "../../features/generation/generationStore";
import { GENERATION_OBJECTIVE_LABELS, GENERATION_OPTIMIZER_LABELS } from "../../features/generation/labels";
import {
  JOB_STATUS_COLOR as STATUS_COLOR,
  waitForSuccessfulJob,
  jobStatusLabel,
  jobTypeLabel,
  mergeJobRows,
  useJobs,
  type JobState,
} from "../../stores/jobs";
import type { DatasetHealth, JobRow } from "../../types/protocol";
import type { GenerationRow } from "../../features/generation/types";
import { describeError } from "../../util/errors";

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

/** Missing-values subtitle: per-property counts when the scan provides them
 * (declared properties only), otherwise the generic fallback. */
function missingValuesSubtitle(
  byProp: DatasetHealth["missing_by_property"],
  t: (key: string) => string,
): string {
  if (!byProp) return t("Across all properties");
  const parts = Object.entries(byProp)
    .filter(([, n]) => (n ?? 0) > 0)
    .map(([k, n]) => `${t(DATASET_PROPERTY_LABELS[k] ?? k)} ${(n ?? 0).toLocaleString("en-US", { useGrouping: false })}`);
  return parts.length > 0 ? parts.join(" · ") : t("Across all properties");
}

export default function RightRail() {
  const page = useWorkspace((s) => s.page);
  const wide = useSyncExternalStore(subscribeResize, () => window.innerWidth >= 1280);
  if (!wide || page === "analysis") return null;
  if (page === "generation") return <GenerationHistoryRail />;
  return page === "descriptors" || page === "results" ? <RecentJobsRail /> : <DataHealthRail />;
}

/** One rail card: fixed-width column, white surface, titled header with help. */
function RailShell({ title, help, children }: { title: string; help: string; children: ReactNode }) {
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
            {title}
          </Typography.Text>
          <Tooltip title={help}>
            <span style={{ color: GRAY, display: "inline-flex", cursor: "default" }}>
              <Info16Regular />
            </span>
          </Tooltip>
        </div>
        {children}
      </div>
    </div>
  );
}


function DataHealthRail() {
  const { message } = AntApp.useApp();
  const { t } = useT();
  const d = useActiveDataset();
  const statsTick = useWorkspace((st) => st.statsTick);
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
          await waitForSuccessfulJob(r.job_id, (p) => !disposed && setScanning(p));
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
  }, [d?.id, statsTick, applyStats]); // eslint-disable-line react-hooks/exhaustive-deps

  const rescan = useCallback(async () => {
    if (!d || scanning !== null) return;
    const dsId = d.id;
    try {
      setScanning(0);
      const r = await ipc.request<{ job_id: string }>("dataset.rescan", { id: dsId });
      await waitForSuccessfulJob(r.job_id, (p) => setScanning(p));
      await refetchDatasets();
      const fresh = await ipc.request<StatisticsResponse>("dataset.statistics", { id: dsId });
      if (useWorkspace.getState().activeDatasetId !== dsId) return;
      applyStats(fresh);
      useWorkspace.getState().bumpStatsTick(); // Overview refetches too
      message.success(t("Rescan complete"));
    } catch (e) {
      const err = e as { code: string; message: string };
      message.error(describeError(err, "DATASET"));
    } finally {
      setScanning(null);
    }
  }, [d, scanning, applyStats, message, t]);

  const openFindings = useWorkspace((s) => s.openFindings);
  const rows: {
    key: string;
    icon: React.ReactNode;
    title: string;
    subtitle: string;
    count: number | null;
    check: string;
  }[] = [
    {
      key: "missing",
      icon: <GridDots16Regular />,
      title: t("Missing values"),
      subtitle: missingValuesSubtitle(health?.missing_by_property, t),
      count: health ? health.missing_values : null,
      check: "missing_values",
    },
    {
      key: "energy",
      icon: <Calculator16Regular />,
      title: t("Energy anomaly"),
      subtitle: t("Per-atom energy ≥ 0 eV/atom"),
      count: health ? health.energy_anomaly : null,
      check: "energy_anomaly",
    },
    {
      key: "cell",
      icon: <Cube16Regular />,
      title: t("Invalid cell"),
      subtitle: t("Non-positive or degenerate"),
      count: health ? health.invalid_cell : null,
      check: "invalid_cell",
    },
    {
      key: "dup",
      icon: <Copy16Regular />,
      title: t("Duplicate structures"),
      subtitle: t("Exact duplicates (hash)"),
      count: health ? health.duplicate_structures : null,
      check: "duplicate_structures",
    },
    {
      key: "force",
      icon: <BroadActivityFeed16Regular />,
      title: t("Extreme force"),
      subtitle: t("|F| > {threshold} eV/Å", { threshold: health ? health.extreme_force_threshold : 50 }),
      count: health ? health.extreme_force : null,
      check: "extreme_force",
    },
    {
      key: "nonphysical",
      icon: <Search16Regular />,
      title: t("Non-physical structures"),
      subtitle: t("Pairs < {coefficient} × covalent radii", {
        coefficient: health ? health.short_contact_coefficient : 0.7,
      }),
      count: health ? health.nonphysical_structures : null,
      check: "nonphysical_structures",
    },
    {
      key: "netforce",
      icon: <Flash16Regular />,
      title: t("Net force"),
      subtitle: t("|ΣF| > {threshold} eV/Å", { threshold: health ? health.net_force_threshold : 0.001 }),
      count: health ? health.net_force : null,
      check: "net_force",
    },
  ];

  return (
    <RailShell title={t("Data Health")} help={t("Quality checks from the last full scan: property values missing on some structures, structures with non-negative per-atom energy, non-positive or degenerate cells, exact duplicate structures (content hash), any atom force above the threshold, atom pairs closer than the covalent-radii bound (non-physical structures), and net force above the threshold.")}>

      {!d ? (
        <Typography.Text type="secondary" style={{ fontSize: 12, padding: "10px 0 14px" }}>
          {t("Register a dataset to see its health.")}
        </Typography.Text>
      ) : (
        <>
          <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
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
                clickable={!!health && !!row.count}
                onClick={() => openFindings(row.check)}
              />
            ))}
            <ScanRow scanning={scanning} lastScanAt={d.last_scan_at} />
          </div>
          <Button
            block
            disabled={scanning !== null}
            onClick={() => void rescan()}
            icon={scanning === null ? <ArrowSync16Regular /> : <ArrowSync16Regular className="rail-spin" />}
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
            {scanning === null ? t("Rescan") : t("Scanning… {percent}%", { percent: Math.round(scanning * 100) })}
          </Button>
        </>
      )}
    </RailShell>
  );
}

function GenerationHistoryRail() {
  const { message } = AntApp.useApp();
  const { t, tr, locale } = useT();
  const rows = useGenerationStore((s) => s.history);
  const historyStatus = useGenerationStore((s) => s.historyStatus);
  const historyError = useGenerationStore((s) => s.historyError);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const deleteRun = async (row: GenerationRow) => {
    setDeletingId(row.id);
    try {
      await ipc.request("generation.delete", { id: row.id });
      const state = useGenerationStore.getState();
      state.setHistory(state.history.filter((item) => item.id !== row.id));
      if (state.activeGenerationId === row.id) {
        state.setActiveGeneration(null);
        state.setLiveRow(null);
        state.setPhase("config");
      }
      window.dispatchEvent(new Event("generation-history-changed"));
      message.success(t("Expansion deleted"));
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(describeError(err, "GENERATION", t("delete failed")));
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <RailShell
      title={t("Expansion history")}
      help={t("Generation runs for the active dataset. Open a run or delete its history.")}
    >
      {historyStatus === "failed" && <Alert
        type="error"
        showIcon
        style={{ marginBottom: 8 }}
        message={t("Could not load expansion history")}
        description={historyError ?? t("Could not refresh expansion history")}
        action={<Button size="small" onClick={() => window.dispatchEvent(new Event("generation-history-changed"))}>{t("Retry")}</Button>}
      />}
      {rows.length === 0 ? (
        <Typography.Text type="secondary" style={{ fontSize: 12, padding: "10px 0 14px" }}>
          {historyStatus === "failed"
            ? t("History could not be verified; retry to check for runs.")
            : historyStatus === "ready"
              ? t("No expansion runs yet — submit one from configuration.")
              : <span><Spin size="small" /> {t("Loading expansion history")}</span>}
        </Typography.Text>
      ) : (
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          {rows.map((row, index) => {
            const active = row.status === "QUEUED" || row.status === "RUNNING";
            const color = row.status === "COMPLETED" ? "green" : row.status === "CANCELLED" ? "orange" : undefined;
            const methodLabel = `${t(GENERATION_OPTIMIZER_LABELS[row.optimizer as keyof typeof GENERATION_OPTIMIZER_LABELS] ?? row.optimizer)} · ${t(GENERATION_OBJECTIVE_LABELS[row.objective as keyof typeof GENERATION_OBJECTIVE_LABELS] ?? row.objective)}`;
            return (
              <div
                key={row.id}
                style={{ padding: "9px 0", borderTop: index === 0 ? "none" : "1px solid #EAECF0" }}
              >
                <Typography.Text
                  strong
                  ellipsis
                  title={methodLabel}
                  style={{ display: "block", fontSize: 12 }}
                >
                  {methodLabel}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ display: "block", fontSize: 11 }}>
                  {new Date(row.created_at).toLocaleString(locale)}
                </Typography.Text>
                <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 4 }}>
                  <Tag color={color} style={{ marginInlineEnd: 0, fontSize: 10 }}>
                    {jobStatusLabel(tr, row.status)}
                  </Tag>
                  <Typography.Text type="secondary" style={{ fontSize: 10, whiteSpace: "nowrap" }}>
                    {row.accepted_count} / {row.evaluations}
                  </Typography.Text>
                  <Space size={0} style={{ marginLeft: "auto" }}>
                    <Button
                      size="small"
                      type="text"
                      icon={<ArrowRight16Regular />}
                      aria-label={t("Open {name} expansion", { name: t(GENERATION_OBJECTIVE_LABELS[row.objective as keyof typeof GENERATION_OBJECTIVE_LABELS] ?? row.objective) })}
                      title={t("Open")}
                      onClick={() => window.dispatchEvent(new CustomEvent("generation-open", { detail: row.id }))}
                    />
                    <Popconfirm
                      title={t("Delete expansion and generated files?")}
                      description={t("This removes the run history, linked job record, and generated files. It cannot be undone.")}
                      okText={t("Delete")}
                      cancelText={t("Cancel")}
                      okButtonProps={{ danger: true, loading: deletingId === row.id }}
                      onConfirm={() => void deleteRun(row)}
                    >
                      <Button
                        size="small"
                        type="text"
                        icon={<Delete16Regular />}
                        aria-label={t("Delete {name} expansion", { name: t(GENERATION_OBJECTIVE_LABELS[row.objective as keyof typeof GENERATION_OBJECTIVE_LABELS] ?? row.objective) })}
                        title={t("Delete")}
                        disabled={active || deletingId !== null}
                        loading={deletingId === row.id}
                      />
                    </Popconfirm>
                  </Space>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </RailShell>
  );
}


function RecentJobsRail() {
  const setJobsDrawerOpen = useWorkspace((s) => s.setJobsDrawerOpen);
  const datasets = useWorkspace((s) => s.datasets);
  const activeDatasetId = useWorkspace((s) => s.activeDatasetId);
  const { t } = useT();
  const jobs = useJobs((s) => s.jobs);
  const order = useJobs((s) => s.order);
  const [rows, setRows] = useState<JobRow[]>([]);
  const datasetNames = useMemo(() => new Map(datasets.map((dataset) => [dataset.id, dataset.name])), [datasets]);

  const load = useCallback(() => {
    if (!activeDatasetId) {
      setRows([]);
      return;
    }
    ipc.request<JobRow[]>("job.list", { dataset_id: activeDatasetId })
      .then((r) => setRows(r))
      .catch((e) => console.error("job.list failed", e));
  }, [activeDatasetId]);

  useEffect(() => {
    load();
    // persisted rows go stale as jobs settle — refetch when any job finishes
    return ipc.on("job.finished", load);
  }, [load]);

  const recent = useMemo(
    () =>
      mergeJobRows(rows, order.map((id) => jobs[id]).filter(Boolean))
        .filter((j) => j.job_type === "descriptor.compute" && j.dataset_id === activeDatasetId)
        .slice(0, RECENT_JOB_LIMIT),
    [rows, jobs, order, activeDatasetId],
  );

  return (
    <RailShell title={t("Recent Jobs")} help={t("Latest descriptor compute jobs. Live jobs update automatically — the top-right Jobs button has the full history.")}>

      {recent.length === 0 ? (
        <Typography.Text type="secondary" style={{ fontSize: 12, padding: "10px 0 14px" }}>
          {t("No descriptor computes yet — submit one from this page.")}
        </Typography.Text>
      ) : (
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          {recent.map((j, i) => (
            <JobRailRow key={j.id} job={j} first={i === 0} datasetName={j.dataset_id ? datasetNames.get(j.dataset_id) ?? null : null} />
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
    </RailShell>
  );
}


function JobRailRow({ job, first, datasetName }: { job: JobState; first: boolean; datasetName: string | null }) {
  const i18n = useT();
  const { tr } = i18n;
  const active = job.status === "RUNNING" || job.status === "QUEUED";
  const jobLabel = jobTypeLabel(tr, job.job_type);
  const title = datasetName ? `${jobLabel} · ${datasetName}` : jobLabel;
  const detail =
    job.status === "QUEUED"
      ? job.queue_position != null
        ? i18n.t("#{n} in queue", { n: job.queue_position })
        : "—"
      : job.completed != null && job.total != null
      ? `${job.completed.toLocaleString("en-US", { useGrouping: false })} / ${job.total.toLocaleString("en-US", { useGrouping: false })}`
      : job.error
        ? job.error.message
        : (job.message ?? "—");
  return (
    <div style={{ padding: "10px 0 9px", borderTop: first ? "none" : "1px solid #EAECF0" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: STATUS_COLOR[job.status], flex: "0 0 auto" }} />
        <span
          title={title}
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
          {title}        </span>
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
      {active && job.status !== "QUEUED" && (
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
  clickable,
  onClick,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
  count: number | null;
  total: number | null;
  first: boolean;
  loading: boolean;
  clickable?: boolean;
  onClick?: () => void;
}) {
  const safeCount = typeof count === "number" && Number.isFinite(count) ? count : 0;
  const safeTotal = typeof total === "number" && Number.isFinite(total) ? total : 0;
  const pct = safeTotal > 0 ? (safeCount / safeTotal) * 100 : 0;
  return (
    <div
      onClick={clickable ? onClick : undefined}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "11px 0",
        borderTop: first ? "none" : "1px solid #EAECF0",
        cursor: clickable ? "pointer" : undefined,
        borderRadius: clickable ? 6 : undefined,
      }}
      className={clickable ? "health-row-clickable" : undefined}
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
        {loading ? "—" : `${safeCount.toLocaleString("en-US", { useGrouping: false })} (${pct.toFixed(2)}%)`}
      </span>
      <span
        style={{
          color: loading ? "#C9CDD4" : safeCount > 0 ? ORANGE : GREEN,
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

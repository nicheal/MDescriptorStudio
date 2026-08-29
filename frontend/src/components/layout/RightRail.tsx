// Persistent right rail (UI.png layout pass): Quick Actions + Recent Jobs.
// Fits the viewport by design — no scrollbar at default window sizes; below
// 1280px window width it hides so Explore/Results keep their working area.
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { App as AntApp, Button, Typography } from "antd";
import {
  ArrowDownload16Regular,
  ArrowSync16Regular,
  CheckmarkCircle16Filled,
  Cube16Regular,
  Grid16Regular,
} from "@fluentui/react-icons";
import { ipc } from "../../ipc/client";
import { JOB_TYPE_LABEL, mergeJobRows, useJobs, type JobState } from "../../stores/jobs";
import { activeDataset, useWorkspace } from "../../stores/workspace";
import type { JobRow } from "../../types/protocol";

const STATUS_COLOR: Record<JobState["status"], string> = {
  QUEUED: "#616161",
  RUNNING: "#0F6CBD",
  COMPLETED: "#107C10",
  FAILED: "#C42B1C",
  CANCELLED: "#8A8A8A",
};

function statusLine(j: JobState): string {
  switch (j.status) {
    case "RUNNING":
      return `Running · ${Math.round(j.progress * 100)}%`;
    case "QUEUED":
      return "Queued";
    case "COMPLETED":
      return "Completed";
    default:
      return j.status.charAt(0) + j.status.slice(1).toLowerCase();
  }
}

const subscribeResize = (cb: () => void) => {
  window.addEventListener("resize", cb);
  return () => window.removeEventListener("resize", cb);
};

export default function RightRail() {
  const { message } = AntApp.useApp();
  const st = useWorkspace();
  const d = activeDataset(st);
  const { jobs, order } = useJobs();
  const wide = useSyncExternalStore(subscribeResize, () => window.innerWidth >= 1280);
  const history = useJobsHistory();

  const recent = mergeJobRows(history, order.map((id) => jobs[id]).filter(Boolean)).sort(
    (a, b) => rank(a) - rank(b),
  ).slice(0, 3);

  function rank(j: JobState): number {
    if (j.status === "RUNNING") return 0;
    if (j.status === "QUEUED") return 1;
    return 2;
  }

  const copySummary = useCallback(async () => {
    if (!d) return;
    const text = JSON.stringify(
      {
        name: d.name,
        format: d.format,
        structures: d.number_of_frames,
        elements: d.elements,
        source_path: d.source_path,
        file_size: d.file_size,
        created_at: d.created_at,
      },
      null,
      2,
    );
    const ok = await copyText(text);
    if (ok) message.success("Dataset summary copied to clipboard");
    else message.error("Could not access the clipboard");
  }, [d, message]);

  const refreshStats = useCallback(async () => {
    if (!d) return;
    try {
      await ipc.request("dataset.statistics", { id: d.id });
      useWorkspace.getState().bumpStatsTick();
      message.info("Statistics refreshed");
    } catch (e) {
      const err = e as { code: string; message: string };
      message.error(`${err.code}: ${err.message}`);
    }
  }, [d, message]);

  if (!wide) return null;

  return (
    <div
      style={{
        width: 264,
        flex: "0 0 264px",
        borderLeft: "1px solid #E1E4E8",
        background: "#FFFFFF",
        padding: "14px 14px",
        display: "flex",
        flexDirection: "column",
        gap: 10,
        overflow: "hidden",
        minWidth: 0,
      }}
    >
      <Typography.Text strong style={{ fontSize: 14 }}>
        Quick Actions
      </Typography.Text>
      <QuickAction
        icon={<Cube16Regular />}
        title="Explore Structures"
        subtitle="Browse structures in 3D"
        onClick={() => st.setPage("explore")}
      />
      <QuickAction
        icon={<Grid16Regular />}
        title="Compute Descriptors"
        subtitle="Calculate descriptors"
        onClick={() => st.setPage("descriptors")}
      />
      <QuickAction
        icon={<ArrowSync16Regular />}
        title="Dataset Statistics"
        subtitle="View detailed analysis"
        onClick={() => void refreshStats()}
      />
      <QuickAction
        icon={<ArrowDownload16Regular />}
        title="Export Dataset Info"
        subtitle="Copy summary to clipboard"
        onClick={() => void copySummary()}
      />
      <div style={{ borderTop: "1px solid #EAECF0", margin: "4px 0" }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Typography.Text strong style={{ fontSize: 14 }}>
          Recent Jobs
        </Typography.Text>
        <Button
          type="link"
          size="small"
          style={{ padding: 0, fontSize: 12 }}
          onClick={() => st.setPage("jobs")}
        >
          View All
        </Button>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, minHeight: 0 }}>
        {recent.length === 0 ? (
          <Typography.Text type="secondary" style={{ fontSize: 12, padding: "8px 2px" }}>
            No jobs yet
          </Typography.Text>
        ) : (
          recent.map((j) => <RecentJob key={j.id} job={j} />)
        )}
      </div>
    </div>
  );
}

function useJobsHistory(): JobRow[] {
  const [rows, setRows] = useState<JobRow[]>([]);
  useEffect(() => {
    let disposed = false;
    void ipc
      .request<JobRow[]>("job.list", {})
      .then((r) => {
        if (!disposed) setRows(r);
      })
      .catch((e) => console.error("job.list failed", e));
    return () => {
      disposed = true;
    };
  }, []);
  return rows;
}

function QuickAction({
  icon,
  title,
  subtitle,
  onClick,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
  onClick: () => void;
}) {
  return (
    <div
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 10px",
        border: "1px solid #EAECF0",
        borderRadius: 8,
        cursor: "pointer",
        background: "#FFFFFF",
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "#F5F8FC")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "#FFFFFF")}
    >
      <span
        style={{
          width: 30,
          height: 30,
          borderRadius: 6,
          background: "#EBF3FC",
          color: "#0F6CBD",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          flex: "0 0 30px",
        }}
      >
        {icon}
      </span>
      <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "#242424" }}>{title}</span>
        <span style={{ fontSize: 11, color: "#616161", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {subtitle}
        </span>
      </span>
    </div>
  );
}

function RecentJob({ job }: { job: JobState }) {
  const done = job.status === "COMPLETED";
  const color = STATUS_COLOR[job.status];
  const running = job.status === "RUNNING" || job.status === "QUEUED";
  return (
    <div style={{ border: "1px solid #EAECF0", borderRadius: 8, padding: "8px 10px" }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: "#242424", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {JOB_TYPE_LABEL[job.job_type] ?? job.job_type}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, color, marginTop: 2 }}>
        {done && <CheckmarkCircle16Filled />}
        {statusLine(job)}
      </div>
      {running && (
        <div style={{ height: 4, background: "#EAECF0", borderRadius: 2, marginTop: 8, overflow: "hidden" }}>
          <div style={{ height: 4, width: `${Math.round(job.progress * 100)}%`, background: color, borderRadius: 2 }} />
        </div>
      )}
    </div>
  );
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

// Jobs entry: top-right badge + right Drawer with live progress & cancel (ADR-6).
// Full history lives on the Jobs page (UI.png layout pass); Recent Jobs rail → View All.
import { useState } from "react";
import { Badge, Button, Drawer, Empty, Popconfirm, Progress, Typography } from "antd";
import { Clock16Regular, Dismiss16Regular } from "@fluentui/react-icons";
import SettingsDrawer from "./SettingsDrawer";
import { ipc } from "../ipc/client";
import { JOB_TYPE_LABEL, useJobs } from "../stores/jobs";
import { useWorkspace } from "../stores/workspace";

export default function JobsDrawer() {
  const { runningJobs } = useWorkspace();
  const { jobs, order } = useJobs();
  const [open, setOpen] = useState(false);

  const listed = order.map((id) => jobs[id]).filter(Boolean);

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <Badge count={runningJobs} size="small" offset={[0, 2]}>
        <Button icon={<Clock16Regular />} onClick={() => setOpen(true)}>
          Jobs
        </Button>
      </Badge>
      <SettingsDrawer />
      <Drawer title="JOBS" placement="right" width={380} open={open} onClose={() => setOpen(false)}>
        {listed.length === 0 ? (
          <Empty description="No jobs yet" style={{ marginTop: 48 }} />
        ) : (
          listed.map((j) => <JobCard key={j.id} job={j} />)
        )}
      </Drawer>
    </div>
  );
}

function JobCard({ job }: { job: ReturnType<typeof useJobs.getState>["jobs"][string] }) {
  const running = job.status === "RUNNING" || job.status === "QUEUED";
  const statusColor =
    job.status === "COMPLETED" ? "#107C10" : job.status === "FAILED" ? "#C42B1C" : job.status === "CANCELLED" ? "#8A8A8A" : "#0F6CBD";
  return (
    <div style={{ borderBottom: "1px solid #EAECF0", padding: "12px 4px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Typography.Text strong style={{ fontSize: 13 }}>
          {JOB_TYPE_LABEL[job.job_type] ?? job.job_type}
        </Typography.Text>
        {running ? (
          <Popconfirm
            title="Cancel this job?"
            onConfirm={() => void ipc.request("job.cancel", { id: job.id })}
            okText="Cancel job"
            okButtonProps={{ danger: true }}
          >
            <Button size="small" icon={<Dismiss16Regular />}>
              Cancel
            </Button>
          </Popconfirm>
        ) : (
          <Typography.Text style={{ color: statusColor, fontSize: 12, fontWeight: 600 }}>
            {job.status}
          </Typography.Text>
        )}
      </div>
      <Progress
        percent={Math.round(job.progress * 100)}
        size="small"
        showInfo={false}
        strokeColor={running ? "#0F6CBD" : statusColor}
        style={{ margin: "6px 0 2px" }}
      />
      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
        {job.completed != null && job.total != null
          ? `${job.completed.toLocaleString()} / ${job.total.toLocaleString()} · ${job.message ?? ""}`
          : (job.message ?? "")}
        {job.error ? ` · ${job.error.code}: ${job.error.message}` : ""}
      </Typography.Text>
    </div>
  );
}

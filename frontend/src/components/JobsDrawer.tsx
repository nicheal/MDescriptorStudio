// Jobs entry: top-right badge + right Drawer — the single jobs surface since
// the old Jobs tab was folded into it. Lists persisted history (job.list)
// merged with the live session jobs, newest first, with cancel for active jobs.
import { useEffect, useState } from "react";
import { Badge, Button, Drawer, Empty, Popconfirm, Progress, Space, Typography } from "antd";
import { Clock16Regular, Dismiss16Regular } from "@fluentui/react-icons";
import SettingsDrawer from "./SettingsDrawer";
import { ipc } from "../ipc/client";
import { jobStatusLabel, jobTypeLabel, mergeJobRows, useJobs, type JobState } from "../stores/jobs";
import { useWorkspace } from "../stores/workspace";
import { useT } from "../i18n";
import type { JobRow } from "../types/protocol";

export default function JobsDrawer() {
  const { runningJobs, jobsDrawerOpen, setJobsDrawerOpen } = useWorkspace();
  const { t } = useT();
  const { jobs, order } = useJobs();
  const [rows, setRows] = useState<JobRow[]>([]);

  useEffect(() => {
    if (!jobsDrawerOpen) return;
    let disposed = false;
    ipc.request<JobRow[]>("job.list", {})
      .then((r) => {
        if (!disposed) setRows(r);
      })
      .catch((e) => console.error("job.list failed", e));
    return () => {
      disposed = true;
    };
  }, [jobsDrawerOpen]);

  const listed = mergeJobRows(rows, order.map((id) => jobs[id]).filter(Boolean));

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <Badge count={runningJobs} size="small" offset={[0, 2]}>
        <Button icon={<Clock16Regular />} onClick={() => setJobsDrawerOpen(true)}>
          {t("Jobs")}
        </Button>
      </Badge>
      <SettingsDrawer />
      <Drawer title={t("JOBS")} placement="right" width={380} open={jobsDrawerOpen} onClose={() => setJobsDrawerOpen(false)}>
        {listed.length === 0 ? (
          <Empty description={t("No jobs yet")} style={{ marginTop: 48 }} />
        ) : (
          listed.map((j) => <JobCard key={j.id} job={j} />)
        )}
      </Drawer>
    </div>
  );
}

function JobCard({ job }: { job: JobState }) {
  const { t, tr } = useT();
  const running = job.status === "RUNNING" || job.status === "QUEUED";
  // A QUEUED job has no progress events yet — a bare 0% bar reads as a stuck
  // job, so say "queued" outright and only show the bar once it is RUNNING.
  const queued = job.status === "QUEUED";
  const statusColor =
    job.status === "COMPLETED" ? "#107C10" : job.status === "FAILED" ? "#C42B1C" : job.status === "CANCELLED" ? "#8A8A8A" : "#0F6CBD";
  return (
    <div style={{ borderBottom: "1px solid #EAECF0", padding: "12px 4px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Typography.Text strong style={{ fontSize: 13 }}>
          {jobTypeLabel(tr, job.job_type)}
        </Typography.Text>
        {running ? (
          <Space size={8}>
            {queued && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {jobStatusLabel(tr, job.status)}
              </Typography.Text>
            )}
            <Popconfirm
              title={t("Stop this job?")}
              onConfirm={() => void ipc.request("job.cancel", { id: job.id })}
              okText={t("Stop job")}
              cancelText={t("Keep running")}
              okButtonProps={{ danger: true }}
            >
              <Button size="small" icon={<Dismiss16Regular />}>
                {t("Stop")}
              </Button>
            </Popconfirm>
          </Space>
        ) : (
          <Typography.Text style={{ color: statusColor, fontSize: 12, fontWeight: 600 }}>
            {jobStatusLabel(tr, job.status)}
          </Typography.Text>
        )}
      </div>
      {!queued && (
        <Progress
          percent={Math.round(job.progress * 100)}
          size="small"
          showInfo={false}
          strokeColor={running ? "#0F6CBD" : statusColor}
          style={{ margin: "6px 0 2px" }}
        />
      )}
      <Typography.Text type="secondary" style={{ fontSize: 11 }}>
        {job.completed != null && job.total != null
          ? `${job.completed.toLocaleString()} / ${job.total.toLocaleString()} · ${job.message ?? ""}`
          : (job.message ?? "")}
        {job.error ? ` · ${job.error.code}: ${job.error.message}` : ""}
      </Typography.Text>
      {job.created_at && (
        <Typography.Text type="secondary" style={{ fontSize: 11, fontVariantNumeric: "tabular-nums", display: "block", marginTop: 2 }}>
          {new Date(job.created_at).toLocaleString()}
        </Typography.Text>
      )}
    </div>
  );
}

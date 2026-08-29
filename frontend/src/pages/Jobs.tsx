// Jobs page (UI.png layout pass, supersedes ADR-6's drawer-only ruling):
// persisted history from job.list merged with live session jobs.
import { useCallback, useEffect, useState } from "react";
import { Button, Empty, Popconfirm, Progress, Table, Typography } from "antd";
import { ArrowSync16Regular, Dismiss16Regular } from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { JOB_TYPE_LABEL, mergeJobRows, useJobs, type JobState } from "../stores/jobs";
import type { JobRow } from "../types/protocol";

const STATUS_COLOR: Record<JobState["status"], string> = {
  QUEUED: "#616161",
  RUNNING: "#0F6CBD",
  COMPLETED: "#107C10",
  FAILED: "#C42B1C",
  CANCELLED: "#8A8A8A",
};

export default function Jobs() {
  const { jobs, order } = useJobs();
  const [rows, setRows] = useState<JobRow[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await ipc.request<JobRow[]>("job.list", {}));
    } catch (e) {
      console.error("job.list failed", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const listed = mergeJobRows(
    rows,
    order.map((id) => jobs[id]).filter(Boolean),
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Typography.Text strong style={{ fontSize: 14 }}>
          Jobs
        </Typography.Text>
        <Button size="small" icon={<ArrowSync16Regular />} onClick={() => void load()} loading={loading}>
          Refresh
        </Button>
      </div>
      <Table
        size="small"
        rowKey="id"
        loading={loading}
        dataSource={listed}
        locale={{ emptyText: <Empty description="No jobs yet" style={{ padding: 24 }} /> }}
        pagination={{ pageSize: 14, hideOnSinglePage: true, showSizeChanger: false }}
        columns={[
          {
            title: "Job",
            dataIndex: "job_type",
            key: "job_type",
            width: 220,
            render: (v: string) => (
              <Typography.Text strong style={{ fontSize: 13 }}>
                {JOB_TYPE_LABEL[v] ?? v}
              </Typography.Text>
            ),
          },
          {
            title: "Status",
            dataIndex: "status",
            key: "status",
            width: 110,
            render: (v: JobState["status"]) => (
              <Typography.Text style={{ color: STATUS_COLOR[v], fontWeight: 600, fontSize: 12 }}>
                {v}
              </Typography.Text>
            ),
          },
          {
            title: "Progress",
            key: "progress",
            width: 170,
            render: (_: unknown, j: JobState) => (
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <Progress
                  percent={Math.round(j.progress * 100)}
                  size="small"
                  strokeColor={STATUS_COLOR[j.status]}
                  style={{ margin: 0, flex: 1 }}
                />
                {(j.status === "RUNNING" || j.status === "QUEUED") && (
                  <Popconfirm
                    title="Cancel this job?"
                    onConfirm={() => void ipc.request("job.cancel", { id: j.id })}
                    okText="Cancel job"
                    okButtonProps={{ danger: true }}
                  >
                    <Button size="small" icon={<Dismiss16Regular />} />
                  </Popconfirm>
                )}
              </div>
            ),
          },
          {
            title: "Detail",
            key: "detail",
            ellipsis: true,
            render: (_: unknown, j: JobState) => {
              const parts = [
                j.completed != null && j.total != null
                  ? `${j.completed.toLocaleString()} / ${j.total.toLocaleString()}`
                  : null,
                j.message ?? null,
                j.error ? `${j.error.code}: ${j.error.message}` : null,
              ].filter(Boolean);
              return (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {parts.length > 0 ? parts.join(" · ") : "—"}
                </Typography.Text>
              );
            },
          },
          {
            title: "Created",
            dataIndex: "created_at",
            key: "created_at",
            width: 170,
            render: (v: string) => (
              <Typography.Text type="secondary" style={{ fontSize: 12, fontVariantNumeric: "tabular-nums" }}>
                {v ? new Date(v).toLocaleString() : "—"}
              </Typography.Text>
            ),
          },
        ]}
      />
    </div>
  );
}

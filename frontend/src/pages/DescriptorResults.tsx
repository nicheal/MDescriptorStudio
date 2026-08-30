import { useCallback, useEffect, useMemo, useState } from "react";
import { App as AntApp, Button, Empty, Popconfirm, Space, Table, Tag, Typography } from "antd";
import {
  ArrowRight16Regular,
  ArrowSync16Regular,
  Delete16Regular,
} from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { activeDataset, useWorkspace } from "../stores/workspace";
import { displayableDescriptorRuns } from "./analysisPreview";
import type { RunRow } from "../types/protocol";

const RUN_STATUS_COLOR: Record<string, string> = {
  QUEUED: "#616161",
  RUNNING: "#0F6CBD",
  COMPLETED: "#107C10",
  STALE: "#F0A000",
  FAILED: "#C42B1C",
  CANCELLED: "#8A8A8A",
};

function SectionHeading({ title, meta }: { title: string; meta?: string }) {
  return (
    <div className="descriptor-results-section-heading">
      <Typography.Text strong>{title}</Typography.Text>
      {meta && <Typography.Text type="secondary">{meta}</Typography.Text>}
    </div>
  );
}

export default function DescriptorResults() {
  const { message } = AntApp.useApp();
  const st = useWorkspace();
  const dataset = activeDataset(st);
  const datasetId = dataset?.id;
  const selectedRun = st.activeDescriptorRunId;
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [deletingRunId, setDeletingRunId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!datasetId) {
      setRuns([]);
      return;
    }
    setRefreshing(true);
    try {
      const resultRows = await ipc.request<RunRow[]>("result.list", { dataset_id: datasetId });
      setRuns(resultRows);
      const completed = displayableDescriptorRuns(resultRows).filter((run) => run.status === "COMPLETED");
      const current = useWorkspace.getState().activeDescriptorRunId;
      if (!current || !completed.some((run) => run.id === current)) {
        useWorkspace.getState().setActiveRun(completed[0]?.id ?? null);
      }
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "RESULT"}: ${err.message ?? "Could not load descriptor results"}`);
    } finally {
      setRefreshing(false);
    }
  }, [datasetId, message]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const resultRuns = useMemo(() => displayableDescriptorRuns(runs), [runs]);
  const completedRuns = useMemo(() => resultRuns.filter((run) => run.status === "COMPLETED"), [resultRuns]);
  const selectedRunRow = resultRuns.find((run) => run.id === selectedRun);
  const descriptorColumnWidth = Math.max(
    112,
    resultRuns.reduce(
      (max, run) => Math.max(max, run.descriptor_name.length * 8 + 24),
      "Descriptor".length * 8 + 24,
    ),
  );

  const deleteRun = async (run: RunRow) => {
    if (deletingRunId || run.status === "QUEUED" || run.status === "RUNNING") return;
    setDeletingRunId(run.id);
    try {
      await ipc.request("result.remove", { run_id: run.id });
      message.success("Descriptor result deleted");
      await refresh();
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "RESULT"}: ${err.message ?? "delete failed"}`);
    } finally {
      setDeletingRunId(null);
    }
  };

  if (!dataset) return <Empty description="Register a dataset first" style={{ marginTop: 120 }} />;

  return (
    <div className="descriptor-results-page">
      <section className="analysis-card descriptor-results-card">
        <div className="descriptor-results-card-header">
          <div>
            <SectionHeading
              title="DESCRIPTOR RESULTS"
              meta={`${completedRuns.length} ready · ${resultRuns.length} total`}
            />
            <Typography.Text type="secondary" className="descriptor-results-description">
              Saved descriptor runs for the active dataset. Failed calculations remain in Jobs and are not listed here.
            </Typography.Text>
          </div>
          <Space wrap>
            {selectedRunRow && <Tag color="blue">Selected: {selectedRunRow.descriptor_name}</Tag>}
            <Button
              size="small"
              icon={<ArrowRight16Regular />}
              disabled={selectedRunRow?.status !== "COMPLETED"}
              onClick={() => st.setPage("analysis")}
            >
              Open Analysis
            </Button>
            <Button size="small" icon={<ArrowSync16Regular />} loading={refreshing} onClick={() => void refresh()}>
              Refresh
            </Button>
          </Space>
        </div>

        {resultRuns.length ? (
          <Table
            className="descriptor-results-table"
            size="small"
            tableLayout="fixed"
            pagination={resultRuns.length > 8 ? { pageSize: 8 } : false}
            rowKey="id"
            dataSource={resultRuns}
            rowClassName={(run) => run.id === selectedRun ? "descriptor-results-row-selected" : ""}
            onRow={(run) => ({
              onClick: () => { if (run.status === "COMPLETED") st.setActiveRun(run.id); },
              style: {
                cursor: run.status === "COMPLETED" ? "pointer" : "default",
              },
            })}
            columns={[
              { title: "Descriptor", dataIndex: "descriptor_name", key: "descriptor", width: descriptorColumnWidth },
              { title: "Scope", dataIndex: "scope", key: "scope", width: 84 },
              {
                title: "Shape",
                dataIndex: "shape",
                key: "shape",
                width: 130,
                align: "center" as const,
                render: (value: string | null | undefined) => value
                  ? <Typography.Text code style={{ fontSize: 11 }} title={value}>{value}</Typography.Text>
                  : <Typography.Text type="secondary">—</Typography.Text>,
              },
              {
                title: "Status",
                dataIndex: "status",
                key: "status",
                width: 100,
                render: (value: string) => (
                  <Typography.Text style={{ color: RUN_STATUS_COLOR[value] ?? "#616161", fontWeight: 600, fontSize: 12 }}>
                    {value}
                  </Typography.Text>
                ),
              },
              { title: "Created", dataIndex: "created_at", key: "created", width: 168, render: (value: string) => new Date(value).toLocaleString() },
              {
                title: "操作",
                key: "actions",
                width: 104,
                render: (_value: unknown, run: RunRow) => {
                  const active = run.status === "QUEUED" || run.status === "RUNNING";
                  return (
                    <span onClick={(event) => event.stopPropagation()}>
                      <Space className="descriptor-results-actions" size={8}>
                        <Button
                          size="small"
                          type="text"
                          aria-label={`Analyze ${run.descriptor_name} result`}
                          title="Open in Analysis"
                          icon={<ArrowRight16Regular />}
                          disabled={run.status !== "COMPLETED"}
                          onClick={() => { st.setActiveRun(run.id); st.setPage("analysis"); }}
                        />
                        <Popconfirm
                          title="Delete this descriptor result?"
                          description="This also removes linked analysis history and stored files."
                          okText="Delete"
                          cancelText="Cancel"
                          okButtonProps={{ danger: true }}
                          disabled={active}
                          onConfirm={() => void deleteRun(run)}
                        >
                          <Button
                            size="small"
                            type="text"
                            aria-label={`Delete ${run.descriptor_name} result`}
                            title={active ? "Cancel the running job first" : "Delete descriptor result"}
                            icon={<Delete16Regular />}
                            loading={deletingRunId === run.id}
                            disabled={active || deletingRunId !== null}
                          />
                        </Popconfirm>
                      </Space>
                    </span>
                  );
                },
              },
            ]}
          />
        ) : (
          <Empty description="No descriptor results yet — compute a descriptor first" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </section>
    </div>
  );
}

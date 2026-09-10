import { useCallback, useEffect, useMemo, useState } from "react";
import { App as AntApp, Button, Empty, Popconfirm, Space, Table, Tag, Typography } from "antd";
import {
  ArrowRight16Regular,
  ArrowSync16Regular,
  Delete16Regular,
} from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { activeDataset, useWorkspace } from "../stores/workspace";
import { jobStatusLabel } from "../stores/jobs";
import { displayableDescriptorRuns } from "./analysisPreview";
import { useT } from "../i18n";
import type { RunRow } from "../types/protocol";
import { formatComputeDuration } from "../util/duration";

const RUN_STATUS_COLOR: Record<string, string> = {
  QUEUED: "#616161",
  RUNNING: "#0F6CBD",
  COMPLETED: "#107C10",
  STALE: "#F0A000",
  FAILED: "#C42B1C",
  CANCELLED: "#8A8A8A",
};
const RESULTS_PAGE_SIZE = 15;

function deviceLabel(value: string | null | undefined): "CPU" | "GPU" {
  const normalized = String(value ?? "cpu").trim().toLowerCase();
  return normalized === "cuda" || normalized === "gpu" ? "GPU" : "CPU";
}

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
  const { t, tr } = useT();
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
      message.success(t("Descriptor result deleted"));
      await refresh();
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "RESULT"}: ${err.message ?? "delete failed"}`);
    } finally {
      setDeletingRunId(null);
    }
  };

  if (!dataset) return <Empty description={t("Register a dataset first")} style={{ marginTop: 120 }} />;

  return (
    <div className="descriptor-results-page">
      <section className="analysis-card descriptor-results-card">
        <div className="descriptor-results-card-header">
          <div>
            <SectionHeading
              title={t("DESCRIPTOR RESULTS")}
              meta={t("{ready} ready · {total} total", { ready: completedRuns.length, total: resultRuns.length })}
            />
            <Typography.Text type="secondary" className="descriptor-results-description">
              {t("Saved descriptor runs for the active dataset. Failed calculations remain in Jobs and are not listed here.")}
            </Typography.Text>
          </div>
          <Space wrap>
            {selectedRunRow && <Tag color="blue">{t("Selected: {name}", { name: selectedRunRow.descriptor_name })}</Tag>}
            <Button
              size="small"
              icon={<ArrowRight16Regular />}
              disabled={selectedRunRow?.status !== "COMPLETED"}
              onClick={() => st.setPage("analysis")}
            >
              {t("Open Analysis")}
            </Button>
            <Button size="small" icon={<ArrowSync16Regular />} loading={refreshing} onClick={() => void refresh()}>
              {t("Refresh")}
            </Button>
          </Space>
        </div>

        {resultRuns.length ? (
          <Table
            className="descriptor-results-table"
            size="small"
            tableLayout="fixed"
            pagination={resultRuns.length > RESULTS_PAGE_SIZE ? { pageSize: RESULTS_PAGE_SIZE, showSizeChanger: false } : false}
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
              { title: t("Descriptor"), dataIndex: "descriptor_name", key: "descriptor", width: descriptorColumnWidth },
              { title: t("Scope"), dataIndex: "scope", key: "scope", width: 84 },
              {
                title: t("Device"),
                dataIndex: "device",
                key: "device",
                width: 76,
                align: "center" as const,
                render: (value: string | null | undefined) => deviceLabel(value),
              },
              {
                title: t("Shape"),
                dataIndex: "shape",
                key: "shape",
                width: 130,
                align: "center" as const,
                render: (value: string | null | undefined) => value
                  ? <Typography.Text code style={{ fontSize: 11 }} title={value}>{value}</Typography.Text>
                  : <Typography.Text type="secondary">—</Typography.Text>,
              },
              {
                title: t("Status"),
                dataIndex: "status",
                key: "status",
                width: 100,
                render: (value: string) => (
                  <Typography.Text style={{ color: RUN_STATUS_COLOR[value] ?? "#616161", fontWeight: 600, fontSize: 12 }}>
                    {jobStatusLabel(tr, value)}
                  </Typography.Text>
                ),
              },
              {
                title: t("Compute time"),
                key: "compute_time",
                width: 112,
                render: (_value: unknown, run: RunRow) => {
                  const value = formatComputeDuration(run.started_at, run.finished_at);
                  return value === "—"
                    ? <Typography.Text type="secondary">{value}</Typography.Text>
                    : <Typography.Text>{value}</Typography.Text>;
                },
              },
              { title: t("Created"), dataIndex: "created_at", key: "created", width: 168, render: (value: string) => new Date(value).toLocaleString() },
              {
                title: t("Actions"),
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
                          aria-label={t("Analyze {name} result", { name: run.descriptor_name })}
                          title={t("Open in Analysis")}
                          icon={<ArrowRight16Regular />}
                          disabled={run.status !== "COMPLETED"}
                          onClick={() => { st.setActiveRun(run.id); st.setPage("analysis"); }}
                        />
                        <Popconfirm
                          title={t("Delete this descriptor result?")}
                          description={t("This also removes linked analysis history and stored files.")}
                          okText={t("Delete")}
                          cancelText={t("Cancel")}
                          okButtonProps={{ danger: true }}
                          disabled={active}
                          onConfirm={() => void deleteRun(run)}
                        >
                          <Button
                            size="small"
                            type="text"
                            aria-label={t("Delete {name} result", { name: run.descriptor_name })}
                            title={active ? t("Cancel the running job first") : t("Delete descriptor result")}
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
          <Empty description={t("No descriptor results yet — compute a descriptor first")} image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </section>
    </div>
  );
}

import { useCallback, useRef, useEffect, useMemo, useState } from "react";
import { Alert, App as AntApp, Button, Empty, Modal, Popconfirm, Space, Table, Tag, Typography } from "antd";
import { open as openDialog, save as saveDialog } from "@tauri-apps/plugin-dialog";
import {
  DataScatterRegular,
  ArrowSync16Regular,
  ArrowExport16Regular,
  Delete16Regular,
} from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { useActiveDataset, useWorkspace } from "../stores/workspace";
import { jobStatusLabel } from "../stores/jobs";
import { displayableDescriptorRuns, stalenessNote } from "./analysisPreview";
import { useT } from "../i18n";
import type { RunRow } from "../types/protocol";
import { formatComputeDuration } from "../util/duration";
import { describeError } from "../util/errors";

const RUN_STATUS_COLOR: Record<string, string> = {
  QUEUED: "#616161",
  RUNNING: "#0F6CBD",
  COMPLETED: "#107C10",
  STALE: "#F0A000",
  FAILED: "#C42B1C",
  CANCELLED: "#8A8A8A",
};
const RESULTS_PAGE_SIZE = 15;

function deviceLabel(value: string | null | undefined): string {
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
  const { t, tr } = useT();
  const dataset = useActiveDataset();
  const datasetId = dataset?.id;
  const selectedRun = useWorkspace((state) => state.activeDescriptorRunId);
  const setActiveRun = useWorkspace((state) => state.setActiveRun);
  const setPage = useWorkspace((state) => state.setPage);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [deletingRunId, setDeletingRunId] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [transferring, setTransferring] = useState(false);
  const [exportingRunId, setExportingRunId] = useState<string | null>(null);
  const [transferError, setTransferError] = useState<string | null>(null);

  // See Analysis.tsx: an old `result.list` answer must not write the new
  // dataset's table, nor pick the active run out of a list it never described
  // (pass 5, 5-B3).
  const refreshGeneration = useRef(0);
  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
    if (!datasetId) {
      setRuns([]);
      return;
    }
    setRefreshing(true);
    try {
      const resultRows = await ipc.request<RunRow[]>("result.list", { dataset_id: datasetId });
      if (generation !== refreshGeneration.current) return;
      setRuns(resultRows);
      const completed = displayableDescriptorRuns(resultRows).filter((run) => run.status === "COMPLETED");
      const current = useWorkspace.getState().activeDescriptorRunId;
      if (!current || !completed.some((run) => run.id === current)) {
        useWorkspace.getState().setActiveRun(completed[0]?.id ?? null);
      }
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(describeError(err, "RESULT", "Could not load descriptor results"));
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
      message.error(describeError(err, "RESULT", "delete failed"));
    } finally {
      setDeletingRunId(null);
    }
  };

  const transfer = async (kind: "import" | "export", runId?: string) => {
    setTransferring(true);
    setExportingRunId(kind === "export" ? runId ?? null : null);
    setTransferError(null);
    try {
      const filters = [{ name: "NumPy NPZ", extensions: ["npz"] }];
      const path = kind === "import"
        ? await openDialog({ multiple: false, directory: false, filters })
        : await saveDialog({ defaultPath: "descriptor.npz", filters });
      if (typeof path !== "string") return;
      const result = await ipc.request<{ run_id?: string }>(`result.${kind}`, {
        path, dataset_id: datasetId, run_id: runId,
      });
      if (kind === "import") {
        setImportOpen(false);
        if (useWorkspace.getState().activeDatasetId === datasetId) {
          await refresh();
          if (result.run_id && useWorkspace.getState().activeDatasetId === datasetId) setActiveRun(result.run_id);
        }
      }
      message.success(tr({ en: "Descriptor transfer completed", zh: "描述符导入/导出完成" }));
    } catch (error) {
      setTransferError(describeError(error as { code?: string; message?: string }, "RESULT", "Descriptor transfer failed"));
    } finally {
      setTransferring(false);
      setExportingRunId(null);
    }
  };

  if (!dataset) return <Empty description={t("Register a dataset first")} style={{ marginTop: 120 }} />;

  return (
    <div className="descriptor-results-page">
      <Modal title={tr({ en: "Import custom descriptors", zh: "导入自定义描述符" })}
        open={importOpen} onCancel={() => { if (!transferring) setImportOpen(false); }}
        onOk={() => void transfer("import")} confirmLoading={transferring}
        okText={tr({ en: "Choose NPZ and import", zh: "选择 NPZ 并导入" })} cancelText={t("Cancel")}>
        <Typography.Paragraph>{tr({
          en: "Import into the active dataset. Rows must follow its frame order; atom rows must also follow each frame's atom order. Register the matching structures first.",
          zh: "导入到当前数据集。行必须遵循数据集的帧顺序；原子级数据还须遵循每帧的原子顺序。请先注册对应结构数据集。",
        })}</Typography.Paragraph>
        <Typography.Paragraph>{tr({
          en: "NPZ requires a finite numeric matrix values [rows, features] and a JSON string metadata with descriptor and row_semantics (structure or atom). Atom descriptors also require integer row_offsets [0, n₀, n₀+n₁, …].",
          zh: "NPZ 须包含有限数值矩阵 values [行数, 特征数]，以及 JSON 字符串 metadata，其中指定 descriptor 名称和 row_semantics（structure 或 atom）。原子级还须提供整数 row_offsets [0, n₀, n₀+n₁, …]。",
        })}</Typography.Paragraph>
        <details>
          <summary style={{ cursor: "pointer" }}>{tr({ en: "Atom-level save example", zh: "原子级保存脚本范例" })}</summary>
          <Typography.Paragraph style={{ marginTop: 8 }}>{tr({
            en: "Supply one matrix per frame in frame_descriptors, with shape [atoms in that frame, features]. Keep the dataset's frame and atom order, and use the same feature columns in every frame.",
            zh: "将每帧自行计算的矩阵放入 frame_descriptors，每个矩阵形状为 [该帧原子数, 特征数]。保持数据集的帧顺序和帧内原子顺序，各帧的特征列定义必须一致。",
          })}</Typography.Paragraph>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{`import json
import numpy as np

# frame_descriptors = [X_frame0, X_frame1, ...]
# X_frame0.shape == (n_atoms_frame0, n_features)
values = np.concatenate(frame_descriptors, axis=0)
atom_counts = [len(x) for x in frame_descriptors]
row_offsets = np.concatenate((
    [0], np.cumsum(atom_counts, dtype=np.int64)
))

np.savez_compressed(
    "custom_atom.npz",
    values=values,
    row_offsets=row_offsets,
    metadata=json.dumps({
        "descriptor": "MyAtomDescriptor",
        "row_semantics": "atom",
        "descriptor_version": "1.0",
        "configuration": {}
    })
)`}</pre>
        </details>
        {transferError && <Alert type="error" showIcon role="alert" message={transferError} />}
      </Modal>
      {!importOpen && transferError && <Alert type="error" showIcon role="alert" message={transferError} closable onClose={() => setTransferError(null)} />}
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
            <Button size="small" disabled={transferring} onClick={() => { setTransferError(null); setImportOpen(true); }}>
              {tr({ en: "Import descriptors", zh: "导入描述符" })}
            </Button>
            {selectedRunRow && <Tag color="blue">{t("Selected: {name}", { name: selectedRunRow.descriptor_name })}</Tag>}
            <Button
              size="small"
              icon={<DataScatterRegular fontSize={16} />}
              disabled={selectedRunRow?.status !== "COMPLETED"}
              onClick={() => setPage("analysis")}
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
              onClick: () => { if (run.status === "COMPLETED") setActiveRun(run.id); },
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
                width: 92,
                align: "center" as const,
                render: (value: string | null | undefined) => value === "imported" || value === "external"
                  ? tr({ en: "Imported", zh: "导入" }) : deviceLabel(value),
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
                render: (value: string, row: RunRow) => {
                  const note = stalenessNote(row.status, row.error_message);
                  return (
                    <Typography.Text
                      style={{
                        color: RUN_STATUS_COLOR[value] ?? "#616161",
                        fontWeight: 600,
                        fontSize: 12,
                        cursor: note ? "help" : undefined,
                      }}
                      title={note ?? undefined}
                    >
                      {jobStatusLabel(tr, value)}
                    </Typography.Text>
                  );
                },
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
                width: 136,
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
                          icon={<DataScatterRegular fontSize={16} />}
                          disabled={run.status !== "COMPLETED"}
                          onClick={() => { setActiveRun(run.id); setPage("analysis"); }}
                        />
                        <Button
                          size="small"
                          type="text"
                          aria-label={tr({ en: `Export ${run.descriptor_name} result`, zh: `导出 ${run.descriptor_name} 结果` })}
                          title={tr({ en: "Export NPZ", zh: "导出 NPZ" })}
                          icon={<ArrowExport16Regular />}
                          loading={exportingRunId === run.id}
                          disabled={transferring || run.status !== "COMPLETED"}
                          onClick={() => void transfer("export", run.id)}
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

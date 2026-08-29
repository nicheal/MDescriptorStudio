import { useMemo, useState } from "react";
import { App as AntApp, Button, Dropdown, Empty, Input, Modal, Space, Tooltip, Typography } from "antd";
import { MoreOutlined, SearchOutlined } from "@ant-design/icons";
import {
  Add16Regular,
  ChevronLeft16Regular,
  ChevronRight16Regular,
  Document16Regular,
  FolderOpen16Regular,
} from "@fluentui/react-icons";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { ipc } from "../../ipc/client";
import { RenameDatasetModal, useDatasetDelete } from "../datasetActions";
import { useWorkspace } from "../../stores/workspace";
import type { DatasetMeta } from "../../types/protocol";

export default function Sidebar() {
  const { message } = AntApp.useApp();
  const confirmDelete = useDatasetDelete();
  const [renameTarget, setRenameTarget] = useState<DatasetMeta | null>(null);
  const { datasets, setActiveDataset } = useWorkspace();
  const [collapsed, setCollapsed] = useState(false);
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const filtered = useMemo(
    () =>
      datasets.filter(
        (d) => !query || d.name.toLowerCase().includes(query.toLowerCase()),
      ),
    [datasets, query],
  );

  const totalBytes = useMemo(
    () => datasets.reduce((s, d) => s + (d.file_size ?? 0), 0),
    [datasets],
  );

  const register = async () => {
    if (!path.trim()) return;
    setBusy(true);
    try {
      const r = await ipc.request<{ job_id: string | null; cache: unknown }>(
        "dataset.register",
        { path: path.trim(), name: name.trim() || undefined },
      );
      if (r.job_id) {
        setAdding(false);
        setPath("");
        setName("");
        message.info("Registering dataset…");
        const off = ipc.on("job.finished", (data) => {
          const d = data as { job_id: string; status: string; error: { message: string } | null };
          if (d.job_id !== r.job_id) return;
          off();
          if (d.status === "COMPLETED") {
            void refreshAfterRegister();
            message.success("Dataset added");
          } else {
            message.error(`Register failed: ${d.error?.message ?? d.status}`);
          }
        });
      }
    } catch (e) {
      const err = e as { code: string; message: string };
      message.error(`${err.code}: ${err.message}`);
    } finally {
      setBusy(false);
    }
  };

  const refreshAfterRegister = async () => {
    const list = await ipc.request<DatasetMeta[]>("dataset.list");
    useWorkspace.getState().setDatasets(list);
    const first = list[0];
    if (!useWorkspace.getState().activeDatasetId && first) setActiveDataset(first.id);
  };

  const browse = async (directory: boolean) => {
    try {
      const selected = await openDialog(
        directory
          ? { directory: true, multiple: false, title: "Select DeepMD dataset folder" }
          : {
              multiple: false,
              title: "Select extxyz dataset file",
              filters: [{ name: "extxyz", extensions: ["xyz", "extxyz"] }],
            },
      );
      const p = Array.isArray(selected) ? selected[0] : selected;
      if (p) setPath(p);
    } catch {
      message.error("Could not open the file dialog");
    }
  };

  if (collapsed) {
    return (
      <div
        style={{
          width: 44,
          flex: "0 0 44px",
          borderRight: "1px solid #E1E4E8",
          background: "#FAFAFA",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          padding: "12px 0",
          gap: 8,
        }}
      >
        <Tooltip title="Show datasets" placement="right">
          <Button
            type="text"
            size="small"
            icon={<ChevronRight16Regular />}
            onClick={() => setCollapsed(false)}
          />
        </Tooltip>
      </div>
    );
  }

  return (
    <div
      style={{
        width: 240,
        flex: "0 0 240px",
        borderRight: "1px solid #E1E4E8",
        background: "#FAFAFA",
        display: "flex",
        flexDirection: "column",
        padding: "12px 12px",
        gap: 8,
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Typography.Text strong style={{ fontSize: 12, color: "#616161", letterSpacing: 1 }}>
          DATASETS
        </Typography.Text>
        <Tooltip title="Collapse sidebar">
          <Button
            type="text"
            size="small"
            icon={<ChevronLeft16Regular />}
            onClick={() => setCollapsed(true)}
          />
        </Tooltip>
      </div>
      <Input
        size="small"
        prefix={<SearchOutlined style={{ color: "#8A8A8A" }} />}
        placeholder="Search datasets..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <Button
        icon={<Add16Regular />}
        onClick={() => setAdding(true)}
        style={{ justifyContent: "flex-start" }}
      >
        Add Dataset
      </Button>
      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        {filtered.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<span style={{ fontSize: 12 }}>No datasets</span>}
            style={{ marginTop: 24 }}
          />
        ) : (
          filtered.map((d) => (
            <DatasetItem key={d.id} d={d} onRename={setRenameTarget} onDelete={confirmDelete} />
          ))
        )}
      </div>
      <div style={{ borderTop: "1px solid #EAECF0", paddingTop: 8 }}>
        <Typography.Text strong style={{ fontSize: 11, color: "#616161", letterSpacing: 0.5 }}>
          Dataset Storage
        </Typography.Text>
        <div style={{ fontSize: 12, color: "#242424", fontVariantNumeric: "tabular-nums", marginTop: 2 }}>
          {datasets.length > 0 && totalBytes > 0
            ? `${formatSize(totalBytes)} · ${datasets.length} dataset${datasets.length > 1 ? "s" : ""}`
            : "—"}
        </div>
      </div>
      <Modal
        title="Add Dataset"
        open={adding}
        onCancel={() => setAdding(false)}
        onOk={register}
        okText="Register"
        confirmLoading={busy}
        okButtonProps={{ disabled: !path.trim() }}
      >
        <Space direction="vertical" style={{ width: "100%" }} size={12}>
          <Space.Compact style={{ width: "100%" }}>
            <Input
              placeholder="D:\datasets\GaAs  (DeepMD directory or .xyz file)"
              value={path}
              onChange={(e) => setPath(e.target.value)}
            />
            <Button
              icon={<FolderOpen16Regular />}
              onClick={() => void browse(true)}
              title="Browse for a DeepMD dataset folder"
            />
            <Button
              icon={<Document16Regular />}
              onClick={() => void browse(false)}
              title="Browse for an .xyz / .extxyz file"
            />
          </Space.Compact>
          <Input
            placeholder="Display name (optional)"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Format is auto-detected (DeepMD raw directory / extxyz file). The source data is
            registered read-only, never copied.
          </Typography.Text>
        </Space>
      </Modal>
      <RenameDatasetModal
        dataset={renameTarget}
        open={renameTarget !== null}
        onClose={() => setRenameTarget(null)}
      />
    </div>
  );
}

function DatasetItem({
  d,
  onRename,
  onDelete,
}: {
  d: DatasetMeta;
  onRename: (d: DatasetMeta) => void;
  onDelete: (d: DatasetMeta) => void;
}) {
  const { setActiveDataset, activeDatasetId } = useWorkspace();
  const active = d.id === activeDatasetId;
  return (
    <div
      className="dataset-item"
      onClick={() => setActiveDataset(d.id)}
      style={{
        padding: "8px 10px",
        marginBottom: 4,
        borderRadius: 6,
        cursor: "pointer",
        background: active ? "#EBF3FC" : "transparent",
        borderLeft: active ? "3px solid #0F6CBD" : "3px solid transparent",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 2 }}>
        <div
          style={{
            flex: 1,
            minWidth: 0,
            fontSize: 13,
            fontWeight: active ? 600 : 400,
            color: active ? "#0F6CBD" : "#242424",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {d.name}
          {!d.cache_valid && (
            <Typography.Text type="warning" style={{ fontSize: 11, marginLeft: 6 }}>
              changed
            </Typography.Text>
          )}
        </div>
        <Dropdown
          menu={{
            items: [
              { key: "rename", label: "Rename" },
              { key: "delete", label: "Delete", danger: true },
            ],
            onClick: ({ key, domEvent }) => {
              domEvent.stopPropagation();
              if (key === "rename") onRename(d);
              if (key === "delete") onDelete(d);
            },
          }}
          trigger={["click"]}
        >
          <Button
            className="dataset-item-actions"
            type="text"
            size="small"
            icon={<MoreOutlined />}
            onClick={(e) => e.stopPropagation()}
            style={{ height: 20, width: 20, minWidth: 0, marginRight: -4, flex: "0 0 auto" }}
          />
        </Dropdown>
      </div>
      <div style={{ fontSize: 11, color: "#616161" }}>
        {formatLabel(d.format)} · {d.number_of_frames.toLocaleString()} structures
      </div>
      <div style={{ fontSize: 11, color: "#8A8A8A" }}>
        {d.elements.join(" · ")} · PBC {d.periodicity.flags.join("") || "—"}
      </div>
    </div>
  );
}

function formatLabel(format: string): string {
  return format.charAt(0).toUpperCase() + format.slice(1);
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}

import { useMemo, useState } from "react";
import { App as AntApp, Button, Empty, Input, Modal, Space, Typography } from "antd";
import { SearchOutlined } from "@ant-design/icons";
import { Add16Regular } from "@fluentui/react-icons";
import { ipc } from "../../ipc/client";
import { useWorkspace } from "../../stores/workspace";
import type { DatasetMeta } from "../../types/protocol";

export default function Sidebar() {
  const { message } = AntApp.useApp();
  const { datasets, setActiveDataset } = useWorkspace();
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

  return (
    <div
      style={{
        width: 240,
        borderRight: "1px solid #E1E4E8",
        background: "#FAFAFA",
        display: "flex",
        flexDirection: "column",
        padding: "12px 12px",
        gap: 8,
      }}
    >
      <Typography.Text strong style={{ fontSize: 12, color: "#616161", letterSpacing: 1 }}>
        DATASETS
      </Typography.Text>
      <Input
        size="small"
        prefix={<SearchOutlined style={{ color: "#8A8A8A" }} />}
        placeholder="Search datasets..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div style={{ flex: 1, overflowY: "auto" }}>
        {filtered.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<span style={{ fontSize: 12 }}>No datasets</span>}
            style={{ marginTop: 24 }}
          />
        ) : (
          filtered.map((d) => <DatasetItem key={d.id} d={d} />)
        )}
      </div>
      <Button
        icon={<Add16Regular />}
        onClick={() => setAdding(true)}
        style={{ justifyContent: "flex-start" }}
      >
        Add Dataset
      </Button>
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
          <Input
            placeholder="D:\datasets\GaAs  (DeepMD directory or .xyz file)"
            value={path}
            onChange={(e) => setPath(e.target.value)}
          />
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
    </div>
  );
}

function DatasetItem({ d }: { d: DatasetMeta }) {
  const { setActiveDataset, activeDatasetId } = useWorkspace();
  const active = d.id === activeDatasetId;
  return (
    <div
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
      <div
        style={{
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
      <div style={{ fontSize: 11, color: "#616161" }}>
        {d.format} · {d.number_of_frames.toLocaleString()} structures
      </div>
      <div style={{ fontSize: 11, color: "#8A8A8A" }}>
        {d.elements.join(" · ")} · PBC {d.periodicity.flags[0] ?? "—"}
      </div>
    </div>
  );
}

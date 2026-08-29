// Settings + About (M5, ADR-12): default threads, data dir, log folder path,
// About with component versions (design doc §52).
import { useEffect, useState } from "react";
import { Button, Descriptions, Drawer, InputNumber, Space, Typography } from "antd";
import { Settings16Regular } from "@fluentui/react-icons";
import { ipc } from "../ipc/client";

interface SystemInfo {
  backend_version: string;
  mdescriptor_version: string;
  mdescriptor_api_version: number;
  protocol_version: number;
  data_dir: string;
  cpu_threads: number;
}

export default function SettingsDrawer() {
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [defaultThreads, setDefaultThreads] = useState<number | null>(null);

  useEffect(() => {
    if (!open) return;
    (async () => {
      const i = await ipc.request<SystemInfo>("system.info");
      setInfo(i);
      const t = await ipc.request<{ value: string | null }>("settings.get", {
        key: "compute.default_threads",
      });
      setDefaultThreads(t.value ? Number(t.value) : null);
    })();
  }, [open]);

  return (
    <>
      <Button
        type="text"
        icon={<Settings16Regular />}
        onClick={() => setOpen(true)}
        title="Settings"
      />
      <Drawer
        title="SETTINGS"
        placement="right"
        width={400}
        open={open}
        onClose={() => setOpen(false)}
      >
        <Typography.Text strong style={{ fontSize: 12, color: "#616161" }}>
          COMPUTE
        </Typography.Text>
        <div style={{ margin: "8px 0 20px" }}>
          <Typography.Text style={{ fontSize: 13, display: "block", marginBottom: 4 }}>
            Default thread count
          </Typography.Text>
          <Space>
            <InputNumber
              min={1}
              max={64}
              value={defaultThreads ?? undefined}
              placeholder="engine default"
              onChange={(v) => setDefaultThreads(v)}
              style={{ width: 160 }}
            />
            <Button
              onClick={async () => {
                if (defaultThreads) {
                  await ipc.request("settings.set", {
                    key: "compute.default_threads",
                    value: String(defaultThreads),
                  });
                }
              }}
            >
              Save
            </Button>
          </Space>
        </div>

        <Typography.Text strong style={{ fontSize: 12, color: "#616161" }}>
          STORAGE
        </Typography.Text>
        <Typography.Paragraph code style={{ fontSize: 11, marginTop: 8, whiteSpace: "pre-wrap" }}>
          {info?.data_dir ?? "—"}
        </Typography.Paragraph>
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          Logs: {info ? `${info.data_dir}\\logs\\backend.log` : "—"}
        </Typography.Text>

        <Typography.Text strong style={{ fontSize: 12, color: "#616161", display: "block", marginTop: 24 }}>
          ABOUT
        </Typography.Text>
        <Descriptions
          column={1}
          size="small"
          colon={false}
          style={{ marginTop: 8 }}
          labelStyle={{ width: 150, color: "#616161", fontSize: 13 }}
          contentStyle={{ fontSize: 13, fontVariantNumeric: "tabular-nums" }}
        >
          <Descriptions.Item label="MDescriptor Studio">0.1.0</Descriptions.Item>
          <Descriptions.Item label="Backend">{info?.backend_version ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="MDescriptor">{info?.mdescriptor_version ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="Protocol">{info?.protocol_version ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="Engine API">v{info?.mdescriptor_api_version ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="Platform">Windows x64</Descriptions.Item>
        </Descriptions>
        <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 16 }}>
          DESCRIBE · ANALYZE · DISCOVER
        </Typography.Text>
      </Drawer>
    </>
  );
}

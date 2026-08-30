// Settings + About (M5, ADR-12): default threads, data dir, log folder path,
// language (EN/ZH), About with component versions (design doc §52) + engine
// update (PyPI).
import { useEffect, useState } from "react";
import { Button, Descriptions, Drawer, InputNumber, Radio, Space, Typography } from "antd";
import { invoke } from "@tauri-apps/api/core";
import { Settings16Regular } from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { useEngineUpdate } from "../stores/engineUpdate";
import { useWorkspace } from "../stores/workspace";
import { useI18n, useT } from "../i18n";

interface SystemInfo {
  backend_version: string;
  mdescriptor_version: string;
  mdescriptor_api_version: number;
  mdescriptor_baseline_version?: string;
  mdescriptor_descriptor_info_schema_version?: number;
  protocol_version: number;
  data_dir: string;
  cpu_threads: number;
}

export default function SettingsDrawer() {
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [defaultThreads, setDefaultThreads] = useState<number | null>(null);
  const upd = useEngineUpdate();
  const setBackendStarting = useWorkspace((s) => s.setBackendStarting ?? (() => {}));
  const { t } = useT();
  const lang = useI18n((s) => s.lang);
  const setLang = useI18n((s) => s.setLang);

  const restartBackend = async () => {
    setBackendStarting();
    await invoke("backend_restart");
  };

  useEffect(() => {
    if (!open) return;
    (async () => {
      const i = await ipc.request<SystemInfo>("system.info");
      setInfo(i);
      const th = await ipc.request<{ value: string | null }>("settings.get", {
        key: "compute.default_threads",
      });
      setDefaultThreads(th.value ? Number(th.value) : null);
    })();
  }, [open]);

  return (
    <>
      <Button
        type="text"
        icon={<Settings16Regular />}
        onClick={() => setOpen(true)}
        title={t("Settings")}
      />
      <Drawer
        title={t("SETTINGS")}
        placement="right"
        width={400}
        open={open}
        onClose={() => setOpen(false)}
      >
        <Typography.Text strong style={{ fontSize: 12, color: "#616161" }}>
          {t("LANGUAGE")}
        </Typography.Text>
        <div style={{ margin: "8px 0 20px" }}>
          <Radio.Group
            value={lang}
            onChange={(e) => setLang(e.target.value as "en" | "zh")}
            optionType="button"
            buttonStyle="solid"
            options={[
              { value: "en", label: "English" },
              { value: "zh", label: "简体中文" },
            ]}
          />
        </div>

        <Typography.Text strong style={{ fontSize: 12, color: "#616161" }}>
          {t("COMPUTE")}
        </Typography.Text>
        <div style={{ margin: "8px 0 20px" }}>
          <Typography.Text style={{ fontSize: 13, display: "block", marginBottom: 4 }}>
            {t("Default thread count")}
          </Typography.Text>
          <Space>
            <InputNumber
              min={1}
              max={64}
              value={defaultThreads ?? undefined}
              placeholder={t("engine default")}
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
              {t("Save")}
            </Button>
          </Space>
        </div>

        <Typography.Text strong style={{ fontSize: 12, color: "#616161" }}>
          {t("STORAGE")}
        </Typography.Text>
        <Typography.Paragraph code style={{ fontSize: 11, marginTop: 8, whiteSpace: "pre-wrap" }}>
          {info?.data_dir ?? "—"}
        </Typography.Paragraph>
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          {t("Logs: {path}", { path: info ? `${info.data_dir}\\logs\\backend.log` : "—" })}
        </Typography.Text>

        <Typography.Text strong style={{ fontSize: 12, color: "#616161", display: "block", marginTop: 24 }}>
          {t("ENGINE UPDATE")}
        </Typography.Text>
        <div style={{ marginTop: 8, fontSize: 13 }}>
          <Row k={t("Installed")} v={upd.installed || info?.mdescriptor_version || "—"} />
          <Row k={t("Latest (PyPI)")} v={upd.latest ?? "—"} />
          <Row k={t("Status")} v={statusLabel(upd.status, t)} />
          {upd.error && (
            <Typography.Text type="danger" style={{ fontSize: 11 }}>
              {upd.error}
            </Typography.Text>
          )}
          <div style={{ marginTop: 8 }}>
            {upd.status === "restart_required" ? (
              <Button type="primary" onClick={() => void restartBackend()}>
                {t("Restart backend to apply")}
              </Button>
            ) : upd.status === "available" ? (
              <Button type="primary" onClick={() => void upd.runUpdate()}>
                {t("Update to {version}", { version: upd.latest ?? "" })}
              </Button>
            ) : upd.status === "unsupported" ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t("Engine ships inside the installer — download a new setup.exe to upgrade.")}
              </Typography.Text>
            ) : upd.status === "updating" ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t("Updating…")}
              </Typography.Text>
            ) : upd.status === "checking" ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t("Checking PyPI…")}
              </Typography.Text>
            ) : (
              <Space>
                <Button size="small" onClick={() => void upd.refresh()}>
                  {t("Re-check")}
                </Button>
                {upd.status === "up_to_date" && (
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    {t("ADR-2: after any update, rerun scripts/probe_engine.py + pytest.")}
                  </Typography.Text>
                )}
              </Space>
            )}
          </div>
        </div>

        <Typography.Text strong style={{ fontSize: 12, color: "#616161", display: "block", marginTop: 24 }}>
          {t("ABOUT")}
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
          <Descriptions.Item label={t("Backend")}>{info?.backend_version ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="MDescriptor">{info?.mdescriptor_version ?? "—"}</Descriptions.Item>
          <Descriptions.Item label={t("Protocol")}>{info?.protocol_version ?? "—"}</Descriptions.Item>
          <Descriptions.Item label={t("Engine API")}>v{info?.mdescriptor_api_version ?? "—"}</Descriptions.Item>
          <Descriptions.Item label={t("GUI schema")}>
            {info?.mdescriptor_baseline_version ?? "—"} / {info?.mdescriptor_descriptor_info_schema_version ?? "—"}
          </Descriptions.Item>
          <Descriptions.Item label={t("Platform")}>Windows x64</Descriptions.Item>
        </Descriptions>
        <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 16 }}>
          {t("DESCRIBE · ANALYZE · DISCOVER")}
        </Typography.Text>
      </Drawer>
    </>
  );
}

function statusLabel(s: string, t: (key: string) => string): string {
  switch (s) {
    case "up_to_date":
      return t("Up to date");
    case "available":
      return t("Update available");
    case "checking":
      return t("Checking PyPI…");
    case "updating":
      return t("Updating…");
    case "restart_required":
      return t("Updated — restart pending");
    case "unsupported":
      return t("In-app update unavailable");
    case "error":
      return t("Check failed");
    default:
      return t("Not checked");
  }
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "3px 0" }}>
      <span style={{ color: "#616161" }}>{k}</span>
      <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 500 }}>{v}</span>
    </div>
  );
}

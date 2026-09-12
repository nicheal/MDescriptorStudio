// Settings + About (M5, ADR-12): default threads, data dir, log folder path,
// language (EN/ZH), About with component versions (design doc §52) + app update.
import { useEffect, useState } from "react";
import { Button, Descriptions, Drawer, InputNumber, Progress, Radio, Space, Typography } from "antd";
import { Settings16Regular } from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { useAppUpdate } from "../stores/appUpdate";
import { getVersion } from "@tauri-apps/api/app";
import EngineVersionCheck from "./EngineVersionCheck";
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
  const appUpdate = useAppUpdate();
  const { t } = useT();
  const lang = useI18n((s) => s.lang);
  const setLang = useI18n((s) => s.setLang);

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

  useEffect(() => {
    if (open) void getVersion().then((current) => useAppUpdate.setState({ current })).catch(() => undefined);
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
          <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 4 }}>
            {t("Caps BLAS/OpenMP threads for analysis compute; descriptor runs are managed by the engine.")}
          </Typography.Text>
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

        {open && <EngineVersionCheck installed={info?.mdescriptor_version} />}

        <Typography.Text strong style={{ fontSize: 12, color: "#616161", display: "block", marginTop: 24 }}>
          {t("Studio application update")}
        </Typography.Text>
        <div style={{ marginTop: 8, fontSize: 13 }}>
          <Typography.Paragraph type="secondary">
            {t("Studio updates require a publicly accessible release source; the current repository is private.")}
          </Typography.Paragraph>
          <Row k={t("Installed")} v={appUpdate.current || "—"} />
          <Row k={t("Latest release")} v={appUpdate.latest ?? "—"} />
          <Row k={t("Status")} v={appStatusLabel(appUpdate.status, t)} />
          {appUpdate.notes && (
            <Typography.Paragraph type="secondary" style={{ fontSize: 11, margin: "6px 0" }}>
              {appUpdate.notes}
            </Typography.Paragraph>
          )}
          {appUpdate.progress !== null && (
            <Progress percent={appUpdate.progress} size="small" showInfo={false} style={{ margin: "4px 0" }} />
          )}
          {appUpdate.error && (
            <Typography.Text type="danger" style={{ fontSize: 11 }}>
              {appUpdate.error}
            </Typography.Text>
          )}
          <div style={{ marginTop: 8 }}>
            <Space direction="vertical" size={6}>
              <Space>
                <Button size="small" onClick={() => void appUpdate.refresh()} loading={appUpdate.status === "checking"} disabled={appUpdate.status === "installing"}>
                  {t("Check for updates")}
                </Button>
                {appUpdate.status === "available" && (
                  <Button type="primary" onClick={() => void appUpdate.install()}>
                    {t("Install and restart")}
                  </Button>
                )}
              </Space>
              {appUpdate.status === "installing" && (
                <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                  {t("The app will close and restart to finish the update.")}
                </Typography.Text>
              )}
            </Space>
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
          <Descriptions.Item label="MDescriptor Studio">{appUpdate.current || "—"}</Descriptions.Item>
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

function appStatusLabel(s: string, t: (key: string) => string): string {
  switch (s) {
    case "up_to_date":
      return t("Up to date");
    case "available":
      return t("Update available");
    case "installing":
      return t("Installing update…");
    case "installed":
      return t("Updated — restarting");
    case "checking":
      return t("Checking for updates…");
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

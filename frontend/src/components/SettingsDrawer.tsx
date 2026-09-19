// Settings + About (M5, ADR-12): default threads, data dir, log folder path,
// language (EN/ZH), About with component versions (design doc §52) + app update.
import { useEffect, useState } from "react";
import { Button, Descriptions, Drawer, InputNumber, Progress, Radio, Space, Typography } from "antd";
import { Settings16Regular } from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { useAppUpdate } from "../stores/appUpdate";
import { getVersion } from "@tauri-apps/api/app";
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
  const [loadFailed, setLoadFailed] = useState(false);
  const [saveState, setSaveState] = useState<"saving" | "saved" | "failed" | null>(null);
  const appUpdate = useAppUpdate();
  const { t } = useT();
  const lang = useI18n((s) => s.lang);
  const setLang = useI18n((s) => s.setLang);

  useEffect(() => {
    if (!open) return;
    let disposed = false;
    (async () => {
      try {
        const i = await ipc.request<SystemInfo>("system.info");
        const th = await ipc.request<{ value: string | null }>("settings.get", {
          key: "compute.default_threads",
        });
        if (disposed) return;
        setInfo(i);
        setDefaultThreads(th.value ? Number(th.value) : null);
        setLoadFailed(false);
      } catch (error) {
        // Both calls reject while the backend is down. Without this the drawer
        // just shows "—" for everything and the rejection escapes unhandled.
        console.error("settings load failed", error);
        if (!disposed) setLoadFailed(true);
      }
    })();
    return () => {
      disposed = true;
    };
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
              onChange={(v) => {
                setDefaultThreads(v);
                setSaveState(null);
              }}
              style={{ width: 160 }}
            />
            <Button
              loading={saveState === "saving"}
              onClick={async () => {
                // Always write: the backend reads an empty value as "engine
                // default" (job_runner._apply_thread_limit), so clearing the
                // box has to be able to reset the override — with `if
                // (defaultThreads)` here a set value could never be removed.
                const value = defaultThreads === null ? "" : String(defaultThreads);
                setSaveState("saving");
                try {
                  await ipc.request("settings.set", { key: "compute.default_threads", value });
                  setSaveState("saved");
                } catch (error) {
                  console.error("settings.set failed", error);
                  setSaveState("failed");
                }
              }}
            >
              {t("Save")}
            </Button>
          </Space>
          {loadFailed && (
            <Typography.Text type="danger" style={{ fontSize: 11, display: "block", marginTop: 4 }}>
              {t("could not load settings")}
            </Typography.Text>
          )}
          {saveState === "failed" && (
            <Typography.Text type="danger" style={{ fontSize: 11, display: "block", marginTop: 4 }}>
              {t("save failed")}
            </Typography.Text>
          )}
          {saveState === "saved" && (
            <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 4 }}>
              {t("saved")}
            </Typography.Text>
          )}
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

        <Typography.Text strong style={{ fontSize: 12, color: "#616161", display: "block", marginTop: 24 }}>
          {t("ABOUT")}
        </Typography.Text>
        <div className="settings-about-update">
          <div className="settings-about-update-header">
            <div className="settings-about-update-title">
              <Typography.Text strong>MDescriptor Studio</Typography.Text>
              <Typography.Text type="secondary" className="settings-about-update-version">
                {appUpdate.current || "—"}
              </Typography.Text>
            </div>
            <Button
              size="small"
              onClick={() => void appUpdate.refresh()}
              loading={appUpdate.status === "checking"}
              disabled={appUpdate.status === "installing"}
            >
              {t("Check for updates")}
            </Button>
          </div>
          {appUpdate.status === "available" && (
            <div className="settings-about-update-details">
              <Typography.Text type="secondary" className="settings-about-update-release">
                {t("Latest release")}: {appUpdate.latest}
              </Typography.Text>
              <Button type="primary" size="small" onClick={() => void appUpdate.install()}>
                {t("Install and restart")}
              </Button>
            </div>
          )}
          {appUpdate.status === "available" && appUpdate.notes && (
            <Typography.Paragraph type="secondary" className="settings-about-update-notes">
              {appUpdate.notes}
            </Typography.Paragraph>
          )}
          {appUpdate.progress !== null && (
            <Progress percent={appUpdate.progress} size="small" showInfo={false} style={{ margin: "8px 0 0" }} />
          )}
          {(appUpdate.status === "installing" || appUpdate.status === "installed") && (
            <Typography.Text type="secondary" className="settings-about-update-message">
              {t("The app will close and restart to finish the update.")}
            </Typography.Text>
          )}
          {appUpdate.status === "up_to_date" && (
            <Typography.Text type="secondary" className="settings-about-update-message">
              {t("Up to date")}
            </Typography.Text>
          )}
          {appUpdate.error && (
            <Typography.Text type="danger" className="settings-about-update-message">
              {appUpdate.error}
            </Typography.Text>
          )}
        </div>
        <Descriptions
          column={1}
          size="small"
          colon={false}
          style={{ marginTop: 8 }}
          labelStyle={{ width: 150, color: "#616161", fontSize: 13 }}
          contentStyle={{ fontSize: 13, fontVariantNumeric: "tabular-nums" }}
        >
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

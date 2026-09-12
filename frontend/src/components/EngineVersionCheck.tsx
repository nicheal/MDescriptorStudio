import { useEffect, useState } from "react";
import { Button, Typography } from "antd";
import { ipc } from "../ipc/client";
import { useT } from "../i18n";

interface EngineVersionState {
  installed: string;
  latest: string | null;
  status: string;
  error: string | null;
  installer_required?: boolean;
}

export default function EngineVersionCheck({ installed }: { installed?: string }) {
  const { t } = useT();
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<EngineVersionState>({
    installed: "", latest: null, status: "checking", error: null,
  });

  useEffect(() => {
    let active = true;
    let receivedEvent = false;
    setState((previous) => ({ ...previous, status: "checking", error: null }));
    // Subscribe first: a fast check can finish before the request reply arrives.
    const unsubscribe = ipc.on("engine.update.state", (data) => {
      receivedEvent = true;
      setState(data as EngineVersionState);
    });
    void ipc.request<EngineVersionState>("engine.check_update").then((result) => {
      if (active && !receivedEvent) setState(result);
    }).catch(() => {
      if (active) setState((previous) => ({ ...previous, status: "error", error: "Unable to check for engine updates" }));
    });
    return () => { active = false; unsubscribe(); };
  }, [attempt]);

  const status = state.status === "checking" ? "Checking PyPI…"
    : state.status === "available" ? "Update available"
    : state.status === "up_to_date" ? "Up to date"
    : state.status === "error" ? "Check failed" : "Not checked";

  return (
    <section aria-label={t("MDescriptor engine version")} style={{ marginTop: 24 }}>
      <Typography.Text strong>{t("MDescriptor engine version")}</Typography.Text>
      <div style={{ marginTop: 8 }}>{t("Installed")}: {state.installed || installed || "—"}</div>
      <div>{t("Latest (PyPI)")}: {state.latest ?? "—"}</div>
      <div role="status">{t("Status")}: {t(status)}</div>
      {state.error && <div role="alert"><Typography.Text type="danger">{t(state.error)}</Typography.Text></div>}
      {state.installer_required && (
        <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
          {t("Engine ships inside the installer — install a newer Studio package to upgrade.")}
        </Typography.Paragraph>
      )}
      <Button size="small" style={{ marginTop: 8 }} loading={state.status === "checking"}
        disabled={state.status === "checking"} onClick={() => setAttempt((value) => value + 1)}>
        {t("Check MDescriptor version")}
      </Button>
    </section>
  );
}

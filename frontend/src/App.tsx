import { useCallback, useEffect, type ReactNode } from "react";
import { App as AntApp, Button, Space } from "antd";
import { invoke } from "@tauri-apps/api/core";
import {
  Grid16Regular,
  Image16Regular,
  Options16Regular,
  Sparkle16Regular,
  DocumentTableRegular,
} from "@fluentui/react-icons";
import Sidebar from "./components/layout/Sidebar";
import ContextBar from "./components/layout/ContextBar";
import StatusBar from "./components/layout/StatusBar";
import RightRail from "./components/layout/RightRail";
import TitleBar from "./components/layout/TitleBar";
import Overview from "./pages/Overview";
import Explore from "./pages/Explore";
import Descriptors from "./pages/Descriptors";
import Results from "./pages/Results";
import Analysis from "./pages/Analysis";
import { ipc } from "./ipc/client";
import { useWorkspace } from "./stores/workspace";
import { wireJobEvents } from "./stores/jobs";
import { useEngineUpdate, wireEngineUpdate } from "./stores/engineUpdate";
import type { DatasetMeta } from "./types/protocol";
import { APP_ICON_URL } from "./brand";

// Jobs is not a tab — the top-right Jobs button/drawer is the single jobs
// surface, and the Descriptors/Results rail shows recent descriptor computes.
const TABS: { key: "overview" | "explore" | "descriptors" | "results" | "analysis"; label: string; icon: ReactNode }[] = [
  { key: "overview", label: "Overview", icon: <Grid16Regular /> },
  { key: "explore", label: "Explore", icon: <Image16Regular /> },
  { key: "descriptors", label: "Descriptors", icon: <Sparkle16Regular /> },
  { key: "results", label: "Results", icon: <DocumentTableRegular /> },
  { key: "analysis", label: "Analysis", icon: <Options16Regular /> },
];

export default function App() {
  const { message, notification } = AntApp.useApp();
  const {
    backendStatus,
    setBackendReady,
    setBackendStarting,
    setBackendError,
    setDatasets,
    setActiveDataset,
    activeDatasetId,
    page,
    setPage,
  } = useWorkspace();

  const restartBackend = useCallback(async () => {
    setBackendStarting();
    await invoke("backend_restart");
  }, [setBackendStarting]);

  const offerEngineUpdate = useCallback(() => {
    const upd = useEngineUpdate.getState();
    if (upd.status !== "available") return;
    notification.info({
      message: "MDescriptor engine update available",
      description: `Installed ${upd.installed} — PyPI has ${upd.latest}. The upgrade runs in the background; a backend restart applies it.`,
      duration: 0,
      btn: (
        <Space>
          <Button
            size="small"
            onClick={() => {
              notification.destroy();
            }}
          >
            Later
          </Button>
          <Button
            type="primary"
            size="small"
            onClick={() => {
              notification.destroy();
              void upd.runUpdate().then((/* done */) => {
                const s = useEngineUpdate.getState();
                if (s.status === "restart_required") {
                  notification.success({
                    message: `Engine updated to ${s.latest}`,
                    description:
                      "Restart the backend to load it. After any engine update, rerun scripts/probe_engine.py + pytest (ADR-2).",
                    duration: 0,
                    btn: (
                      <Button
                        type="primary"
                        size="small"
                        onClick={() => {
                          notification.destroy();
                          void restartBackend();
                        }}
                      >
                        Restart backend
                      </Button>
                    ),
                  });
                } else if (s.status === "error") {
                  message.error(`Engine update failed: ${s.error}`);
                }
              });
            }}
          >
            Update to {upd.latest}
          </Button>
        </Space>
      ),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notification, message, restartBackend]);

  const refreshDatasets = useCallback(async () => {
    try {
      const list = await ipc.request<DatasetMeta[]>("dataset.list");
      setDatasets(list);
      const st = useWorkspace.getState();
      if (!st.activeDatasetId && list.length > 0) {
        const saved = await ipc.request<{ value: string | null }>("settings.get", {
          key: "workspace.activeDatasetId",
        });
        const wanted = saved.value && list.some((d) => d.id === saved.value) ? saved.value : list[0].id;
        useWorkspace.setState({ activeDatasetId: wanted, activeFrameIndex: 0 });
      }
      if (st.activeDatasetId && !list.some((d) => d.id === st.activeDatasetId)) {
        setActiveDataset(list[0]?.id ?? null);
      }
    } catch (e) {
      console.error("dataset.list failed", e);
    }
  }, [setDatasets, setActiveDataset]);

  useEffect(() => {
    let disposed = false;
    let poller: ReturnType<typeof setInterval> | null = null;
    (async () => {
      await ipc.connect(() => {
        useWorkspace.getState().setBackendError();
        message.error("Backend process exited");
      });
      const handleReady = async () => {
        if (disposed) return;
        try {
          const info = await ipc.request<{
            mdescriptor_version: string;
            mdescriptor_api_version: number;
            mdescriptor_baseline_version?: string;
            mdescriptor_descriptor_info_schema_version?: number;
            cpu_threads?: number;
          }>("system.info");
          setBackendReady(info.mdescriptor_version, info.cpu_threads ?? null);
          await refreshDatasets();
          // engine update check (PyPI) — non-blocking, UI notifies when available
          wireEngineUpdate();
          await useEngineUpdate.getState().refresh();
          offerEngineUpdate();
        } catch (e) {
          console.error(e);
          setBackendError();
        }
      };
      ipc.on("backend.ready", handleReady);
      wireJobEvents(useWorkspace.getState().setRunningJobs);
      // pull the ready snapshot in case the line arrived before our listener
      const { invoke } = await import("@tauri-apps/api/core");
      const started = Date.now();
      poller = setInterval(async () => {
        if (disposed) return;
        try {
          const line = await invoke<string | null>("backend_ready_line");
          if (line) {
            ipc.processLine(line);
            clearInterval(poller!);
            poller = null;
          } else if (Date.now() - started > 90_000) {
            clearInterval(poller!);
            setBackendError();
          }
        } catch {
          /* window not ready yet */
        }
      }, 600);
    })();
    return () => {
      disposed = true;
      if (poller) clearInterval(poller);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offerEngineUpdate, refreshDatasets, setBackendError, setBackendReady]);

  if (backendStatus !== "ready") {
    return (
      <div
        style={{
          height: "100vh",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <TitleBar />
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
          }}
        >
          <img
            src={APP_ICON_URL}
            alt="MDescriptor Studio"
            style={{
              width: 96,
              height: 96,
              objectFit: "contain",
              display: "block",
            }}
          />
          <div style={{ color: "#616161" }}>
            {backendStatus === "starting" ? "Starting backend…" : "Backend exited. Restart the app."}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      <TitleBar />
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <Sidebar />
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          <ContextBar />
          <div
            style={{
              padding: "0 24px",
              background: "#FFFFFF",
              borderBottom: "1px solid #EAECF0",
              display: "flex",
              gap: 4,
            }}
          >
            {TABS.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setPage(tab.key)}
                style={{
                  padding: "9px 14px",
                  border: "none",
                  background: "transparent",
                  cursor: "pointer",
                  fontSize: 14,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 7,
                  color: page === tab.key ? "#0F6CBD" : "#616161",
                  fontWeight: page === tab.key ? 600 : 400,
                  borderBottom: page === tab.key ? "2px solid #0F6CBD" : "2px solid transparent",
                }}
              >
                {tab.icon}
                {tab.label}
              </button>
            ))}
          </div>
          {/* content + persistent right rail; the pane scrolls only when a
              page's minimum content exceeds the viewport (default: it fits) */}
          <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
            <div
              style={{
                flex: 1,
                minWidth: 0,
                overflow: "auto",
                padding: "12px 24px 16px",
              }}
            >
              {page === "overview" && <Overview />}
              {page === "explore" && <Explore />}
              {page === "descriptors" && <Descriptors />}
              {page === "results" && <Results />}
              {page === "analysis" && <Analysis />}
            </div>
            <RightRail />
          </div>
        </div>
      </div>
      <StatusBar />
      <span style={{ display: "none" }}>{activeDatasetId}</span>
    </div>
  );
}

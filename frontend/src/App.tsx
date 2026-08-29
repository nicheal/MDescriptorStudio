import { useCallback, useEffect } from "react";
import { App as AntApp } from "antd";
import Sidebar from "./components/layout/Sidebar";
import ContextBar from "./components/layout/ContextBar";
import StatusBar from "./components/layout/StatusBar";
import JobsDrawer from "./components/JobsDrawer";
import Overview from "./pages/Overview";
import Explore from "./pages/Explore";
import Descriptors from "./pages/Descriptors";
import Results from "./pages/Results";
import { ipc } from "./ipc/client";
import { useWorkspace } from "./stores/workspace";
import { wireJobEvents } from "./stores/jobs";
import type { DatasetMeta } from "./types/protocol";

const TABS: [string, string][] = [
  ["overview", "Overview"],
  ["explore", "Explore"],
  ["descriptors", "Descriptors"],
  ["results", "Results"],
];

export default function App() {
  const { message } = AntApp.useApp();
  const {
    backendStatus,
    setBackendReady,
    setBackendError,
    setDatasets,
    setActiveDataset,
    activeDatasetId,
    page,
    setPage,
  } = useWorkspace();

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
        if (disposed || useWorkspace.getState().backendStatus === "ready") return;
        try {
          const info = await ipc.request<{
            mdescriptor_version: string;
            mdescriptor_api_version: number;
          }>("system.info");
          setBackendReady(info.mdescriptor_version);
          await refreshDatasets();
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
  }, []);

  if (backendStatus !== "ready") {
    return (
      <div
        style={{
          height: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 8,
        }}
      >
        <div style={{ fontSize: 18, fontWeight: 600 }}>MDescriptor Studio</div>
        <div style={{ color: "#616161" }}>
          {backendStatus === "starting" ? "Starting backend…" : "Backend exited. Restart the app."}
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 16px",
          borderBottom: "1px solid #E1E4E8",
          background: "#FFFFFF",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600 }}>
          <span
            style={{
              width: 22,
              height: 22,
              borderRadius: 5,
              background: "#0F6CBD",
              color: "#fff",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 13,
            }}
          >
            M
          </span>
          MDescriptor Studio
        </div>
        <JobsDrawer />
      </div>
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
            {TABS.map(([key, label]) => (
              <button
                key={key}
                onClick={() => setPage(key as typeof page)}
                style={{
                  padding: "10px 14px",
                  border: "none",
                  background: "transparent",
                  cursor: "pointer",
                  fontSize: 14,
                  color: page === key ? "#0F6CBD" : "#616161",
                  fontWeight: page === key ? 600 : 400,
                  borderBottom: page === key ? "2px solid #0F6CBD" : "2px solid transparent",
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <div style={{ flex: 1, overflow: "auto", padding: "20px 24px" }}>
            {page === "overview" && <Overview />}
            {page === "explore" && <Explore />}
            {page === "descriptors" && <Descriptors />}
            {page === "results" && <Results />}
          </div>
        </div>
      </div>
      <StatusBar />
      <span style={{ display: "none" }}>{activeDatasetId}</span>
    </div>
  );
}

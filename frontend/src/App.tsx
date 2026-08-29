import { useCallback, useEffect, useState } from "react";
import { App as AntApp } from "antd";
import Sidebar from "./components/layout/Sidebar";
import ContextBar from "./components/layout/ContextBar";
import StatusBar from "./components/layout/StatusBar";
import JobsBadge from "./components/layout/JobsBadge";
import Overview from "./pages/Overview";
import Explore from "./pages/Explore";
import Descriptors from "./pages/Descriptors";
import Results from "./pages/Results";
import { ipc } from "./ipc/client";
import { useWorkspace } from "./stores/workspace";
import type { DatasetMeta } from "./types/protocol";

export type Page = "overview" | "explore" | "descriptors" | "results";

export default function App() {
  const { message } = AntApp.useApp();
  const [page, setPage] = useState<Page>("overview");
  const {
    backendStatus,
    setBackendReady,
    setBackendError,
    setDatasets,
    setActiveDataset,
    activeDatasetId,
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
    (async () => {
      await ipc.connect(() => {
        useWorkspace.getState().setBackendError();
        message.error("Backend process exited");
      });
      // handshake line arrives as an event frame; wait for ready, then verify
      const offReady = ipc.on("backend.ready", async () => {
        if (disposed) return;
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
      });
      return () => {
        offReady();
      };
    })();
    return () => {
      disposed = true;
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
        <JobsBadge />
      </div>
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <Sidebar onGo={setPage} />
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
            {(
              [
                ["overview", "Overview"],
                ["explore", "Explore"],
                ["descriptors", "Descriptors"],
                ["results", "Results"],
              ] as [Page, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setPage(key)}
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
            {page === "overview" && <Overview onGo={setPage} />}
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

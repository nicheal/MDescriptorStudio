import { lazy, Suspense, useCallback, useEffect, useRef, type ReactNode } from "react";
import { App as AntApp, Button } from "antd";
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
import HealthFindingsDrawer from "./components/HealthFindingsDrawer";
import ErrorBoundary from "./components/ErrorBoundary";
import { ipc } from "./ipc/client";
import { useWorkspace, hydrateActiveRun } from "./stores/workspace";
import { hydrateAnalysisUi } from "./features/analysis";
import { wireJobEvents } from "./stores/jobs";
import { getT, initLanguage, useT } from "./i18n";
import { preloadStructureViewer } from "./viz/StructureViewer";
import type { DatasetMeta } from "./types/protocol";
import { APP_ICON_URL } from "./brand";

// Keep heavyweight page code out of the startup chunk. Vite caches these
// imports after the first visit, so switching tabs keeps the same behavior.
const Overview = lazy(() => import("./pages/Overview"));
const Explore = lazy(() => import("./pages/Explore"));
const Descriptors = lazy(() => import("./pages/Descriptors"));
const Results = lazy(() => import("./pages/DescriptorResults"));
const Analysis = lazy(() => import("./pages/Analysis"));

function PageLoading() {
  return <div style={{ padding: 24, color: "#616161" }}>Loading…</div>;
}

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
  const { message } = AntApp.useApp();
  // One selector per field. Reading the whole store here re-renders the shell,
  // the rail and the mounted page on every write, which during a descriptor
  // run means every job.progress tick (see stores/workspace.ts).
  const backendStatus = useWorkspace((s) => s.backendStatus);
  const page = useWorkspace((s) => s.page);
  const setPage = useWorkspace((s) => s.setPage);
  const setBackendReady = useWorkspace((s) => s.setBackendReady);
  const setBackendStarting = useWorkspace((s) => s.setBackendStarting);
  const setBackendError = useWorkspace((s) => s.setBackendError);
  const backendLogDir = useWorkspace((s) => s.backendLogDir);
  const setDatasets = useWorkspace((s) => s.setDatasets);
  const setActiveDataset = useWorkspace((s) => s.setActiveDataset);
  const { t } = useT();
  // A user-initiated restart deliberately emits backend-exit, so the exit
  // handler must not report it as a crash; the flag clears when the new
  // process greets us (or if the restart call itself fails).
  const restartingRef = useRef(false);

  // 3Dmol is the largest thing the structure pages wait on, and it sits behind a
  // dynamic import: loading it only when the viewer mounts leaves the pane empty
  // for the whole fetch. Start it while the shell comes up instead.
  useEffect(() => {
    preloadStructureViewer();
  }, []);

  const restartBackend = useCallback(async () => {
    setBackendStarting();
    restartingRef.current = true;
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("backend_restart");
      // backend_restart emits the exit event, and the exit handler re-arms the
      // listeners (see the mount effect), so the new process is heard.
    } catch (error) {
      console.error(error);
      restartingRef.current = false;
      setBackendError();
    }
  }, [setBackendStarting, setBackendError]);

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
        // through the action, not setState: the fallback choice is only stable
        // across launches if it is persisted, and this is the one path that can
        // pick a dataset the settings row never named.
        setActiveDataset(wanted);
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
    let offReady: (() => void) | null = null;
    let readyHandled = false;
    const handleReady = async () => {
      if (disposed || readyHandled) return;
      readyHandled = true;
      restartingRef.current = false;
      try {
        // apply the persisted UI language before the main UI renders
        await initLanguage();
        const info = await ipc.request<{
          mdescriptor_version: string;
          mdescriptor_api_version: number;
          mdescriptor_baseline_version?: string;
          mdescriptor_descriptor_info_schema_version?: number;
          cpu_threads?: number;
        }>("system.info");
        // restore the persisted analysis view + active run before any page
        // renders so the Analysis page mounts on what was last on screen
        await Promise.all([hydrateActiveRun(), hydrateAnalysisUi()]);
        if (disposed) return;
        setBackendReady(info.mdescriptor_version, info.cpu_threads ?? null);
        await refreshDatasets();
      } catch (e) {
        console.error(e);
        if (!disposed) setBackendError();
      }
    };
    const stopPoller = () => {
      if (poller) clearInterval(poller);
      poller = null;
    };
    // Arming installs everything that talks to one backend process: the Tauri
    // listeners, the ready handshake and the snapshot poller. A backend exit
    // tears the listeners down (ipc/client.ts releases both, which is the
    // contract client.test.ts pins) and leaves `connected` false, so
    // re-arming here is the only way "Restart the backend" can ever be seen:
    // without it every later request rejects synchronously and the ready frame
    // of the new process has no listener to arrive on. An exit that lands
    // mid-arm is remembered rather than dropped, because that in-flight arm is
    // about to abort on the generation change and would otherwise leave no
    // listeners attached at all.
    let arming = false;
    let reArmRequested = false;
    const arm = async () => {
      if (arming) {
        reArmRequested = true;
        return;
      }
      arming = true;
      try {
        do {
          reArmRequested = false;
          offReady?.();
          offReady = null;
          readyHandled = false;
          stopPoller();
          await ipc.connect((logDir) => {
            if (disposed) return;
            if (!restartingRef.current) message.error(getT().t("Backend process exited"));
            useWorkspace.getState().setBackendError(logDir);
            void arm();
          });
          if (disposed) break;
          if (!ipc.isReady) continue;
          offReady = ipc.on("backend.ready", handleReady);
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
                stopPoller();
              } else if (Date.now() - started > 90_000) {
                stopPoller();
                setBackendError();
              }
            } catch {
              /* window not ready yet */
            }
          }, 600);
        } while (reArmRequested && !disposed);
      } finally {
        arming = false;
      }
    };
    void arm();
    return () => {
      disposed = true;
      offReady?.();
      offReady = null;
      stopPoller();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshDatasets, setBackendError, setBackendReady]);

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
            {backendStatus === "starting" ? t("Starting backend…") : t("Backend process exited.")}
          </div>
          {backendStatus === "error" && (
            <Button type="primary" onClick={() => void restartBackend()}>
              {t("Restart the backend")}
            </Button>
          )}
          {backendStatus === "error" && backendLogDir && (
            // A release build has no console: this directory is the only place
            // the shell's own failure reason can be found.
            <div
              title={backendLogDir}
              style={{ maxWidth: "72%", color: "#8A8A8A", fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {t("Startup log: {path}", { path: backendLogDir })}
            </div>
          )}
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
                {t(tab.label)}
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
              {/* A page that throws on render must not take the shell, the rail
                  or the status bar down with it. The key is part of that:
                  without it the boundary keeps its error and every other tab
                  renders the crash screen too, leaving Reload as the only way
                  out of a single bad view. */}
              <ErrorBoundary key={page}>
                <Suspense fallback={<PageLoading />}>
                  {page === "overview" && <Overview />}
                  {page === "explore" && <Explore />}
                  {page === "descriptors" && <Descriptors />}
                  {page === "results" && <Results />}
                  {page === "analysis" && <Analysis />}
                </Suspense>
              </ErrorBoundary>
            </div>
            <RightRail />
          </div>
        </div>
      </div>
      <StatusBar />
      <HealthFindingsDrawer />
    </div>
  );
}

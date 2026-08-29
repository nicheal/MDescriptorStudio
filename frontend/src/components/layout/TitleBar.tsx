import { useCallback, useEffect, useState, type MouseEvent } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  Dismiss16Regular,
  Maximize16Regular,
  SquareMultiple16Regular,
  Subtract16Regular,
} from "@fluentui/react-icons";
import JobsDrawer from "../JobsDrawer";
import { APP_ICON_URL } from "../../brand";

export default function TitleBar() {
  const [maximized, setMaximized] = useState(false);

  const syncMaximized = useCallback(async () => {
    try {
      setMaximized(await getCurrentWindow().isMaximized());
    } catch {
      // The browser preview has no native window to query.
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    const appWindow = getCurrentWindow();
    const refreshMaximizedState = () => {
      void appWindow.isMaximized().then((value) => {
        if (!disposed) setMaximized(value);
      }).catch(() => {
        // Ignore window API failures while the native window is initializing.
      });
    };

    refreshMaximizedState();

    let unlisten: (() => void) | undefined;
    void appWindow.onResized(refreshMaximizedState).then((stopListening) => {
      if (disposed) {
        stopListening();
      } else {
        unlisten = stopListening;
      }
    }).catch(() => {
      // The browser preview may not expose native resize events.
    });

    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);

  const runWindowCommand = useCallback((command: () => Promise<void>) => {
    void command().catch((error: unknown) => {
      console.error("Window command failed", error);
    });
  }, []);

  const toggleMaximize = useCallback(() => {
    runWindowCommand(async () => {
      await getCurrentWindow().toggleMaximize();
      await syncMaximized();
    });
  }, [runWindowCommand, syncMaximized]);

  const handleTitleBarDoubleClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.detail === 2) toggleMaximize();
  };

  return (
    <header className="app-titlebar" role="banner">
      <div
        className="app-titlebar-brand"
        data-tauri-drag-region
        onDoubleClick={handleTitleBarDoubleClick}
      >
        <img src={APP_ICON_URL} alt="" aria-hidden="true" />
        <span>MDescriptor Studio</span>
      </div>

      <div
        className="app-titlebar-drag-region"
        data-tauri-drag-region
        onDoubleClick={handleTitleBarDoubleClick}
        aria-hidden="true"
      />

      <div className="app-titlebar-actions">
        <JobsDrawer />
        <span className="app-titlebar-divider" aria-hidden="true" />
        <div className="app-window-controls">
          <button
            type="button"
            className="app-window-control"
            aria-label="Minimize"
            title="Minimize"
            onClick={() => runWindowCommand(() => getCurrentWindow().minimize())}
          >
            <Subtract16Regular />
          </button>
          <button
            type="button"
            className="app-window-control"
            aria-label={maximized ? "Restore" : "Maximize"}
            title={maximized ? "Restore" : "Maximize"}
            onClick={toggleMaximize}
          >
            {maximized ? <SquareMultiple16Regular /> : <Maximize16Regular />}
          </button>
          <button
            type="button"
            className="app-window-control app-window-control-close"
            aria-label="Close"
            title="Close"
            onClick={() => runWindowCommand(() => getCurrentWindow().close())}
          >
            <Dismiss16Regular />
          </button>
        </div>
      </div>
    </header>
  );
}

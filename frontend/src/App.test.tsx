// @vitest-environment jsdom
// App owns the backend channel: it must re-arm the IPC listeners after a
// backend exit, because ipc/client.ts releases both Tauri listeners on exit
// (its own contract) and every request rejects while disconnected. Without a
// re-arm, "Restart the backend" spawns a healthy process the UI can never
// reach. These tests use the real IpcClient over mocked Tauri events so the
// reconnection is exercised end to end.
import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { App as AntApp } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listeners = new Map<string, (event?: unknown) => void>();
const { invokeMock, unlistenCalls } = vi.hoisted(() => ({
  invokeMock: vi.fn(),
  unlistenCalls: { n: 0 },
}));

vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async (event: string, handler: (event?: unknown) => void) => {
    listeners.set(event, handler);
    return () => {
      unlistenCalls.n += 1;
      listeners.delete(event);
    };
  }),
}));

vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

vi.mock("@fluentui/react-icons", () => {
  const Icon = () => null;
  return {
    Grid16Regular: Icon,
    Image16Regular: Icon,
    Options16Regular: Icon,
    Sparkle16Regular: Icon,
    DocumentTableRegular: Icon,
  };
});

// Only the shell chrome is mocked: this file tests the mount effect, not the
// layout components or the update checker.
vi.mock("./components/layout/Sidebar", () => ({ default: () => null }));
vi.mock("./components/layout/ContextBar", () => ({ default: () => null }));
vi.mock("./components/layout/StatusBar", () => ({ default: () => null }));
vi.mock("./components/layout/RightRail", () => ({ default: () => null }));
vi.mock("./components/layout/TitleBar", () => ({ default: () => null }));
vi.mock("./components/HealthFindingsDrawer", () => ({ default: () => null }));
vi.mock("./components/SettingsDrawer", () => ({ default: () => null }));
vi.mock("./components/JobsDrawer", () => ({ default: () => null }));
vi.mock("./stores/appUpdate", () => ({
  useAppUpdate: () => ({ current: "0.0.0", checking: false, checkingAvailable: false }),
  getVersion: () => Promise.resolve("0.0.0"),
}));

import App from "./App";
import { ipc, BACKEND_EXIT_EVENT, BACKEND_MESSAGE_EVENT } from "./ipc/client";
import { useWorkspace } from "./stores/workspace";

const flush = async () => {
  for (let i = 0; i < 8; i += 1) await act(async () => { await Promise.resolve(); });
};

let root: Root | null = null;

const mount = async () => {
  const container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root!.render(createElement(AntApp, null, createElement(App) as ReactNode));
  });
  await flush();
};

beforeEach(() => {
  invokeMock.mockReset();
  invokeMock.mockImplementation((cmd: string) => {
    if (cmd === "backend_ready_line") return Promise.resolve(null);
    return Promise.resolve(1);
  });
  unlistenCalls.n = 0;
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  ipc.disconnect();
  listeners.clear();
  useWorkspace.setState({ datasets: [], activeDatasetId: null, backendStatus: "starting" });
});

describe("App backend channel", () => {
  it("attaches the message listener on mount", async () => {
    await mount();
    expect(listeners.has(BACKEND_MESSAGE_EVENT)).toBe(true);
    expect(listeners.has(BACKEND_EXIT_EVENT)).toBe(true);
    expect(ipc.isReady).toBe(true);
  });

  it("names the shell's log directory on the offline screen", async () => {
    await mount();
    act(() => listeners.get(BACKEND_EXIT_EVENT)?.({ payload: { logDir: "C:/Users/me/AppData/Local/MDescriptorStudio/logs" } }));
    await flush();

    // A release build has no console, so a user who is told "the backend
    // exited" has nowhere to look unless the exit event carries the path.
    expect(useWorkspace.getState().backendLogDir).toBe("C:/Users/me/AppData/Local/MDescriptorStudio/logs");
    expect(document.body.textContent).toContain("MDescriptorStudio/logs");
    useWorkspace.setState({ backendLogDir: null });
  });

  it("re-arms the listeners after a backend exit and answers the new process", async () => {
    await mount();
    const before = unlistenCalls.n;

    act(() => listeners.get(BACKEND_EXIT_EVENT)?.({ payload: { logDir: null } }));
    await flush();

    // The exit released the old pair, and App installed a fresh one: without
    // this the app is a dead shell and only a process restart helps.
    expect(unlistenCalls.n).toBeGreaterThan(before);
    expect(listeners.has(BACKEND_MESSAGE_EVENT)).toBe(true);
    expect(listeners.has(BACKEND_EXIT_EVENT)).toBe(true);
    expect(ipc.isReady).toBe(true);

    // The re-armed channel is usable: a request reaches the backend and the
    // frame carried by the new listener settles it.
    let settled: unknown = "pending";
    void ipc.request("dataset.list").then((value) => { settled = value; });
    await flush();
    act(() => listeners.get(BACKEND_MESSAGE_EVENT)?.({
      payload: JSON.stringify({ protocol_version: 1, id: 1, result: [] }),
    }));
    await flush();
    expect(settled).toEqual([]);
  });

  it("answers exactly one handshake per ready frame after repeated re-arms", async () => {
    await mount();
    act(() => listeners.get(BACKEND_EXIT_EVENT)?.({ payload: { logDir: null } }));
    await flush();
    act(() => listeners.get(BACKEND_EXIT_EVENT)?.({ payload: { logDir: null } }));
    await flush();

    // The handshake is one-way and parks on its first request here (no frame
    // answers it), so count requests instead of the system.info that would
    // follow: a leaked `backend.ready` subscription from an earlier arm would
    // start a second handshake over the same frame.
    const handshakeCalls = () => invokeMock.mock.calls.filter(
      ([cmd]) => cmd === "backend_request",
    ).length;
    expect(handshakeCalls()).toBe(0);

    act(() => listeners.get(BACKEND_MESSAGE_EVENT)?.({
      payload: JSON.stringify({ protocol_version: 1, event: "backend.ready", data: {} }),
    }));
    await flush();
    expect(handshakeCalls()).toBeGreaterThan(0);
    const afterFirst = handshakeCalls();

    act(() => listeners.get(BACKEND_MESSAGE_EVENT)?.({
      payload: JSON.stringify({ protocol_version: 1, event: "backend.ready", data: {} }),
    }));
    await flush();
    expect(handshakeCalls()).toBe(afterFirst);
    expect(ipc.isReady).toBe(true);
  });
});

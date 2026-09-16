// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { App as AntApp } from "antd";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import Sidebar from "./Sidebar";
import { ipc } from "../../ipc/client";
import { useJobs } from "../../stores/jobs";
import { useWorkspace } from "../../stores/workspace";
import type { DatasetMeta, JobRow } from "../../types/protocol";

const { openDialogMock } = vi.hoisted(() => ({ openDialogMock: vi.fn() }));

vi.mock("@tauri-apps/plugin-dialog", () => ({ open: openDialogMock, save: vi.fn() }));
vi.mock("@fluentui/react-icons", () => {
  const Icon = () => null;
  return {
    Add16Regular: Icon,
    BranchFork16Regular: Icon,
    ChevronLeft16Regular: Icon,
    ChevronRight16Regular: Icon,
    Document16Regular: Icon,
    FolderOpen16Regular: Icon,
    MoreHorizontal16Regular: Icon,
    Search16Regular: Icon,
  };
});
vi.mock("../datasetActions", () => ({
  RenameDatasetModal: () => null,
  useDatasetDelete: () => vi.fn(),
}));

const originalMatchMedia = window.matchMedia;
const originalGetComputedStyle = window.getComputedStyle;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (media: string) => ({
      matches: false,
      media,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
  Object.defineProperty(window, "getComputedStyle", {
    configurable: true,
    value: () => ({ getPropertyValue: () => "" }),
  });
});

afterAll(() => {
  Object.defineProperty(window, "matchMedia", { configurable: true, value: originalMatchMedia });
  Object.defineProperty(window, "getComputedStyle", { configurable: true, value: originalGetComputedStyle });
});

beforeEach(() => {
  openDialogMock.mockResolvedValue("C:\\datasets\\fast.extxyz");
});

afterEach(() => {
  ipc.disconnect();
  useJobs.setState({ jobs: {}, order: [] });
  useWorkspace.setState({ datasets: [], activeDatasetId: null, runningJobs: 0 });
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

function dataset(id: string): DatasetMeta {
  return {
    id,
    name: id,
    format: "extxyz",
    source_path: `${id}.extxyz`,
    number_of_frames: 2,
    elements: ["Si"],
    properties: { energy: { per_structure: true, per_atom: true }, forces: { per_atom: true } },
    periodicity: { fully_periodic: true, isolated: false, mixed: false, flags: ["periodic"] },
    fingerprint: id,
    file_size: 1,
    created_at: "2026-01-01T00:00:00Z",
    last_scan_at: null,
    cache_valid: true,
  };
}

function jobRow(id: string, status: JobRow["status"], error: string | null = null): JobRow {
  return {
    id,
    job_type: "dataset.register",
    dataset_id: null,
    descriptor_run_id: null,
    status,
    progress: status === "COMPLETED" ? 1 : 0,
    completed: null,
    total: null,
    message: error ? "scan failed" : null,
    error,
    created_at: "2026-01-01T00:00:00Z",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:00:01Z",
    result: null,
  };
}

async function flushEffects() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function mountSidebar(): Promise<{ root: Root; host: HTMLDivElement }> {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(createElement(AntApp, null, createElement(Sidebar)));
    await Promise.resolve();
  });
  await flushEffects();
  return { root, host };
}

async function clickRegister() {
  const add = [...document.querySelectorAll("button")].find((button) => button.textContent?.includes("Add Dataset"));
  expect(add).toBeDefined();
  await act(async () => { add!.click(); });
  await flushEffects();

  const browseFile = document.querySelector('button[title="Browse for an .xyz / .extxyz file"]') as HTMLButtonElement | null;
  expect(browseFile).not.toBeNull();
  await act(async () => { browseFile!.click(); });
  await flushEffects();

  const registerButton = [...document.querySelectorAll("button")].find((button) => button.textContent?.trim() === "Register");
  expect(registerButton).toBeDefined();
  await act(async () => { registerButton!.click(); });
  await flushEffects();
}

describe("Sidebar dataset registration", () => {
  it("refreshes after a fast completion even when its live event was missed", async () => {
    const completed = jobRow("job-fast", "COMPLETED");
    const registered = dataset("fast-dataset");
    const request = vi.spyOn(ipc, "request").mockImplementation((method) => {
      if (method === "dataset.view.list") return Promise.resolve([]) as never;
      if (method === "dataset.register") {
        // Simulate the backend finishing before register() attaches watchJob.
        ipc.processLine(JSON.stringify({ protocol_version: 1, event: "job.finished", data: { job_id: "job-fast", status: "COMPLETED", result: null, error: null } }));
        return Promise.resolve({ job_id: "job-fast", cache: null }) as never;
      }
      if (method === "job.get") return Promise.resolve(completed) as never;
      if (method === "dataset.list") return Promise.resolve([registered]) as never;
      return Promise.resolve(undefined) as never;
    });
    const mounted = await mountSidebar();

    await clickRegister();
    await flushEffects();

    expect(request.mock.calls.some(([method]) => method === "job.get")).toBe(true);
    expect(request.mock.calls.filter(([method]) => method === "dataset.list")).toHaveLength(1);
    expect(useWorkspace.getState().datasets).toEqual([registered]);
    expect(document.body.textContent).toContain("Dataset added");

    await act(async () => mounted.root.unmount());
    mounted.host.remove();
  });

  it("keeps the successful registration message when the dataset refresh fails", async () => {
    const completed = jobRow("job-refresh-failed", "COMPLETED");
    const request = vi.spyOn(ipc, "request").mockImplementation((method) => {
      if (method === "dataset.view.list") return Promise.resolve([]) as never;
      if (method === "dataset.register") return Promise.resolve({ job_id: "job-refresh-failed", cache: null }) as never;
      if (method === "job.get") return Promise.resolve(completed) as never;
      if (method === "dataset.list") return Promise.reject({ code: "BACKEND_DOWN", message: "backend unavailable" }) as never;
      return Promise.resolve(undefined) as never;
    });
    const mounted = await mountSidebar();

    await clickRegister();
    await flushEffects();

    expect(request.mock.calls.some(([method]) => method === "job.get")).toBe(true);
    expect(request.mock.calls.filter(([method]) => method === "dataset.list")).toHaveLength(1);
    expect(document.body.textContent).toContain("Dataset registered, but the list could not be refreshed");
    expect(document.body.textContent).not.toContain("Register failed");

    await act(async () => mounted.root.unmount());
    mounted.host.remove();
  });

  it("shows the persisted job failure instead of waiting on a one-shot event", async () => {
    const failed = jobRow("job-failed", "FAILED", "SCAN_FAILED");
    const request = vi.spyOn(ipc, "request").mockImplementation((method) => {
      if (method === "dataset.view.list") return Promise.resolve([]) as never;
      if (method === "dataset.register") return Promise.resolve({ job_id: "job-failed", cache: null }) as never;
      if (method === "job.get") return Promise.resolve(failed) as never;
      return Promise.resolve(undefined) as never;
    });
    const mounted = await mountSidebar();

    await clickRegister();
    await flushEffects();

    expect(request.mock.calls.some(([method]) => method === "job.get")).toBe(true);
    expect(document.body.textContent).toContain("Register failed: scan failed");

    await act(async () => mounted.root.unmount());
    mounted.host.remove();
  });
});

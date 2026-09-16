// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import HealthFindingsDrawer from "./HealthFindingsDrawer";
import { ipc } from "../ipc/client";
import { waitForSuccessfulJob } from "../stores/jobs";
import { useWorkspace } from "../stores/workspace";
import type { DatasetMeta, FindingsRow, Stats } from "../types/protocol";

vi.mock("./SaveViewModal", () => ({ default: () => null }));
vi.mock("../stores/jobs", async () => {
  const actual = await vi.importActual<typeof import("../stores/jobs")>("../stores/jobs");
  return { ...actual, waitForSuccessfulJob: vi.fn() };
});
vi.mock("@fluentui/react-icons", () => ({
  ArrowLeft16Regular: () => null,
  ArrowRight16Regular: () => null,
  Eye16Regular: () => null,
}));

const originalMatchMedia = window.matchMedia;
const originalGetComputedStyle = window.getComputedStyle;
const waitForSuccessfulJobMock = vi.mocked(waitForSuccessfulJob);

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

beforeEach(() => {
  waitForSuccessfulJobMock.mockReset().mockResolvedValue(undefined);
});

afterAll(() => {
  Object.defineProperty(window, "matchMedia", { configurable: true, value: originalMatchMedia });
  Object.defineProperty(window, "getComputedStyle", { configurable: true, value: originalGetComputedStyle });
});

type StatisticsResponse = { recalculating: boolean; job_id: string | null; stats: Stats | null };
type FindingsResponse = { recalculating: boolean; job_id: string | null; total: number; returned: number; rows: FindingsRow[] };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function dataset(id: string): DatasetMeta {
  return {
    id,
    name: id,
    format: "extxyz",
    source_path: `${id}.xyz`,
    number_of_frames: 3,
    elements: ["Si"],
    properties: { energy: { per_structure: true, per_atom: true }, forces: { per_atom: true }, virial: { per_structure: true } },
    periodicity: { fully_periodic: true, isolated: false, mixed: false, flags: ["periodic"] },
    fingerprint: id,
    file_size: 1,
    created_at: "2026-01-01T00:00:00Z",
    last_scan_at: null,
    cache_valid: true,
  };
}

function statsWithFinding(index: number): Stats {
  return {
    health_findings: {
      cap: 1000,
      missing_values: [index],
      energy_anomaly: [],
      invalid_cell: [],
      duplicate_structures: [],
      extreme_force: [],
      nonphysical_structures: [],
      net_force: [],
    },
  } as unknown as Stats;
}

function finding(index: number, formula: string): FindingsRow {
  return { index, natoms: 1, formula, force_max: null, volume: null, min_distance: null, missing_props: ["energy"] };
}

async function flushEffects() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function mountDrawer(): Promise<{ root: Root; host: HTMLDivElement }> {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(createElement(HealthFindingsDrawer));
    await Promise.resolve();
  });
  return { root, host };
}

function mockRequests(statistics: Array<ReturnType<typeof deferred<StatisticsResponse>>>, findings: Array<ReturnType<typeof deferred<FindingsResponse>>>) {
  return vi.spyOn(ipc, "request").mockImplementation((method) => {
    if (method === "dataset.statistics") return statistics.shift()!.promise as never;
    if (method === "dataset.findings") return findings.shift()!.promise as never;
    return Promise.resolve(undefined) as never;
  });
}

afterEach(() => {
  document.body.innerHTML = "";
  useWorkspace.setState({ datasets: [], activeDatasetId: null, findingsDrawerOpen: false, findingsCheck: null, statsTick: 0 });
  vi.restoreAllMocks();
});

describe("HealthFindingsDrawer loading path", () => {
  it("does not let a late request from before close/reopen populate the drawer", async () => {
    const ds = dataset("dataset-a");
    const oldStats = deferred<StatisticsResponse>();
    const freshStats = deferred<StatisticsResponse>();
    const freshRows = deferred<FindingsResponse>();
    const request = mockRequests([oldStats, freshStats], [freshRows]);
    useWorkspace.setState({ datasets: [ds], activeDatasetId: ds.id, findingsDrawerOpen: true, findingsCheck: "missing_values" });
    const mounted = await mountDrawer();
    await flushEffects();

    act(() => useWorkspace.getState().closeFindings());
    await flushEffects();
    act(() => useWorkspace.getState().openFindings("missing_values"));
    await flushEffects();

    oldStats.resolve({ recalculating: false, job_id: null, stats: statsWithFinding(1) });
    await flushEffects();
    expect(request.mock.calls.filter(([method]) => method === "dataset.findings")).toHaveLength(0);

    freshStats.resolve({ recalculating: false, job_id: null, stats: statsWithFinding(2) });
    await flushEffects();
    freshRows.resolve({ recalculating: false, job_id: null, total: 1, returned: 1, rows: [finding(2, "FRESH")] });
    await flushEffects();
    expect(document.body.textContent).toContain("FRESH");

    act(() => mounted.root.unmount());
    mounted.host.remove();
  });

  it("ignores the old dataset response after switching datasets", async () => {
    const first = dataset("dataset-a");
    const second = dataset("dataset-b");
    const oldStats = deferred<StatisticsResponse>();
    const newStats = deferred<StatisticsResponse>();
    const newRows = deferred<FindingsResponse>();
    const request = mockRequests([oldStats, newStats], [newRows]);
    useWorkspace.setState({ datasets: [first, second], activeDatasetId: first.id, findingsDrawerOpen: true, findingsCheck: "missing_values" });
    const mounted = await mountDrawer();
    await flushEffects();

    act(() => useWorkspace.setState({ activeDatasetId: second.id }));
    await flushEffects();
    oldStats.resolve({ recalculating: false, job_id: null, stats: statsWithFinding(1) });
    await flushEffects();
    expect(request.mock.calls.filter(([method]) => method === "dataset.findings")).toHaveLength(0);

    newStats.resolve({ recalculating: false, job_id: null, stats: statsWithFinding(2) });
    await flushEffects();
    newRows.resolve({ recalculating: false, job_id: null, total: 1, returned: 1, rows: [finding(2, "NEW-DATASET")] });
    await flushEffects();
    expect(document.body.textContent).toContain("NEW-DATASET");

    act(() => mounted.root.unmount());
    mounted.host.remove();
  });

  it("does not commit or continue loading after unmount", async () => {
    const ds = dataset("dataset-a");
    const pendingStats = deferred<StatisticsResponse>();
    const request = mockRequests([pendingStats], []);
    useWorkspace.setState({ datasets: [ds], activeDatasetId: ds.id, findingsDrawerOpen: true, findingsCheck: "missing_values" });
    const mounted = await mountDrawer();
    await flushEffects();

    act(() => mounted.root.unmount());
    pendingStats.resolve({ recalculating: false, job_id: null, stats: statsWithFinding(1) });
    await flushEffects();

    expect(request.mock.calls.filter(([method]) => method === "dataset.findings")).toHaveLength(0);
    mounted.host.remove();
  });

  it("clears the table loading state when the statistics job fails", async () => {
    const ds = dataset("dataset-a");
    const pendingStats = deferred<StatisticsResponse>();
    mockRequests([pendingStats], []);
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});
    waitForSuccessfulJobMock.mockRejectedValueOnce(new Error("job failed"));
    useWorkspace.setState({ datasets: [ds], activeDatasetId: ds.id, findingsDrawerOpen: true, findingsCheck: "missing_values" });
    const mounted = await mountDrawer();
    await flushEffects();

    pendingStats.resolve({ recalculating: false, job_id: "failed-job", stats: null });
    await flushEffects();

    expect(waitForSuccessfulJobMock).toHaveBeenCalledWith("failed-job");
    expect(errorLog).toHaveBeenCalledWith("dataset.findings failed", expect.any(Error));
    expect(document.querySelector(".ant-spin-spinning")).toBeNull();
    expect(document.body.textContent).not.toContain("FRESH");

    act(() => mounted.root.unmount());
    mounted.host.remove();
  });
});

// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import Overview from "./Overview";
import { ipc } from "../ipc/client";
import { waitForSuccessfulJob } from "../stores/jobs";
import { useWorkspace } from "../stores/workspace";
import type { DatasetMeta, Stats } from "../types/protocol";

vi.mock("../components/Histogram", () => ({ default: () => null }));
vi.mock("echarts-for-react", () => ({ default: () => null }));
vi.mock("../stores/jobs", async () => {
  const actual = await vi.importActual<typeof import("../stores/jobs")>("../stores/jobs");
  return { ...actual, waitForSuccessfulJob: vi.fn() };
});

const originalMatchMedia = window.matchMedia;
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
});

afterAll(() => {
  Object.defineProperty(window, "matchMedia", { configurable: true, value: originalMatchMedia });
});

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

function stats(atomsTotal: number): Stats {
  return {
    structures: 3,
    atoms_total: atomsTotal,
    elements: [{ symbol: "Si", count: atomsTotal }],
    compositions: [],
    formulas: [],
    element_atom_counts: {},
    atoms_per_structure: null,
    atoms_per_structure_summary: null,
    energy_per_atom: null,
    energy_per_atom_summary: null,
    force_magnitude: null,
    force_magnitude_summary: null,
    max_force: null,
    max_force_summary: null,
    min_distance: null,
    min_distance_summary: null,
    volume: null,
    volume_summary: null,
    properties: {
      energy: { per_structure: true, per_atom: true },
      forces: { per_atom: true },
      virial: { per_structure: true },
    },
    periodicity: { fully_periodic: true, isolated: false, mixed: false, flags: ["periodic"] },
    stats_version: 3,
    health: {
      missing_values: 0, energy_anomaly: 0, invalid_cell: 0, duplicate_structures: 0,
      extreme_force: 0, extreme_force_threshold: 0.5, nonphysical_structures: 0,
      short_contact_coefficient: 0.7, net_force: 0, net_force_threshold: 0.01,
    },
    health_findings: {
      cap: 5000, missing_values: [], energy_anomaly: [], invalid_cell: [],
      duplicate_structures: [], extreme_force: [], nonphysical_structures: [], net_force: [],
    },
  };
}

async function flushEffects() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function mountOverview(): Promise<{ root: Root; host: HTMLDivElement }> {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(createElement(Overview));
    await Promise.resolve();
  });
  return { root, host };
}

beforeEach(() => {
  waitForSuccessfulJobMock.mockReset().mockResolvedValue(undefined);
});

afterEach(() => {
  document.body.innerHTML = "";
  useWorkspace.setState({ datasets: [], activeDatasetId: null, statsTick: 0 });
  vi.restoreAllMocks();
});

describe("Overview statistics loading", () => {
  it("re-reads completed statistics after a missed job.finished event", async () => {
    const ds = dataset("dataset-a");
    const request = vi.spyOn(ipc, "request")
      .mockResolvedValueOnce({ recalculating: true, job_id: "job-1", stats: null } as never)
      .mockResolvedValueOnce({ recalculating: false, job_id: null, stats: stats(42) } as never);
    useWorkspace.setState({ datasets: [ds], activeDatasetId: ds.id });
    const mounted = await mountOverview();
    await flushEffects();

    expect(waitForSuccessfulJobMock).toHaveBeenCalledWith("job-1");
    expect(request.mock.calls.filter(([method]) => method === "dataset.statistics")).toHaveLength(2);
    expect(document.body.textContent).toContain("42");
    expect(document.body.textContent).not.toContain("Recomputing statistics…");

    act(() => mounted.root.unmount());
    mounted.host.remove();
  });

  it("does not commit a late response from the previous dataset", async () => {
    const first = dataset("dataset-a");
    const second = dataset("dataset-b");
    const oldStats = deferred<{ recalculating: boolean; job_id: string | null; stats: Stats | null }>();
    const newStats = deferred<{ recalculating: boolean; job_id: string | null; stats: Stats | null }>();
    const request = vi.spyOn(ipc, "request").mockImplementation((method, params) => {
      if (method === "dataset.statistics") {
        return (params?.id === first.id ? oldStats.promise : newStats.promise) as never;
      }
      return Promise.resolve(undefined) as never;
    });
    useWorkspace.setState({ datasets: [first, second], activeDatasetId: first.id });
    const mounted = await mountOverview();
    await flushEffects();

    act(() => useWorkspace.setState({ activeDatasetId: second.id }));
    await flushEffects();
    newStats.resolve({ recalculating: false, job_id: null, stats: stats(222) });
    await flushEffects();
    oldStats.resolve({ recalculating: false, job_id: null, stats: stats(111) });
    await flushEffects();

    expect(document.body.textContent).toContain("222");
    expect(document.body.textContent).not.toContain("111");
    expect(request.mock.calls.filter(([method]) => method === "dataset.statistics")).toHaveLength(2);

    act(() => mounted.root.unmount());
    mounted.host.remove();
  });

  it("ends the recomputing state when the statistics job fails", async () => {
    const ds = dataset("dataset-a");
    const request = vi.spyOn(ipc, "request").mockResolvedValueOnce({
      recalculating: true,
      job_id: "failed-job",
      stats: null,
    } as never);
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});
    waitForSuccessfulJobMock.mockRejectedValueOnce(new Error("job failed"));
    useWorkspace.setState({ datasets: [ds], activeDatasetId: ds.id });
    const mounted = await mountOverview();
    await flushEffects();

    expect(waitForSuccessfulJobMock).toHaveBeenCalledWith("failed-job");
    expect(errorLog).toHaveBeenCalled();
    expect(document.body.textContent).not.toContain("Recomputing statistics…");
    expect(request.mock.calls.filter(([method]) => method === "dataset.statistics")).toHaveLength(1);

    act(() => mounted.root.unmount());
    mounted.host.remove();
  });
});

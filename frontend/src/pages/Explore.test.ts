import { describe, expect, it, vi } from "vitest";
import { loadExploreHealth } from "./Explore";
import { createExploreFrameLoader, resolveExternalFrame } from "./exploreFrameLoader";
import type { ExploreStatisticsResponse } from "./Explore";
import type { DatasetHealth, FramePayload, HealthFindings } from "../types/protocol";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function frame(index: number): FramePayload {
  return { index, xyz: "", atoms: [] } as unknown as FramePayload;
}

function healthStatistics(marker: number): ExploreStatisticsResponse {
  return {
    recalculating: false,
    job_id: null,
    stats: {
      health: { marker } as unknown as DatasetHealth,
      health_findings: { marker } as unknown as HealthFindings,
    },
  };
}

function requestFor(
  datasetId: string,
  index: number,
  onLoading: (loading: boolean) => void,
  onFrame: (value: FramePayload, index: number) => void,
  onError?: (error: unknown) => void,
) {
  return {
    datasetId,
    total: 10,
    index,
    bondCutoff: 2.4,
    requestCutoff: 2.4,
    onLoading,
    onFrame,
    onError,
  };
}

describe("Explore frame loading", () => {
  it("commits only the newest production frame request", async () => {
    const oldResponse = deferred<FramePayload>();
    const newResponse = deferred<FramePayload>();
    const request = vi.fn()
      .mockReturnValueOnce(oldResponse.promise)
      .mockReturnValueOnce(newResponse.promise);
    const committed: number[] = [];
    const loading: boolean[] = [];
    const loader = createExploreFrameLoader(request, () => "dataset-a");

    const oldLoad = loader.load(requestFor("dataset-a", 3, (value) => loading.push(value), (value) => {
      committed.push(value.index);
    }));
    const newLoad = loader.load(requestFor("dataset-a", 7, (value) => loading.push(value), (value) => {
      committed.push(value.index);
    }));

    newResponse.resolve(frame(7));
    await newLoad;
    oldResponse.resolve(frame(3));
    await oldLoad;

    expect(request).toHaveBeenNthCalledWith(1, "dataset.frame", {
      id: "dataset-a",
      index: 3,
      bond_cutoff: 2.4,
    });
    expect(committed).toEqual([7]);
    expect(loading).toEqual([true, true, false]);
  });

  it("replays an external jump that arrived while a fetch was in flight", () => {
    // The findings drawer's "preview this frame" moves the shared pointer. If a
    // frame is already being fetched the jump used to be dropped outright, and
    // the landing response then overwrote the pointer with the frame it had been
    // fetching - the drawer reported a jump the screen never made (pass 4, E-5).
    const held = resolveExternalFrame({ pointer: 100, displayed: 6, loading: true, pending: null });
    expect(held).toEqual({ pending: 100, fetch: null });
    // The response lands on 6 and rewrites the pointer; the remembered jump
    // survives exactly because `pending` is not derived from the pointer.
    expect(resolveExternalFrame({ pointer: 6, displayed: 6, loading: false, pending: held.pending }))
      .toEqual({ pending: null, fetch: 100 });
    // Arriving at the target ends the replay rather than looping it.
    expect(resolveExternalFrame({ pointer: 100, displayed: 100, loading: false, pending: null }))
      .toEqual({ pending: null, fetch: null });
    // Nothing in flight, the jump is fetched straight away.
    expect(resolveExternalFrame({ pointer: 100, displayed: 6, loading: false, pending: null }))
      .toEqual({ pending: null, fetch: 100 });
  });

  it("drops a response after dataset switch or component unmount invalidates loading", async () => {
    const response = deferred<FramePayload>();
    let activeDatasetId: string | null = "dataset-a";
    const request = vi.fn().mockReturnValue(response.promise);
    const committed: number[] = [];
    const loading: boolean[] = [];
    const errors: unknown[] = [];
    const loader = createExploreFrameLoader(request, () => activeDatasetId);

    const load = loader.load(requestFor("dataset-a", 1, (value) => loading.push(value), (value) => {
      committed.push(value.index);
    }, (error) => errors.push(error)));
    activeDatasetId = "dataset-b";
    loader.invalidate();
    response.reject(new Error("late frame failure"));
    await load;

    expect(committed).toEqual([]);
    expect(loading).toEqual([true, false]);
    expect(errors).toEqual([]);
  });
});

describe("Explore health loading", () => {
  it("re-reads statistics after persisted job completion even when the event was missed", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce({ recalculating: true, job_id: "job-1", stats: null })
      .mockResolvedValueOnce(healthStatistics(2));
    const waitForJob = vi.fn().mockResolvedValue(undefined);
    const committed: number[] = [];

    let readsWhenRowRefetched = -1;
    await loadExploreHealth("dataset-a", request, waitForJob, () => true, (stats) => {
      committed.push((stats?.health as { marker?: number } | undefined)?.marker ?? -1);
    }, async () => {
      readsWhenRowRefetched = request.mock.calls.length;
    });

    expect(waitForJob).toHaveBeenCalledWith("job-1");
    expect(request).toHaveBeenCalledTimes(2);
    expect(committed).toEqual([2]);
    // The dataset row is refreshed after the job and before the second read, so
    // the statistics and the row they describe land together (pass 4, E-8).
    expect(readsWhenRowRefetched).toBe(1);
  });

  it("does not commit an old dataset response after the active dataset changes", async () => {
    const oldResponse = deferred<ExploreStatisticsResponse>();
    let activeDatasetId = "dataset-a";
    const request = vi.fn((datasetId: string) => datasetId === "dataset-a"
      ? oldResponse.promise
      : Promise.resolve(healthStatistics(2)));
    const committed: number[] = [];
    const isCurrent = (datasetId: string) => () => activeDatasetId === datasetId;

    const oldLoad = loadExploreHealth("dataset-a", request, vi.fn(), isCurrent("dataset-a"), (stats) => {
      committed.push((stats?.health as { marker?: number } | undefined)?.marker ?? -1);
    });
    await Promise.resolve();
    activeDatasetId = "dataset-b";
    const newLoad = loadExploreHealth("dataset-b", request, vi.fn(), isCurrent("dataset-b"), (stats) => {
      committed.push((stats?.health as { marker?: number } | undefined)?.marker ?? -1);
    });
    await newLoad;
    oldResponse.resolve(healthStatistics(1));
    await oldLoad;

    expect(committed).toEqual([2]);
    expect(request).toHaveBeenCalledTimes(2);
  });
});

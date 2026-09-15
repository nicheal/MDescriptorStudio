import { afterEach, describe, expect, it, vi } from "vitest";
import { ipc } from "../ipc/client";
import { fromJobRow, mergeJobRows, trackJob, useJobs, watchJob } from "./jobs";
import type { JobRow } from "../types/protocol";

const baseRow = (overrides: Partial<JobRow>): JobRow => ({
  id: "job-1",
  job_type: "descriptor.compute",
  dataset_id: null,
  descriptor_run_id: null,
  status: "QUEUED",
  progress: 0,
  completed: null,
  total: null,
  message: null,
  error: null,
  created_at: "2026-09-06T00:00:00+00:00",
  started_at: null,
  finished_at: null,
  ...overrides,
});

describe("queue_position", () => {
  afterEach(() => {
    useJobs.setState({ jobs: {}, order: [] });
    vi.restoreAllMocks();
  });

  it("fromJobRow keeps the persisted position", () => {
    expect(fromJobRow(baseRow({ queue_position: 2 })).queue_position).toBe(2);
    // absent for running jobs — the key must not even exist, so the live
    // overlay in mergeJobRows cannot clobber a persisted value
    expect("queue_position" in fromJobRow(baseRow({ status: "RUNNING" }))).toBe(false);
  });

  it("mergeJobRows keeps the persisted position when the live job has none", () => {
    trackJob("job-1", "descriptor.compute");
    const live = useJobs.getState().jobs["job-1"];
    const merged = mergeJobRows([baseRow({ queue_position: 3 })], [live]);
    expect(merged[0].queue_position).toBe(3);
  });

  it("keeps the dataset association for persisted and live jobs", () => {
    expect(fromJobRow(baseRow({ dataset_id: "ds-persisted" })).dataset_id).toBe("ds-persisted");
    trackJob("job-live", "descriptor.compute", "ds-live");
    expect(useJobs.getState().jobs["job-live"].dataset_id).toBe("ds-live");
    trackJob("job-1", "descriptor.compute");
    const merged = mergeJobRows([baseRow({ dataset_id: "ds-persisted" })], [useJobs.getState().jobs["job-1"]]);
    expect(merged[0].dataset_id).toBe("ds-persisted");
  });
});

describe("watchJob", () => {
  afterEach(() => {
    useJobs.setState({ jobs: {}, order: [] });
    vi.restoreAllMocks();
  });

  it("settles from persisted terminal state when the finished event was missed", async () => {
    vi.spyOn(ipc, "request").mockResolvedValue({
      id: "job-fast",
      status: "COMPLETED",
      result: { path: "saved.extxyz" },
      message: null,
      error: null,
    } as never);

    await expect(watchJob("job-fast")).resolves.toMatchObject({ status: "COMPLETED", result: { path: "saved.extxyz" }, error: null });
  });

  it("reconciles the tracked job when polling recovers a missed event", async () => {
    trackJob("job-fast", "analysis.property_correlation");
    vi.spyOn(ipc, "request").mockResolvedValue({
      id: "job-fast",
      job_type: "analysis.property_correlation",
      dataset_id: null,
      descriptor_run_id: null,
      status: "COMPLETED",
      progress: 1,
      completed: null,
      total: null,
      message: null,
      error: null,
      created_at: "2026-09-15T00:00:00+00:00",
      started_at: "2026-09-15T00:00:00+00:00",
      finished_at: "2026-09-15T00:00:01+00:00",
      result: { analysis_id: "ana-1" },
    } as never);

    await watchJob("job-fast");

    expect(useJobs.getState().jobs["job-fast"]).toMatchObject({ status: "COMPLETED", progress: 1 });
  });

  it("reconciles a running job before its first progress event", async () => {
    trackJob("job-running", "analysis.property_correlation");
    const request = vi.spyOn(ipc, "request");
    request.mockResolvedValueOnce({
      id: "job-running",
      job_type: "analysis.property_correlation",
      dataset_id: null,
      descriptor_run_id: null,
      status: "RUNNING",
      progress: 0,
      completed: null,
      total: null,
      message: "loading descriptor results",
      error: null,
      created_at: "2026-09-15T00:00:00+00:00",
      started_at: "2026-09-15T00:00:00+00:00",
      finished_at: null,
      result: null,
    } as never);
    const pending = watchJob("job-running");
    await vi.waitFor(() => expect(useJobs.getState().jobs["job-running"]).toMatchObject({ status: "RUNNING", message: "loading descriptor results" }));
    ipc.processLine(JSON.stringify({ protocol_version: 1, event: "job.finished", data: { job_id: "job-running", status: "COMPLETED", result: null, error: null } }));
    await pending;
  });

  it("prefers the live finished event result", async () => {
    vi.spyOn(ipc, "request").mockResolvedValue({
      id: "job-live",
      status: "RUNNING",
      message: null,
      error: null,
    } as never);
    const pending = watchJob("job-live");
    ipc.processLine(JSON.stringify({ protocol_version: 1, event: "job.finished", data: { job_id: "job-live", status: "COMPLETED", result: { analysis_id: "ana-1" }, error: null } }));

    await expect(pending).resolves.toMatchObject({ status: "COMPLETED", result: { analysis_id: "ana-1" } });
  });

  it("keeps watching when a poll is rejected with BUSY (backend jam)", async () => {
    const request = vi
      .spyOn(ipc, "request")
      .mockRejectedValueOnce({ code: "BUSY", message: "Backend is busy; try again shortly." })
      .mockResolvedValue({ id: "job-busy", status: "RUNNING", message: null, error: null } as never);
    const pending = watchJob("job-busy");
    // Let the first poll observe the BUSY rejection and schedule its retry.
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 0));

    // The watch must still be alive: the live event settles it normally.
    ipc.processLine(JSON.stringify({ protocol_version: 1, event: "job.finished", data: { job_id: "job-busy", status: "COMPLETED", result: null, error: null } }));
    await expect(pending).resolves.toMatchObject({ status: "COMPLETED", error: null });
  });
});

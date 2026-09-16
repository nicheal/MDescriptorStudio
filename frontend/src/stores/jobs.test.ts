import { afterEach, describe, expect, it, vi } from "vitest";
import { ipc } from "../ipc/client";
import { fromJobRow, mergeJobRows, trackJob, useJobs, waitForSuccessfulJob, watchJob, wireJobEvents } from "./jobs";
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
    vi.useRealTimers();
    ipc.disconnect();
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

  it("does not overwrite a finished job with an older in-flight poll", async () => {
    wireJobEvents(() => {});
    trackJob("job-late-poll", "descriptor.compute");
    let resolvePoll!: (row: JobRow) => void;
    vi.spyOn(ipc, "request").mockReturnValue(new Promise<JobRow>((resolve) => {
      resolvePoll = resolve;
    }) as never);
    const pending = watchJob("job-late-poll");
    ipc.processLine(JSON.stringify({
      protocol_version: 1,
      event: "job.finished",
      data: { job_id: "job-late-poll", status: "COMPLETED", result: null, error: null },
    }));
    await pending;

    resolvePoll(baseRow({ id: "job-late-poll", status: "RUNNING", progress: 0.5 }));
    await Promise.resolve();

    expect(useJobs.getState().jobs["job-late-poll"]).toMatchObject({ status: "COMPLETED", progress: 1 });
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

  it("resets the transient failure budget after successful polls", async () => {
    vi.useFakeTimers();
    const request = vi.spyOn(ipc, "request");
    for (let i = 0; i < 19; i += 1) {
      request.mockRejectedValueOnce({ code: "TRANSIENT", message: "temporary failure" });
      request.mockResolvedValueOnce(baseRow({ id: "job-intermittent", status: "RUNNING" }) as never);
    }
    request.mockRejectedValueOnce({ code: "TRANSIENT", message: "temporary failure" });
    request.mockResolvedValueOnce(baseRow({ id: "job-intermittent", status: "COMPLETED", progress: 1 }) as never);

    const pending = watchJob("job-intermittent");
    await vi.runAllTimersAsync();

    await expect(pending).resolves.toMatchObject({ status: "COMPLETED" });
    expect(request).toHaveBeenCalledTimes(40);
  });

  it("resets the transient failure budget when BUSY interrupts the sequence", async () => {
    vi.useFakeTimers();
    const request = vi.spyOn(ipc, "request");
    for (let i = 0; i < 19; i += 1) {
      request.mockRejectedValueOnce({ code: "TRANSIENT", message: "temporary failure" });
    }
    request.mockRejectedValueOnce({ code: "BUSY", message: "backend busy" });
    for (let i = 0; i < 19; i += 1) {
      request.mockRejectedValueOnce({ code: "TRANSIENT", message: "temporary failure" });
    }
    request.mockResolvedValueOnce(baseRow({ id: "job-busy-reset", status: "COMPLETED", progress: 1 }) as never);

    const pending = watchJob("job-busy-reset");
    await vi.runAllTimersAsync();

    await expect(pending).resolves.toMatchObject({ status: "COMPLETED" });
    expect(request).toHaveBeenCalledTimes(40);
  });

  it("stops after twenty consecutive non-BUSY poll failures", async () => {
    vi.useFakeTimers();
    const request = vi.spyOn(ipc, "request").mockRejectedValue({ code: "TRANSIENT", message: "temporary failure" });

    const pending = watchJob("job-down");
    await vi.runAllTimersAsync();

    await expect(pending).resolves.toMatchObject({
      status: "FAILED",
      error: { code: "TRANSIENT", message: "temporary failure" },
    });
    expect(request).toHaveBeenCalledTimes(20);
  });
});

describe("wireJobEvents", () => {
  afterEach(() => {
    vi.useRealTimers();
    ipc.disconnect();
    useJobs.setState({ jobs: {}, order: [] });
    vi.restoreAllMocks();
  });

  it.each(["COMPLETED", "FAILED", "CANCELLED"] as const)("ignores progress after %s", (status) => {
    trackJob("job-terminal-progress", "descriptor.compute");
    const setRunning = vi.fn();
    wireJobEvents(setRunning);

    ipc.processLine(JSON.stringify({
      protocol_version: 1,
      event: "job.finished",
      data: { job_id: "job-terminal-progress", status, result: null, error: null },
    }));
    ipc.processLine(JSON.stringify({
      protocol_version: 1,
      event: "job.progress",
      data: { job_id: "job-terminal-progress", progress: 0.5, completed: 5, total: 10, message: "late" },
    }));

    expect(useJobs.getState().jobs["job-terminal-progress"]).toMatchObject({ status });
    expect(setRunning).toHaveBeenLastCalledWith(0);
  });

  it("rebinds after disconnect, replaces callbacks, and does not duplicate listeners", () => {
    trackJob("job-rewire", "descriptor.compute");
    const first = vi.fn();
    const second = vi.fn();
    const staleCleanup = wireJobEvents(first);

    ipc.processLine(JSON.stringify({
      protocol_version: 1,
      event: "job.progress",
      data: { job_id: "job-rewire", progress: 0.1, completed: 1, total: 10, message: null },
    }));
    expect(first).toHaveBeenCalledTimes(1);

    wireJobEvents(second);
    staleCleanup();
    ipc.processLine(JSON.stringify({
      protocol_version: 1,
      event: "job.progress",
      data: { job_id: "job-rewire", progress: 0.2, completed: 2, total: 10, message: null },
    }));
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);

    ipc.disconnect();
    wireJobEvents(second);
    ipc.processLine(JSON.stringify({
      protocol_version: 1,
      event: "job.progress",
      data: { job_id: "job-rewire", progress: 0.3, completed: 3, total: 10, message: null },
    }));
    expect(second).toHaveBeenCalledTimes(2);

    wireJobEvents(second);
    ipc.processLine(JSON.stringify({
      protocol_version: 1,
      event: "job.progress",
      data: { job_id: "job-rewire", progress: 0.4, completed: 4, total: 10, message: null },
    }));
    expect(second).toHaveBeenCalledTimes(3);
  });
});

describe("waitForSuccessfulJob", () => {
  afterEach(() => {
    vi.useRealTimers();
    ipc.disconnect();
    useJobs.setState({ jobs: {}, order: [] });
    vi.restoreAllMocks();
  });

  it("recovers completion when the finished event happened before the watcher", async () => {
    vi.spyOn(ipc, "request").mockResolvedValue({
      id: "job-fast",
      status: "COMPLETED",
      result: null,
      message: null,
      error: null,
    } as never);
    // No listener exists yet, so this live event is intentionally missed.
    ipc.processLine(JSON.stringify({
      protocol_version: 1,
      event: "job.finished",
      data: { job_id: "job-fast", status: "COMPLETED", result: null, error: null },
    }));

    await expect(waitForSuccessfulJob("job-fast")).resolves.toBeUndefined();
  });

  it.each([
    ["FAILED", "JOB_FAILED", "The job failed."],
    ["CANCELLED", "JOB_CANCELLED", "The job was cancelled."],
  ])("rejects a %s job so callers enter their catch path", async (status, code, message) => {
    vi.spyOn(ipc, "request").mockResolvedValue({
      id: "job-terminal",
      status: "RUNNING",
      result: null,
      message: null,
      error: null,
    } as never);
    const pending = waitForSuccessfulJob("job-terminal");
    ipc.processLine(JSON.stringify({
      protocol_version: 1,
      event: "job.finished",
      data: {
        job_id: "job-terminal",
        status,
        result: null,
        error: { code, message },
      },
    }));

    await expect(pending).rejects.toMatchObject({ code, message });
  });

  it("forwards progress events while waiting", async () => {
    vi.spyOn(ipc, "request").mockResolvedValue({
      id: "job-progress",
      status: "RUNNING",
      result: null,
      message: null,
      error: null,
    } as never);
    const onProgress = vi.fn();
    const pending = waitForSuccessfulJob("job-progress", onProgress);
    ipc.processLine(JSON.stringify({
      protocol_version: 1,
      event: "job.progress",
      data: { job_id: "job-progress", progress: 0.5 },
    }));
    ipc.processLine(JSON.stringify({
      protocol_version: 1,
      event: "job.finished",
      data: { job_id: "job-progress", status: "COMPLETED", result: null, error: null },
    }));

    await pending;
    expect(onProgress).toHaveBeenCalledWith(0.5);
  });
});

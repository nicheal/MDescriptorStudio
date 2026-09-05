import { afterEach, describe, expect, it, vi } from "vitest";
import { ipc } from "../ipc/client";
import { watchJob } from "./jobs";

describe("watchJob", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("settles from persisted terminal state when the finished event was missed", async () => {
    vi.spyOn(ipc, "request").mockResolvedValue({
      id: "job-fast",
      status: "COMPLETED",
      message: null,
      error: null,
    } as never);

    await expect(watchJob("job-fast")).resolves.toMatchObject({ status: "COMPLETED", result: null, error: null });
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
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { ipc } from "../../ipc/client";
import { persistSlots, persistView } from "./persistence";

const flush = async () => {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
};

describe("settings persistence", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("answers one key at a time and keeps only the newest queued value", async () => {
    // The sidecar answers on a thread pool and settings.set is an unconditional
    // UPSERT, so two overlapping writes for one key could land in the opposite
    // order and a stale blob would be what greets the next launch.
    const seen: string[] = [];
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.spyOn(ipc, "request").mockImplementation((async (_method: string, params?: unknown) => {
      seen.push((params as { value: string }).value);
      await gate;
      return {} as never;
    }) as never);

    persistView({ n: 1 });
    persistView({ n: 2 });
    persistView({ n: 3 });
    await flush();
    // Only one write may be outstanding for a key at a time.
    expect(seen).toEqual(['{"n":1}']);

    release();
    await flush();
    // The newest value wins; the stale middle one is dropped on the floor.
    expect(seen).toEqual(['{"n":1}', '{"n":3}']);
  });

  it("does not let one key block another", async () => {
    const seen: string[] = [];
    vi.spyOn(ipc, "request").mockImplementation((async (method: string, params?: unknown) => {
      seen.push(`${method}:${(params as { key: string }).key}`);
      return {} as never;
    }) as never);

    persistView({ n: 1 });
    persistSlots({});
    await flush();

    expect(seen).toEqual([
      "settings.set:workspace.analysisUi",
      "settings.set:workspace.analysisSlots",
    ]);
  });
});

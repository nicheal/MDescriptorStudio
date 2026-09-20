import { afterEach, describe, expect, it, vi } from "vitest";

const listeners = new Map<string, (event?: unknown) => void>();
const unlisten = vi.fn();
const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));

vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async (event: string, handler: (event?: unknown) => void) => {
    listeners.set(event, handler);
    return () => {
      listeners.delete(event);
      unlisten();
    };
  }),
}));

vi.mock("@tauri-apps/api/core", () => ({ invoke: invokeMock }));

import { listen } from "@tauri-apps/api/event";
import { ipc, BACKEND_EXIT_EVENT, BACKEND_MESSAGE_EVENT } from "./client";

describe("IpcClient connection lifecycle", () => {
  afterEach(() => {
    ipc.disconnect();
    listeners.clear();
    vi.clearAllMocks();
    invokeMock.mockReset();
  });

  it("attaches listeners once and releases both on backend exit", async () => {
    const firstExit = vi.fn();
    const secondExit = vi.fn();

    await Promise.all([ipc.connect(firstExit), ipc.connect(secondExit)]);

    expect(listen).toHaveBeenCalledTimes(2);
    expect(ipc.isReady).toBe(true);

    listeners.get(BACKEND_EXIT_EVENT)?.({ payload: { logDir: null } });

    expect(unlisten).toHaveBeenCalledTimes(2);
    expect(firstExit).not.toHaveBeenCalled();
    expect(secondExit).toHaveBeenCalledTimes(1);
    expect(ipc.isReady).toBe(false);
    expect(listeners.has(BACKEND_MESSAGE_EVENT)).toBe(false);
  });

  it("rejects delayed invokes on exit and ignores late frames from the old generation", async () => {
    let resolveDelayed!: (id: number) => void;
    const delayedInvoke = new Promise<number>((resolve) => {
      resolveDelayed = resolve;
    });
    invokeMock.mockReturnValueOnce(delayedInvoke).mockResolvedValueOnce(41);

    await ipc.connect();
    const oldMessageListener = listeners.get(BACKEND_MESSAGE_EVENT);
    const oldExitListener = listeners.get(BACKEND_EXIT_EVENT);
    const request = ipc.request("slow");

    listeners.get(BACKEND_EXIT_EVENT)?.({ payload: { logDir: null } });

    await expect(
      Promise.race([
        request,
        new Promise<never>((_, reject) => setTimeout(() => reject(new Error("timeout")), 100)),
      ]),
    ).rejects.toMatchObject({ code: "BACKEND_DOWN", message: "backend exited" });

    await ipc.connect();
    resolveDelayed(41);
    await Promise.resolve();
    await Promise.resolve();

    // Deliberately malformed: a listener fired without an event object must
    // still tear the connection down rather than throw inside the handler.
    oldExitListener?.();
    expect(ipc.isReady).toBe(true);

    oldMessageListener?.({
      payload: JSON.stringify({ protocol_version: 1, id: 41, result: "stale" }),
    });

    const internal = ipc as unknown as {
      pending: Map<number, unknown>;
      pendingInvokes: Set<unknown>;
      orphanFrames: Map<number, unknown>;
    };
    expect(internal.pending.size).toBe(0);
    expect(internal.pendingInvokes.size).toBe(0);
    expect(internal.orphanFrames.size).toBe(0);

    const freshRequest = ipc.request("fresh");
    await Promise.resolve();
    await Promise.resolve();
    ipc.processLine(JSON.stringify({ protocol_version: 1, id: 41, result: "fresh" }));

    await expect(freshRequest).resolves.toBe("fresh");
    expect(internal.pending.size).toBe(0);
    expect(internal.orphanFrames.size).toBe(0);
  });
});

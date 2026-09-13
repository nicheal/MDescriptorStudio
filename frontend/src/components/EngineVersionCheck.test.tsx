import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ipc } from "../ipc/client";
import EngineVersionCheck from "./EngineVersionCheck";

vi.mock("../i18n", () => ({ useT: () => ({ t: (key: string) => key }) }));
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
afterEach(() => vi.restoreAllMocks());

it("checks the engine, keeps a fast completion event, and unsubscribes", async () => {
  let handler: (data: unknown) => void = () => {};
  const unsubscribe = vi.fn();
  vi.spyOn(ipc, "on").mockImplementation((name, callback) => {
    expect(name).toBe("engine.update.state");
    handler = callback;
    return unsubscribe;
  });
  const request = vi.spyOn(ipc, "request").mockImplementation(async () => {
    handler({ installed: "0.3.2", latest: "0.3.3", status: "available", error: null, installer_required: true });
    return { installed: "0.3.2", latest: null, status: "checking", error: null };
  });
  const container = document.createElement("div");
  const root = createRoot(container);
  try {
    await act(async () => root.render(<EngineVersionCheck />));
    expect(request).toHaveBeenCalledWith("engine.check_update");
    expect(container.textContent).toContain("Latest (PyPI): 0.3.3");
    expect(container.textContent).toContain("Update available");
    expect(container.textContent).not.toContain("install a newer Studio package");
    expect(container.textContent).not.toContain("Checking PyPI");
    await act(async () => container.querySelector("button")!.click());
    expect(request).toHaveBeenCalledTimes(2);
  } finally {
    await act(async () => root.unmount());
  }
  expect(unsubscribe).toHaveBeenCalledTimes(2);
});

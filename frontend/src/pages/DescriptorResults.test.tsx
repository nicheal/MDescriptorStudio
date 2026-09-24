// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { App as AntApp } from "antd";
import { expect, it, vi } from "vitest";
import DescriptorResults from "./DescriptorResults";
import { ipc } from "../ipc/client";
import { useWorkspace } from "../stores/workspace";
import { useI18n } from "../i18n";
import type { DatasetMeta, RunRow } from "../types/protocol";

vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn().mockResolvedValue("D:/custom.npz"),
  save: vi.fn().mockResolvedValue("D:/export.npz"),
}));

it("imports, selects the new run, exports it, and opens Analysis", async () => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("matchMedia", () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }));
  const style = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) => style(element));
  useI18n.setState({ lang: "en" });
  useWorkspace.setState({ datasets: [{ id: "ds_1", name: "Structures" } as DatasetMeta], activeDatasetId: "ds_1", activeDescriptorRunId: null });
  let rows: RunRow[] = [];
  const request = vi.spyOn(ipc, "request").mockImplementation(async (method) => {
    if (method === "result.list") return rows;
    if (method === "result.import") {
      rows = [
        { id: "run_custom", descriptor_name: "Custom", device: "external", status: "COMPLETED", scope: "dataset", created_at: "2026-01-01" } as RunRow,
        { id: "run_other", descriptor_name: "Other", device: "cpu", status: "COMPLETED", scope: "dataset", created_at: "2026-01-01" } as RunRow,
      ];
      return { run_id: "run_custom" };
    }
    return {};
  });
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const click = async (text: string) => {
    const button = Array.from(document.querySelectorAll("button")).find((el) => el.textContent?.trim() === text);
    expect(button, text).toBeTruthy();
    await act(async () => button!.click());
  };
  try {
    await act(async () => root.render(<AntApp><DescriptorResults /></AntApp>));
    await click("Import descriptors");
    expect(document.body.textContent).toContain("row_offsets");
    await click("Choose NPZ and import");
    expect(request).toHaveBeenCalledWith("result.import", expect.objectContaining({ dataset_id: "ds_1", path: "D:/custom.npz" }));
    expect(useWorkspace.getState().activeDescriptorRunId).toBe("run_custom");
    expect(host.textContent).toContain("Imported");
    expect(host.querySelector(".descriptor-results-card-header")?.textContent).not.toContain("Export NPZ");
    await act(async () => (host.querySelector('[aria-label="Export Other result"]') as HTMLButtonElement).click());
    expect(request).toHaveBeenCalledWith("result.export", expect.objectContaining({ run_id: "run_other", path: "D:/export.npz" }));
    expect(useWorkspace.getState().activeDescriptorRunId).toBe("run_custom");
    await act(async () => useI18n.setState({ lang: "zh" }));
    expect(Array.from(host.querySelectorAll("td")).some((cell) => cell.textContent === "导入")).toBe(true);
    await act(async () => useI18n.setState({ lang: "en" }));
    await click("Open Analysis");
    expect(useWorkspace.getState().page).toBe("analysis");
  } finally {
    await act(async () => root.unmount());
    host.remove();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  }
});

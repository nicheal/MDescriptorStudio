import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import ExploreSampleIndex from "./ExploreSampleIndex";
import { QueryIndexExploreButton } from "./AnalysisModuleControls";
import { ipc } from "../ipc/client";
import { useWorkspace } from "../stores/workspace";

vi.mock("./analysisShared", () => ({
  CrossDatasetPicker: () => null,
  ParamLabel: () => null,
  ProjectionControls: () => null,
  SamplingControls: () => null,
}));

let container: HTMLDivElement;
let root: Root;
const scope = { datasetId: "ds_1", runId: "run_1", mode: "atom" as const, viewId: "view_1" };
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  useWorkspace.setState({ activeDatasetId: "ds_1", selectedSample: null, activeFrameIndex: 0 });
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

it("shows the scoped reverse index and locates an entered index across frames", async () => {
  const request = vi.spyOn(ipc, "request").mockImplementation(async (_method, params) => {
    const p = params as Record<string, unknown>;
    return { dataset_id: "ds_1", total: 10, i: p.i ?? 0, frame: p.i == null ? 2 : 3, row: p.i == null ? 0 : 1 } as never;
  });
  await act(async () => root.render(<ExploreSampleIndex scope={scope} frame={2} atom={0} />));
  expect(request).toHaveBeenCalledWith("analysis.sample_identity", { run_id: "run_1", mode: "atom", view_id: "view_1", frame: 2, row: 0 });
  const input = container.querySelector("input")!;
  expect(input.value).toBe("0");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "6");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => container.querySelector("button")!.click());
  expect(request).toHaveBeenLastCalledWith("analysis.sample_identity", { run_id: "run_1", mode: "atom", view_id: "view_1", i: 6 });
  expect(useWorkspace.getState().selectedSample).toMatchObject({ frame: 3, atom: 1, runId: "run_1" });
  expect(useWorkspace.getState().activeFrameIndex).toBe(3);
});

it("does not invent an index for atoms outside the selected subset", async () => {
  vi.spyOn(ipc, "request").mockResolvedValue({ dataset_id: "ds_1", total: 10, i: null, frame: null, row: null });
  await act(async () => root.render(<ExploreSampleIndex scope={scope} frame={0} atom={0} />));
  expect(container.querySelector("input")!.value).toBe("");
  expect(container.querySelector("button")!.disabled).toBe(true);
});

it("ignores an old lookup after the selected atom changes", async () => {
  let finishOld!: (value: unknown) => void;
  vi.spyOn(ipc, "request")
    .mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; }))
    .mockResolvedValue({ dataset_id: "ds_1", total: 10, i: 7, frame: 3, row: 2 });
  await act(async () => root.render(<ExploreSampleIndex scope={scope} frame={2} atom={0} />));
  await act(async () => root.render(<ExploreSampleIndex scope={scope} frame={3} atom={2} />));
  await act(async () => finishOld({ dataset_id: "ds_1", total: 10, i: 0, frame: 2, row: 0 }));
  expect(container.querySelector("input")!.value).toBe("7");
});

it("opens query index zero in Explore with the analysis scope and atom selection", async () => {
  useWorkspace.setState({ page: "analysis" });
  const request = vi.spyOn(ipc, "request").mockResolvedValue({ dataset_id: "ds_1", frame: 5, row: 2 });
  await act(async () => root.render(<QueryIndexExploreButton runId="run_1" mode="atom" viewId="view_1" index={0} />));
  await act(async () => container.querySelector("button")!.click());
  expect(request).toHaveBeenCalledWith("analysis.sample_identity", { run_id: "run_1", mode: "atom", view_id: "view_1", i: 0 });
  expect(useWorkspace.getState()).toMatchObject({ page: "explore", activeFrameIndex: 5, selectedSample: { frame: 5, atom: 2 }, analysisSampleScope: scope });
});

it("stays in Analysis when the query index cannot be resolved", async () => {
  useWorkspace.setState({ page: "analysis" });
  vi.spyOn(ipc, "request").mockRejectedValue(new Error("sample index is out of range"));
  await act(async () => root.render(<QueryIndexExploreButton runId="run_1" mode="structure" viewId={null} index={99} />));
  await act(async () => container.querySelector("button")!.click());
  expect(useWorkspace.getState().page).toBe("analysis");
  expect(container.textContent).toContain("sample index is out of range");
});

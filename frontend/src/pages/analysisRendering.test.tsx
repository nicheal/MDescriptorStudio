import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { ipc } from "../ipc/client";
import type { AnalysisPreview } from "../types/protocol";
import { analysisCache } from "./analysisCache";
import AnalysisResultVisualization from "./analysisVisualizations";
import { useAnalysisArtifactArrays } from "./useAnalysisArtifactArrays";

const plots = vi.hoisted(() => ({ renders: 0, mounts: 0 }));
vi.mock("../viz/ScientificPlot", () => ({
  default: function Plot() {
    plots.renders++;
    useEffect(() => { plots.mounts++; }, []);
    return <div data-plot />;
  },
}));

it("mounts sampling charts only after arrays arrive and skips unrelated parent updates", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const canvasContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  plots.renders = 0;
  plots.mounts = 0;
  analysisCache.clear();
  const pending: Array<() => void> = [];
  const request = vi.spyOn(ipc, "request").mockImplementation(() => new Promise((resolve) => {
    pending.push(() => resolve({ data: [1, 0.5], shape: [2], truncated: false }));
  }));
  const preview: AnalysisPreview = { analysis_id: "sampling-render", kind: "sampling", algorithm: "fps" };
  const points = [{ i: 0, frame: 0, x: 1, y: 2 }];
  let selectedIndices = [0];
  const onSelect = vi.fn();
  function Result() {
    const { arrays, narrowed, busy } = useAnalysisArtifactArrays({ analysisId: preview.analysis_id, preview, retry: 0 });
    return <AnalysisResultVisualization preview={preview} arrays={arrays} narrowed={narrowed} points={points} selectedIndices={selectedIndices} onSelect={onSelect} loading={busy} />;
  }
  const host = document.createElement("div");
  const root = createRoot(host);
  try {
    await act(async () => root.render(<Result />));
    expect(pending.length).toBeGreaterThan(0);
    expect(plots.mounts).toBe(0);
    await act(async () => pending.forEach((resolve) => resolve()));
    expect(plots.mounts).toBe(3);
    const renders = plots.renders;
    await act(async () => root.render(<Result />));
    expect(plots.renders).toBe(renders);
    await act(async () => root.render(<Result />));
    expect(plots.mounts).toBe(3);
    selectedIndices = [];
    await act(async () => root.render(<Result />));
    expect(plots.renders).toBeGreaterThan(renders);
    expect(plots.mounts).toBe(3);
  } finally {
    await act(async () => root.unmount());
    request.mockRestore();
    canvasContext.mockRestore();
    analysisCache.clear();
    vi.unstubAllGlobals();
  }
});

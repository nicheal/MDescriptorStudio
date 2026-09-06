import { beforeEach, describe, expect, it, vi } from "vitest";

const requestMock = vi.fn<(method: string, params?: unknown) => Promise<unknown>>(async () => ({
  value: null,
}));

vi.mock("../ipc/client", () => ({
  ipc: {
    request: (method: string, params?: unknown) => requestMock(method, params),
  },
}));

import {
  DEFAULT_ANALYSIS_VIEW,
  hydrateAnalysisUi,
  parseAnalysisView,
  useAnalysisUi,
} from "./analysisUi";

const persisted: Record<string, unknown> = {
  tab: "projection",
  projection: "pca",
  overviewAnalysis: "feature_correlation",
  mode: "atom",
  preprocess: "standardized",
  runId: "run-1",
  analysisId: "ana-1",
};

describe("parseAnalysisView", () => {
  it("parses a persisted view and keeps known values", () => {
    expect(parseAnalysisView(JSON.stringify(persisted))).toEqual(persisted);
  });

  it("falls back to defaults for unknown fields", () => {
    expect(parseAnalysisView(JSON.stringify({ ...persisted, tab: "nope", projection: 3, mode: "quantum", preprocess: 0, runId: 9 }))).toEqual({
      ...DEFAULT_ANALYSIS_VIEW,
      overviewAnalysis: "feature_correlation",
      runId: null,
      analysisId: "ana-1",
    });
  });

  it("rejects non-string or unparseable values", () => {
    expect(parseAnalysisView(null)).toBeNull();
    expect(parseAnalysisView(42)).toBeNull();
    expect(parseAnalysisView("")).toBeNull();
    expect(parseAnalysisView("not-json")).toBeNull();
    expect(parseAnalysisView('{"tab":"overview"')).toBeNull();
  });
});

describe("useAnalysisUi", () => {
  beforeEach(() => {
    requestMock.mockClear();
    useAnalysisUi.setState({ view: DEFAULT_ANALYSIS_VIEW });
  });

  it("updates the view and persists it on tab changes", () => {
    useAnalysisUi.getState().setTab("projection");
    expect(useAnalysisUi.getState().view.tab).toBe("projection");
    expect(requestMock).toHaveBeenCalledWith("settings.set", {
      key: "workspace.analysisUi",
      value: JSON.stringify({ ...DEFAULT_ANALYSIS_VIEW, tab: "projection" }),
    });
  });

  it("rememberResult records the displayed analysis for its run", () => {
    useAnalysisUi.getState().rememberResult("run-1", "ana-1");
    expect(useAnalysisUi.getState().view).toMatchObject({ runId: "run-1", analysisId: "ana-1" });
    useAnalysisUi.getState().clearResult();
    expect(useAnalysisUi.getState().view.analysisId).toBeNull();
    expect(useAnalysisUi.getState().view.runId).toBe("run-1");
  });

  it("hydrates the persisted view from backend settings", async () => {
    requestMock.mockResolvedValueOnce({ value: JSON.stringify(persisted) });
    await hydrateAnalysisUi();
    expect(useAnalysisUi.getState().view).toEqual(persisted);
  });

  it("keeps the current view when hydration fails or is empty", async () => {
    useAnalysisUi.getState().setTab("kernel");
    requestMock.mockResolvedValueOnce({ value: null });
    await hydrateAnalysisUi();
    expect(useAnalysisUi.getState().view.tab).toBe("kernel");
    requestMock.mockRejectedValueOnce(new Error("backend down"));
    await hydrateAnalysisUi();
    expect(useAnalysisUi.getState().view.tab).toBe("kernel");
  });
});

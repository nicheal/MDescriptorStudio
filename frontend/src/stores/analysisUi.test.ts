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
  MAX_SLOTS,
  ANALYSIS_NAV_GROUPS,
  analysisNavModuleForAnalysisType,
  analysisNavModuleForView,
  buildParamsKey,
  hydrateAnalysisUi,
  latestSlotForModule,
  latestSlotForTab,
  parseAnalysisSlots,
  parseAnalysisView,
  slotForParams,
  slotKey,
  useAnalysisUi,
  type AnalysisParams,
  type AnalysisSlot,
} from "./analysisUi";

const persistedView = {
  tab: "projection",
  projection: "pca",
  overviewAnalysis: "feature_correlation",
  coverageMode: "coverage",
  mode: "atom",
  preprocess: "standardized",
  effectiveDimensionPreprocess: "center",
  colorBy: "energy",
  nearZeroThreshold: 0.0002,
  lowVariationThreshold: 0.02,
  featureCorrelationMethod: "spearman",
  featureCorrelationThreshold: 0.9,
};

const slotA: AnalysisSlot = { runId: "run-1", analysisId: "ana-a", tab: "similarity", paramsKey: "query|structure|10|0|full", updatedAt: 100, seq: 1 };
const slotB: AnalysisSlot = { runId: "run-1", analysisId: "ana-b", tab: "projection", paramsKey: "pca|structure|raw|full", updatedAt: 200, seq: 2 };
const slotC: AnalysisSlot = { runId: "run-1", analysisId: "ana-c", tab: "projection", paramsKey: "umap|atom|raw|full", updatedAt: 300, seq: 3 };

const baseParams: AnalysisParams = {
  projection: "pca", mode: "structure", preprocess: "raw", effectiveDimensionPreprocess: "standardized", tsnePerplexity: 30,
  similarityMode: "query", k: 10, queryIndex: 0,
  clusterAlgorithm: "kmeans", nClusters: 6,
  outlierAlgorithm: "lof", contamination: 0.01,
  samplingAlgorithm: "fps", nSamples: 1000, uncertaintyK: 8, samplingStrategy: "global", samplingScaling: "robust", samplingMinDistance: 0, samplingExistingRunId: null,
  samplingBlocks: [], samplingBudgetMode: "count", samplingCoverage: 95,
  coverageMode: "coverage", compareMode: "geometry",
  mantelMethod: "pearson", mantelPermutations: 999,
  localCutoff: 3, kernelName: "rbf",
  overviewAnalysis: "feature_variance", propertyName: "energy_per_atom",
  propertyFolds: 5, propertyReliabilityK: 5, propertyDistanceMetric: "euclidean", propertySparsePercentile: 90, propertyOodPercentile: 99,
  perturbationType: "jitter", perturbationCount: 8, perturbationMaximum: 0.2, perturbationStructures: 64, perturbationMetric: "euclidean",
  nearZeroThreshold: 1e-4, lowVariationThreshold: 1e-2, featureCorrelationMethod: "pearson", featureCorrelationThreshold: 0.95,
  referenceRunId: "run-ref", queryRunId: "run-query", referenceViewId: null, queryViewId: "view-query", viewId: null,
};

describe("buildParamsKey", () => {
  it("keys the projection tab by method, granularity and preprocess", () => {
    expect(buildParamsKey("projection", baseParams)).toBe("pca|structure|raw||full");
    expect(buildParamsKey("projection", { ...baseParams, projection: "umap", mode: "atom" })).toBe("umap|atom|raw||full");
    expect(buildParamsKey("projection", { ...baseParams, projection: "tsne", tsnePerplexity: 45 })).toBe("tsne|structure|raw|45|full");
    // perplexity only matters for t-SNE
    expect(buildParamsKey("projection", { ...baseParams, tsnePerplexity: 45 })).toBe("pca|structure|raw||full");
    // the single-run dataset view scope is part of every single-run key
    expect(buildParamsKey("projection", { ...baseParams, viewId: "view-1" })).toBe("pca|structure|raw||view-1");
  });

  it("keys module-specific parameters only for their module", () => {
    expect(buildParamsKey("similarity", baseParams)).toBe("query|structure|10|0|full");
    expect(buildParamsKey("similarity", { ...baseParams, similarityMode: "all_neighbors" })).toBe("all_neighbors|structure|10||full");
    expect(buildParamsKey("overview", baseParams)).toBe("feature_variance|0.0001|0.01|full");
    expect(buildParamsKey("overview", { ...baseParams, nearZeroThreshold: 0.2, lowVariationThreshold: 0.4 })).toBe("feature_variance|0.2|0.4|full");
    expect(buildParamsKey("overview", { ...baseParams, overviewAnalysis: "feature_correlation", featureCorrelationMethod: "spearman", featureCorrelationThreshold: 0.9 })).toBe("feature_correlation|spearman|0.9|full");
    expect(buildParamsKey("overview", { ...baseParams, overviewAnalysis: "effective_dimension", effectiveDimensionPreprocess: "standardized" })).toBe("effective_dimension|standardized|full");
    expect(buildParamsKey("coverage", baseParams)).toBe("coverage|structure|run-ref|full|run-query|view-query");
    expect(buildParamsKey("overview", { ...baseParams, overviewAnalysis: "drift" })).toBe("drift||run-ref:full:run-query:view-query");
    // FPS keys its strategy, scaling, distance threshold, warm-start run,
    // composite blocks, and budget mode; other methods leave those blank.
    expect(buildParamsKey("sampling", baseParams)).toBe("fps|1000|structure||global:robust:0:none:descriptor:count|full");
    expect(buildParamsKey("sampling", { ...baseParams, samplingStrategy: "grouped", samplingScaling: "raw", samplingMinDistance: 0.25, samplingExistingRunId: "run-warm" })).toBe("fps|1000|structure||grouped:raw:0.25:run-warm:descriptor:count|full");
    expect(buildParamsKey("sampling", { ...baseParams, samplingBlocks: ["descriptor", "lattice"], samplingBudgetMode: "coverage", samplingCoverage: 95 })).toBe("fps|1000|structure||global:robust:0:none:descriptor+lattice:cov95|full");
    expect(buildParamsKey("sampling", { ...baseParams, samplingAlgorithm: "random" })).toBe("random|1000|structure|||full");
    // The trajectory module is configured entirely inside its result view, so
    // one completed trajectory per descriptor run stays reusable.
    expect(buildParamsKey("overview", { ...baseParams, overviewAnalysis: "trajectory" })).toBe("trajectory||full");
    expect(buildParamsKey("overview", { ...baseParams, overviewAnalysis: "property_correlation" })).toBe("property_correlation|structure|energy_per_atom|5|5|euclidean|90|99|full");
    expect(buildParamsKey("overview", { ...baseParams, overviewAnalysis: "property_correlation", mode: "atom" })).toBe("property_correlation|atom|energy_per_atom|5|5|euclidean|90|99|full");
    expect(buildParamsKey("overview", {
      ...baseParams,
      overviewAnalysis: "perturbation_sensitivity",
      perturbationType: "strain", perturbationCount: 4, perturbationMaximum: 0.1, perturbationMetric: "cosine",
    })).toBe("perturbation_sensitivity|strain|4|0.1|cosine|64|full");
    // The structure cap changes what the descriptor sweep actually computed.
    expect(buildParamsKey("overview", {
      ...baseParams,
      overviewAnalysis: "perturbation_sensitivity",
      perturbationStructures: 256,
    })).toBe("perturbation_sensitivity|jitter|8|0.2|euclidean|256|full");
  });

  it("lets a single parameter be probed against the rest of the current combination", () => {
    const probed = { ...baseParams, projection: "umap" as const };
    expect(buildParamsKey("projection", probed)).toBe("umap|structure|raw||full");
    expect(buildParamsKey("projection", { ...probed, preprocess: "standardized" })).toBe("umap|structure|standardized||full");
  });
});

describe("parseAnalysisView", () => {
  it("parses a persisted view and keeps known values", () => {
    expect(parseAnalysisView(JSON.stringify(persistedView))).toEqual(persistedView);
  });

  it("bounds threshold settings and keeps the near-zero threshold below low variation", () => {
    expect(parseAnalysisView(JSON.stringify({ ...persistedView, nearZeroThreshold: 2, lowVariationThreshold: -1 }))).toMatchObject({ nearZeroThreshold: 1, lowVariationThreshold: 1 });
  });

  it("falls back to defaults for unknown fields", () => {
    expect(parseAnalysisView(JSON.stringify({ ...persistedView, tab: "nope", projection: 3, mode: "quantum", preprocess: 0, colorBy: "rainbow", featureCorrelationMethod: "kendall" }))).toEqual({
      ...DEFAULT_ANALYSIS_VIEW,
      overviewAnalysis: "feature_correlation",
      effectiveDimensionPreprocess: persistedView.effectiveDimensionPreprocess,
      nearZeroThreshold: persistedView.nearZeroThreshold,
      lowVariationThreshold: persistedView.lowVariationThreshold,
      featureCorrelationMethod: DEFAULT_ANALYSIS_VIEW.featureCorrelationMethod,
      featureCorrelationThreshold: persistedView.featureCorrelationThreshold,
    });
  });

  it("defaults a missing coverage mode without migrating the old tab or overview", () => {
    expect(parseAnalysisView(JSON.stringify({ tab: "overview", overviewAnalysis: "drift" }))).toMatchObject({
      tab: "overview",
      overviewAnalysis: "drift",
      coverageMode: "coverage",
    });
    expect(DEFAULT_ANALYSIS_VIEW).toMatchObject({ tab: "projection", projection: "pca" });
  });

  it("rejects non-string or unparseable values", () => {
    expect(parseAnalysisView(null)).toBeNull();
    expect(parseAnalysisView(42)).toBeNull();
    expect(parseAnalysisView("")).toBeNull();
    expect(parseAnalysisView("not-json")).toBeNull();
  });
});

describe("parseAnalysisSlots", () => {
  it("parses valid slots and drops invalid entries", () => {
    const slots = parseAnalysisSlots(JSON.stringify({
      "similarity|run-1|x": slotA,
      "projection|run-1|y": slotB,
      bad: { runId: "run-1" },
      "projection|run-1|z": { ...slotC, tab: "nope" },
    }));
    expect(Object.keys(slots ?? {}).sort()).toEqual(["projection|run-1|y", "similarity|run-1|x"]);
  });

  it("returns null for junk input and enforces the cap", () => {
    expect(parseAnalysisSlots("junk")).toBeNull();
    expect(parseAnalysisSlots([])).toBeNull();
    const many = Object.fromEntries(
      Array.from({ length: MAX_SLOTS + 5 }, (_, i) => [`${i}`, { ...slotA, updatedAt: i }]),
    );
    expect(Object.keys(parseAnalysisSlots(JSON.stringify(many)) ?? {}).length).toBe(MAX_SLOTS);
  });
});

describe("slot lookups", () => {
  const slots: Record<string, AnalysisSlot> = {
    [slotKey(slotA)]: slotA,
    [slotKey(slotB)]: slotB,
    [slotKey(slotC)]: slotC,
  };

  it("slotForParams matches tab + run + parameters exactly", () => {
    expect(slotForParams(slots, "similarity", "run-1", "query|structure|10|0|full")).toEqual(slotA);
    expect(slotForParams(slots, "similarity", "run-1", "other")).toBeNull();
    expect(slotForParams(slots, "similarity", "run-2", "query|structure|10|0|full")).toBeNull();
    expect(slotForParams(slots, "similarity", null, "query|structure|10|0|full")).toBeNull();
  });

  it("latestSlotForTab picks the newest slot of a tab regardless of parameters", () => {
    expect(latestSlotForTab(slots, "projection", "run-1")).toEqual(slotC);
    expect(latestSlotForTab(slots, "clusters", "run-1")).toBeNull();
  });

  it("latestSlotForModule keeps shared overview slots isolated", () => {
    const trajectory: AnalysisSlot = { runId: "run-1", analysisId: "ana-t", tab: "overview", paramsKey: "trajectory||full", updatedAt: 400, seq: 4 };
    const variance: AnalysisSlot = { runId: "run-1", analysisId: "ana-v", tab: "overview", paramsKey: "feature_variance|0.0001|0.01|full", updatedAt: 500, seq: 5 };
    const overviewSlots = { [slotKey(trajectory)]: trajectory, [slotKey(variance)]: variance };
    expect(latestSlotForModule(overviewSlots, "descriptor_trajectory", "run-1")).toEqual(trajectory);
    expect(latestSlotForModule(overviewSlots, "feature_variance", "run-1")).toEqual(variance);
  });

  it("maps legacy history names to the existing module options", () => {
    expect(analysisNavModuleForAnalysisType("hierarchical")?.key).toBe("structural_clusters");
    expect(analysisNavModuleForAnalysisType("iforest")?.key).toBe("outlier_environments");
    expect(analysisNavModuleForAnalysisType("acquisition")?.key).toBe("representative_sampling");
  });
});

describe("useAnalysisUi", () => {
  beforeEach(() => {
    requestMock.mockClear();
    useAnalysisUi.setState({ view: DEFAULT_ANALYSIS_VIEW, slots: {}, recentModulesByGroup: {} });
  });

  it("updates the view and persists it on parameter changes", () => {
    useAnalysisUi.getState().setColorBy("energy");
    expect(useAnalysisUi.getState().view.colorBy).toBe("energy");
    expect(requestMock).toHaveBeenCalledWith("settings.set", {
      key: "workspace.analysisUi",
      value: JSON.stringify({ ...DEFAULT_ANALYSIS_VIEW, colorBy: "energy" }),
    });
  });

  it("updates navigation target atomically and persists once", () => {
    useAnalysisUi.getState().setNavigationTarget({ tab: "coverage", coverageMode: "overlap" });
    expect(useAnalysisUi.getState().view).toMatchObject({ tab: "coverage", coverageMode: "overlap" });
    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(requestMock).toHaveBeenCalledWith("settings.set", {
      key: "workspace.analysisUi",
      value: JSON.stringify({ ...DEFAULT_ANALYSIS_VIEW, tab: "coverage", coverageMode: "overlap" }),
    });
  });

  it("remembers the latest module per group in memory only", () => {
    useAnalysisUi.getState().rememberNavigationModule("evolution_response", "descriptor_trajectory");
    expect(useAnalysisUi.getState().recentModulesByGroup).toEqual({ evolution_response: "descriptor_trajectory" });
    expect(requestMock).not.toHaveBeenCalled();
    expect(analysisNavModuleForView("overview", "trajectory", "coverage")?.key).toBe("descriptor_trajectory");
    expect(ANALYSIS_NAV_GROUPS).toHaveLength(6);
  });

  it("keeps threshold setters ordered and bounded", () => {
    useAnalysisUi.getState().setNearZeroThreshold(0.4);
    expect(useAnalysisUi.getState().view).toMatchObject({ nearZeroThreshold: 0.4, lowVariationThreshold: 0.4 });
    useAnalysisUi.getState().setLowVariationThreshold(-1);
    expect(useAnalysisUi.getState().view).toMatchObject({ nearZeroThreshold: 0.4, lowVariationThreshold: 0.4 });
  });

  it("rememberResult keys by tab + run + parameters and prunes to the cap", () => {
    const entry = { runId: "run-1", analysisId: "ana-a", tab: "similarity" as const, paramsKey: "k" };
    useAnalysisUi.getState().rememberResult(entry);
    expect(useAnalysisUi.getState().slots["similarity|run-1|k"]).toMatchObject({ ...entry, updatedAt: expect.any(Number) });
    for (let i = 0; i < MAX_SLOTS + 2; i += 1) {
      useAnalysisUi.getState().rememberResult({ ...entry, analysisId: `ana-${i}`, paramsKey: `k${i}` });
    }
    const slots = useAnalysisUi.getState().slots;
    expect(Object.keys(slots).length).toBe(MAX_SLOTS);
    expect(slots["similarity|run-1|k"]).toBeUndefined();
    expect(requestMock).toHaveBeenLastCalledWith("settings.set", {
      key: "workspace.analysisSlots",
      value: expect.any(String),
    });
  });

  it("clearResult forgets every slot of a deleted analysis", () => {
    useAnalysisUi.getState().rememberResult({ ...slotA });
    useAnalysisUi.getState().rememberResult({ ...slotB, analysisId: "ana-a", tab: "projection" as const });
    useAnalysisUi.getState().rememberResult({ ...slotC });
    useAnalysisUi.getState().clearResult("ana-a");
    const slots = useAnalysisUi.getState().slots;
    expect(Object.values(slots).some((slot) => slot.analysisId === "ana-a")).toBe(false);
    expect(Object.keys(slots).length).toBe(1);
    expect(useAnalysisUi.getState().view.tab).toBe(DEFAULT_ANALYSIS_VIEW.tab);
  });

  it("forgetSlot drops one slot by key and ignores unknown keys", () => {
    useAnalysisUi.getState().rememberResult({ ...slotA });
    useAnalysisUi.getState().rememberResult({ ...slotC });
    useAnalysisUi.getState().forgetSlot(slotKey(slotA));
    let slots = useAnalysisUi.getState().slots;
    expect(slots[slotKey(slotA)]).toBeUndefined();
    expect(slots[slotKey(slotC)]).toMatchObject({ ...slotC, updatedAt: expect.any(Number), seq: expect.any(Number) });
    useAnalysisUi.getState().forgetSlot("overview|run-1|never");
    slots = useAnalysisUi.getState().slots;
    expect(Object.keys(slots).length).toBe(1);
  });

  it("hydrates the persisted view and slots from backend settings", async () => {
    requestMock.mockImplementation(async (_method, params) => ({
      value: (params as { key: string }).key === "workspace.analysisUi"
        ? JSON.stringify(persistedView)
        : JSON.stringify({ "projection|run-1|y": slotB }),
    }));
    await hydrateAnalysisUi();
    expect(useAnalysisUi.getState().view).toEqual(persistedView);
    expect(useAnalysisUi.getState().slots["projection|run-1|y"]).toEqual(slotB);
  });

  it("keeps the current state when hydration fails or is empty", async () => {
    useAnalysisUi.getState().setTab("kernel");
    requestMock.mockImplementation(async () => ({ value: null }));
    await hydrateAnalysisUi();
    expect(useAnalysisUi.getState().view.tab).toBe("kernel");
    requestMock.mockRejectedValue(new Error("backend down"));
    await hydrateAnalysisUi();
    expect(useAnalysisUi.getState().view.tab).toBe("kernel");
  });
});

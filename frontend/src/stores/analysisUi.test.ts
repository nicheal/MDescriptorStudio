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
  MAX_PERSISTED_SLOT_CHARS,
  ANALYSIS_NAV_GROUPS,
  analysisNavModuleForAnalysisType,
  analysisNavModuleForView,
  buildAnalysisInputKey,
  buildParamsKey,
  hydrateAnalysisUi,
  latestSlotForModule,
  parseAnalysisSlots,
  parseAnalysisView,
  serializeAnalysisSlots,
  slotForParams,
  slotKey,
  useAnalysisUi,
  type AnalysisParams,
  type AnalysisSlot,
} from "../features/analysis";

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

const slotA: AnalysisSlot = { analysisId: "ana-a", moduleKey: "similarity", inputKey: "run-1|full", parameterKey: "query|structure|10|0|full", updatedAt: 100, seq: 1 };
const slotB: AnalysisSlot = { analysisId: "ana-b", moduleKey: "descriptor_space", inputKey: "run-1|full", parameterKey: "pca|structure|raw|full", updatedAt: 200, seq: 2 };
const slotC: AnalysisSlot = { analysisId: "ana-c", moduleKey: "descriptor_space", inputKey: "run-1|full", parameterKey: "umap|atom|raw|full", updatedAt: 300, seq: 3 };

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
    expect(buildParamsKey("overview", { ...baseParams, overviewAnalysis: "drift" })).toBe("drift|structure|run-ref:full:run-query:view-query");
    // Drift is measured on whichever matrix `mode` selects, so the two granularities
    // are two different answers - the same rule property_correlation already follows.
    expect(buildParamsKey("overview", { ...baseParams, overviewAnalysis: "drift", mode: "atom" })).toBe("drift|atom|run-ref:full:run-query:view-query");
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

  it("keeps every descriptor input in the cache identity", () => {
    expect(buildAnalysisInputKey("compare", baseParams, "run-a", "run-b"))
      .not.toBe(buildAnalysisInputKey("compare", baseParams, "run-a", "run-c"));
    expect(buildAnalysisInputKey("overview", { ...baseParams, overviewAnalysis: "sensitivity" }, "run-a", "run-b"))
      .not.toBe(buildAnalysisInputKey("overview", { ...baseParams, overviewAnalysis: "sensitivity" }, "run-a", "run-c"));
    expect(buildAnalysisInputKey("sampling", { ...baseParams, samplingExistingRunId: "run-b" }, "run-a", null))
      .not.toBe(buildAnalysisInputKey("sampling", { ...baseParams, samplingExistingRunId: "run-c" }, "run-a", null));
  });
});

describe("parseAnalysisView", () => {
  it("parses a persisted view and keeps known values", () => {
    expect(parseAnalysisView(JSON.stringify(persistedView))).toEqual(persistedView);
  });

  it("bounds threshold settings and keeps the near-zero threshold below low variation", () => {
    expect(parseAnalysisView(JSON.stringify({ ...persistedView, nearZeroThreshold: 2, lowVariationThreshold: -1 }))).toMatchObject({ nearZeroThreshold: 1, lowVariationThreshold: 1 });
  });

  it("rejects a persisted preprocess the backend would refuse", () => {
    expect(parseAnalysisView(JSON.stringify({ ...persistedView, preprocess: "banana" }))).toMatchObject({
      preprocess: DEFAULT_ANALYSIS_VIEW.preprocess,
    });
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
      bad: { analysisId: "ana-bad" },
      "projection|run-1|z": { ...slotC, moduleKey: "nope" },
    }));
    expect(Object.keys(slots ?? {}).sort()).toEqual([slotKey(slotA), slotKey(slotB)].sort());
  });

  it("returns null for junk input and enforces the byte budget", () => {
    expect(parseAnalysisSlots("junk")).toBeNull();
    expect(parseAnalysisSlots([])).toBeNull();
    const many = Object.fromEntries(
      Array.from({ length: 40 }, (_, i) => [`${i}`, { ...slotA, inputKey: `run-${i}-${"x".repeat(40)}`, parameterKey: `k${i}-${"y".repeat(40)}`, updatedAt: i, seq: i }]),
    );
    const serialized = serializeAnalysisSlots(many);
    expect(serialized.length).toBeLessThanOrEqual(MAX_PERSISTED_SLOT_CHARS);
    const parsed = parseAnalysisSlots(serialized);
    expect(parsed && Object.keys(parsed).length).toBeGreaterThan(0);
    expect(Object.values(parsed ?? {}).some((slot) => slot.inputKey.includes("run-39"))).toBe(true);
  });
});

describe("slot lookups", () => {
  const slots: Record<string, AnalysisSlot> = {
    [slotKey(slotA)]: slotA,
    [slotKey(slotB)]: slotB,
    [slotKey(slotC)]: slotC,
  };

  it("slotForParams matches module + input + parameters exactly", () => {
    expect(slotForParams(slots, "similarity", "run-1|full", "query|structure|10|0|full")).toEqual(slotA);
    expect(slotForParams(slots, "similarity", "run-1|full", "other")).toBeNull();
    expect(slotForParams(slots, "similarity", "run-2|full", "query|structure|10|0|full")).toBeNull();
    expect(slotForParams(slots, null, "run-1|full", "query|structure|10|0|full")).toBeNull();
  });

  it("latestSlotForModule keeps shared overview slots isolated", () => {
    const trajectory: AnalysisSlot = { analysisId: "ana-t", moduleKey: "descriptor_trajectory", inputKey: "run-1|full", parameterKey: "trajectory||full", updatedAt: 400, seq: 4 };
    const variance: AnalysisSlot = { analysisId: "ana-v", moduleKey: "feature_variance", inputKey: "run-1|full", parameterKey: "feature_variance|0.0001|0.01|full", updatedAt: 500, seq: 5 };
    const overviewSlots = { [slotKey(trajectory)]: trajectory, [slotKey(variance)]: variance };
    expect(latestSlotForModule(overviewSlots, "descriptor_trajectory", "run-1|full")).toEqual(trajectory);
    expect(latestSlotForModule(overviewSlots, "feature_variance", "run-1|full")).toEqual(variance);
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

  it("rememberResult keys by module + input + parameters and prunes to the byte budget", () => {
    const entry = { analysisId: "ana-a", moduleKey: "similarity" as const, inputKey: "run-1|full", parameterKey: "k" };
    useAnalysisUi.getState().rememberResult(entry);
    expect(useAnalysisUi.getState().slots["similarity|run-1|full|k"]).toMatchObject({ ...entry, updatedAt: expect.any(Number) });
    for (let i = 0; i < 40; i += 1) {
      useAnalysisUi.getState().rememberResult({ ...entry, analysisId: `ana-${i}`, inputKey: `run-${i}-${"x".repeat(40)}`, parameterKey: `k${i}-${"y".repeat(40)}` });
    }
    const slots = useAnalysisUi.getState().slots;
    expect(Object.keys(slots).length).toBeLessThan(40);
    expect(slots["similarity|run-1|full|k"]).toBeUndefined();
    expect(requestMock).toHaveBeenLastCalledWith("settings.set", {
      key: "workspace.analysisSlots",
      value: expect.stringMatching(new RegExp(`^.{0,${MAX_PERSISTED_SLOT_CHARS}}$`)),
    });
  });

  it("clearResult forgets every slot of a deleted analysis", () => {
    useAnalysisUi.getState().rememberResult({ ...slotA });
    useAnalysisUi.getState().rememberResult({ ...slotB, analysisId: "ana-a" });
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
        : JSON.stringify({ [slotKey(slotB)]: slotB }),
    }));
    await hydrateAnalysisUi();
    expect(useAnalysisUi.getState().view).toEqual(persistedView);
    expect(useAnalysisUi.getState().slots[slotKey(slotB)]).toEqual(slotB);
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

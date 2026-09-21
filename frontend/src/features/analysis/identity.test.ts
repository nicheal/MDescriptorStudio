// identity.ts is the one place that decides "has this exact computation already
// been done?". Its contract is therefore not "the key looks stable" but
// "the key changes whenever the request changes" — a field missing from the key
// makes two different analyses collide on one slot, and the second then re-displays
// the first one's result. This sweeps every tab against every parameter instead
// of trusting a hand-written list.
import { describe, expect, it } from "vitest";
import { buildAnalysisInputKey, buildParamsKey } from "./identity";
import { buildSubmission, type SubmissionContext } from "./submission";
import type { AnalysisParams, TabKey } from "./types";
import { DEFAULT_ANALYSIS_VIEW } from "./navigation";

const params = (over: Partial<AnalysisParams> = {}): AnalysisParams => ({
  projection: DEFAULT_ANALYSIS_VIEW.projection,
  mode: DEFAULT_ANALYSIS_VIEW.mode,
  preprocess: DEFAULT_ANALYSIS_VIEW.preprocess,
  effectiveDimensionPreprocess: DEFAULT_ANALYSIS_VIEW.effectiveDimensionPreprocess,
  tsnePerplexity: 30,
  similarityMode: "query",
  k: 10,
  queryIndex: 0,
  clusterAlgorithm: "kmeans",
  nClusters: 6,
  outlierAlgorithm: "lof",
  contamination: 0.01,
  samplingAlgorithm: "fps",
  nSamples: 1000,
  uncertaintyK: 8,
  samplingStrategy: "global",
  samplingScaling: "robust",
  samplingMinDistance: 0,
  samplingExistingRunId: null,
  samplingBlocks: [],
  samplingBudgetMode: "count",
  samplingCoverage: 95,
  coverageMode: DEFAULT_ANALYSIS_VIEW.coverageMode,
  compareMode: "geometry",
  mantelMethod: "pearson",
  mantelPermutations: 999,
  localCutoff: 3,
  kernelName: "rbf",
  overviewAnalysis: DEFAULT_ANALYSIS_VIEW.overviewAnalysis,
  propertyName: "energy_per_atom",
  propertyFolds: 5,
  propertyReliabilityK: 5,
  propertyDistanceMetric: "euclidean",
  propertySparsePercentile: 90,
  propertyOodPercentile: 99,
  perturbationType: "jitter",
  perturbationCount: 8,
  perturbationMaximum: 0.2,
  perturbationStructures: 64,
  perturbationMetric: "euclidean",
  nearZeroThreshold: 1e-4,
  lowVariationThreshold: 1e-2,
  featureCorrelationMethod: DEFAULT_ANALYSIS_VIEW.featureCorrelationMethod,
  featureCorrelationThreshold: DEFAULT_ANALYSIS_VIEW.featureCorrelationThreshold,
  referenceRunId: "run_ref",
  queryRunId: "run_query",
  referenceViewId: null,
  queryViewId: null,
  viewId: null,
  ...over,
});

const context: SubmissionContext = {
  selectedRun: "run_a",
  secondRun: "run_b",
  crossInputsReady: true,
  referenceDescriptor: "soap",
  queryDescriptor: "soap",
};

// A second value for every field, chosen so a field that participates in the
// request produces a different request.
const alternates: Record<keyof AnalysisParams, unknown> = {
  projection: "umap",
  mode: "atom",
  preprocess: "standardized",
  effectiveDimensionPreprocess: "center",
  tsnePerplexity: 45,
  similarityMode: "all_neighbors",
  k: 11,
  queryIndex: 4,
  clusterAlgorithm: "dbscan",
  nClusters: 7,
  outlierAlgorithm: "knn",
  contamination: 0.02,
  samplingAlgorithm: "random",
  nSamples: 500,
  uncertaintyK: 12,
  samplingStrategy: "blocked",
  samplingScaling: "none",
  samplingMinDistance: 0.5,
  samplingExistingRunId: "run_prev",
  samplingBlocks: ["energy"],
  samplingBudgetMode: "coverage",
  samplingCoverage: 90,
  coverageMode: "overlap",
  compareMode: "mantel",
  mantelMethod: "spearman",
  mantelPermutations: 499,
  localCutoff: 4,
  kernelName: "linear",
  overviewAnalysis: "property_correlation",
  propertyName: "volume",
  propertyFolds: 6,
  propertyReliabilityK: 7,
  propertyDistanceMetric: "cosine",
  propertySparsePercentile: 80,
  propertyOodPercentile: 95,
  perturbationType: "strain",
  perturbationCount: 9,
  perturbationMaximum: 0.3,
  perturbationStructures: 32,
  perturbationMetric: "cosine",
  nearZeroThreshold: 1e-3,
  lowVariationThreshold: 5e-2,
  featureCorrelationMethod: "pearson",
  featureCorrelationThreshold: 0.7,
  referenceRunId: "run_other_ref",
  queryRunId: "run_other_query",
  referenceViewId: "view_ref",
  queryViewId: "view_query",
  viewId: "view_1",
};

const TABS: TabKey[] = [
  "overview", "projection", "similarity", "clusters", "outliers",
  "sampling", "coverage", "compare", "local", "kernel",
];

/** Each tab's own selectors picked in turn: which parameters a module submits
 * depends on them, so one baseline per tab would leave whole branches unswept. */
const VARIANTS: Partial<Record<TabKey, Partial<AnalysisParams>[]>> = {
  overview: (["feature_variance", "feature_correlation", "effective_dimension", "property_correlation",
    "trajectory", "drift", "sensitivity", "perturbation_sensitivity"] as const)
    .map((overviewAnalysis) => ({ overviewAnalysis })),
  projection: (["pca", "umap", "tsne"] as const).map((projection) => ({ projection })),
  similarity: (["query", "all_neighbors", "pairwise"] as const).map((similarityMode) => ({ similarityMode })),
  sampling: (["fps", "random", "cluster_representative", "novelty_fps", "uncertainty_diversity"] as const)
    .map((samplingAlgorithm) => ({ samplingAlgorithm })),
  outliers: (["lof", "knn", "isolation_forest", "mahalanobis"] as const)
    .map((outlierAlgorithm) => ({ outlierAlgorithm })),
  clusters: (["kmeans", "dbscan", "hdbscan", "agglomerative"] as const)
    .map((clusterAlgorithm) => ({ clusterAlgorithm })),
  compare: (["geometry", "mantel"] as const).map((compareMode) => ({ compareMode })),
};

/** What the page would actually put on the wire, in the projection tab's own words. */
function request(tab: TabKey, p: AnalysisParams): string {
  const submission = buildSubmission(tab, p, context);
  if (submission.kind === "projection") {
    // Analysis.tsx:runProjection owns this payload; mirror its shape here so the
    // projection tab is covered by the same invariant as the run tabs.
    return JSON.stringify({
      method: `analysis.${p.projection}`,
      params: {
        mode: p.mode,
        preprocess: p.preprocess,
        ...(p.projection === "tsne" ? { perplexity: p.tsnePerplexity } : {}),
        ...(p.viewId ? { view_id: p.viewId } : {}),
      },
    });
  }
  return JSON.stringify(submission);
}

function identity(tab: TabKey, p: AnalysisParams): string {
  return `${buildParamsKey(tab, p)}#${buildAnalysisInputKey(tab, p, context.selectedRun, context.secondRun)}`;
}

interface Case {
  label: string;
  tab: TabKey;
  base: AnalysisParams;
}

const CASES: Case[] = TABS.flatMap((tab) =>
  (VARIANTS[tab] ?? [{}]).map((variant) => ({
    label: `${tab}${Object.keys(variant).length ? ` ${JSON.stringify(variant)}` : ""}`,
    tab,
    base: params(variant),
  })),
);

describe("analysis identity keys", () => {
  it.each(CASES.map((item) => [item.label, item] as const))("%s covers every parameter it submits", (_label, item) => {
    const { tab, base } = item;
    // A field the request never reads may move freely: that is what keeps an
    // unrelated k from blanking a module that does not use k.
    const uncovered = (Object.keys(alternates) as (keyof AnalysisParams)[])
      .filter((field) => {
        const variant = { ...base, [field]: alternates[field] };
        return request(tab, variant) !== request(tab, base) && identity(tab, variant) === identity(tab, base);
      });
    // Report the whole list, so one run names every hole in this case.
    expect(uncovered, `${item.label} ignores ${JSON.stringify(uncovered)} that it submits`).toEqual([]);
  });

  it("separates drift measured on structures from drift measured on atoms", () => {
    // mode picks AtomDescriptorMatrix over StructureDescriptorMatrix in the
    // loader, so these are two different answers, not two spellings of one.
    expect(buildParamsKey("overview", params({ overviewAnalysis: "drift", mode: "structure" })))
      .not.toBe(buildParamsKey("overview", params({ overviewAnalysis: "drift", mode: "atom" })));
  });

  it("leaves the key alone where the request genuinely ignores the field", () => {
    // The other half of the contract: a field a module never reads must not be
    // able to blank its cached result.
    const ignored: [TabKey, keyof AnalysisParams][] = [
      ["clusters", "k"],
      ["clusters", "contamination"],
      ["local", "mode"],
      ["outliers", "nClusters"],
      ["compare", "mantelPermutations"],
      ["coverage", "viewId"],
      ["similarity", "projection"],
      ["kernel", "nSamples"],
    ];
    const base = params();
    for (const [tab, field] of ignored) {
      const variant = params({ [field]: alternates[field] });
      expect(request(tab, variant), `${tab} on ${field}`).toBe(request(tab, base));
      expect(identity(tab, variant), `${tab} on ${field}`).toBe(identity(tab, base));
    }
  });
});

// The submit payload is the contract between the Analysis page and the backend:
// one case per module, pinned by method name and exact parameter object. These
// are the tests that make touching the page's run wiring safe.
import { describe, expect, it } from "vitest";
import { buildSubmission, type SubmissionContext } from "./submission";
import type { AnalysisParams, TabKey } from "./types";

const params = (overrides: Partial<AnalysisParams> = {}): AnalysisParams => ({
  projection: "pca",
  mode: "structure",
  preprocess: "center",
  effectiveDimensionPreprocess: "standardized",
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
  coverageMode: "coverage",
  compareMode: "geometry",
  mantelMethod: "pearson",
  mantelPermutations: 999,
  localCutoff: 3,
  kernelName: "rbf",
  overviewAnalysis: "feature_variance",
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
  nearZeroThreshold: 0.02,
  lowVariationThreshold: 0.05,
  featureCorrelationMethod: "spearman",
  featureCorrelationThreshold: 0.8,
  referenceRunId: null,
  queryRunId: null,
  referenceViewId: null,
  queryViewId: null,
  viewId: null,
  ...overrides,
});

const context = (overrides: Partial<SubmissionContext> = {}): SubmissionContext => ({
  selectedRun: "run_a",
  secondRun: null,
  crossInputsReady: false,
  referenceDescriptor: null,
  queryDescriptor: null,
  ...overrides,
});

const run = (tab: TabKey, p: Partial<AnalysisParams> = {}, c: Partial<SubmissionContext> = {}) => {
  const submission = buildSubmission(tab, params(p), context(c));
  if (submission.kind !== "run") throw new Error(`expected a run, got ${submission.kind}`);
  return submission;
};

describe("analysis submission payloads", () => {
  it("hands the projection tab back to the page, which owns PCA/UMAP/t-SNE", () => {
    expect(buildSubmission("projection", params(), context())).toEqual({ kind: "projection" });
  });

  it("runs a query-mode similarity over a raw cosine space", () => {
    expect(run("similarity").method).toBe("analysis.similarity");
    expect(run("similarity", { k: 12, queryIndex: 3 })).toMatchObject({
      params: { k: 12, query_index: 3, metric: "cosine", preprocess: "raw", mode: "structure" },
    });
  });

  it("omits k from pairwise similarity, which correlates every stored pair", () => {
    expect(run("similarity", { similarityMode: "pairwise", k: 42 })).toEqual({
      kind: "run",
      method: "analysis.pairwise",
      label: "Pairwise similarity",
      params: { metric: "cosine", preprocess: "raw", mode: "structure", max_samples: 400 },
    });
  });

  it("labels the all-neighbours variant as its own method", () => {
    expect(run("similarity", { similarityMode: "all_neighbors" }).method).toBe("analysis.neighbors");
  });

  it("standardizes the space for clustering and outlier scoring", () => {
    expect(run("clusters", { clusterAlgorithm: "gmm", nClusters: 4 }).params).toEqual({
      algorithm: "gmm", n_clusters: 4, preprocess: "standardized", mode: "structure",
    });
    // isForest never reads k, so it must not be sent: an unrelated k change on
    // another panel would otherwise look like a different analysis here.
    expect(run("outliers", { outlierAlgorithm: "iforest", contamination: 0.05 }).params).toEqual({
      algorithm: "isolation_forest", contamination: 0.05, preprocess: "standardized", mode: "structure",
    });
    expect(run("outliers", { outlierAlgorithm: "lof", k: 7 }).params).toMatchObject({ algorithm: "lof", k: 7 });
    expect(run("outliers", { outlierAlgorithm: "mahalanobis" }).params).not.toHaveProperty("k");
  });

  it("slices every single-run module by the active dataset view", () => {
    for (const tab of ["similarity", "clusters", "outliers", "sampling", "kernel"] as TabKey[]) {
      expect(run(tab, { viewId: "view_1" }).params).toMatchObject({ view_id: "view_1" });
    }
  });

  it("maps the fps budget mode onto a target coverage quantile", () => {
    expect(run("sampling", { samplingBudgetMode: "coverage", samplingCoverage: 90 }).params).toMatchObject({
      target_coverage: 0.9,
    });
    expect(run("sampling", { samplingAlgorithm: "random" }).params).not.toHaveProperty("strategy");
    // Only the two algorithms that measure a distance name the space they did it in.
    expect(run("sampling", { samplingAlgorithm: "random" }).params).not.toHaveProperty("scaling");
    expect(run("sampling", { samplingAlgorithm: "cluster_representative" }).params)
      .toMatchObject({ scaling: "robust" });
    expect(run("sampling", { samplingBlocks: ["energy", "forces"] }).params).toMatchObject({ blocks: ["energy", "forces"] });
    expect(run("sampling", { samplingExistingRunId: "run_prev" }).params).toMatchObject({ existing_run_id: "run_prev" });
  });

  it("anchors acquisition and coverage modules to the reference run", () => {
    const acquisition = run("sampling", {
      samplingAlgorithm: "uncertainty_diversity", uncertaintyK: 7, nSamples: 50,
      referenceRunId: "run_ref", queryRunId: "run_q", referenceViewId: "view_r", queryViewId: "view_q",
    }, { crossInputsReady: true });
    expect(acquisition).toMatchObject({ method: "analysis.acquisition", label: "Uncertainty acquisition", anchoredToReference: true });
    expect(acquisition.params).toEqual({
      reference_run_id: "run_ref", query_run_id: "run_q", reference_view_id: "view_r", query_view_id: "view_q",
      n_samples: 50, mode: "structure", acquisition_method: "uncertainty_diversity",
      novelty_weight: 0.65, uncertainty_weight: 0.65, uncertainty_k: 7,
    });
    expect(run("coverage", { coverageMode: "overlap", referenceRunId: "run_ref", queryRunId: "run_q" }, { crossInputsReady: true })).toMatchObject({
      method: "analysis.overlap", label: "Overlap", anchoredToReference: true,
      params: { reference_run_id: "run_ref", query_run_id: "run_q", metric: "euclidean", mode: "structure" },
    });
  });

  it("refuses the cross-dataset modules until both runs are compatible", () => {
    const warning = buildSubmission("coverage", params({ referenceRunId: "run_ref", queryRunId: "run_q" }), context());
    expect(warning).toEqual({ kind: "warning", message: "Select compatible reference and query runs" });
    expect(buildSubmission("sampling", params({ samplingAlgorithm: "novelty_fps" }), context())).toEqual(warning);
  });

  it("compares a run pair, with Mantel bringing its own sampling budget", () => {
    expect(run("compare", {}, { secondRun: "run_b" })).toEqual({
      kind: "run", method: "analysis.compare", label: "Compare",
      params: { left_run_id: "run_a", right_run_id: "run_b", mode: "structure" },
    });
    expect(run("compare", { compareMode: "mantel", mantelMethod: "spearman", mantelPermutations: 499 }, { secondRun: "run_b" }).params).toMatchObject({
      method: "spearman", permutations: 499, max_samples: 600,
    });
    expect(buildSubmission("compare", params(), context())).toEqual({ kind: "warning", message: "Select a descriptor run pair" });
  });

  it("keeps local diversity on atom rows and caps the neighbour read", () => {
    expect(run("local", { localCutoff: 4.5, nClusters: 3, k: 9 })).toMatchObject({
      method: "analysis.local_diversity", label: "Local diversity",
      params: { mode: "atom", n_clusters: 3, k: 9, cutoff: 4.5, max_neighbors: 128 },
    });
  });

  it("runs the overview modules with per-module payloads", () => {
    expect(run("overview", { overviewAnalysis: "feature_variance", nearZeroThreshold: 0.03, lowVariationThreshold: 0.06 }).params).toEqual({
      top_k: 20, near_zero_relative_threshold: 0.03, low_variance_relative_threshold: 0.06,
    });
    expect(run("overview", { overviewAnalysis: "feature_correlation" }).params).toEqual({
      top_k: 20, method: "spearman", correlation_threshold: 0.8,
    });
    expect(run("overview", { overviewAnalysis: "effective_dimension", effectiveDimensionPreprocess: "center" as AnalysisParams["effectiveDimensionPreprocess"] }).params).toEqual({ preprocess: "center" });
    expect(run("overview", { overviewAnalysis: "property_correlation", propertySparsePercentile: 80, propertyOodPercentile: 95 }).params).toEqual({
      property: "energy_per_atom", folds: 5, top_k: 50, mode: "structure", reliability_k: 5,
      distance_metric: "euclidean", sparse_quantile: 0.8, ood_quantile: 0.95,
    });
    expect(run("overview", { overviewAnalysis: "perturbation_sensitivity", perturbationType: "strain", perturbationStructures: 12 }).params).toEqual({
      perturbation: "strain", n_amplitudes: 8, max_amplitude: 0.2, metric: "euclidean", max_structures: 12, preprocess: "standardized",
    });
    expect(run("overview", { overviewAnalysis: "trajectory", viewId: "view_1" }).params).toEqual({ view_id: "view_1" });
    expect(run("overview", { overviewAnalysis: "novelty" as AnalysisParams["overviewAnalysis"] }).labelFromModule).toBe(true);
  });

  it("takes drift's context from the reference run and sensitivity from both ids", () => {
    const drift = run("overview", { overviewAnalysis: "drift", referenceRunId: "run_ref", queryRunId: "run_q", viewId: "view_ignored" }, { crossInputsReady: true });
    expect(drift).toMatchObject({ method: "analysis.drift", anchoredToReference: true });
    // drift carries its own two views, so the single-run scope does not apply
    expect(drift.params).not.toHaveProperty("view_id");
    expect(run("overview", { overviewAnalysis: "sensitivity" }, { secondRun: "run_b", referenceDescriptor: "SOAP", queryDescriptor: "SOAP" }).params).toEqual({
      run_ids: ["run_a", "run_b"],
    });
  });

  it("routes mismatched descriptors away from sensitivity", () => {
    expect(buildSubmission("overview", params({ overviewAnalysis: "sensitivity" }), context({ secondRun: "run_b", referenceDescriptor: "SOAP", queryDescriptor: "ACE" }))).toEqual({
      kind: "warning", message: "Parameter sensitivity requires the same descriptor; use Compare for different descriptors",
    });
    expect(buildSubmission("overview", params({ overviewAnalysis: "sensitivity" }), context())).toEqual({
      kind: "warning", message: "Select a reference/query run pair",
    });
  });

  it("spends no view suffix where the module addresses runs directly", () => {
    expect(run("compare", { viewId: "view_1" }, { secondRun: "run_b" }).params).not.toHaveProperty("view_id");
    expect(run("overview", { overviewAnalysis: "sensitivity", viewId: "view_1" }, { secondRun: "run_b", referenceDescriptor: "SOAP", queryDescriptor: "SOAP" }).params).not.toHaveProperty("view_id");
  });
});

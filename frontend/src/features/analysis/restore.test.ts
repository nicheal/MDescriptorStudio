// Reopening a stored analysis must reproduce the controls it was computed with,
// including the parameter names and clamping rules that predate the current UI.
import { describe, expect, it } from "vitest";
import { restoreAnalysisParams, type RestoreInput } from "./restore";
import type { AnalysisParams } from "./types";

const current: AnalysisParams = {
  projection: "pca", mode: "structure", preprocess: "center",
  effectiveDimensionPreprocess: "center", tsnePerplexity: 30,
  similarityMode: "query", k: 10, queryIndex: 0,
  clusterAlgorithm: "kmeans", nClusters: 6, outlierAlgorithm: "lof", contamination: 0.01,
  samplingAlgorithm: "fps", nSamples: 1000, uncertaintyK: 8, samplingStrategy: "global",
  samplingScaling: "robust", samplingMinDistance: 0, samplingExistingRunId: null, samplingBlocks: [],
  samplingBudgetMode: "count", samplingCoverage: 95, coverageMode: "coverage",
  compareMode: "geometry", mantelMethod: "pearson", mantelPermutations: 999,
  localCutoff: 3, kernelName: "rbf", overviewAnalysis: "feature_variance",
  propertyName: "energy_per_atom", propertyFolds: 5, propertyReliabilityK: 5,
  propertyDistanceMetric: "euclidean", propertySparsePercentile: 90, propertyOodPercentile: 99,
  perturbationType: "jitter", perturbationCount: 8, perturbationMaximum: 0.2,
  perturbationStructures: 64, perturbationMetric: "euclidean",
  nearZeroThreshold: 0.02, lowVariationThreshold: 0.05,
  featureCorrelationMethod: "spearman", featureCorrelationThreshold: 0.8,
  referenceRunId: null, queryRunId: null, referenceViewId: null, queryViewId: null, viewId: null,
};

const restore = (tab: RestoreInput["tab"], analysisType: string, parameters: Record<string, unknown> = {}) =>
  restoreAnalysisParams({ tab, analysisType, parameters, current });

describe("restoring controls from a stored analysis row", () => {
  it("keeps the current value for anything the row does not carry", () => {
    expect(restore("clusters", "kmeans", {})).toEqual({
      clusterAlgorithm: "kmeans", nClusters: 6, mode: "structure",
    });
    expect(restore("projection", "pca", {})).toMatchObject({ preprocess: "center", tsnePerplexity: 30 });
    // Rows written before the control existed ran on each algorithm's own
    // default: umap and tsne take the matrix raw, pca centres it.
    expect(restore("projection", "umap", {})).toMatchObject({ preprocess: "raw" });
    expect(restore("projection", "tsne", {})).toMatchObject({ preprocess: "raw" });
    expect(restore("projection", "umap", { preprocess: "standardized" })).toMatchObject({ preprocess: "standardized" });
    expect(restore("overview", "unknown_module", {})).toEqual({});
  });

  it("restores the granularity a drift row was measured on", () => {
    // Drift is the cross-dataset module whose own controls include which matrix
    // it compares, and its identity key moves with `mode`: restoring a row
    // without it left the panel showing the other matrix's points under a
    // reference run that had been compared on this one.
    expect(restore("overview", "drift", { mode: "atom" })).toEqual({ mode: "atom" });
    expect(restore("overview", "drift", {})).toEqual({ mode: "structure" });
    expect(restore("overview", "drift", { mode: "electrons" })).toEqual({ mode: "structure" });
  });

  it("maps the algorithm names older rows wrote", () => {
    expect(restore("clusters", "cluster", { algorithm: "hierarchical" })).toMatchObject({ clusterAlgorithm: "agglomerative" });
    expect(restore("outliers", "outlier", { algorithm: "iForest" })).toMatchObject({ outlierAlgorithm: "isolation_forest" });
    expect(restore("outliers", "outlier", { algorithm: "mahalanobis_distance" })).toMatchObject({ outlierAlgorithm: "mahalanobis" });
    expect(restore("sampling", "sampling", { algorithm: "Cluster" })).toMatchObject({ samplingAlgorithm: "cluster_representative" });
    expect(restore("sampling", "sampling", { algorithm: "element" })).toMatchObject({ samplingAlgorithm: "per_element" });
    // an algorithm the controls cannot express leaves the current one alone
    expect(restore("kernel", "kernel", { kernel: "laplacian" })).toMatchObject({ kernelName: "rbf" });
  });

  it("clamps every restored number into the range its control allows", () => {
    expect(restore("outliers", "outlier", { contamination: 9, k: 0.4 }).contamination).toBe(0.5);
    expect(restore("outliers", "outlier", { contamination: 0 }).contamination).toBe(0.001);
    expect(restore("outliers", "outlier", { contamination: 9 }).contamination).toBe(0.5);
    expect(restore("local", "local_diversity", { cutoff: 99, k: 2.6 })).toEqual({ mode: "atom", k: 3, nClusters: 6, localCutoff: 10 });
    expect(restore("overview", "feature_variance", { near_zero_relative_threshold: -1 }).nearZeroThreshold).toBe(0);
  });

  it("keeps the two feature-variance thresholds ordered", () => {
    // A row whose low variation sits under its near-zero threshold cannot be
    // restored into a control pair the page would refuse to compute with.
    expect(restore("overview", "feature_variance", {
      near_zero_relative_threshold: 0.4, low_variance_relative_threshold: 0.1,
    })).toEqual({ nearZeroThreshold: 0.4, lowVariationThreshold: 0.4 });
    expect(restore("overview", "feature_variance", { low_variance_relative_threshold: 0.3 }))
      .toEqual({ nearZeroThreshold: 0.02, lowVariationThreshold: 0.3 });
  });

  it("restores similarity variants, including the pre-rename neighbour count", () => {
    expect(restore("similarity", "neighbors", {})).toMatchObject({ similarityMode: "all_neighbors" });
    expect(restore("similarity", "pairwise_similarity", {})).toMatchObject({ similarityMode: "pairwise" });
    expect(restore("similarity", "similarity", { similarity_mode: "pairwise" })).toMatchObject({ similarityMode: "pairwise" });
    expect(restore("similarity", "similarity", { n_neighbors: 7 })).toMatchObject({ k: 7 });
  });

  it("only restores the FPS knobs for an FPS row", () => {
    const fps = restore("sampling", "sampling", {
      algorithm: "fps", strategy: "grouped", scaling: "raw", blocks: ["energy", 7],
      existing_run_id: "run_prev", min_distance: 0.25, target_coverage: 0.8,
    });
    expect(fps).toMatchObject({
      samplingStrategy: "grouped", samplingScaling: "raw", samplingBlocks: ["energy", "7"],
      samplingExistingRunId: "run_prev", samplingMinDistance: 0.25,
      samplingBudgetMode: "coverage", samplingCoverage: 80,
    });
    expect(restore("sampling", "sampling", { algorithm: "random", target_coverage: 0.8 }))
      .not.toHaveProperty("samplingBudgetMode");
    expect(restore("sampling", "acquisition", { acquisition_method: "uncertainty_diversity" }))
      .toMatchObject({ samplingAlgorithm: "uncertainty_diversity" });
    expect(restore("sampling", "acquisition", {})).toMatchObject({ samplingAlgorithm: "novelty_fps" });
  });

  it("reads the coverage/overlap choice from the module, never the row", () => {
    expect(restore("coverage", "overlap", { mode: "atom" })).toEqual({ mode: "atom" });
  });

  it("restores the overview modules from their own parameter spellings", () => {
    expect(restore("overview", "property_correlation", {})).toMatchObject({
      propertyName: "energy_per_atom", propertyFolds: 5, propertyReliabilityK: 5,
      propertySparsePercentile: 90, propertyOodPercentile: 99, mode: "structure",
    });
    expect(restore("overview", "property_correlation", {
      property: "force_max", folds: 3.6, reliability_k: 0, sparse_quantile: 0.75, ood_quantile: 0.9, mode: "atom",
    })).toMatchObject({ propertyName: "force_max", propertyFolds: 4, propertyReliabilityK: 1, propertySparsePercentile: 75, propertyOodPercentile: 90, mode: "atom" });
    expect(restore("overview", "feature_correlation", { method: "spearman", redundancy_threshold: 2 })).toMatchObject({
      featureCorrelationMethod: "spearman", featureCorrelationThreshold: 1,
    });
    expect(restore("overview", "effective_dimension", {})).toEqual({ effectiveDimensionPreprocess: "center" });
    expect(restore("overview", "perturbation_sensitivity", { perturbation: "strain", n_amplitudes: 1 })).toMatchObject({
      perturbationType: "strain", perturbationCount: 2, perturbationMaximum: 0.2, perturbationStructures: 64,
    });
  });

  it("keeps the Mantel variant and its permutation budget", () => {
    expect(restore("compare", "mantel", { permutations: 199.4 })).toMatchObject({
      compareMode: "mantel", mantelPermutations: 199,
    });
    expect(restore("compare", "compare", { compare_mode: "mantel", method: "spearman" })).toMatchObject({
      compareMode: "mantel", mantelMethod: "spearman",
    });
  });
});

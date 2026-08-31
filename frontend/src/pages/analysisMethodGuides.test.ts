import { describe, expect, it } from "vitest";
import { ANALYSIS_METHOD_GUIDES, getAnalysisMethodGuide } from "./analysisMethodGuides";

describe("Analysis method guides", () => {
  it("provides theory and applications for every selectable analysis method", () => {
    const selectableKeys = [
      "projection.pca",
      "projection.umap",
      "projection.tsne",
      "similarity.query",
      "similarity.all_neighbors",
      "similarity.pairwise",
      "cluster.kmeans",
      "cluster.dbscan",
      "cluster.hdbscan",
      "cluster.agglomerative",
      "outlier.lof",
      "outlier.knn",
      "outlier.isolation_forest",
      "outlier.mahalanobis",
      "sampling.fps",
      "sampling.novelty_fps",
      "sampling.uncertainty_diversity",
      "sampling.random",
      "sampling.stratified",
      "sampling.cluster_representative",
      "sampling.per_element",
      "coverage.coverage",
      "coverage.overlap",
      "compare.geometry",
      "compare.mantel",
      "local.local_diversity",
      "kernel.rbf",
      "kernel.linear",
      "kernel.cosine",
      "kernel.polynomial",
      "overview.feature_variance",
      "overview.feature_correlation",
      "overview.effective_dimension",
      "overview.property_correlation",
      "overview.trajectory",
      "overview.drift",
      "overview.sensitivity",
      "overview.perturbation_sensitivity",
    ];

    for (const key of selectableKeys) {
      const guide = getAnalysisMethodGuide(key);
      expect(guide.title.en).toBeTruthy();
      expect(guide.title.zh).toBeTruthy();
      expect(guide.theory.en).toBeTruthy();
      expect(guide.theory.zh).toBeTruthy();
      expect(guide.application.en).toBeTruthy();
      expect(guide.application.zh).toBeTruthy();
      expect(ANALYSIS_METHOD_GUIDES[key]).toBe(guide);
    }
  });

  it("falls back to a documented guide for an unknown method key", () => {
    expect(getAnalysisMethodGuide("unknown.method")).toBe(ANALYSIS_METHOD_GUIDES["overview.feature_variance"]);
  });
});

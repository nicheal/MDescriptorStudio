// Turning a stored analysis row back into the controls that produced it.
//
// The backend keeps each analysis's own parameters, so reopening a result has to
// derive every control value from `row.parameters` — including the legacy names
// and the clamping the controls themselves apply. It lives here, away from the
// page, so those rules are one tested function instead of a branch inside a
// render body.
import type { AnalysisParams, TabKey } from "./types";
import type { PcaMode } from "../../stores/workspace";

const finite = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const text = (value: unknown): string => (typeof value === "string" ? value : "");

const mode = (value: unknown): PcaMode => (value === "atom" ? "atom" : "structure");

const intAt = (value: unknown, fallback: number, minimum: number): number =>
  Math.max(minimum, Math.round(finite(value) ?? fallback));

/** Names the row may still carry from before a control was renamed. */
const ONE_OF = <T extends string>(candidates: readonly T[], value: unknown, fallback: T): T =>
  candidates.includes(value as T) ? (value as T) : fallback;

export interface RestoreInput {
  tab: TabKey;
  /** Lower-cased `analysis_type` of the stored row. */
  analysisType: string;
  parameters: Record<string, unknown>;
  /** The page's current values: a parameter the row does not carry keeps them. */
  current: AnalysisParams;
}

/**
 * The parameters this row was computed with. Only keys the row can speak to are
 * returned, and callers apply them to both the controls and the run context, so
 * the two cannot drift apart.
 */
export function restoreAnalysisParams(input: RestoreInput): Partial<AnalysisParams> {
  const { tab, analysisType, parameters: p, current } = input;
  switch (tab) {
    case "overview":
      return restoreOverview(analysisType, p, current);

    case "projection": {
      const projection = ONE_OF(["pca", "umap", "tsne"] as const, analysisType, "pca");
      return {
        projection,
        mode: mode(p.mode),
        // The fallback has to be what the stored run actually used: umap and
        // tsne take the matrix raw unless told otherwise, pca centres it. One
        // shared "center" fallback labelled an old umap plot with a
        // preprocessing step it never had.
        preprocess: ONE_OF(["raw", "center", "standardized"] as const, p.preprocess, projection === "pca" ? "center" : "raw"),
        tsnePerplexity: intAt(p.perplexity, current.tsnePerplexity, 2),
      };
    }

    case "similarity": {
      const requested = text(p.similarity_mode);
      return {
        similarityMode: analysisType === "neighbors" || requested === "all_neighbors"
          ? "all_neighbors"
          : analysisType === "pairwise" || analysisType === "pairwise_similarity" || requested === "pairwise"
            ? "pairwise"
            : "query",
        mode: mode(p.mode),
        k: intAt(p.k ?? p.n_neighbors, current.k, 1),
        queryIndex: intAt(p.query_index, current.queryIndex, 0),
      };
    }

    case "clusters":
      return {
        clusterAlgorithm: restoreClusterAlgorithm((text(p.algorithm) || analysisType).toLowerCase(), current.clusterAlgorithm),
        nClusters: intAt(p.n_clusters ?? p.nClusters, current.nClusters, 2),
        mode: mode(p.mode),
      };

    case "outliers":
      return {
        outlierAlgorithm: restoreOutlierAlgorithm((text(p.algorithm) || analysisType).toLowerCase(), current.outlierAlgorithm),
        k: intAt(p.k, current.k, 1),
        contamination: Math.min(0.5, Math.max(0.001, finite(p.contamination) ?? current.contamination)),
        mode: mode(p.mode),
      };

    case "sampling": {
      const algorithm = restoreSamplingAlgorithm(analysisType, p, current.samplingAlgorithm);
      const restored: Partial<AnalysisParams> = {
        samplingAlgorithm: algorithm,
        nSamples: intAt(p.n_samples, current.nSamples, 1),
        mode: mode(p.mode),
        uncertaintyK: intAt(p.uncertainty_k, current.uncertaintyK, 2),
      };
      if (algorithm !== "fps") return restored;
      // A stored target coverage is a budget mode as much as a number is.
      const coverage = finite(p.target_coverage);
      return {
        ...restored,
        samplingStrategy: p.strategy === "grouped" ? "grouped" : "global",
        samplingScaling: ONE_OF(["robust", "standardized", "raw"] as const, p.scaling, "robust"),
        samplingMinDistance: Math.max(0, finite(p.min_distance) ?? 0),
        samplingExistingRunId: text(p.existing_run_id) || null,
        samplingBlocks: Array.isArray(p.blocks) ? p.blocks.map(String) : [],
        samplingBudgetMode: coverage != null && coverage > 0 ? "coverage" : "count",
        samplingCoverage: coverage != null && coverage > 0 ? Math.round(coverage * 100) : 95,
      };
    }

    case "coverage":
      // The module target owns coverage/overlap, so the row cannot restore it.
      return { mode: mode(p.mode) };

    case "compare":
      return {
        compareMode: analysisType === "mantel" || p.compare_mode === "mantel" ? "mantel" : "geometry",
        mode: mode(p.mode),
        mantelMethod: p.method === "spearman" || p.mantel_method === "spearman" ? "spearman" : "pearson",
        mantelPermutations: intAt(p.permutations ?? p.mantel_permutations, current.mantelPermutations, 1),
      };

    case "local":
      return {
        mode: "atom",
        k: intAt(p.k, current.k, 1),
        nClusters: intAt(p.n_clusters, current.nClusters, 2),
        localCutoff: Math.max(0.1, Math.min(10, finite(p.cutoff) ?? current.localCutoff)),
      };

    case "kernel":
      return {
        kernelName: ONE_OF(["rbf", "linear", "cosine", "polynomial"] as const, text(p.kernel).toLowerCase() || text(p.kernel_name).toLowerCase(), current.kernelName),
        mode: mode(p.mode),
      };
  }
  return {};
}

function restoreOverview(analysisType: string, p: Record<string, unknown>, current: AnalysisParams): Partial<AnalysisParams> {
  switch (analysisType) {
    case "feature_variance": {
      const near = finite(p.near_zero_relative_threshold);
      const low = finite(p.low_variance_relative_threshold);
      const nearZero = near == null ? current.nearZeroThreshold : Math.min(1, Math.max(0, near));
      // The two thresholds are ordered against each other by the controls.
      const lowVariation = low == null
        ? Math.max(nearZero, current.lowVariationThreshold)
        : Math.min(1, Math.max(nearZero, low));
      return { nearZeroThreshold: nearZero, lowVariationThreshold: lowVariation };
    }
    case "feature_correlation": {
      const threshold = finite(p.correlation_threshold ?? p.redundancy_threshold);
      return {
        featureCorrelationMethod: ONE_OF(["pearson", "spearman"] as const, p.method, current.featureCorrelationMethod),
        featureCorrelationThreshold: threshold == null
          ? current.featureCorrelationThreshold
          : Math.min(1, Math.max(0, threshold)),
      };
    }
    case "effective_dimension":
      // Rows written before the preprocessing control existed used centred
      // data; restoring them must not claim a scale they never computed with.
      return { effectiveDimensionPreprocess: p.preprocess === "standardized" ? "standardized" : "center" };
    case "property_correlation":
      return {
        propertyName: String(p.property ?? "energy_per_atom"),
        propertyFolds: intAt(p.folds, 5, 2),
        propertyReliabilityK: intAt(p.reliability_k, 5, 1),
        propertyDistanceMetric: p.distance_metric === "cosine" ? "cosine" : "euclidean",
        propertySparsePercentile: Math.round((finite(p.sparse_quantile) ?? 0.9) * 100),
        propertyOodPercentile: Math.round((finite(p.ood_quantile) ?? 0.99) * 100),
        mode: mode(p.mode),
      };
    case "perturbation_sensitivity":
      return {
        perturbationType: p.perturbation === "strain" ? "strain" : "jitter",
        perturbationCount: intAt(p.n_amplitudes, 8, 2),
        perturbationMaximum: Math.max(0.001, finite(p.max_amplitude) ?? 0.2),
        perturbationStructures: intAt(p.max_structures, 64, 1),
        perturbationMetric: text(p.metric) || "euclidean",
      };
    case "drift":
      // The reference/query pair is owned by the module target and a row cannot
      // restore it, but `mode` decides which matrix the two runs are compared
      // on, the panel shows it as Granularity, and the identity key carries it.
      return { mode: mode(p.mode) };
    default:
      return {};
  }
}

function restoreClusterAlgorithm(requested: string, fallback: string): string {
  if (requested === "hierarchical") return "agglomerative";
  return ONE_OF(["kmeans", "dbscan", "hdbscan", "agglomerative"] as const, requested, fallback);
}

function restoreOutlierAlgorithm(requested: string, fallback: string): string {
  if (requested === "isolation-forest" || requested === "iforest") return "isolation_forest";
  if (requested === "mahalanobis_distance") return "mahalanobis";
  return ONE_OF(["lof", "knn", "isolation_forest", "mahalanobis"] as const, requested, fallback);
}

function restoreSamplingAlgorithm(analysisType: string, p: Record<string, unknown>, fallback: string): string {
  if (analysisType === "acquisition") {
    return p.acquisition_method === "uncertainty_diversity" ? "uncertainty_diversity" : "novelty_fps";
  }
  const requested = (text(p.algorithm) || analysisType).toLowerCase();
  if (requested === "cluster") return "cluster_representative";
  if (requested === "element") return "per_element";
  return ONE_OF(
    ["fps", "novelty_fps", "uncertainty_diversity", "random", "stratified", "cluster_representative", "per_element"] as const,
    requested,
    fallback,
  );
}


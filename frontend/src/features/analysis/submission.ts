// What a Run button actually submits. The page renders the controls; this owns
// the method name, the parameter payload and the precondition that refuses a
// run — so those three stay one fact in one place instead of a branch inside a
// 1600-line component.
import type { AnalysisParams, TabKey } from "./types";
import { canonicalClusterAlgorithm, canonicalOutlierAlgorithm, canonicalSamplingAlgorithm } from "./identity";

export type Submission =
  | {
      kind: "run";
      method: string;
      params: Record<string, unknown>;
      /** English UI string, translated by the caller. */
      label: string;
      /** True when the label comes from the navigation registry instead. */
      labelFromModule?: boolean;
      /** Cross-dataset modules run against the reference, not the active run. */
      anchoredToReference?: boolean;
    }
  | { kind: "warning"; message: string }
  | { kind: "projection" };

export interface SubmissionContext {
  selectedRun: string | null;
  /** The compare/sensitivity partner of the active run. */
  secondRun: string | null;
  /** Reference and query runs both picked and feature-space compatible. */
  crossInputsReady: boolean;
  referenceDescriptor: string | null;
  queryDescriptor: string | null;
}

const viewSuffix = (viewId: string | null) => (viewId ? { view_id: viewId } : {});

const crossSuffix = (p: AnalysisParams) => ({
  reference_run_id: p.referenceRunId,
  query_run_id: p.queryRunId,
  ...(p.referenceViewId ? { reference_view_id: p.referenceViewId } : {}),
  ...(p.queryViewId ? { query_view_id: p.queryViewId } : {}),
});

export function buildSubmission(tab: TabKey, p: AnalysisParams, ctx: SubmissionContext): Submission {
  switch (tab) {
    case "projection":
      return { kind: "projection" };

    case "similarity": {
      if (p.similarityMode === "pairwise") {
        // Pairwise correlates every stored pair, so it takes a sample budget
        // rather than a neighbourhood size.
        return { kind: "run", method: "analysis.pairwise", label: "Pairwise similarity", params: { metric: "cosine", preprocess: "raw", mode: p.mode, max_samples: 400, ...viewSuffix(p.viewId) } };
      }
      // Only analysis.similarity reads query_index; the neighbour graph scores
      // every row, so sending it there would make an unrelated query change look
      // like a different analysis with no control on screen to move it back.
      const params = { ...(p.similarityMode === "query" ? { query_index: p.queryIndex } : {}), k: p.k, metric: "cosine", preprocess: "raw", mode: p.mode, ...viewSuffix(p.viewId) };
      if (p.similarityMode === "all_neighbors") return { kind: "run", method: "analysis.neighbors", label: "Neighbor graph", params };
      return { kind: "run", method: "analysis.similarity", label: "Similarity", params };
    }

    case "clusters": {
      const algorithm = canonicalClusterAlgorithm(p.clusterAlgorithm);
      return { kind: "run", method: "analysis.cluster", label: algorithm.toUpperCase(), params: { algorithm, n_clusters: p.nClusters, preprocess: "standardized", mode: p.mode, ...viewSuffix(p.viewId) } };
    }

    case "outliers": {
      const algorithm = canonicalOutlierAlgorithm(p.outlierAlgorithm);
      // LOF and k-NN score against k neighbours; isolation forest and
      // Mahalanobis do not read it at all, so sending it there would make an
      // unrelated k change look like a different analysis.
      const usesK = algorithm === "lof" || algorithm === "knn";
      return { kind: "run", method: "analysis.outlier", label: algorithm.toUpperCase(), params: { algorithm, ...(usesK ? { k: p.k } : {}), contamination: p.contamination, preprocess: "standardized", mode: p.mode, ...viewSuffix(p.viewId) } };
    }

    case "sampling": {
      const samplingAlgorithm = canonicalSamplingAlgorithm(p.samplingAlgorithm);
      const samplingMode = samplingAlgorithm === "per_element" ? "atom" : p.mode;
      if (samplingAlgorithm === "novelty_fps" || samplingAlgorithm === "uncertainty_diversity") {
        if (!ctx.crossInputsReady || !p.referenceRunId || !p.queryRunId) return { kind: "warning", message: "Select compatible reference and query runs" };
        const uncertainty = samplingAlgorithm === "uncertainty_diversity";
        return {
          kind: "run",
          method: "analysis.acquisition",
          label: uncertainty ? "Uncertainty acquisition" : "Novelty acquisition",
          anchoredToReference: true,
          params: {
            ...crossSuffix(p),
            n_samples: p.nSamples,
            mode: p.mode,
            acquisition_method: p.samplingAlgorithm,
            novelty_weight: 0.65,
            uncertainty_weight: 0.65,
            // Only the uncertainty branch reads k; a novelty run that stored one
            // would claim a parameter it never used.
            ...(uncertainty ? { uncertainty_k: p.uncertaintyK } : {}),
          },
        };
      }
      const fpsParams = samplingAlgorithm === "fps"
        ? {
            strategy: p.samplingStrategy,
            min_distance: p.samplingMinDistance,
            ...(p.samplingExistingRunId ? { existing_run_id: p.samplingExistingRunId } : {}),
            ...(p.samplingBlocks.length ? { blocks: p.samplingBlocks } : {}),
            ...(p.samplingBudgetMode === "coverage" ? { target_coverage: p.samplingCoverage / 100 } : {}),
          }
        : {};
      // Only the two algorithms that measure a distance run in a scaled space,
      // so only they send the `scaling` they actually applied.
      const distanceBased = samplingAlgorithm === "fps" || samplingAlgorithm === "cluster_representative";
      return { kind: "run", method: "analysis.sampling", label: "Sampling", params: { algorithm: samplingAlgorithm, n_samples: p.nSamples, mode: samplingMode, ...(samplingAlgorithm === "stratified" ? { stratification_source: p.samplingStratificationSource ?? "composition" } : {}), ...(distanceBased ? { scaling: p.samplingScaling } : {}), ...fpsParams, ...viewSuffix(p.viewId) } };
    }

    case "coverage":
      if (!ctx.crossInputsReady || !p.referenceRunId || !p.queryRunId) return { kind: "warning", message: "Select compatible reference and query runs" };
      return {
        kind: "run",
        method: `analysis.${p.coverageMode}`,
        label: p.coverageMode === "coverage" ? "Coverage" : "Overlap",
        anchoredToReference: true,
        params: { ...crossSuffix(p), metric: "euclidean", mode: p.mode },
      };

    case "compare": {
      if (!ctx.secondRun || !ctx.selectedRun) return { kind: "warning", message: "Select a descriptor run pair" };
      if (p.compareMode === "mantel") {
        return {
          kind: "run",
          method: "analysis.mantel",
          label: "Mantel test",
          params: { left_run_id: ctx.selectedRun, right_run_id: ctx.secondRun, mode: p.mode, method: p.mantelMethod, permutations: p.mantelPermutations, max_samples: 600 },
        };
      }
      return { kind: "run", method: "analysis.compare", label: "Compare", params: { left_run_id: ctx.selectedRun, right_run_id: ctx.secondRun, mode: p.mode } };
    }

    case "local":
      // Local diversity is defined on the local-environment rows, so it always
      // runs in atom mode regardless of the scope selector.
      return { kind: "run", method: "analysis.local_diversity", label: "Local diversity", params: { mode: "atom", n_clusters: p.nClusters, k: p.k, cutoff: p.localCutoff, max_neighbors: 128, ...viewSuffix(p.viewId) } };

    case "kernel":
      return { kind: "run", method: "analysis.kernel", label: "Kernel diagnostics", params: { kernel: p.kernelName, mode: p.mode, max_samples: 400, ...viewSuffix(p.viewId) } };

    case "overview": {
      if (p.overviewAnalysis === "drift" && (!ctx.crossInputsReady || !p.referenceRunId || !p.queryRunId)) {
        return { kind: "warning", message: "Select compatible reference and query runs" };
      }
      if (p.overviewAnalysis === "sensitivity") {
        if (!ctx.secondRun || !ctx.selectedRun) return { kind: "warning", message: "Select a reference/query run pair" };
        if (!ctx.referenceDescriptor || ctx.referenceDescriptor !== ctx.queryDescriptor) {
          return { kind: "warning", message: "Parameter sensitivity requires the same descriptor; use Compare for different descriptors" };
        }
      }
      const params: Record<string, unknown> = p.overviewAnalysis === "drift"
        ? { ...crossSuffix(p), metric: "euclidean", mode: p.mode }
        : p.overviewAnalysis === "sensitivity"
          ? { run_ids: [ctx.selectedRun, ctx.secondRun] }
          : p.overviewAnalysis === "perturbation_sensitivity"
            ? { perturbation: p.perturbationType, n_amplitudes: p.perturbationCount, max_amplitude: p.perturbationMaximum, metric: p.perturbationMetric, max_structures: p.perturbationStructures, preprocess: "standardized" }
            : p.overviewAnalysis === "trajectory"
              ? {}
            : p.overviewAnalysis === "property_correlation"
              ? { property: p.propertyName, folds: p.propertyFolds, top_k: 50, mode: p.mode, reliability_k: p.propertyReliabilityK, distance_metric: p.propertyDistanceMetric, sparse_quantile: p.propertySparsePercentile / 100, ood_quantile: p.propertyOodPercentile / 100 }
              : p.overviewAnalysis === "feature_correlation"
                ? { top_k: 20, method: p.featureCorrelationMethod, correlation_threshold: p.featureCorrelationThreshold }
                : p.overviewAnalysis === "effective_dimension"
                  ? { preprocess: p.effectiveDimensionPreprocess }
                  : p.overviewAnalysis === "feature_variance"
                    ? { top_k: 20, near_zero_relative_threshold: p.nearZeroThreshold, low_variance_relative_threshold: p.lowVariationThreshold }
                    : { top_k: 20 };
      // drift carries its own reference/query views; sensitivity addresses two
      // runs and has no single-run scope to slice.
      if (p.viewId && p.overviewAnalysis !== "drift" && p.overviewAnalysis !== "sensitivity") params.view_id = p.viewId;
      return {
        kind: "run",
        method: `analysis.${p.overviewAnalysis}`,
        label: p.overviewAnalysis,
        labelFromModule: true,
        anchoredToReference: p.overviewAnalysis === "drift" || undefined,
        params,
      };
    }
  }
}

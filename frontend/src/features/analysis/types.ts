import type { Pair } from "../../i18n";
import type { PcaMode } from "../../stores/workspace";

export type TabKey =
  | "overview"
  | "projection"
  | "similarity"
  | "clusters"
  | "outliers"
  | "sampling"
  | "coverage"
  | "compare"
  | "local"
  | "kernel";
export type ProjectionName = "pca" | "umap" | "tsne";
export type OverviewAnalysis =
  | "feature_variance"
  | "feature_correlation"
  | "effective_dimension"
  | "property_correlation"
  | "trajectory"
  | "drift"
  | "sensitivity"
  | "perturbation_sensitivity";
export type ColorBy = "none" | "energy" | "force_max" | "volume";
export type FeatureCorrelationMethod = "pearson" | "spearman";
export type EffectiveDimensionPreprocess = "center" | "standardized";
export type CoverageMode = "coverage" | "overlap";

export type AnalysisGroupKey =
  | "structure_environments"
  | "property_information"
  | "evolution_response"
  | "coverage_novelty"
  | "representation_quality"
  | "dataset_sampling";

export type AnalysisModuleKey =
  | "descriptor_space"
  | "similarity"
  | "structural_clusters"
  | "local_environment"
  | "property_information"
  | "descriptor_trajectory"
  | "structural_perturbation_response"
  | "data_coverage"
  | "train_test_overlap"
  | "dataset_drift"
  | "outlier_environments"
  | "feature_variance"
  | "feature_correlation"
  | "effective_dimension"
  | "kernel_analysis"
  | "parameter_sensitivity"
  | "descriptor_comparison"
  | "representative_sampling";

export interface AnalysisNavTarget {
  tab: TabKey;
  overviewAnalysis?: OverviewAnalysis;
  coverageMode?: CoverageMode;
}

export interface AnalysisNavModule {
  key: AnalysisModuleKey;
  label: Pair;
  target: AnalysisNavTarget;
}

export interface AnalysisNavGroup {
  key: AnalysisGroupKey;
  label: Pair;
  modules: readonly AnalysisNavModule[];
}

export interface AnalysisView {
  tab: TabKey;
  projection: ProjectionName;
  overviewAnalysis: OverviewAnalysis;
  coverageMode: CoverageMode;
  mode: PcaMode;
  preprocess: string;
  effectiveDimensionPreprocess: EffectiveDimensionPreprocess;
  colorBy: ColorBy;
  nearZeroThreshold: number;
  lowVariationThreshold: number;
  featureCorrelationMethod: FeatureCorrelationMethod;
  featureCorrelationThreshold: number;
}

export interface AnalysisParams {
  projection: ProjectionName;
  mode: PcaMode;
  preprocess: string;
  effectiveDimensionPreprocess: EffectiveDimensionPreprocess;
  tsnePerplexity: number;
  similarityMode: string;
  k: number;
  queryIndex: number;
  clusterAlgorithm: string;
  nClusters: number;
  outlierAlgorithm: string;
  contamination: number;
  samplingAlgorithm: string;
  nSamples: number;
  uncertaintyK: number;
  samplingStrategy: string;
  samplingScaling: string;
  samplingMinDistance: number;
  samplingExistingRunId: string | null;
  samplingBlocks: string[];
  samplingBudgetMode: string;
  samplingCoverage: number;
  coverageMode: CoverageMode;
  compareMode: string;
  mantelMethod: string;
  mantelPermutations: number;
  localCutoff: number;
  kernelName: string;
  overviewAnalysis: OverviewAnalysis;
  propertyName: string;
  propertyFolds: number;
  propertyReliabilityK: number;
  propertyDistanceMetric: string;
  propertySparsePercentile: number;
  propertyOodPercentile: number;
  perturbationType: string;
  perturbationCount: number;
  perturbationMaximum: number;
  perturbationStructures: number;
  perturbationMetric: string;
  nearZeroThreshold: number;
  lowVariationThreshold: number;
  featureCorrelationMethod: FeatureCorrelationMethod;
  featureCorrelationThreshold: number;
  referenceRunId: string | null;
  queryRunId: string | null;
  referenceViewId: string | null;
  queryViewId: string | null;
  viewId: string | null;
}

export interface AnalysisSlot {
  analysisId: string;
  moduleKey: AnalysisModuleKey;
  inputKey: string;
  parameterKey: string;
  updatedAt: number;
  seq: number;
}

export interface AnalysisSlotInput {
  analysisId: string;
  moduleKey: AnalysisModuleKey;
  inputKey: string;
  parameterKey: string;
}

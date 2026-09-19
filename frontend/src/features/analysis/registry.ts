import type { Pair } from "../../i18n";
import type {
  AnalysisModuleKey,
  AnalysisNavGroup,
  AnalysisNavModule,
  CoverageMode,
  OverviewAnalysis,
  TabKey,
} from "./types";

/** The single user-facing Analysis taxonomy and its legacy execution targets. */
export const ANALYSIS_NAV_GROUPS: readonly AnalysisNavGroup[] = [
  {
    key: "structure_environments",
    label: { en: "Structure & Environments", zh: "结构与环境" },
    modules: [
      { key: "descriptor_space", label: { en: "Descriptor Space", zh: "描述符空间" }, target: { tab: "projection" } },
      { key: "similarity", label: { en: "Similarity", zh: "相似性" }, target: { tab: "similarity" } },
      { key: "structural_clusters", label: { en: "Structural Clusters", zh: "结构族群" }, target: { tab: "clusters" } },
      { key: "local_environment", label: { en: "Local Environment", zh: "局部环境" }, target: { tab: "local" } },
    ],
  },
  {
    key: "property_information",
    label: { en: "Property Information", zh: "属性信息" },
    modules: [
      { key: "property_information", label: { en: "Property Information Analysis", zh: "属性信息分析" }, target: { tab: "overview", overviewAnalysis: "property_correlation" } },
    ],
  },
  {
    key: "evolution_response",
    label: { en: "Evolution & Response", zh: "演化与响应" },
    modules: [
      { key: "descriptor_trajectory", label: { en: "Descriptor Trajectory", zh: "描述符轨迹" }, target: { tab: "overview", overviewAnalysis: "trajectory" } },
      { key: "structural_perturbation_response", label: { en: "Structural Perturbation Response", zh: "结构扰动响应" }, target: { tab: "overview", overviewAnalysis: "perturbation_sensitivity" } },
    ],
  },
  {
    key: "coverage_novelty",
    label: { en: "Coverage & Novelty", zh: "覆盖与新颖性" },
    modules: [
      { key: "data_coverage", label: { en: "Data Coverage", zh: "数据覆盖度" }, target: { tab: "coverage", coverageMode: "coverage" } },
      { key: "train_test_overlap", label: { en: "Train / Test Overlap", zh: "训练/测试重叠" }, target: { tab: "coverage", coverageMode: "overlap" } },
      { key: "dataset_drift", label: { en: "Dataset Drift", zh: "数据集漂移" }, target: { tab: "overview", overviewAnalysis: "drift" } },
      { key: "outlier_environments", label: { en: "Outlier Environments", zh: "离群环境" }, target: { tab: "outliers" } },
    ],
  },
  {
    key: "representation_quality",
    label: { en: "Representation Quality", zh: "表示质量" },
    modules: [
      { key: "feature_variance", label: { en: "Feature Variance", zh: "特征方差" }, target: { tab: "overview", overviewAnalysis: "feature_variance" } },
      { key: "feature_correlation", label: { en: "Feature Correlation", zh: "特征相关性" }, target: { tab: "overview", overviewAnalysis: "feature_correlation" } },
      { key: "effective_dimension", label: { en: "Effective Dimension", zh: "有效维度" }, target: { tab: "overview", overviewAnalysis: "effective_dimension" } },
      { key: "kernel_analysis", label: { en: "Kernel Analysis", zh: "核分析" }, target: { tab: "kernel" } },
      { key: "parameter_sensitivity", label: { en: "Parameter Sensitivity", zh: "参数敏感性" }, target: { tab: "overview", overviewAnalysis: "sensitivity" } },
      { key: "descriptor_comparison", label: { en: "Descriptor Comparison", zh: "描述符对比" }, target: { tab: "compare" } },
    ],
  },
  {
    key: "dataset_sampling",
    label: { en: "Dataset Sampling", zh: "数据采样" },
    modules: [
      { key: "representative_sampling", label: { en: "Representative Sampling", zh: "代表性采样" }, target: { tab: "sampling" } },
    ],
  },
];

const ANALYSIS_NAV_MODULES = ANALYSIS_NAV_GROUPS.flatMap((group) => group.modules);

export function analysisNavModuleForKey(moduleKey: string): AnalysisNavModule | null {
  return ANALYSIS_NAV_MODULES.find((module) => module.key === moduleKey) ?? null;
}

export function analysisNavModuleForView(tab: TabKey, overviewAnalysis: OverviewAnalysis, coverageMode: CoverageMode): AnalysisNavModule | null {
  return ANALYSIS_NAV_MODULES.find(({ target }) =>
    target.tab === tab
      && (target.overviewAnalysis == null || target.overviewAnalysis === overviewAnalysis)
      && (target.coverageMode == null || target.coverageMode === coverageMode),
  ) ?? null;
}

const ANALYSIS_TYPE_ALIASES: Record<string, AnalysisModuleKey> = {
  projection: "descriptor_space", pca: "descriptor_space", umap: "descriptor_space", tsne: "descriptor_space",
  similarity: "similarity", neighbors: "similarity", pairwise: "similarity", pairwise_similarity: "similarity",
  cluster: "structural_clusters", clusters: "structural_clusters", kmeans: "structural_clusters", dbscan: "structural_clusters", hdbscan: "structural_clusters", agglomerative: "structural_clusters", hierarchical: "structural_clusters",
  local_diversity: "local_environment", property_correlation: "property_information", trajectory: "descriptor_trajectory", perturbation_sensitivity: "structural_perturbation_response",
  coverage: "data_coverage", overlap: "train_test_overlap", drift: "dataset_drift", outlier: "outlier_environments", outliers: "outlier_environments", lof: "outlier_environments", knn: "outlier_environments", isolation_forest: "outlier_environments", "isolation-forest": "outlier_environments", iforest: "outlier_environments", mahalanobis: "outlier_environments", mahalanobis_distance: "outlier_environments",
  feature_variance: "feature_variance", feature_correlation: "feature_correlation", effective_dimension: "effective_dimension", kernel: "kernel_analysis", sensitivity: "parameter_sensitivity", compare: "descriptor_comparison", mantel: "descriptor_comparison",
  fps: "representative_sampling", novelty_fps: "representative_sampling", uncertainty_diversity: "representative_sampling", random: "representative_sampling", stratified: "representative_sampling", cluster_representative: "representative_sampling", per_element: "representative_sampling", acquisition: "representative_sampling", sampling: "representative_sampling", element: "representative_sampling",
};

export function analysisNavModuleForAnalysisType(analysisType: string): AnalysisNavModule | null {
  const key = ANALYSIS_TYPE_ALIASES[analysisType.toLowerCase()];
  return key ? analysisNavModuleForKey(key) : null;
}

export const OVERVIEW_KIND_LABELS: Record<string, Pair> = {
  feature_variance: { en: "FEATURE VARIANCE", zh: "特征方差" },
  feature_correlation: { en: "FEATURE CORRELATION", zh: "特征相关性" },
  effective_dimension: { en: "EFFECTIVE DIMENSION", zh: "有效维度" },
  trajectory: { en: "TRAJECTORY", zh: "轨迹" },
  drift: { en: "DATASET DRIFT", zh: "数据集漂移" },
  sensitivity: { en: "PARAMETER SENSITIVITY", zh: "参数敏感性" },
  perturbation_sensitivity: { en: "STRUCTURAL PERTURBATION SENSITIVITY", zh: "结构扰动敏感性" },
};

export const SAMPLING_LABELS: Record<string, Pair> = {
  fps: { en: "fps", zh: "FPS 最远点采样" },
  novelty_fps: { en: "novelty fps", zh: "新颖性 FPS 采样" },
  uncertainty_diversity: { en: "uncertainty + diversity", zh: "不确定性 + 多样性" },
  random: { en: "random", zh: "随机采样" },
  stratified: { en: "stratified", zh: "分层采样" },
  cluster_representative: { en: "cluster representative", zh: "簇代表采样" },
  per_element: { en: "per element", zh: "按元素采样" },
};

export const ARTIFACT_ARRAYS: Record<string, string[]> = {
  pairwise_similarity: ["similarity_matrix", "distance_matrix", "sample_indices"],
  compare: ["left_coords", "right_coords", "left_pair_distances", "right_pair_distances", "neighbor_overlap", "sample_indices"],
  mantel: ["left_pair_distances", "right_pair_distances", "null_distribution", "sample_indices"],
  feature_correlation: ["correlation_matrix", "correlation_feature_indices"],
  effective_dimension: ["explained_variance"],
  property_correlation: ["sample_indices", "sample_frames", "sample_rows", "targets", "predictions", "residuals", "absolute_errors", "feature_indices", "pearson_correlations", "spearman_correlations", "mutual_information", "oof_distances", "reliability_bin_center", "reliability_bin_median", "reliability_bin_p90", "reliability_bin_p95"],
  coverage: ["projection_coords", "projection_source", "projection_sample_indices", "labels", "distances"],
  overlap: ["projection_coords", "projection_source", "projection_sample_indices", "labels", "distances"],
  drift: ["projection_coords", "projection_source", "projection_sample_indices", "labels", "distances"],
  trajectory: ["time", "frames", "sample_indices", "step_distance", "reference_distance", "cumulative_distance", "coords", "pc_explained_variance", "event_indices"],
  perturbation_sensitivity: ["amplitudes", "mean_response", "median_response", "p95_response", "max_response", "response_matrix", "sample_indices"],
  local_diversity: ["coords", "sample_indices", "labels", "scores", "cluster_labels", "elements", "coordination", "neighbor_offsets", "neighbor_indices", "neighbor_distances"],
  kernel: ["kernel_matrix", "eigenvalues", "sample_indices"],
  sampling: ["coverage_radius_curve", "coverage_mean_curve", "coverage_r2_curve"],
};

// Analysis page view state that must survive leaving the page and an app
// restart (ADR-17 persistence rule). The computed charts themselves live on
// the backend (analysis_runs table + artifacts, re-served by analysis.preview
// / result.get_pca); only the light "what was on screen" pointers live here:
// the UI parameters, and one slot per (tab, run, parameter combination) so
// every computed result can be re-displayed without recomputing.
import { create } from "zustand";
import { ipc } from "../ipc/client";
import type { Pair } from "../i18n";
import type { PcaMode } from "./workspace";

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

const VIEW_SETTINGS_KEY = "workspace.analysisUi";
const SLOTS_SETTINGS_KEY = "workspace.analysisSlots";
// The settings KV caps values at 4096 chars; ~200 chars per slot keeps 16 safe.
export const MAX_SLOTS = 16;

const TAB_KEYS: TabKey[] = [
  "overview",
  "projection",
  "similarity",
  "clusters",
  "outliers",
  "sampling",
  "coverage",
  "compare",
  "local",
  "kernel",
];
const PROJECTION_NAMES: ProjectionName[] = ["pca", "umap", "tsne"];
const OVERVIEW_ANALYSES: OverviewAnalysis[] = [
  "feature_variance",
  "feature_correlation",
  "effective_dimension",
  "property_correlation",
  "trajectory",
  "drift",
  "sensitivity",
  "perturbation_sensitivity",
];
const COLOR_BY: ColorBy[] = ["none", "energy", "force_max", "volume"];

/**
 * The user-facing Analysis hierarchy. Each module points at the existing
 * calculation tab and, where needed, its existing sub-mode. Keep these
 * targets stable: analysis slots still use the legacy TabKey cache identity.
 */
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

/** Find the module whose target matches the current legacy UI state. */
export function analysisNavModuleForView(
  tab: TabKey,
  overviewAnalysis: OverviewAnalysis,
  coverageMode: CoverageMode,
): AnalysisNavModule | null {
  return ANALYSIS_NAV_MODULES.find(({ target }) =>
    target.tab === tab
      && (target.overviewAnalysis == null || target.overviewAnalysis === overviewAnalysis)
      && (target.coverageMode == null || target.coverageMode === coverageMode),
  ) ?? null;
}

/** Map persisted/backend analysis names to their sole user-facing module. */
export function analysisNavModuleForAnalysisType(analysisType: string): AnalysisNavModule | null {
  const normalized = analysisType.toLowerCase();
  const aliases: Record<string, AnalysisModuleKey> = {
    projection: "descriptor_space",
    pca: "descriptor_space",
    umap: "descriptor_space",
    tsne: "descriptor_space",
    similarity: "similarity",
    neighbors: "similarity",
    pairwise: "similarity",
    pairwise_similarity: "similarity",
    cluster: "structural_clusters",
    clusters: "structural_clusters",
    kmeans: "structural_clusters",
    dbscan: "structural_clusters",
    hdbscan: "structural_clusters",
    agglomerative: "structural_clusters",
    hierarchical: "structural_clusters",
    local_diversity: "local_environment",
    property_correlation: "property_information",
    trajectory: "descriptor_trajectory",
    perturbation_sensitivity: "structural_perturbation_response",
    coverage: "data_coverage",
    overlap: "train_test_overlap",
    drift: "dataset_drift",
    outlier: "outlier_environments",
    outliers: "outlier_environments",
    lof: "outlier_environments",
    knn: "outlier_environments",
    isolation_forest: "outlier_environments",
    "isolation-forest": "outlier_environments",
    iforest: "outlier_environments",
    mahalanobis: "outlier_environments",
    mahalanobis_distance: "outlier_environments",
    feature_variance: "feature_variance",
    feature_correlation: "feature_correlation",
    effective_dimension: "effective_dimension",
    kernel: "kernel_analysis",
    sensitivity: "parameter_sensitivity",
    compare: "descriptor_comparison",
    mantel: "descriptor_comparison",
    fps: "representative_sampling",
    novelty_fps: "representative_sampling",
    uncertainty_diversity: "representative_sampling",
    random: "representative_sampling",
    stratified: "representative_sampling",
    cluster_representative: "representative_sampling",
    per_element: "representative_sampling",
    acquisition: "representative_sampling",
    sampling: "representative_sampling",
    element: "representative_sampling",
  };
  const key = aliases[normalized];
  return key ? ANALYSIS_NAV_MODULES.find((module) => module.key === key) ?? null : null;
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

export const DEFAULT_ANALYSIS_VIEW: AnalysisView = {
  tab: "projection",
  projection: "pca",
  overviewAnalysis: "feature_variance",
  coverageMode: "coverage",
  mode: "structure",
  preprocess: "raw",
  effectiveDimensionPreprocess: "standardized",
  colorBy: "none",
  nearZeroThreshold: 1e-4,
  lowVariationThreshold: 1e-2,
  featureCorrelationMethod: "pearson",
  featureCorrelationThreshold: 0.95,
};

/** One remembered computed result: which tab + parameters produced it. */
export interface AnalysisSlot {
  runId: string;
  analysisId: string;
  tab: TabKey;
  /** Stable key of the parameters the result was computed with. */
  paramsKey: string;
  updatedAt: number;
  /** Monotonic write counter; breaks updatedAt ties deterministically. */
  seq: number;
}

export const slotKey = (slot: Pick<AnalysisSlot, "tab" | "runId" | "paramsKey">): string =>
  `${slot.tab}|${slot.runId}|${slot.paramsKey}`;

/** Every UI parameter that changes what the current analysis tab computes. */
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
  /** Composite sampling space: ASCII block names; empty = plain descriptor. */
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
  /** Dataset view scoping the single-run modules (null = full dataset). */
  viewId: string | null;
}

/**
 * Stable key of the parameter combination a tab's result was computed with.
 * Parameters that only apply to one module (perplexity for t-SNE, the query
 * index for query-mode similarity, …) are blanked out elsewhere so keys stay
 * comparable.
 */
export function buildParamsKey(tab: TabKey, p: AnalysisParams): string {
  switch (tab) {
    case "projection":
      return [p.projection, p.mode, p.preprocess, p.projection === "tsne" ? p.tsnePerplexity : "", p.viewId ?? "full"].join("|");
    case "similarity":
      return [p.similarityMode, p.mode, p.k, p.similarityMode === "query" ? String(p.queryIndex) : "", p.viewId ?? "full"].join("|");
    case "clusters":
      return [p.clusterAlgorithm, p.nClusters, p.mode, p.viewId ?? "full"].join("|");
    case "outliers":
      return [p.outlierAlgorithm, p.k, p.contamination, p.mode, p.viewId ?? "full"].join("|");
    case "sampling":
      return [
        p.samplingAlgorithm,
        p.nSamples,
        p.mode,
        p.samplingAlgorithm === "uncertainty_diversity" ? p.uncertaintyK : "",
        p.samplingAlgorithm === "fps"
          ? [
              p.samplingStrategy,
              p.samplingScaling,
              p.samplingMinDistance,
              p.samplingExistingRunId ?? "none",
              p.samplingBlocks.length ? p.samplingBlocks.join("+") : "descriptor",
              p.samplingBudgetMode === "coverage" ? `cov${p.samplingCoverage}` : "count",
            ].join(":")
          : "",
        p.samplingAlgorithm === "novelty_fps" || p.samplingAlgorithm === "uncertainty_diversity"
          ? [p.referenceRunId, p.referenceViewId ?? "full", p.queryRunId, p.queryViewId ?? "full"].join(":")
          : p.viewId ?? "full",
      ].join("|");
    case "coverage":
      return [p.coverageMode, p.mode, p.referenceRunId, p.referenceViewId ?? "full", p.queryRunId, p.queryViewId ?? "full"].join("|");
    case "compare":
      return [p.compareMode, p.mode, p.compareMode === "mantel" ? `${p.mantelMethod}|${p.mantelPermutations}` : ""].join("|");
    case "local":
      return [p.nClusters, p.k, p.localCutoff, p.viewId ?? "full"].join("|");
    case "kernel":
      return [p.kernelName, p.mode, p.viewId ?? "full"].join("|");
    case "overview":
      {
        const moduleParts =
          p.overviewAnalysis === "property_correlation"
            ? `${p.mode}|${p.propertyName}|${p.propertyFolds}|${p.propertyReliabilityK}|${p.propertyDistanceMetric}|${p.propertySparsePercentile}|${p.propertyOodPercentile}`
            : p.overviewAnalysis === "perturbation_sensitivity"
              ? `${p.perturbationType}|${p.perturbationCount}|${p.perturbationMaximum}|${p.perturbationMetric}|${p.perturbationStructures}`
              : p.overviewAnalysis === "feature_variance"
                ? `${p.nearZeroThreshold}|${p.lowVariationThreshold}`
                : p.overviewAnalysis === "feature_correlation"
                  ? `${p.featureCorrelationMethod}|${p.featureCorrelationThreshold}`
                  : p.overviewAnalysis === "effective_dimension"
                    ? p.effectiveDimensionPreprocess
                    : "";
        if (p.overviewAnalysis === "drift") {
          // Cross-dataset: scoped by its reference/query views, not viewId.
          return [p.overviewAnalysis, moduleParts, [p.referenceRunId, p.referenceViewId ?? "full", p.queryRunId, p.queryViewId ?? "full"].join(":")].join("|");
        }
        return [p.overviewAnalysis, moduleParts, p.viewId ?? "full"].join("|");
      }
    default:
      return "";
  }
}

/** Slot for an exact parameter combination, if one was computed. */
export function slotForParams(
  slots: Record<string, AnalysisSlot>,
  tab: TabKey,
  runId: string | null,
  paramsKey: string,
): AnalysisSlot | null {
  if (!runId) return null;
  return slots[slotKey({ tab, runId, paramsKey })] ?? null;
}

/** Most recent slot recorded for a tab + run, across parameter combinations. */
export function latestSlotForTab(
  slots: Record<string, AnalysisSlot>,
  tab: TabKey,
  runId: string | null,
): AnalysisSlot | null {
  if (!runId) return null;
  let best: AnalysisSlot | null = null;
  for (const slot of Object.values(slots)) {
    if (slot.tab === tab && slot.runId === runId && (!best || slot.updatedAt > best.updatedAt)) {
      best = slot;
    }
  }
  return best;
}

function slotBelongsToModule(slot: AnalysisSlot, module: AnalysisNavModule): boolean {
  if (slot.tab !== module.target.tab) return false;
  if (module.target.tab === "overview") {
    return typeof module.target.overviewAnalysis === "string"
      && slot.paramsKey.startsWith(`${module.target.overviewAnalysis}|`);
  }
  if (module.target.tab === "coverage") {
    return typeof module.target.coverageMode === "string"
      && slot.paramsKey.startsWith(`${module.target.coverageMode}|`);
  }
  return true;
}

/** Whether a legacy slot key can be attributed to one concrete module. */
export function analysisSlotMatchesModule(
  slot: AnalysisSlot,
  moduleKey: AnalysisModuleKey | string | null,
): boolean {
  const module = moduleKey ? analysisNavModuleForKey(moduleKey) : null;
  return module ? slotBelongsToModule(slot, module) : false;
}

/** Most recent slot for one concrete navigation module + source run. */
export function latestSlotForModule(
  slots: Record<string, AnalysisSlot>,
  moduleKey: AnalysisModuleKey | string | null,
  runId: string | null,
): AnalysisSlot | null {
  if (!runId || !moduleKey) return null;
  const module = analysisNavModuleForKey(moduleKey);
  if (!module) return null;
  let best: AnalysisSlot | null = null;
  for (const slot of Object.values(slots)) {
    if (slot.runId !== runId || !slotBelongsToModule(slot, module)) continue;
    if (!best || slot.updatedAt > best.updatedAt || (slot.updatedAt === best.updatedAt && slot.seq > best.seq)) {
      best = slot;
    }
  }
  return best;
}

/** Parse a persisted settings value; returns null when it is unusable. */
export function parseAnalysisView(raw: unknown): AnalysisView | null {
  if (typeof raw !== "string" || !raw) return null;
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null) return null;
  const rec = data as Record<string, unknown>;
  const nearZeroThreshold = persistedThreshold(rec.nearZeroThreshold, DEFAULT_ANALYSIS_VIEW.nearZeroThreshold);
  const lowVariationThreshold = Math.max(
    nearZeroThreshold,
    persistedThreshold(rec.lowVariationThreshold, DEFAULT_ANALYSIS_VIEW.lowVariationThreshold),
  );
  const featureCorrelationThreshold = persistedThreshold(rec.featureCorrelationThreshold, DEFAULT_ANALYSIS_VIEW.featureCorrelationThreshold);
  const featureCorrelationMethod: FeatureCorrelationMethod = rec.featureCorrelationMethod === "spearman" ? "spearman" : "pearson";
  const effectiveDimensionPreprocess: EffectiveDimensionPreprocess = rec.effectiveDimensionPreprocess === "center" ? "center" : "standardized";
  return {
    tab: TAB_KEYS.includes(rec.tab as TabKey) ? (rec.tab as TabKey) : DEFAULT_ANALYSIS_VIEW.tab,
    projection: PROJECTION_NAMES.includes(rec.projection as ProjectionName)
      ? (rec.projection as ProjectionName)
      : DEFAULT_ANALYSIS_VIEW.projection,
    overviewAnalysis: OVERVIEW_ANALYSES.includes(rec.overviewAnalysis as OverviewAnalysis)
      ? (rec.overviewAnalysis as OverviewAnalysis)
      : DEFAULT_ANALYSIS_VIEW.overviewAnalysis,
    coverageMode: rec.coverageMode === "overlap" ? "overlap" : "coverage",
    mode: rec.mode === "atom" ? "atom" : "structure",
    preprocess: typeof rec.preprocess === "string" && rec.preprocess ? rec.preprocess : DEFAULT_ANALYSIS_VIEW.preprocess,
    effectiveDimensionPreprocess,
    colorBy: COLOR_BY.includes(rec.colorBy as ColorBy) ? (rec.colorBy as ColorBy) : DEFAULT_ANALYSIS_VIEW.colorBy,
    nearZeroThreshold,
    lowVariationThreshold,
    featureCorrelationMethod,
    featureCorrelationThreshold,
  };
}

function persistedThreshold(value: unknown, fallback: number): number {
  if (typeof value !== "number" && typeof value !== "string") return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(1, Math.max(0, parsed)) : fallback;
}

function normalizeThreshold(value: number): number {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
}

/** Parse the persisted slot map; invalid entries are dropped and the cap enforced. */
export function parseAnalysisSlots(raw: unknown): Record<string, AnalysisSlot> | null {
  if (typeof raw !== "string" || !raw) return null;
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null || Array.isArray(data)) return null;
  const slots: Record<string, AnalysisSlot> = {};
  for (const [key, value] of Object.entries(data as Record<string, unknown>)) {
    if (typeof value !== "object" || value === null) continue;
    const rec = value as Record<string, unknown>;
    if (
      typeof rec.runId !== "string" || !rec.runId
      || typeof rec.analysisId !== "string" || !rec.analysisId
      || !TAB_KEYS.includes(rec.tab as TabKey)
      || typeof rec.paramsKey !== "string"
      || typeof rec.updatedAt !== "number" || !Number.isFinite(rec.updatedAt)
    ) continue;
    slots[key] = { runId: rec.runId, analysisId: rec.analysisId, tab: rec.tab as TabKey, paramsKey: rec.paramsKey, updatedAt: rec.updatedAt, seq: typeof rec.seq === "number" && Number.isFinite(rec.seq) ? rec.seq : 0 };
  }
  const capped = pruneSlots(slots);
  return Object.keys(capped).length ? capped : null;
}

function pruneSlots(slots: Record<string, AnalysisSlot>): Record<string, AnalysisSlot> {
  // Newest first; the per-slot seq breaks ties (a coarse system clock can
  // stamp several writes with the same millisecond) — never the object key
  // order, which repeated fromEntries rebuilds scramble. Keys are preserved.
  const ranked = Object.entries(slots).map(([key, slot]) => ({ key, slot }));
  ranked.sort((a, b) => (b.slot.updatedAt - a.slot.updatedAt) || (b.slot.seq - a.slot.seq));
  return Object.fromEntries(ranked.slice(0, MAX_SLOTS).map(({ key, slot }) => [key, slot]));
}

function persistView(view: AnalysisView): void {
  void ipc.request("settings.set", { key: VIEW_SETTINGS_KEY, value: JSON.stringify(view) }).catch(() => {});
}

// Monotonic write counter for slot recency ties; re-based on hydration.
let slotSeq = 0;

function persistSlots(slots: Record<string, AnalysisSlot>): void {
  void ipc.request("settings.set", { key: SLOTS_SETTINGS_KEY, value: JSON.stringify(slots) }).catch(() => {});
}

function applyView(partial: Partial<AnalysisView>): void {
  const view = { ...useAnalysisUi.getState().view, ...partial };
  useAnalysisUi.setState({ view });
  persistView(view);
}

export interface AnalysisSlotInput {
  runId: string;
  analysisId: string;
  tab: TabKey;
  paramsKey: string;
}

interface AnalysisUiState {
  view: AnalysisView;
  slots: Record<string, AnalysisSlot>;
  /** Session-only group memory; the compact view remains the only persisted navigation state. */
  recentModulesByGroup: Partial<Record<AnalysisGroupKey, AnalysisModuleKey>>;
  setTab: (tab: TabKey) => void;
  setNavigationTarget: (target: AnalysisNavTarget) => void;
  setProjection: (projection: ProjectionName) => void;
  setOverviewAnalysis: (overviewAnalysis: OverviewAnalysis) => void;
  setCoverageMode: (coverageMode: CoverageMode) => void;
  setMode: (mode: PcaMode) => void;
  setPreprocess: (preprocess: string) => void;
  setEffectiveDimensionPreprocess: (preprocess: EffectiveDimensionPreprocess) => void;
  setColorBy: (colorBy: ColorBy) => void;
  setNearZeroThreshold: (value: number) => void;
  setLowVariationThreshold: (value: number) => void;
  setFeatureCorrelationMethod: (method: FeatureCorrelationMethod) => void;
  setFeatureCorrelationThreshold: (value: number) => void;
  rememberNavigationModule: (group: AnalysisGroupKey, module: AnalysisModuleKey) => void;
  /** Records the analysis currently displayed for its tab + parameter combination. */
  rememberResult: (entry: AnalysisSlotInput) => void;
  /** Forgets one slot, e.g. one recorded under an inconsistent context. */
  forgetSlot: (key: string) => void;
  /** Forgets every slot pointing at a deleted analysis. */
  clearResult: (analysisId: string) => void;
}

export const useAnalysisUi = create<AnalysisUiState>()(() => ({
  view: DEFAULT_ANALYSIS_VIEW,
  slots: {},
  recentModulesByGroup: {},
  setTab: (tab) => applyView({ tab }),
  setNavigationTarget: (target) => applyView({
    tab: target.tab,
    ...(target.overviewAnalysis ? { overviewAnalysis: target.overviewAnalysis } : {}),
    ...(target.coverageMode ? { coverageMode: target.coverageMode } : {}),
  }),
  setProjection: (projection) => applyView({ projection }),
  setOverviewAnalysis: (overviewAnalysis) => applyView({ overviewAnalysis }),
  setCoverageMode: (coverageMode) => applyView({ coverageMode }),
  setMode: (mode) => applyView({ mode }),
  setPreprocess: (preprocess) => applyView({ preprocess }),
  setEffectiveDimensionPreprocess: (effectiveDimensionPreprocess) => applyView({ effectiveDimensionPreprocess }),
  setColorBy: (colorBy) => applyView({ colorBy }),
  setNearZeroThreshold: (value) => {
    const nearZeroThreshold = normalizeThreshold(value);
    const current = useAnalysisUi.getState().view;
    applyView({ nearZeroThreshold, lowVariationThreshold: Math.max(nearZeroThreshold, current.lowVariationThreshold) });
  },
  setLowVariationThreshold: (value) => {
    const lowVariationThreshold = normalizeThreshold(value);
    const current = useAnalysisUi.getState().view;
    applyView({ lowVariationThreshold: Math.max(current.nearZeroThreshold, lowVariationThreshold) });
  },
  setFeatureCorrelationMethod: (featureCorrelationMethod) => applyView({ featureCorrelationMethod }),
  setFeatureCorrelationThreshold: (value) => applyView({ featureCorrelationThreshold: normalizeThreshold(value) }),
  rememberNavigationModule: (group, module) => {
    if (useAnalysisUi.getState().recentModulesByGroup[group] === module) return;
    useAnalysisUi.setState({
      recentModulesByGroup: { ...useAnalysisUi.getState().recentModulesByGroup, [group]: module },
    });
  },
  rememberResult: (entry) => {
    const slots = pruneSlots({
      ...useAnalysisUi.getState().slots,
      [slotKey(entry)]: { ...entry, updatedAt: Date.now(), seq: ++slotSeq },
    });
    useAnalysisUi.setState({ slots });
    persistSlots(slots);
  },
  forgetSlot: (key) => {
    const current = useAnalysisUi.getState().slots;
    if (!(key in current)) return;
    const slots = { ...current };
    delete slots[key];
    useAnalysisUi.setState({ slots });
    persistSlots(slots);
  },
  clearResult: (analysisId) => {
    const slots = { ...useAnalysisUi.getState().slots };
    let changed = false;
    for (const [key, slot] of Object.entries(slots)) {
      if (slot.analysisId === analysisId) {
        delete slots[key];
        changed = true;
      }
    }
    if (!changed) return;
    useAnalysisUi.setState({ slots });
    persistSlots(slots);
  },
}));

/**
 * Load the persisted view + slots before the pages render (backend.ready
 * path). Missing or unusable settings keep the in-session state.
 */
export async function hydrateAnalysisUi(): Promise<void> {
  try {
    const [viewR, slotsR] = await Promise.all([
      ipc.request<{ value: string | null }>("settings.get", { key: VIEW_SETTINGS_KEY }),
      ipc.request<{ value: string | null }>("settings.get", { key: SLOTS_SETTINGS_KEY }),
    ]);
    const patch: { view?: AnalysisView; slots?: Record<string, AnalysisSlot> } = {};
    const view = parseAnalysisView(viewR.value);
    if (view) patch.view = view;
    const slots = parseAnalysisSlots(slotsR.value);
    if (slots) {
      patch.slots = slots;
      slotSeq = Math.max(0, ...Object.values(slots).map((slot) => slot.seq));
    }
    if (Object.keys(patch).length) useAnalysisUi.setState(patch);
  } catch {
    /* backend not reachable yet — keep the in-session state */
  }
}

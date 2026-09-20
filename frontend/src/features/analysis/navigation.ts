import type {
  AnalysisView,
  ColorBy,
  EffectiveDimensionPreprocess,
  FeatureCorrelationMethod,
  OverviewAnalysis,
  ProjectionName,
  TabKey,
} from "./types";

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

const TAB_KEYS: TabKey[] = ["overview", "projection", "similarity", "clusters", "outliers", "sampling", "coverage", "compare", "local", "kernel"];
const PROJECTION_NAMES: ProjectionName[] = ["pca", "umap", "tsne"];
const OVERVIEW_ANALYSES: OverviewAnalysis[] = ["feature_variance", "feature_correlation", "effective_dimension", "property_correlation", "trajectory", "drift", "sensitivity", "perturbation_sensitivity"];
const COLOR_BY: ColorBy[] = ["none", "energy", "force_max", "volume"];
// The three spellings the backend accepts; anything else in persisted state
// would turn the next run into an ANALYSIS_INPUT_INVALID.
const PREPROCESS_MODES = ["raw", "center", "standardized"];

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
  const lowVariationThreshold = Math.max(nearZeroThreshold, persistedThreshold(rec.lowVariationThreshold, DEFAULT_ANALYSIS_VIEW.lowVariationThreshold));
  const featureCorrelationThreshold = persistedThreshold(rec.featureCorrelationThreshold, DEFAULT_ANALYSIS_VIEW.featureCorrelationThreshold);
  const featureCorrelationMethod: FeatureCorrelationMethod = rec.featureCorrelationMethod === "spearman" ? "spearman" : "pearson";
  const effectiveDimensionPreprocess: EffectiveDimensionPreprocess = rec.effectiveDimensionPreprocess === "center" ? "center" : "standardized";
  return {
    tab: TAB_KEYS.includes(rec.tab as TabKey) ? (rec.tab as TabKey) : DEFAULT_ANALYSIS_VIEW.tab,
    projection: PROJECTION_NAMES.includes(rec.projection as ProjectionName) ? (rec.projection as ProjectionName) : DEFAULT_ANALYSIS_VIEW.projection,
    overviewAnalysis: OVERVIEW_ANALYSES.includes(rec.overviewAnalysis as OverviewAnalysis) ? (rec.overviewAnalysis as OverviewAnalysis) : DEFAULT_ANALYSIS_VIEW.overviewAnalysis,
    coverageMode: rec.coverageMode === "overlap" ? "overlap" : "coverage",
    mode: rec.mode === "atom" ? "atom" : "structure",
    preprocess: PREPROCESS_MODES.includes(rec.preprocess as string) ? (rec.preprocess as string) : DEFAULT_ANALYSIS_VIEW.preprocess,
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

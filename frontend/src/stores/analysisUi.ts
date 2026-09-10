// Analysis page view state that must survive leaving the page and an app
// restart (ADR-17 persistence rule). The computed charts themselves live on
// the backend (analysis_runs table + artifacts, re-served by analysis.preview
// / result.get_pca); only the light "what was on screen" pointers live here:
// the UI parameters, and one slot per (tab, run, parameter combination) so
// every computed result can be re-displayed without recomputing.
import { create } from "zustand";
import { ipc } from "../ipc/client";
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

export interface AnalysisView {
  tab: TabKey;
  projection: ProjectionName;
  overviewAnalysis: OverviewAnalysis;
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
  tab: "overview",
  projection: "pca",
  overviewAnalysis: "feature_variance",
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
  coverageMode: string;
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
  perturbationMetric: string;
  nearZeroThreshold: number;
  lowVariationThreshold: number;
  featureCorrelationMethod: FeatureCorrelationMethod;
  featureCorrelationThreshold: number;
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
      return [p.projection, p.mode, p.preprocess, p.projection === "tsne" ? p.tsnePerplexity : ""].join("|");
    case "similarity":
      return [p.similarityMode, p.mode, p.k, p.similarityMode === "query" ? String(p.queryIndex) : ""].join("|");
    case "clusters":
      return [p.clusterAlgorithm, p.nClusters, p.mode].join("|");
    case "outliers":
      return [p.outlierAlgorithm, p.k, p.contamination, p.mode].join("|");
    case "sampling":
      return [p.samplingAlgorithm, p.nSamples, p.mode, p.samplingAlgorithm === "uncertainty_diversity" ? p.uncertaintyK : ""].join("|");
    case "coverage":
      return [p.coverageMode, p.mode].join("|");
    case "compare":
      return [p.compareMode, p.mode, p.compareMode === "mantel" ? `${p.mantelMethod}|${p.mantelPermutations}` : ""].join("|");
    case "local":
      return [p.nClusters, p.k, p.localCutoff].join("|");
    case "kernel":
      return [p.kernelName, p.mode].join("|");
    case "overview":
      {
        const keyParts = [
          p.overviewAnalysis,
          p.overviewAnalysis === "property_correlation"
            ? `${p.propertyName}|${p.propertyFolds}|${p.propertyReliabilityK}|${p.propertyDistanceMetric}|${p.propertySparsePercentile}|${p.propertyOodPercentile}`
            : "",
          p.overviewAnalysis === "perturbation_sensitivity"
            ? `${p.perturbationType}|${p.perturbationCount}|${p.perturbationMaximum}|${p.perturbationMetric}`
            : p.overviewAnalysis === "feature_variance"
              ? `${p.nearZeroThreshold}|${p.lowVariationThreshold}`
              : p.overviewAnalysis === "feature_correlation"
                ? `${p.featureCorrelationMethod}|${p.featureCorrelationThreshold}`
                : p.overviewAnalysis === "effective_dimension"
                  ? p.effectiveDimensionPreprocess
                  : "",
        ];
        return keyParts.join("|");
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
  setTab: (tab: TabKey) => void;
  setProjection: (projection: ProjectionName) => void;
  setOverviewAnalysis: (overviewAnalysis: OverviewAnalysis) => void;
  setMode: (mode: PcaMode) => void;
  setPreprocess: (preprocess: string) => void;
  setEffectiveDimensionPreprocess: (preprocess: EffectiveDimensionPreprocess) => void;
  setColorBy: (colorBy: ColorBy) => void;
  setNearZeroThreshold: (value: number) => void;
  setLowVariationThreshold: (value: number) => void;
  setFeatureCorrelationMethod: (method: FeatureCorrelationMethod) => void;
  setFeatureCorrelationThreshold: (value: number) => void;
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
  setTab: (tab) => applyView({ tab }),
  setProjection: (projection) => applyView({ projection }),
  setOverviewAnalysis: (overviewAnalysis) => applyView({ overviewAnalysis }),
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

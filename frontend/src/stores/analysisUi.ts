// Analysis page view state that must survive leaving the page and an app
// restart (ADR-17 persistence rule). The computed charts themselves live on
// the backend (analysis_runs table + artifacts, re-served by analysis.preview
// / result.get_pca); only the light "what was on screen" pointer is kept here
// so remounting the page can re-display it without recomputing.
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

const SETTINGS_KEY = "workspace.analysisUi";

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

export interface AnalysisView {
  tab: TabKey;
  projection: ProjectionName;
  overviewAnalysis: OverviewAnalysis;
  mode: PcaMode;
  preprocess: string;
  /** Descriptor run the displayed analysis belongs to. */
  runId: string | null;
  /** Last analysis displayed on the page (backend analysis id). */
  analysisId: string | null;
}

export const DEFAULT_ANALYSIS_VIEW: AnalysisView = {
  tab: "overview",
  projection: "pca",
  overviewAnalysis: "feature_variance",
  mode: "structure",
  preprocess: "raw",
  runId: null,
  analysisId: null,
};

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
    runId: typeof rec.runId === "string" && rec.runId ? rec.runId : null,
    analysisId: typeof rec.analysisId === "string" && rec.analysisId ? rec.analysisId : null,
  };
}

function persist(view: AnalysisView): void {
  void ipc.request("settings.set", { key: SETTINGS_KEY, value: JSON.stringify(view) }).catch(() => {});
}

function applyView(partial: Partial<AnalysisView>): void {
  const view = { ...useAnalysisUi.getState().view, ...partial };
  useAnalysisUi.setState({ view });
  persist(view);
}

interface AnalysisUiState {
  view: AnalysisView;
  setTab: (tab: TabKey) => void;
  setProjection: (projection: ProjectionName) => void;
  setOverviewAnalysis: (overviewAnalysis: OverviewAnalysis) => void;
  setMode: (mode: PcaMode) => void;
  setPreprocess: (preprocess: string) => void;
  /** Records the analysis currently displayed so a later mount restores it. */
  rememberResult: (runId: string, analysisId: string) => void;
  clearResult: () => void;
}

export const useAnalysisUi = create<AnalysisUiState>()(() => ({
  view: DEFAULT_ANALYSIS_VIEW,
  setTab: (tab) => applyView({ tab }),
  setProjection: (projection) => applyView({ projection }),
  setOverviewAnalysis: (overviewAnalysis) => applyView({ overviewAnalysis }),
  setMode: (mode) => applyView({ mode }),
  setPreprocess: (preprocess) => applyView({ preprocess }),
  rememberResult: (runId, analysisId) => applyView({ runId, analysisId }),
  clearResult: () => applyView({ analysisId: null }),
}));

/** Load the persisted view before the pages render (backend.ready path). */
export async function hydrateAnalysisUi(): Promise<void> {
  try {
    const r = await ipc.request<{ value: string | null }>("settings.get", { key: SETTINGS_KEY });
    const view = parseAnalysisView(r.value);
    if (view) useAnalysisUi.setState({ view });
  } catch {
    /* backend not reachable yet — keep the in-session view */
  }
}

import { create } from "zustand";
import { ipc } from "../../ipc/client";
import { DEFAULT_ANALYSIS_VIEW, parseAnalysisView } from "./navigation";
import { slotKey } from "./identity";
import { parseAnalysisSlots, persistSlots, persistView, pruneSlots } from "./persistence";
import type { AnalysisGroupKey, AnalysisModuleKey, AnalysisNavTarget, AnalysisSlot, AnalysisSlotInput, AnalysisView, ColorBy, CoverageMode, EffectiveDimensionPreprocess, FeatureCorrelationMethod, OverviewAnalysis, ProjectionName, TabKey } from "./types";

interface AnalysisUiState {
  view: AnalysisView;
  slots: Record<string, AnalysisSlot>;
  recentModulesByGroup: Partial<Record<AnalysisGroupKey, AnalysisModuleKey>>;
  setTab: (tab: TabKey) => void;
  setNavigationTarget: (target: AnalysisNavTarget) => void;
  setProjection: (projection: ProjectionName) => void;
  setOverviewAnalysis: (overviewAnalysis: OverviewAnalysis) => void;
  setCoverageMode: (coverageMode: CoverageMode) => void;
  setMode: (mode: AnalysisView["mode"]) => void;
  setModeTransient: (mode: AnalysisView["mode"]) => void;
  setPreprocess: (preprocess: string) => void;
  setEffectiveDimensionPreprocess: (preprocess: EffectiveDimensionPreprocess) => void;
  setColorBy: (colorBy: ColorBy) => void;
  setNearZeroThreshold: (value: number) => void;
  setLowVariationThreshold: (value: number) => void;
  setFeatureCorrelationMethod: (method: FeatureCorrelationMethod) => void;
  setFeatureCorrelationThreshold: (value: number) => void;
  rememberNavigationModule: (group: AnalysisGroupKey, module: AnalysisModuleKey) => void;
  rememberResult: (entry: AnalysisSlotInput) => void;
  forgetSlot: (key: string) => void;
  clearResult: (analysisId: string) => void;
}

const normalizeThreshold = (value: number): number => Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
let slotSeq = 0;

export const useAnalysisUi = create<AnalysisUiState>()(() => ({
  view: DEFAULT_ANALYSIS_VIEW,
  slots: {},
  recentModulesByGroup: {},
  setTab: (tab) => applyView({ tab }),
  setNavigationTarget: (target) => applyView({ tab: target.tab, ...(target.overviewAnalysis ? { overviewAnalysis: target.overviewAnalysis } : {}), ...(target.coverageMode ? { coverageMode: target.coverageMode } : {}) }),
  setProjection: (projection) => applyView({ projection }),
  setOverviewAnalysis: (overviewAnalysis) => applyView({ overviewAnalysis }),
  setCoverageMode: (coverageMode) => applyView({ coverageMode }),
  setMode: (mode) => applyView({ mode }),
  setModeTransient: (mode) => applyView({ mode }, false),
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
    useAnalysisUi.setState({ recentModulesByGroup: { ...useAnalysisUi.getState().recentModulesByGroup, [group]: module } });
  },
  rememberResult: (entry) => {
    const slots = pruneSlots({ ...useAnalysisUi.getState().slots, [slotKey(entry)]: { ...entry, updatedAt: Date.now(), seq: ++slotSeq } });
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

function applyView(partial: Partial<AnalysisView>, persist = true): void {
  const view = { ...useAnalysisUi.getState().view, ...partial };
  useAnalysisUi.setState({ view });
  if (persist) persistView(view);
}

export async function hydrateAnalysisUi(): Promise<void> {
  try {
    const [viewR, slotsR] = await Promise.all([
      ipc.request<{ value: string | null }>("settings.get", { key: "workspace.analysisUi" }),
      ipc.request<{ value: string | null }>("settings.get", { key: "workspace.analysisSlots" }),
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

export type { AnalysisUiState };

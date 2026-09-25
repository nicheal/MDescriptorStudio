// Generation page state, deliberately separate from the analysis UI store:
// expansion configs are nested objects (not a flat param bag), and polluting
// AnalysisParams with ga*/pso* optionals would recreate the illegal-state
// surface the discriminated union exists to prevent.
import { create } from "zustand";
import type { GenerationConfig, GenerationOptimizer, GenerationPhase, GenerationRow, OptimizerConfig } from "./types";

/** Exhaustive per-optimizer defaults — switching method in the UI resets to
 * the backend catalog defaults for that optimizer. */
export function defaultOptimizerConfig(type: GenerationOptimizer): OptimizerConfig {
  switch (type) {
    case "random":
      return { type: "random", childrenPerSeed: 8, batchAccept: 8, nSeeds: 64, reuseAcceptedSeeds: false };
  }
}

export const defaultGenerationConfig = (): GenerationConfig => ({
  source: { datasetId: null, descriptorRunId: null, seedViewId: null },
  objective: {
    type: "local_environment_novelty",
    aggregation: "top_fraction_mean",
    topFraction: 0.2,
    quantile: 0.5,
    noveltyThreshold: 0.25,
    structureWeight: 0.3,
    localWeight: 0.7,
    scaling: "robust",
  },
  searchSpace: {
    atomicDisplacement: true,
    maxDisplacement: 0.15,
    hardCutoff: null,
    isotropicStrain: true,
    anisotropicStrain: true,
    maxStrain: 0.05,
    cellShear: false,
    maxShear: 0.05,
    vacancy: false,
    interstitialAtom: false,
    interstitialElement: "",
    substitution: false,
    substitutionElement: "",
    antisiteSwap: false,
  },
  constraints: {
    minDistanceMode: "covalent",
    minDistanceFactor: 0.7,
    minDistanceAbsolute: 1.0,
    minDistancePairs: "",
    maxVolumeChange: 0.2,
    minVolumePerAtom: null,
    maxVolumePerAtom: null,
    // Locked-by-default is the single scientific default shared with the
    // backend (GeometryConstraints / parse_request / catalog). Count-changing
    // operators unlock these switches explicitly.
    compositionLocked: true,
    atomCountLocked: true,
  },
  optimizer: { ...defaultOptimizerConfig("random") },
  budget: {
    maxEvaluations: 10_000,
    maxAccepted: 500,
    maxGenerations: 200,
    targetNovelty: null,
    noImprovementRounds: 10,
  },
  seedMode: "fixed",
  seed: 42,
});

interface GenerationState {
  config: GenerationConfig;
  phase: GenerationPhase;
  /** The run currently shown in the running/results phases. */
  activeGenerationId: string | null;
  /** Live row during the running phase (polled). */
  liveRow: GenerationRow | null;
  history: GenerationRow[];

  setConfig: (patch: Partial<GenerationConfig>) => void;
  updateConfig: (fn: (config: GenerationConfig) => GenerationConfig) => void;
  setPhase: (phase: GenerationPhase) => void;
  setActiveGeneration: (id: string | null) => void;
  setLiveRow: (row: GenerationRow | null) => void;
  setHistory: (rows: GenerationRow[]) => void;
  reset: () => void;
}

export const useGenerationStore = create<GenerationState>((set) => ({
  config: defaultGenerationConfig(),
  phase: "config",
  activeGenerationId: null,
  liveRow: null,
  history: [],
  setConfig: (patch) => set((s) => ({ config: { ...s.config, ...patch } })),
  updateConfig: (fn) => set((s) => ({ config: fn(s.config) })),
  setPhase: (phase) => set({ phase }),
  setActiveGeneration: (id) => set({ activeGenerationId: id }),
  setLiveRow: (row) => set({ liveRow: row }),
  setHistory: (rows) => set({ history: rows }),
  reset: () =>
    set({
      config: defaultGenerationConfig(),
      phase: "config",
      activeGenerationId: null,
      liveRow: null,
    }),
}));

// Builds the generation.submit RPC payload from the UI config. The shape this
// emits is the single serialization fact: the page renders controls, the
// backend parses them back (generation.models.parse_request).
import type { GenerationConfig } from "./types";
import { ATOMIC_MASS } from "../../util/elements";

export interface SubmissionCheck {
  ok: boolean;
  reason?: string;
}

function parsePairMinDistances(value: string): Record<string, number> | null {
  const pairs: Record<string, number> = {};
  if (!value.trim()) return pairs;
  const validElements = new Set(Object.keys(ATOMIC_MASS));
  const entryPattern = /^([A-Z][a-z]?)-([A-Z][a-z]?)\s*=\s*((?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)$/i;
  for (const entry of value.split(/[;,]/)) {
    const match = entry.trim().match(entryPattern);
    if (!match) return null;
    const normalize = (symbol: string) => symbol[0].toUpperCase() + symbol.slice(1).toLowerCase();
    const first = normalize(match[1]);
    const second = normalize(match[2]);
    const distance = Number(match[3]);
    if (!validElements.has(first) || !validElements.has(second) || !Number.isFinite(distance) || distance <= 0 || distance > 20) {
      return null;
    }
    const pair = [first, second].sort().join("-");
    if (Object.prototype.hasOwnProperty.call(pairs, pair)) return null;
    pairs[pair] = distance;
  }
  return pairs;
}

function normalizeElement(value: string): string | null {
  const element = value.trim();
  if (!element) return "";
  const symbol = element[0].toUpperCase() + element.slice(1).toLowerCase();
  return Object.prototype.hasOwnProperty.call(ATOMIC_MASS, symbol) ? symbol : null;
}

export function validateConfig(config: GenerationConfig): SubmissionCheck {
  if (!config.source.datasetId) return { ok: false, reason: "Select a source dataset" };
  if (!config.source.descriptorRunId) return { ok: false, reason: "Select a descriptor run" };
  const space = config.searchSpace;
  if (
    !space.atomicDisplacement && !space.isotropicStrain && !space.anisotropicStrain && !space.cellShear &&
    !space.vacancy && !space.interstitialAtom && !space.substitution && !space.antisiteSwap
  ) {
    return { ok: false, reason: "Enable at least one structure operator" };
  }
  if (config.optimizer.type === "random" && config.optimizer.reuseAcceptedSeeds && !space.atomicDisplacement) {
    return { ok: false, reason: "Accepted-seed feedback requires atomic displacement" };
  }
  if (space.interstitialAtom && normalizeElement(space.interstitialElement) == null) {
    return { ok: false, reason: "Enter a valid interstitial element symbol" };
  }
  if (space.hardCutoff != null && (!Number.isFinite(space.hardCutoff) || space.hardCutoff <= 0)) {
    return { ok: false, reason: "Hard displacement cutoff must be positive" };
  }
  if (space.substitution && normalizeElement(space.substitutionElement) == null) {
    return { ok: false, reason: "Enter a valid substitution element symbol" };
  }
  if ((space.vacancy || space.interstitialAtom) && (config.constraints.compositionLocked || config.constraints.atomCountLocked)) {
    return { ok: false, reason: "Vacancy and interstitial operators require unlocked composition and atom count" };
  }
  if (space.substitution && config.constraints.compositionLocked) {
    return { ok: false, reason: "Substitution requires unlocked composition" };
  }
  if (config.constraints.minDistanceMode === "absolute" && config.constraints.minDistanceAbsolute <= 0) {
    return { ok: false, reason: "Minimum distance must be positive" };
  }
  if (parsePairMinDistances(config.constraints.minDistancePairs) == null) {
    return { ok: false, reason: "Use element pairs like C-C=1.5, C-H=1.0 with positive distances up to 20 Å" };
  }
  const minVolume = config.constraints.minVolumePerAtom;
  const maxVolume = config.constraints.maxVolumePerAtom;
  if ([minVolume, maxVolume].some((v) => v != null && (!Number.isFinite(v) || v <= 0))) {
    return { ok: false, reason: "Volume per atom bounds must be positive" };
  }
  if (minVolume != null && maxVolume != null && minVolume > maxVolume) {
    return { ok: false, reason: "Minimum volume per atom must not exceed maximum" };
  }
  if (config.budget.maxEvaluations < 1 || config.budget.maxAccepted < 1) {
    return { ok: false, reason: "Budgets must be positive" };
  }
  return { ok: true };
}

export function buildSubmitPayload(config: GenerationConfig): Record<string, unknown> {
  const space = config.searchSpace;
  const objective: Record<string, unknown> = { type: config.objective.type, scaling: config.objective.scaling };
  if (config.objective.type === "local_environment_novelty" || config.objective.type === "composite") {
    objective.aggregation = config.objective.aggregation;
    objective.top_fraction = config.objective.topFraction;
    objective.quantile = config.objective.quantile;
    objective.novelty_threshold = config.objective.noveltyThreshold;
  }
  if (config.objective.type === "composite") {
    objective.structure_weight = config.objective.structureWeight;
    objective.local_weight = config.objective.localWeight;
  }

  const operators: Record<string, unknown> = {};
  if (space.atomicDisplacement) {
    // max_sigma bounds the Gaussian spread; hard_cutoff (optional) bounds
    // every atom's displacement norm. Separate semantics — see the operator.
    operators.atomic_displacement = {
      enabled: true,
      max_sigma: space.maxDisplacement,
      ...(space.hardCutoff != null ? { hard_cutoff: space.hardCutoff } : {}),
    };
  }
  if (space.isotropicStrain) operators.isotropic_strain = { enabled: true, max_strain: space.maxStrain };
  if (space.anisotropicStrain) operators.anisotropic_strain = { enabled: true, max_strain: space.maxStrain };
  if (space.cellShear) operators.cell_shear = { enabled: true, max_shear: space.maxShear };
  if (space.vacancy) operators.vacancy = { enabled: true };
  if (space.interstitialAtom) {
    const element = normalizeElement(space.interstitialElement);
    operators.interstitial_atom = { enabled: true, ...(element ? { element } : {}) };
  }
  if (space.substitution) {
    const element = normalizeElement(space.substitutionElement);
    operators.substitution = { enabled: true, ...(element ? { element } : {}) };
  }
  if (space.antisiteSwap) operators.antisite_swap = { enabled: true };

  const constraints: Record<string, unknown> = {
    min_distance_mode: config.constraints.minDistanceMode,
    max_volume_change: config.constraints.maxVolumeChange,
    composition_locked: config.constraints.compositionLocked,
    atom_count_locked: config.constraints.atomCountLocked,
  };
  if (config.constraints.minDistanceMode === "covalent") {
    constraints.min_distance_factor = config.constraints.minDistanceFactor;
  }
  if (config.constraints.minDistanceMode === "absolute") {
    constraints.min_distance = config.constraints.minDistanceAbsolute;
  }
  const pairMinDistances = parsePairMinDistances(config.constraints.minDistancePairs) ?? {};
  if (Object.keys(pairMinDistances).length) constraints.min_distance_pairs = pairMinDistances;
  if (config.constraints.minVolumePerAtom != null) constraints.min_volume_per_atom = config.constraints.minVolumePerAtom;
  if (config.constraints.maxVolumePerAtom != null) constraints.max_volume_per_atom = config.constraints.maxVolumePerAtom;

  const optimizer = config.optimizer;
  const optimizerParams: Record<string, unknown> = {};
  if (optimizer.type === "random") {
    optimizerParams.children_per_seed = optimizer.childrenPerSeed;
    optimizerParams.batch_accept = optimizer.batchAccept;
    optimizerParams.n_seeds = optimizer.nSeeds;
    optimizerParams.reuse_accepted_seeds = optimizer.reuseAcceptedSeeds;
  }

  const budget: Record<string, unknown> = {
    max_evaluations: config.budget.maxEvaluations,
    max_accepted: config.budget.maxAccepted,
    max_generations: config.budget.maxGenerations,
  };
  if (config.budget.targetNovelty != null) budget.target_novelty = config.budget.targetNovelty;
  if (config.budget.noImprovementRounds != null) budget.no_improvement_rounds = config.budget.noImprovementRounds;

  return {
    dataset_id: config.source.datasetId,
    descriptor_run_id: config.source.descriptorRunId,
    seed_view_id: config.source.seedViewId,
    optimizer: optimizer.type,
    optimizer_params: optimizerParams,
    objective,
    operators,
    constraints,
    budget,
    seed: config.seedMode === "fixed" ? config.seed : "random",
  };
}

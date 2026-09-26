// Builds the generation.submit RPC payload from the UI config. The shape this
// emits is the single serialization fact: the page renders controls, the
// backend parses them back (generation.models.parse_request).
import type { GenerationConfig } from "./types";
import { ATOMIC_MASS } from "../../util/elements";

export interface SubmissionCheck {
  ok: boolean;
  reason?: string;
}

export interface ConfigIssue {
  field: string;
  reason: string;
}

export type AnchorFrameParse =
  | { ok: true; frames: number[] }
  | { ok: false; reason: string };

/** Parse without coercion so partial input such as `12abc` is never submitted as frame 12. */
export function parseAnchorFrames(value: string, frameCount?: number): AnchorFrameParse {
  const input = value.trim();
  if (!input) return { ok: true, frames: [] };
  if (!/^\d+(?:\s*(?:,|\s)\s*\d+)*$/.test(input)) {
    return { ok: false, reason: "Anchor frames must be whole numbers separated by commas or spaces" };
  }
  const frames = input.split(/[\s,]+/).map(Number);
  if (frames.some((frame) => !Number.isSafeInteger(frame) || frame < 0)) {
    return { ok: false, reason: "Anchor frames must be non-negative whole numbers" };
  }
  if (frames.length > 16) return { ok: false, reason: "Use at most 16 anchor frames" };
  if (new Set(frames).size !== frames.length) return { ok: false, reason: "Anchor frame indices must not repeat" };
  if (frameCount != null && frames.some((frame) => frame >= frameCount)) {
    return { ok: false, reason: "Anchor frame index is outside the source dataset range" };
  }
  return { ok: true, frames };
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

export function validateConfigFields(config: GenerationConfig): ConfigIssue[] {
  const issues: ConfigIssue[] = [];
  const add = (field: string, reason: string) => issues.push({ field, reason });
  if (!config.source.datasetId) add("source.datasetId", "Select a source dataset");
  if (!config.source.descriptorRunId) add("source.descriptorRunId", "Select a descriptor run");
  const space = config.searchSpace;
  if (
    !space.atomicDisplacement && !space.isotropicStrain && !space.anisotropicStrain && !space.cellShear &&
    !space.vacancy && !space.interstitialAtom && !space.substitution && !space.antisiteSwap
  ) {
    add("searchSpace.operators", "Enable at least one structure operator");
  }
  if (config.optimizer.type === "random" && config.optimizer.reuseAcceptedSeeds && !space.atomicDisplacement) {
    add("optimizer.reuseAcceptedSeeds", "Accepted-seed feedback requires atomic displacement");
  }
  const anchorFrames = config.searchTarget.anchorFrames;
  if (anchorFrames.length > 16 || anchorFrames.some((frame) => !Number.isSafeInteger(frame) || frame < 0)) {
    add("searchTarget.anchorFrames", "Anchor frames must be 0–16 non-negative whole dataset indices");
  } else if (new Set(anchorFrames).size !== anchorFrames.length) {
    add("searchTarget.anchorFrames", "Anchor frame indices must not repeat");
  }
  if (anchorFrames.length > 0 && config.optimizer.type === "random" && config.optimizer.reuseAcceptedSeeds) {
    add("optimizer.reuseAcceptedSeeds", "Accepted-seed feedback is unavailable with a target region");
  }
  if (space.interstitialAtom && normalizeElement(space.interstitialElement) == null) {
    add("searchSpace.interstitialElement", "Enter a valid interstitial element symbol");
  }
  if (space.hardCutoff != null && (!Number.isFinite(space.hardCutoff) || space.hardCutoff <= 0)) {
    add("searchSpace.hardCutoff", "Hard displacement cutoff must be positive");
  }
  if (space.substitution && normalizeElement(space.substitutionElement) == null) {
    add("searchSpace.substitutionElement", "Enter a valid substitution element symbol");
  }
  if ((space.vacancy || space.interstitialAtom) && (config.constraints.compositionLocked || config.constraints.atomCountLocked)) {
    add("constraints.compositionLocked", "Vacancy and interstitial operators require unlocked composition and atom count");
  }
  if (space.substitution && config.constraints.compositionLocked) {
    add("constraints.compositionLocked", "Substitution requires unlocked composition");
  }
  if (config.constraints.minDistanceMode === "absolute" && config.constraints.minDistanceAbsolute <= 0) {
    add("constraints.minDistanceAbsolute", "Minimum distance must be positive");
  }
  if (parsePairMinDistances(config.constraints.minDistancePairs) == null) {
    add("constraints.minDistancePairs", "Use element pairs like C-C=1.5, C-H=1.0 with positive distances up to 20 Å");
  }
  const minVolume = config.constraints.minVolumePerAtom;
  const maxVolume = config.constraints.maxVolumePerAtom;
  if ([minVolume, maxVolume].some((v) => v != null && (!Number.isFinite(v) || v <= 0))) {
    add("constraints.volume", "Volume per atom bounds must be positive");
  }
  if (minVolume != null && maxVolume != null && minVolume > maxVolume) {
    add("constraints.volume", "Minimum volume per atom must not exceed maximum");
  }
  if (!Number.isInteger(config.budget.maxEvaluations) || config.budget.maxEvaluations < 1 || config.budget.maxEvaluations > 10_000_000) {
    add("budget.maxEvaluations", "Max descriptor evaluations must be a whole number in range 1–10,000,000");
  }
  if (!Number.isInteger(config.budget.maxAccepted) || config.budget.maxAccepted < 1 || config.budget.maxAccepted > 1_000_000) {
    add("budget.maxAccepted", "Max accepted structures must be a whole number in range 1–1,000,000");
  }
  if (!Number.isInteger(config.budget.maxGenerations) || config.budget.maxGenerations < 1 || config.budget.maxGenerations > 100_000) {
    add("budget.maxGenerations", "Max generations must be a whole number in range 1–100,000");
  }
  if (config.budget.noImprovementRounds != null && (!Number.isInteger(config.budget.noImprovementRounds) || config.budget.noImprovementRounds < 1 || config.budget.noImprovementRounds > 10_000)) {
    add("budget.noImprovementRounds", "No-improvement rounds must be a whole number in range 1–10,000");
  }
  if (config.budget.targetNovelty != null && (!Number.isFinite(config.budget.targetNovelty) || config.budget.targetNovelty <= 0)) {
    add("budget.targetNovelty", "Target novelty must be positive or empty");
  }
  return issues;
}

export function validateConfig(config: GenerationConfig): SubmissionCheck {
  const issue = validateConfigFields(config)[0];
  return issue ? { ok: false, reason: issue.reason } : { ok: true };
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
  if (optimizer.type === "genetic") {
    optimizerParams.children_per_seed = optimizer.childrenPerSeed;
    optimizerParams.batch_accept = optimizer.batchAccept;
    optimizerParams.n_seeds = optimizer.nSeeds;
    optimizerParams.parent_fraction = optimizer.parentFraction;
    optimizerParams.immigrant_fraction = optimizer.immigrantFraction;
  }
  if (optimizer.type === "pso") {
    optimizerParams.children_per_seed = optimizer.childrenPerSeed;
    optimizerParams.batch_accept = optimizer.batchAccept;
    optimizerParams.n_seeds = optimizer.nSeeds;
    optimizerParams.pso_weight_pbest = optimizer.psoWeightPbest;
    optimizerParams.pso_weight_gbest = optimizer.psoWeightGbest;
    optimizerParams.pso_weight_mut = optimizer.psoWeightMut;
    optimizerParams.immigrant_fraction = optimizer.immigrantFraction;
  }

  const budget: Record<string, unknown> = {
    max_evaluations: config.budget.maxEvaluations,
    max_accepted: config.budget.maxAccepted,
    max_generations: config.budget.maxGenerations,
  };
  if (config.budget.targetNovelty != null) budget.target_novelty = config.budget.targetNovelty;
  if (config.budget.noImprovementRounds != null) budget.no_improvement_rounds = config.budget.noImprovementRounds;

  const payload: Record<string, unknown> = {
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
  if (config.searchTarget.anchorFrames.length > 0) {
    payload.anchor_frames = config.searchTarget.anchorFrames;
    payload.region_radius = config.searchTarget.regionRadius;
  }
  return payload;
}

// Builds the generation.submit RPC payload from the UI config. The shape this
// emits is the single serialization fact: the page renders controls, the
// backend parses them back (generation.models.parse_request).
import type { GenerationConfig } from "./types";

export interface SubmissionCheck {
  ok: boolean;
  reason?: string;
}

export function validateConfig(config: GenerationConfig): SubmissionCheck {
  if (!config.source.datasetId) return { ok: false, reason: "Select a source dataset" };
  if (!config.source.descriptorRunId) return { ok: false, reason: "Select a descriptor run" };
  const space = config.searchSpace;
  if (!space.atomicDisplacement && !space.isotropicStrain && !space.anisotropicStrain && !space.cellShear) {
    return { ok: false, reason: "Enable at least one structure operator" };
  }
  if (config.constraints.minDistanceMode === "absolute" && config.constraints.minDistanceAbsolute <= 0) {
    return { ok: false, reason: "Minimum distance must be positive" };
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
  if (space.atomicDisplacement) operators.atomic_displacement = { enabled: true, max_sigma: space.maxDisplacement };
  if (space.isotropicStrain) operators.isotropic_strain = { enabled: true, max_strain: space.maxStrain };
  if (space.anisotropicStrain) operators.anisotropic_strain = { enabled: true, max_strain: space.maxStrain };
  if (space.cellShear) operators.cell_shear = { enabled: true, max_shear: space.maxShear };

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

  const optimizer = config.optimizer;
  const optimizerParams: Record<string, unknown> = {};
  if (optimizer.type === "random") {
    optimizerParams.children_per_seed = optimizer.childrenPerSeed;
    optimizerParams.batch_accept = optimizer.batchAccept;
    optimizerParams.n_seeds = optimizer.nSeeds;
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

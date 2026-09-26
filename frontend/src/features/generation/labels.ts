import type { GenerationObjectiveType, GenerationOptimizer } from "./types";

export const GENERATION_OBJECTIVE_LABELS: Record<GenerationObjectiveType, string> = {
  novelty: "Structure novelty",
  local_environment_novelty: "Local-environment novelty",
  coverage: "Coverage gain",
  composite: "Composite objective",
};

export const GENERATION_OPTIMIZER_LABELS: Record<GenerationOptimizer, string> = {
  random: "Descriptor-guided random",
  genetic: "Genetic algorithm (mutation-only)",
  pso: "Particle swarm (memory)",
};

export const GENERATION_STOP_REASON_LABELS: Record<string, string> = {
  cancelled: "Cancelled",
  max_generations: "Generation limit reached",
  max_evaluations: "Evaluation limit reached",
  max_accepted: "Accepted-structure limit reached",
  no_improvement: "No improvement for several rounds",
  target_novelty: "Target novelty reached",
  discovery_saturated: "Novel-structure discovery saturated",
};

export const GENERATION_GEOMETRY_REJECTION_LABELS: Record<string, string> = {
  non_finite_positions: "Non-finite positions",
  non_finite_cell: "Non-finite cell",
  singular_periodic_cell: "Singular periodic cell",
  periodic_cell_too_skewed: "Periodic cell search limit",
  empty_structure: "Empty structure",
  minimum_distance: "Minimum-distance violation",
  displacement_limit: "Displacement limit",
  volume_change_limit: "Volume-change limit",
  volume_per_atom_range: "Volume per atom out of range",
  composition_lock: "Composition lock",
  atom_count_lock: "Atom-count lock",
  other: "Other geometry constraint",
  unknown: "Unspecified geometry constraint",
};

import type { GenerationObjectiveType, GenerationOptimizer } from "./types";

export const GENERATION_OBJECTIVE_LABELS: Record<GenerationObjectiveType, string> = {
  novelty: "Structure novelty",
  local_environment_novelty: "Local-environment novelty",
  coverage: "Coverage gain",
  target_region: "Target-region search",
  composite: "Composite objective",
};

export const GENERATION_OPTIMIZER_LABELS: Record<GenerationOptimizer, string> = {
  random: "Descriptor-guided random",
  genetic: "Genetic algorithm",
  pso: "Particle swarm optimization",
  external: "External optimizer",
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

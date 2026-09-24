// Generation page contracts. Discriminated unions keep every illegal state
// unrepresentable: an OptimizerConfig is exactly one optimizer's parameters,
// never a bag of partially-filled ga*/pso* optionals.

export type GenerationOptimizer = "random" | "genetic" | "pso" | "external";

export type GenerationObjectiveType =
  | "novelty"
  | "local_environment_novelty"
  | "coverage"
  | "target_region"
  | "composite";

export type LocalAggregation = "mean" | "top_fraction_mean" | "quantile" | "max";

export type MinDistanceMode = "none" | "absolute" | "covalent";

export type SeedMode = "fixed" | "random";

export interface ObjectiveConfig {
  type: GenerationObjectiveType;
  aggregation: LocalAggregation;
  topFraction: number;
  quantile: number;
  noveltyThreshold: number;
  structureWeight: number;
  localWeight: number;
  scaling: "raw" | "standardized" | "robust";
}

export interface SearchSpaceConfig {
  atomicDisplacement: boolean;
  maxDisplacement: number; // Å
  isotropicStrain: boolean;
  anisotropicStrain: boolean;
  maxStrain: number; // fraction
  cellShear: boolean;
  maxShear: number; // fraction
}

export interface ConstraintConfig {
  minDistanceMode: MinDistanceMode;
  minDistanceFactor: number; // covalent multiplier
  minDistanceAbsolute: number; // Å, absolute mode
  maxVolumeChange: number; // fraction
  compositionLocked: boolean;
  atomCountLocked: boolean;
}

export type OptimizerConfig =
  | { type: "random"; childrenPerSeed: number; batchAccept: number; nSeeds: number };

export interface BudgetConfig {
  maxEvaluations: number;
  maxAccepted: number;
  maxGenerations: number;
  targetNovelty: number | null;
  noImprovementRounds: number | null;
}

export interface SourceConfig {
  datasetId: string | null;
  descriptorRunId: string | null;
  seedViewId: string | null;
}

export interface GenerationConfig {
  source: SourceConfig;
  objective: ObjectiveConfig;
  searchSpace: SearchSpaceConfig;
  constraints: ConstraintConfig;
  optimizer: OptimizerConfig;
  budget: BudgetConfig;
  seedMode: SeedMode;
  seed: number;
}

export interface GenerationRound {
  generation: number;
  evaluations: number;
  proposed: number;
  rejected_geometry: number;
  rejected_geometry_by_reason?: Record<string, number>;
  rejected_duplicate: number;
  accepted: number;
  best_fitness: number;
  best_novelty: number | null;
  mean_novelty: number | null;
  coverage_radius: number;
  novel_environments: number;
}

export interface GenerationPreview {
  status: string;
  stopped_by: string | null;
  accepted: number;
  evaluations: number;
  rounds: GenerationRound[];
}

export interface GenerationRow {
  id: string;
  dataset_id: string;
  descriptor_run_id: string | null;
  descriptor_name?: string | null;
  optimizer: string;
  objective: string;
  params_json: string;
  status: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  evaluations: number;
  accepted_count: number;
  result_path: string | null;
  cache_key: string | null;
  preview?: GenerationPreview | null;
  artifact_complete?: boolean;
}

export interface GenerationCatalog {
  optimizers: { name: string; params: Record<string, unknown> }[];
  objectives: { name: string; params: Record<string, unknown> }[];
  operators: { name: string; params: Record<string, unknown> }[];
  constraints: Record<string, unknown>;
  budget: Record<string, unknown>;
}

export type GenerationPhase = "config" | "running" | "results";

export interface GenerationPcaDiscovery {
  original_structures: number;
  accepted_structures: number;
  original_environments: number;
  generated_environments: number;
  novel_environments: number;
}

export interface GenerationPca {
  x_label: string;
  y_label: string;
  original: [number, number][];
  original_frames: number[];
  evaluated: [number, number][];
  evaluated_generation: number[];
  evaluated_novelty: (number | null)[];
  evaluated_accepted: boolean[];
  discovery: GenerationPcaDiscovery;
}

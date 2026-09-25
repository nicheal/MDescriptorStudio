// Generation page contracts. Discriminated unions keep every illegal state
// unrepresentable: an OptimizerConfig is exactly one optimizer's parameters,
// never a bag of partially-filled optionals. The vocabulary below is the
// *runtime* vocabulary — roadmap items (GA/PSO/target-region) join when the
// backend catalog actually offers them, never earlier.

export type GenerationOptimizer = "random";

export type GenerationObjectiveType =
  | "novelty"
  | "local_environment_novelty"
  | "coverage"
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
  /** Largest per-structure Gaussian σ in Å — bounds the distribution, not any single displacement. */
  maxDisplacement: number; // Å (σ cap)
  /** Hard per-atom displacement bound in Å; null = unbounded Gaussian tail. */
  hardCutoff: number | null;
  isotropicStrain: boolean;
  anisotropicStrain: boolean;
  maxStrain: number; // fraction
  cellShear: boolean;
  maxShear: number; // fraction
  vacancy: boolean;
  interstitialAtom: boolean;
  interstitialElement: string;
  substitution: boolean;
  substitutionElement: string;
  antisiteSwap: boolean;
}

export interface ConstraintConfig {
  minDistanceMode: MinDistanceMode;
  minDistanceFactor: number; // covalent multiplier
  minDistanceAbsolute: number; // Å, absolute mode
  minDistancePairs: string; // comma-separated element-pair cutoffs, e.g. C-C=1.5
  maxVolumeChange: number; // fraction
  minVolumePerAtom: number | null; // Å³/atom, fully periodic structures only
  maxVolumePerAtom: number | null; // Å³/atom, fully periodic structures only
  compositionLocked: boolean;
  atomCountLocked: boolean;
}

export type OptimizerConfig =
  | { type: "random"; childrenPerSeed: number; batchAccept: number; nSeeds: number; reuseAcceptedSeeds: boolean };

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
  /** Covering radius of the accepted set over the reference domain; null until the first accept. */
  coverage_radius: number | null;
  novel_environments: number;
  /** Selection-order-deduplicated novel environments; null when the objective emits no counts. */
  unique_novel_environments: number | null;
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

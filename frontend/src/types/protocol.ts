// Protocol types mirroring docs/plan/02-IPC_PROTOCOL.md
export const PROTOCOL_VERSION = 1;

export interface ErrorFrame {
  code: string;
  message: string;
  error_id?: string;
  details?: Record<string, unknown>;
}

export interface DatasetMeta {
  id: string;
  name: string;
  format: "deepmd" | "extxyz";
  source_path: string;
  number_of_frames: number;
  elements: string[];
  properties: {
    energy?: { per_structure?: boolean; per_atom?: boolean };
    forces?: { per_atom?: boolean };
    virial?: { per_structure?: boolean };
  };
  periodicity: {
    fully_periodic: boolean;
    isolated: boolean;
    mixed: boolean;
    flags: string[];
  };
  fingerprint: string;
  file_size: number | null;
  created_at: string;
  last_scan_at: string | null;
  cache_valid: boolean;
}

export interface Hist {
  edges: number[];
  counts: number[];
}

export interface Stats {
  structures: number;
  atoms_total: number;
  elements: { symbol: string; count: number }[];
  /** structures grouped by their exact element combination (unary/binary/…);
   *  undefined on caches computed before this field existed */
  compositions?: { elements: string[]; count: number }[];
  /** structures grouped by exact stoichiometry (Hill-notation formula, actual
   *  atom counts); most common first; undefined on legacy caches */
  formulas?: { formula: string; elements: string[]; count: number }[];
  /** per-element histogram of how many atoms of that element each structure
   *  contains (integer-aligned bins); undefined on legacy caches */
  element_atom_counts?: Record<string, Hist | null>;
  atoms_per_structure: Hist | null;
  atoms_per_structure_summary: Summary | null;
  energy_per_atom: Hist | null;
  energy_per_atom_summary: Summary | null;
  force_magnitude: Hist | null;
  force_magnitude_summary: Summary | null;
  max_force: Hist | null;
  max_force_summary: Summary | null;
  min_distance: Hist | null;
  min_distance_summary: Summary | null;
  volume: Hist | null;
  volume_summary: Summary | null;
  properties: {
    energy: { per_structure: boolean; per_atom: boolean };
    forces: { per_atom: boolean };
    virial: { per_structure: boolean };
  };
  periodicity: {
    fully_periodic: boolean;
    isolated: boolean;
    mixed: boolean;
    flags: string[];
  };
  /** present in scans since the data-health pass; undefined on legacy caches */
  health?: DatasetHealth;
  /** frame indices behind the health counts (original file positions, capped
   * per check); undefined on caches older than the findings pass */
  health_findings?: HealthFindings;
  /** frames soft-deleted by the user; statistics describe the remainder */
  excluded_frames?: { count: number; indices: number[] };
}

export interface HealthFindings {
  cap: number;
  missing_values: number[];
  invalid_cell: number[];
  duplicate_structures: number[];
  /** parallel to duplicate_structures: first-occurrence frame behind each
   * flagged copy; absent on caches from before the mapping existed */
  duplicate_structures_of?: number[];
  extreme_force: number[];
  nonphysical_structures: number[];
  net_force: number[];
}

/** Per-frame summary behind a health check (dataset.findings). */
export interface FindingsRow {
  index: number;
  natoms: number;
  formula: string;
  force_max: number | null;
  volume: number | null;
  /** shortest interatomic distance (Å); computed only for the
   * non-physical-structures check, null elsewhere */
  min_distance: number | null;
  /** declared properties this frame lacks (missing-values finding) */
  missing_props?: string[];
  excluded: boolean;
}

export interface DatasetHealth {
  /** frames missing at least one property other frames carry */
  missing_values: number;
  /** frames missing each property the check watches (declared properties
   * only); present since the missing-values breakdown, undefined on legacy
   * caches */
  missing_by_property?: Partial<Record<"energy" | "forces" | "virial", number>>;
  /** frames claiming periodicity with a non-positive/degenerate cell */
  invalid_cell: number;
  /** redundant copies beyond the first, exact content hash */
  duplicate_structures: number;
  /** frames with any atom |F| above extreme_force_threshold (eV/Å) */
  extreme_force: number;
  extreme_force_threshold: number;
  /** frames with an atom pair (periodic images included) closer than
   * short_contact_coefficient × the pair's covalent-radii sum —
   * non-physical structures (NepTrainKit's bond-length filter) */
  nonphysical_structures: number;
  short_contact_coefficient: number;
  /** frames with net force ‖ΣF‖ above net_force_threshold (eV/Å) */
  net_force: number;
  net_force_threshold: number;
}

export interface Summary {
  min: number;
  max: number;
  mean: number;
  median: number;
}

export interface FramePayload {
  index: number;
  natoms: number;
  formula: string;
  xyz: string;
  atom_rows: {
    i: number;
    el: string;
    x: number;
    y: number;
    z: number;
    fx: number | null;
    fy: number | null;
    fz: number | null;
    f: number | null;
  }[];
  energy: number | null;
  energy_per_atom: number | null;
  force_max: number | null;
  /** whether the source frame carries a virial (missing-values transparency) */
  virial_present?: boolean;
  /** virial tensor W (eV) flattened row-major [Wxx, Wxy, Wxz, Wyx, …]; null when the frame lacks one */
  virial: number[] | null;
  volume: number | null;
  pbc: string;
  cell: number[] | null;
  ghost_count: number;
  /** parent real-atom index for each optional periodic image appended to xyz; local-shell viewers may consume these images */
  ghost_parents?: number[];
  bond_cutoff: number;
}

export interface DescriptorInfo {
  name: string;
  display_name: string;
  description: string;
  schema_version: number;
  descriptor_version: string;
  level: string;
  backend: string;
  execution_engine: string;
  category: string;
  capabilities: string[];
  input: {
    periodicity: string[];
    mixed_periodicity: boolean;
    spin: boolean;
    charge_spin: boolean;
  };
}

export interface ParamSchema {
  type: string;
  display_name?: string;
  description?: string;
  default?: unknown;
  required?: boolean;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  enum?: string[];
  unit?: string;
  items?: ParamSchema;
  properties?: Record<string, ParamSchema>;
}

export interface DescriptorSchema {
  schema_version: number;
  name: string;
  descriptor_version: string;
  display_name: string;
  description: string;
  category: string;
  level: string;
  backend: string;
  execution_engine: string;
  capabilities: string[];
  parameters: Record<string, ParamSchema>;
  execution: {
    devices: string[];
    num_threads: boolean;
    cooperative_cancel: boolean;
  };
  input: {
    periodicity: string[];
    mixed_periodicity: boolean;
    spin: boolean;
    charge_spin: boolean;
  };
  output: { dtypes: string[]; sparse: boolean };
  asset: {
    policy: "none" | "required" | string;
    parameter: string | null;
    allow_external: boolean;
    bundled_resources: string[];
    file_extensions: string[];
  };
}

export interface JobRow {
  id: string;
  job_type: string;
  dataset_id: string | null;
  descriptor_run_id: string | null;
  analysis_run_id?: string | null;
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
  progress: number;
  completed: number | null;
  total: number | null;
  message: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** 1-based position within its category pool; only present while QUEUED. */
  queue_position?: number;
}

export interface JobFinishedData {
  job_id: string;
  status: "COMPLETED" | "FAILED" | "CANCELLED";
  result: {
    dataset_id?: string;
    run_id?: string;
    shape?: number[];
    dtype?: string;
    level?: string;
    feature_count?: number;
    analysis_id?: string;
    n_points?: number;
  } | null;
  error: ErrorFrame | null;
}

export interface RunRow {
  id: string;
  dataset_id: string;
  dataset_name: string | null;
  descriptor_name: string;
  engine_version: string;
  scope: string;
  device?: string | null;
  status: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  result_path: string | null;
  shape?: string | null;
  metadata?: Record<string, unknown>;
  memory_peak_bytes?: number | null;
}

export type AnalysisStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED" | "STALE";

export interface AnalysisRow {
  id: string;
  descriptor_run_id: string;
  analysis_type: string;
  status: AnalysisStatus | string;
  parameters?: Record<string, unknown>;
  input_run_ids?: string[];
  dataset_ids?: string[];
  cache_key?: string | null;
  schema_version?: number;
  algorithm_version?: string | null;
  preprocessing?: Record<string, unknown>;
  warnings?: string[];
  artifact_manifest?: Record<string, unknown>;
  stale_reason?: string | null;
  result_path?: string | null;
  created_at: string;
  finished_at?: string | null;
  preview?: Record<string, unknown>;
}

export interface AnalysisJobResponse {
  job_id: string | null;
  analysis_id: string;
  cache: {
    existing_analysis_id: string;
    status?: AnalysisStatus;
    cache_key?: string;
  } | null;
}

export interface AnalysisPreview {
  analysis_id: string;
  kind?: string;
  points?: Record<string, unknown>[];
  rows?: Record<string, unknown>[];
  selected?: Record<string, unknown>[];
  [key: string]: unknown;
}

export interface AnalysisChunk {
  analysis_id: string;
  array: string;
  offset: number;
  next_offset: number;
  shape: number[];
  dtype: string;
  data: unknown[];
}

export interface PcaPayload {
  analysis_id: string;
  run_id: string;
  mode?: "structure" | "atom";
  n_points: number;
  points: {
    i: number;
    frame: number;
    atom?: number;
    pc1: number;
    pc2: number;
    energy: number | null;
    force_max: number | null;
    volume: number | null;
  }[];
  explained_variance: number[];
  x_label: string;
  y_label: string;
}

export interface PcaAnalysisResponse {
  job_id: string | null;
  analysis_id: string;
  cache: {
    existing_analysis_id: string;
    status?: "QUEUED" | "RUNNING" | "COMPLETED";
  } | null;
}

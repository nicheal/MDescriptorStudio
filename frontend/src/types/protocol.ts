// Protocol types mirroring docs/plan/02-IPC_PROTOCOL.md
export const PROTOCOL_VERSION = 1;

export interface ErrorFrame {
  code: string;
  message: string;
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
  atoms_per_structure: Hist | null;
  atoms_per_structure_summary: Summary | null;
  energy_per_atom: Hist | null;
  energy_per_atom_summary: Summary | null;
  force_magnitude: Hist | null;
  force_magnitude_summary: Summary | null;
  max_force: Hist | null;
  max_force_summary: Summary | null;
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
}

export interface DatasetHealth {
  /** frames missing at least one property other frames carry */
  missing_values: number;
  /** frames claiming periodicity with a non-positive/degenerate cell */
  invalid_cell: number;
  /** redundant copies beyond the first, exact content hash */
  duplicate_structures: number;
  /** frames with any atom |F| above extreme_force_threshold (eV/Å) */
  extreme_force: number;
  extreme_force_threshold: number;
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
  volume: number | null;
  pbc: string;
  cell: number[] | null;
  ghost_count: number;
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
  status: string;
  created_at: string;
  result_path: string | null;
  shape?: string | null;
  metadata?: Record<string, unknown>;
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

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
}

export interface DescriptorInfo {
  name: string;
  display_name: string;
  level: string;
  backend: string;
  category: string;
  capabilities: string[];
}

export interface ParamSchema {
  type: string;
  description?: string;
  default?: unknown;
  required?: boolean;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  enum?: string[];
  unit?: string;
  items?: { type: string };
  properties?: Record<string, ParamSchema>;
}

export interface DescriptorSchema {
  schema_version: number;
  name: string;
  display_name: string;
  description: string;
  category: string;
  level: string;
  backend: string;
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
  shape?: string;
  metadata?: Record<string, unknown>;
}

export interface PcaPayload {
  analysis_id: string;
  run_id: string;
  n_points: number;
  points: {
    i: number;
    frame: number;
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

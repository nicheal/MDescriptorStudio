// Dev-only browser preview of the real App without the Tauri shell.
// Open via `npm run dev` → http://localhost:1420/preview.html
// It stubs window.__TAURI_INTERNALS__ (invoke/transformCallback) and serves a
// tiny in-browser mock backend over the same NDJSON protocol. Not part of the
// production bundle (vite builds only index.html's entry).
import "./global.css";
import { setAppIcon } from "./brand";

setAppIcon();

interface MockFrame {
  protocol_version?: number;
  id?: number;
  method?: string;
  params?: Record<string, unknown>;
}

type Handler = (params: Record<string, unknown>) => unknown;

// ---------- gaussian helpers ----------
function gauss(mu: number, sigma: number): number {
  let u = 0;
  let v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return mu + sigma * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function hist(lo: number, hi: number, bins: number, mu: number, sigma: number) {
  const edges: number[] = [];
  const counts: number[] = [];
  for (let i = 0; i <= bins; i++) edges.push(lo + ((hi - lo) * i) / bins);
  for (let i = 0; i < bins; i++) {
    const mid = (edges[i] + edges[i + 1]) / 2;
    counts.push(Math.max(0, Math.round(2600 * Math.exp(-((mid - mu) ** 2) / (2 * sigma ** 2)))));
  }
  return { edges, counts };
}

// ---------- mock datasets ----------
const NOW = Date.now();
const DS = [
  {
    id: "ds-gaas",
    name: "GaAs Training Set",
    format: "deepmd",
    source_path: "D:\\Datasets\\GaAs_Training",
    number_of_frames: 12480,
    elements: ["Ga", "As"],
    properties: {
      energy: { per_structure: true, per_atom: true },
      forces: { per_atom: true },
      virial: { per_structure: true },
    },
    periodicity: { fully_periodic: true, isolated: false, mixed: false, flags: ["X", "Y", "Z"] },
    fingerprint: "mock-gaas",
    file_size: 152 * 1024 ** 3,
    created_at: "2024-05-18T14:32:21Z",
    last_scan_at: new Date(NOW - 24 * 60000).toISOString(),
    cache_valid: true,
  },
  {
    id: "ds-si",
    name: "Si Training Set",
    format: "extxyz",
    source_path: "D:\\Datasets\\Si.extxyz",
    number_of_frames: 6320,
    elements: ["Si"],
    properties: { energy: { per_structure: true }, forces: { per_atom: true } },
    periodicity: { fully_periodic: true, isolated: false, mixed: false, flags: ["X", "Y", "Z"] },
    fingerprint: "mock-si",
    file_size: 41 * 1024 ** 3,
    created_at: "2024-04-02T09:11:45Z",
    last_scan_at: new Date(NOW - 26 * 3600_000).toISOString(),
    cache_valid: true,
  },
  {
    id: "ds-mos2",
    name: "MoS2 AIMD",
    format: "extxyz",
    source_path: "D:\\Datasets\\MoS2_AIMD.xyz",
    number_of_frames: 2000,
    elements: ["Mo", "S"],
    properties: { energy: { per_structure: true } },
    periodicity: { fully_periodic: true, isolated: false, mixed: false, flags: ["X", "Y", "Z"] },
    fingerprint: "mock-mos2",
    file_size: 3.2 * 1024 ** 3,
    created_at: "2024-06-11T18:03:10Z",
    last_scan_at: new Date(NOW - 3 * 24 * 3600_000).toISOString(),
    cache_valid: true,
  },
  {
    id: "ds-al2o3",
    name: "Al2O3 Dataset",
    format: "deepmd",
    source_path: "D:\\Datasets\\Al2O3_deepmd",
    number_of_frames: 8500,
    elements: ["Al", "O"],
    properties: {
      energy: { per_structure: true, per_atom: true },
      forces: { per_atom: true },
      virial: { per_structure: true },
    },
    periodicity: { fully_periodic: true, isolated: false, mixed: false, flags: ["X", "Y", "Z"] },
    fingerprint: "mock-al2o3",
    file_size: 128 * 1024 ** 3,
    created_at: "2024-03-21T11:47:00Z",
    last_scan_at: null,
    cache_valid: false,
  },
];

const STATS: Record<string, unknown> = {
  "ds-gaas": {
    structures: 12480,
    atoms_total: 798720,
    elements: [
      { symbol: "Ga", count: 397762 },
      { symbol: "As", count: 400958 },
    ],
    atoms_per_structure: hist(56, 72, 16, 64, 2),
    atoms_per_structure_summary: { min: 64, max: 64, mean: 64, median: 64 },
    energy_per_atom: hist(-6, -1, 50, -3.8, 0.45),
    energy_per_atom_summary: { min: -5.6, max: -2.1, mean: -3.82, median: -3.8 },
    force_magnitude: hist(0, 10, 50, 2.6, 1.1),
    force_magnitude_summary: { min: 0.02, max: 9.4, mean: 2.63, median: 2.5 },
    max_force: hist(0, 10, 50, 2.8, 1.2),
    max_force_summary: { min: 0.3, max: 9.9, mean: 2.9, median: 2.7 },
    volume: hist(400, 1000, 40, 650, 70),
    volume_summary: { min: 405, max: 995, mean: 651, median: 648 },
    properties: { energy: { per_structure: true, per_atom: true }, forces: { per_atom: true }, virial: { per_structure: true } },
    periodicity: { fully_periodic: true, isolated: false, mixed: false, flags: ["X", "Y", "Z"] },
    health: {
      missing_values: 0,
      invalid_cell: 0,
      duplicate_structures: 2,
      extreme_force: 3,
      extreme_force_threshold: 50,
    },
  },
};

const JOB_ROWS = [
  {
    id: "job-dpa2",
    job_type: "descriptor.compute",
    dataset_id: "ds-gaas",
    descriptor_run_id: "run-dpa2",
    status: "RUNNING",
    progress: 0.72,
    completed: 9000,
    total: 12480,
    message: "Computing DPA-2 descriptors",
    error: null,
    created_at: new Date(NOW - 5 * 60000).toISOString(),
    started_at: new Date(NOW - 4 * 60000).toISOString(),
    finished_at: null,
  },
  {
    id: "job-soap",
    job_type: "descriptor.compute",
    dataset_id: "ds-gaas",
    descriptor_run_id: null,
    status: "QUEUED",
    progress: 0,
    completed: null,
    total: null,
    message: null,
    error: null,
    created_at: new Date(NOW - 2 * 60000).toISOString(),
    started_at: null,
    finished_at: null,
  },
  {
    id: "job-stats",
    job_type: "dataset.statistics",
    dataset_id: "ds-gaas",
    descriptor_run_id: null,
    status: "COMPLETED",
    progress: 1,
    completed: 12480,
    total: 12480,
    message: null,
    error: null,
    created_at: new Date(NOW - 30 * 60000).toISOString(),
    started_at: new Date(NOW - 30 * 60000).toISOString(),
    finished_at: new Date(NOW - 29 * 60000).toISOString(),
  },
  {
    id: "job-pca",
    job_type: "analysis.pca",
    dataset_id: "ds-gaas",
    descriptor_run_id: "run-dpa2",
    status: "COMPLETED",
    progress: 1,
    completed: 12480,
    total: 12480,
    message: null,
    error: null,
    created_at: new Date(NOW - 60 * 60000).toISOString(),
    started_at: new Date(NOW - 60 * 60000).toISOString(),
    finished_at: new Date(NOW - 58 * 60000).toISOString(),
  },
];

let mockLatestAnalysisId = "ana-mock-analysis";
let mockLatestAnalysisKind = "projection";
let mockAnalysisArrays: Record<string, unknown[]> = {};
let mockLatestPcaMode = "structure";
let mockLatestPcaPreprocess = "raw";
let mockLatestAcquisitionMethod = "novelty_fps";

function mockAnalysisSubmit(jobId: string, analysisId = "ana-mock-analysis", kind = "projection") {
  mockLatestAnalysisId = analysisId;
  mockLatestAnalysisKind = kind;
  mockAnalysisArrays = {};
  window.setTimeout(() => {
    mockEmit("job.finished", { job_id: jobId, status: "COMPLETED", result: { analysis_id: analysisId }, error: null });
  }, 800);
  return { job_id: jobId, analysis_id: analysisId, cache: null };
}

function mockAnalysisPoints(kind: string, count = 180) {
  return Array.from({ length: count }, (_, i) => {
    const label = i % 4;
    const row = kind === "local_diversity" ? i % 8 : undefined;
    return {
      i,
      frame: Math.floor(i / (row == null ? 1 : 8)),
      row,
      element: row == null ? undefined : (i % 2 ? 33 : 31),
      x: Math.sin(i / 12) * 3 + label * 0.45,
      y: Math.cos(i / 17) * 2 + label * 0.25,
      label: kind === "outliers" ? (i % 23 === 0 ? 1 : 0) : kind === "local_diversity" ? (i % 31 === 0 ? 2 : i % 11 === 0 ? 1 : 0) : label,
      cluster: label,
      score: 0.1 + Math.abs(Math.sin(i / 9)),
      distance: 0.08 + Math.abs(Math.cos(i / 14)),
      novelty: kind === "acquisition" ? 0.15 + Math.abs(Math.sin(i / 13)) * 1.2 : undefined,
      uncertainty: kind === "acquisition" ? 0.2 + Math.abs(Math.cos(i / 16)) * 1.5 : undefined,
      diversity: kind === "acquisition" ? 0.1 + Math.abs(Math.sin(i / 19)) : undefined,
      coordination: kind === "local_diversity" ? 3 + (i % 5) : undefined,
      sample_id: row == null ? `frame:${i}` : `frame:${Math.floor(i / 8)}:row:${row}`,
    };
  });
}

function mockOverviewPreview() {
  if (mockLatestAnalysisKind === "feature_variance") {
    return { analysis_id: mockLatestAnalysisId, kind: "feature_variance", top_k: 12, top_indices: [12, 3, 41, 7, 28, 16, 55, 2, 36, 19, 64, 8], top_values: [2.84, 2.17, 1.92, 1.68, 1.51, 1.34, 1.18, 1.04, 0.92, 0.81, 0.74, 0.68] };
  }
  if (mockLatestAnalysisKind === "feature_correlation") {
    const featureIndices = [2, 3, 7, 8, 12, 16, 28, 41];
    mockAnalysisArrays = {
      correlation_feature_indices: featureIndices,
      correlation_matrix: featureIndices.map((_, i) => featureIndices.map((__, j) => i === j ? 1 : Number((Math.cos((i + 1) * (j + 1)) * 0.82).toFixed(4)))),
    };
    return {
      analysis_id: mockLatestAnalysisId,
      kind: "feature_correlation",
      feature_count: 256,
      zero_variance_count: 3,
      highly_correlated_pairs: 7,
      redundant_feature_count: 5,
      redundancy_ratio: 0.0195,
      pairs: [
        { feature_a: 12, feature_b: 41, correlation: 0.94 },
        { feature_a: 3, feature_b: 7, correlation: -0.91 },
        { feature_a: 28, feature_b: 55, correlation: 0.87 },
        { feature_a: 16, feature_b: 64, correlation: -0.82 },
        { feature_a: 2, feature_b: 36, correlation: 0.77 },
        { feature_a: 19, feature_b: 31, correlation: -0.71 },
        { feature_a: 8, feature_b: 44, correlation: 0.66 },
      ],
    };
  }
  if (mockLatestAnalysisKind === "similarity") {
    const rows = Array.from({ length: 18 }, (_, index) => ({ rank: index + 1, i: index + 1, frame: index + 1, sample_id: `frame:${index + 1}`, distance: 0.02 + index * 0.035, similarity: 0.98 - index * 0.035 }));
    return { analysis_id: mockLatestAnalysisId, kind: "similarity", metric: "cosine", query_index: 0, rows };
  }
  if (mockLatestAnalysisKind === "pairwise_similarity") {
    const size = 36;
    mockAnalysisArrays = { similarity_matrix: Array.from({ length: size }, (_, i) => Array.from({ length: size }, (__, j) => Number(Math.exp(-Math.abs(i - j) / 8).toFixed(4)))) };
    return { analysis_id: mockLatestAnalysisId, kind: "pairwise_similarity", metric: "cosine", sample_count: size, distance_min: 0, distance_max: 1 };
  }
  if (["clusters", "outliers", "sampling", "acquisition", "local_diversity"].includes(mockLatestAnalysisKind)) {
    const points = mockAnalysisPoints(mockLatestAnalysisKind);
    const selected = points.filter((_, index) => index % 17 === 0).map(({ i, frame, row, sample_id }) => ({ i, frame, row, sample_id }));
    if (mockLatestAnalysisKind === "local_diversity") {
      mockAnalysisArrays = {
        coordination: points.map((point) => point.coordination ?? 0),
        neighbor_distances: Array.from({ length: 420 }, (_, index) => 2.1 + Math.abs(Math.sin(index / 17)) * 1.4),
      };
      return { analysis_id: mockLatestAnalysisId, kind: "local_diversity", sample_count: points.length, cutoff: 3.0, mean_coordination: 5.1, max_coordination: 7, categories: ["main", "distorted", "outlier"], element_summary: [{ element: 31, samples: 90, clusters: 4, distorted: 8, outliers: 3, effective_dimension: 6.2 }, { element: 33, samples: 90, clusters: 4, distorted: 9, outliers: 3, effective_dimension: 5.8 }], points, rows: points };
    }
    return { analysis_id: mockLatestAnalysisId, kind: mockLatestAnalysisKind, algorithm: mockLatestAnalysisKind === "acquisition" ? mockLatestAcquisitionMethod : mockLatestAnalysisKind, uncertainty_method: mockLatestAcquisitionMethod === "uncertainty_diversity" ? "knn_extrapolation" : null, cluster_count: 4, noise_count: 3, outlier_count: 8, selected_count: selected.length, candidate_pool: points.length, mean_selected_novelty: 0.82, mean_selected_uncertainty: 1.04, points, rows: points, selected };
  }
  if (["coverage", "overlap", "drift"].includes(mockLatestAnalysisKind)) {
    const reference = Array.from({ length: 100 }, (_, i) => [Math.sin(i / 9) * 2, Math.cos(i / 13) * 1.5]);
    const query = Array.from({ length: 80 }, (_, i) => [Math.sin(i / 8) * 2 + 0.6, Math.cos(i / 11) * 1.5 + 0.35]);
    const labels = query.map((_, i) => i < 52 ? 0 : i < 70 ? 1 : 2);
    mockAnalysisArrays = { projection_coords: [...reference, ...query], projection_source: [...reference.map(() => 0), ...query.map(() => 1)], labels };
    const rows = query.map((_, i) => ({ i, frame: i, sample_id: `frame:${i}`, labels: labels[i], distances: Number((0.1 + i / 85).toFixed(4)), reference_i: i % reference.length }));
    return mockLatestAnalysisKind === "overlap"
      ? { analysis_id: mockLatestAnalysisId, kind: "overlap", categories: ["near_duplicate", "highly_similar", "independent"], near_duplicates: 52, highly_similar: 18, independent: 10, overlap_fraction: 0.875, mean_distance: 0.43, rows }
      : { analysis_id: mockLatestAnalysisId, kind: mockLatestAnalysisKind, categories: ["covered", "marginal", "out_of_coverage"], covered: 52, marginal: 18, out_of_coverage: 10, mean_distance: 0.43, mmd: 0.18, centroid_distance: 0.37, covariance_shift: 0.12, rows };
  }
  if (mockLatestAnalysisKind === "compare") {
    const pairs = Array.from({ length: 800 }, (_, i) => 0.1 + Math.abs(Math.sin(i / 21)) * 2.4);
    const coords = Array.from({ length: 120 }, (_, i) => [Math.sin(i / 10) * 2, Math.cos(i / 14) * 1.6]);
    mockAnalysisArrays = { left_pair_distances: pairs, right_pair_distances: pairs.map((value, i) => value * 0.94 + Math.sin(i / 12) * 0.08), left_coords: coords, right_coords: coords.map(([x, y]) => [x * 0.96 + 0.2, y * 1.04 - 0.1]) };
    return { analysis_id: mockLatestAnalysisId, kind: "compare", pairwise_distance_pearson: 0.96, pairwise_distance_spearman: 0.94, neighbor_overlap: 0.81, clustering_stability: 0.87, pca_topology_error: 0.12, left_effective_dimension: 8.4, right_effective_dimension: 9.1 };
  }
  if (mockLatestAnalysisKind === "mantel") {
    const pairs = Array.from({ length: 800 }, (_, i) => 0.1 + Math.abs(Math.sin(i / 21)) * 2.4);
    const rightPairs = pairs.map((value, i) => value * 0.93 + Math.sin(i / 12) * 0.08);
    const nullDistribution = Array.from({ length: 99 }, (_, i) => 0.03 + Math.sin(i / 8) * 0.12);
    mockAnalysisArrays = { left_pair_distances: pairs, right_pair_distances: rightPairs, null_distribution: nullDistribution, sample_indices: Array.from({ length: 40 }, (_, i) => i) };
    return { analysis_id: mockLatestAnalysisId, kind: "mantel", method: "pearson", metric: "euclidean", alternative: "two-sided", statistic: 0.91, p_value: 0.02, permutations: 99, sample_count: 40, pair_count: pairs.length, significant_at_05: true };
  }
  if (mockLatestAnalysisKind === "property_correlation") {
    const targets = Array.from({ length: 160 }, (_, i) => -4.2 + Math.sin(i / 17) * 0.8);
    const predictions = targets.map((value, i) => value + Math.sin(i / 8) * 0.12);
    mockAnalysisArrays = { targets, predictions, residuals: targets.map((value, i) => value - predictions[i]), pair_distance: Array.from({ length: 800 }, (_, i) => Math.abs(Math.sin(i / 19)) * 4), pair_property_delta: Array.from({ length: 800 }, (_, i) => Math.abs(Math.sin(i / 19)) * 0.7 + Math.abs(Math.cos(i / 7)) * 0.1) };
    return { analysis_id: mockLatestAnalysisId, kind: "property_correlation", property: "energy_per_atom", sample_count: targets.length, r2: 0.91, rmse: 0.084, mae: 0.067, distance_property_correlation: 0.78, top_features: Array.from({ length: 12 }, (_, i) => ({ feature: i * 3 + 2, correlation: 0.9 - i * 0.11 })) };
  }
  if (mockLatestAnalysisKind === "effective_dimension") {
    const explained = [0.24, 0.17, 0.12, 0.1, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.012, 0.008, 0.004];
    mockAnalysisArrays = { explained_variance: explained };
    return { analysis_id: mockLatestAnalysisId, kind: "effective_dimension", participation_ratio: 6.8, components_for_threshold: { "0.9": 8, "0.95": 10, "0.99": 13 } };
  }
  if (mockLatestAnalysisKind === "trajectory") {
    const time = Array.from({ length: 180 }, (_, index) => index);
    const stepDistance = time.map((index) => Number((0.08 + Math.abs(Math.sin(index / 13)) * 0.24 + (index > 118 && index < 132 ? 0.52 : 0)).toFixed(5)));
    const referenceDistance = time.map((_, index) => Math.abs(Math.sin(index / 31)) * 2.5 + index / 280);
    mockAnalysisArrays = { time, frames: time, step_distance: stepDistance, reference_distance: referenceDistance, cumulative_distance: stepDistance.map((_, index) => stepDistance.slice(0, index + 1).reduce((sum, value) => sum + value, 0)), event_indices: [119, 122, 126, 129, 132] };
    return { analysis_id: mockLatestAnalysisId, kind: "trajectory", frame_start: 0, frame_end: 179, frame_step: 1, time_unit: "frame", total_distance: Number(stepDistance.reduce((sum, value) => sum + value, 0).toFixed(4)), max_reference_distance: Math.max(...referenceDistance), event_count: 5, points: mockAnalysisPoints("trajectory", time.length) };
  }
  if (mockLatestAnalysisKind === "drift") {
    const rows = Array.from({ length: 240 }, (_, index) => {
      const label = index < 174 ? 0 : index < 222 ? 1 : 2;
      const distance = 0.18 + index / 800 + (label === 1 ? 0.38 : label === 2 ? 0.76 : 0) + Math.abs(Math.sin(index / 11)) * 0.08;
      return { i: index, frame: index, sample_id: `frame:${index}`, labels: label, distances: Number(distance.toFixed(5)) };
    });
    return { analysis_id: mockLatestAnalysisId, kind: "drift", categories: ["covered", "marginal", "out_of_coverage"], q95: 0.62, q99: 0.98, metric: "euclidean", covered: 174, marginal: 48, out_of_coverage: 18, mean_distance: 0.534, median_distance: 0.348, max_distance: 1.136, rows, total_rows: rows.length };
  }
  if (mockLatestAnalysisKind === "sensitivity") {
    return {
      analysis_id: mockLatestAnalysisId,
      kind: "sensitivity",
      baseline_run_id: "run-dpa2",
      runs: [
        { run_id: "run-dpa2", parameters: { cutoff: 5, sel: 96 }, pairwise_distance_pearson: 1, neighbor_overlap: 1, clustering_stability: 1, effective_dimension: 8.1, memory_peak_bytes: 520 * 1024 * 1024 },
        { run_id: "run-dpa2-cut6", parameters: { cutoff: 6, sel: 96 }, pairwise_distance_pearson: 0.97, neighbor_overlap: 0.86, clustering_stability: 0.92, effective_dimension: 8.4, memory_peak_bytes: 610 * 1024 * 1024 },
        { run_id: "run-dpa2-cut7", parameters: { cutoff: 7, sel: 128 }, pairwise_distance_pearson: 0.91, neighbor_overlap: 0.78, clustering_stability: 0.84, effective_dimension: 9.2, memory_peak_bytes: 720 * 1024 * 1024 },
        { run_id: "run-dpa2-cut8", parameters: { cutoff: 8, sel: 128 }, pairwise_distance_pearson: 0.86, neighbor_overlap: 0.69, clustering_stability: 0.75, effective_dimension: 9.8, memory_peak_bytes: 840 * 1024 * 1024 },
      ],
    };
  }
  if (mockLatestAnalysisKind === "perturbation_sensitivity") {
    const amplitudes = [0, 0.04, 0.08, 0.12, 0.16];
    const responseMatrix = Array.from({ length: 36 }, (_, structure) => amplitudes.map((amplitude) => amplitude * (0.8 + (structure % 7) * 0.12) + Math.abs(Math.sin(structure / 5)) * amplitude * 0.2));
    mockAnalysisArrays = {
      amplitudes,
      mean_response: amplitudes.map((amplitude) => amplitude * 1.25),
      median_response: amplitudes.map((amplitude) => amplitude * 1.15),
      p95_response: amplitudes.map((amplitude) => amplitude * 1.8),
      max_response: amplitudes.map((amplitude) => amplitude * 2.1),
      response_matrix: responseMatrix,
      sample_indices: Array.from({ length: responseMatrix.length }, (_, i) => i),
    };
    return { analysis_id: mockLatestAnalysisId, kind: "perturbation_sensitivity", perturbation: "jitter", metric: "euclidean", response_unit: "scaled descriptor distance", sample_count: responseMatrix.length, curve_count: amplitudes.length, baseline_included: true };
  }
  if (mockLatestAnalysisKind === "kernel") {
    const size = 48;
    mockAnalysisArrays = { kernel_matrix: Array.from({ length: size }, (_, i) => Array.from({ length: size }, (__, j) => Number(Math.exp(-Math.abs(i - j) / 10).toFixed(4)))), eigenvalues: Array.from({ length: size }, (_, i) => Math.exp(-i / 7) * 12) };
    return { analysis_id: mockLatestAnalysisId, kind: "kernel", kernel: "rbf", sample_count: size, effective_rank: 8.7, top_eigenvalue_fraction: 0.18, kernel_min: 0.01, kernel_max: 1 };
  }
  return {
    analysis_id: mockLatestAnalysisId,
    kind: "projection",
    points: Array.from({ length: 250 }, (_, i) => ({
      i,
      frame: i % 6320,
      x: Math.sin(i / 17) * 3 + gauss(0, 0.25),
      y: Math.cos(i / 23) * 2 + gauss(0, 0.25),
      sample_id: `frame:${i}`,
    })),
  };
}

function mockFramePayload(index: number) {
  // 8-atom zincblende GaAs cell, a=5.65 Å, 2×1×1 supercell
  const a = 5.65;
  const base = [
    ["Ga", 0, 0, 0], ["As", 0.25, 0.25, 0.25],
    ["Ga", 0.5, 0.5, 0], ["As", 0.75, 0.75, 0.25],
    ["Ga", 0.5, 0, 0.5], ["As", 0.75, 0.25, 0.75],
    ["Ga", 0, 0.5, 0.5], ["As", 0.25, 0.75, 0.75],
  ] as [string, number, number, number][];
  const rows: {
    i: number;
    el: string;
    x: number;
    y: number;
    z: number;
    fx: number;
    fy: number;
    fz: number;
    f: number | null;
  }[] = base.map(([el, x, y, z], i) => ({
    i,
    el,
    x: x * 2 * a,
    y: y * a,
    z: z * a,
    fx: gauss(0, 0.3),
    fy: gauss(0, 0.3),
    fz: gauss(0, 0.3),
    f: null,
  }));
  for (const r of rows) r.f = Math.sqrt(r.fx ** 2 + r.fy ** 2 + r.fz ** 2);
  return {
    index,
    natoms: rows.length,
    formula: "Ga4As4",
    xyz: [
      "8",
      `Lattice="11.3 0.0 0.0 0.0 5.65 0.0 0.0 0.0 5.65" Properties=species:S:1:pos:R:3`,
      ...rows.map((r) => `${r.el} ${r.x.toFixed(4)} ${r.y.toFixed(4)} ${r.z.toFixed(4)}`),
    ].join("\n"),
    atom_rows: rows,
    energy: -28.42,
    energy_per_atom: -3.55,
    force_max: Math.max(...rows.map((r) => r.f ?? 0)),
    volume: 2 * a ** 3,
    pbc: "XYZ",
    cell: [2 * a, 0, 0, 0, a, 0, 0, 0, a],
    ghost_count: 0,
    bond_cutoff: 2.4,
  };
}

// representative engine values so sidebar badges/tooltip render all variants
const MOCK_DESCRIPTORS = [
  {
    name: "dpa2",
    display_name: "DPA4",
    level: "atom",
    backend: "numpy",
    category: "model_backed",
    capabilities: ["periodic", "charged"],
  },
  {
    name: "soap",
    display_name: "SOAP",
    level: "structure",
    backend: "cpp",
    category: "local",
    capabilities: ["periodic", "isolated"],
  },
  {
    name: "coulomb_matrix",
    display_name: "Coulomb Matrix",
    level: "structure",
    backend: "cpp",
    category: "matrix",
    capabilities: ["periodic"],
  },
  {
    name: "neighbor_list",
    display_name: "Neighbor List",
    level: "pair",
    backend: "cpp",
    category: "local",
    capabilities: ["periodic"],
  },
  {
    name: "so3",
    display_name: "SO3",
    level: "atom",
    backend: "cpp",
    category: "rotational",
    capabilities: ["periodic", "num_threads"],
  },
];

const METHODS: Record<string, Handler> = {
  "system.info": () => ({
    backend_version: "0.3.2",
    protocol_version: 1,
    platform: "Windows-11-preview",
    mdescriptor_version: "0.3.2",
    mdescriptor_api_version: 3,
    data_dir: "C:\\Users\\preview\\AppData\\Roaming\\mdescriptor-studio",
    cpu_threads: 16,
  }),
  "dataset.list": () => DS,
  "dataset.statistics": (p) => ({
    recalculating: false,
    job_id: null,
    stats: STATS[p.id as string] ?? null,
  }),
  "dataset.rescan": () => {
    window.setTimeout(() => {
      mockEmit("job.finished", { job_id: "job-rescan", status: "COMPLETED", result: null, error: null });
    }, 1500);
    return { job_id: "job-rescan" };
  },
  "dataset.frame": (p) => mockFramePayload(Number(p.index ?? 0)),
  "job.list": () => JOB_ROWS,
  "job.get": (p) => {
    const id = String(p.id ?? "");
    const row = JOB_ROWS.find((job) => job.id === id);
    return row ?? {
      id,
      job_type: "analysis",
      dataset_id: "ds-gaas",
      descriptor_run_id: null,
      status: "RUNNING",
      progress: 0,
      completed: null,
      total: null,
      message: null,
      error: null,
      created_at: new Date().toISOString(),
      started_at: new Date().toISOString(),
      finished_at: null,
    };
  },
  "settings.get": () => ({ value: "ds-gaas" }),
  "settings.set": () => ({}),
  "engine.check_update": () => ({
    installed: "0.3.2",
    latest: "0.3.2",
    hasUpdate: false,
    status: "up_to_date",
    error: null,
  }),
  "descriptor.list": () => MOCK_DESCRIPTORS,
  "descriptor.describe": (p) => {
    const meta = MOCK_DESCRIPTORS.find((x) => x.name === (p.name ?? "dpa2")) ?? MOCK_DESCRIPTORS[0];
    return {
      schema_version: 1,
      name: meta.name,
      display_name: meta.display_name,
      description: "Mock descriptor for the browser preview.",
      category: meta.category,
      level: meta.level,
      backend: meta.backend,
      capabilities: meta.capabilities,
      parameters:
        meta.name === "soap"
          ? {
              r_cut: { type: "number", default: 6.0, minimum: 1, maximum: 20, description: "Cutoff radius (Å)" },
              n_max: { type: "integer", default: 8, minimum: 1, maximum: 16, description: "Radial basis size" },
              l_max: { type: "integer", default: 8, minimum: 1, maximum: 16, description: "Angular basis size" },
              sigma: { type: "number", default: 0.5, exclusiveMinimum: 0, description: "Gaussian width (Å)" },
            }
          : {
              cutoff: { type: "number", default: 6.0, minimum: 1, maximum: 20, description: "Neighbor cutoff (Å)" },
              sel: { type: "integer", default: 128, minimum: 1, description: "Max neighbors per atom" },
              species: { type: "array", items: { type: "string" }, default: [], description: "Species filter (symbols)" },
              precision: { type: "string", enum: ["float32", "float64"], default: "float32" },
            },
      execution: { devices: ["cpu"], num_threads: true, cooperative_cancel: false },
      input: { periodicity: ["isolated", "fully_periodic"], mixed_periodicity: false, spin: false, charge_spin: false },
      output: { dtypes: ["float32"], sparse: false },
      asset: {
        policy: meta.name === "soap" ? "none" : "required",
        parameter: meta.name === "soap" ? null : "model",
        allow_external: true,
        bundled_resources: [],
        file_extensions: [".pt"],
      },
    };
  },
  "run.list": () => [
    {
      id: "run-dpa2",
      dataset_id: "ds-gaas",
      dataset_name: "GaAs Training Set",
      descriptor_name: "DPA-2",
      engine_version: "0.3.2",
      scope: "dataset",
      status: "COMPLETED",
      created_at: new Date(NOW - 3600_000).toISOString(),
      result_path: null,
      shape: "[12480, 256]",
    },
  ],
  // mutable run rows + a scripted job lifecycle so the Results page can be
  // watched flipping QUEUED -> RUNNING -> COMPLETED without the real backend
  "result.list": () => {
    startJobPlaybook();
    return RUNS;
  },
  "result.remove": (p) => {
    const runId = String(p.run_id ?? "");
    const index = RUNS.findIndex((run) => run.id === runId);
    if (index >= 0 && RUNS[index].status !== "QUEUED" && RUNS[index].status !== "RUNNING") {
      RUNS.splice(index, 1);
    }
    return { ok: true };
  },
  "analysis.pca": (_p) => {
    mockLatestAnalysisId = "ana-mock-pca";
    mockLatestAnalysisKind = "projection";
    mockLatestPcaMode = String(_p.mode ?? "structure");
    mockLatestPcaPreprocess = String(_p.preprocess ?? "center");
    mockAnalysisArrays = {};
    window.setTimeout(() => {
      mockEmit("job.finished", { job_id: "job-pca-live", status: "COMPLETED", result: { analysis_id: "ana-mock-pca" }, error: null });
    }, 800);
    return { job_id: "job-pca-live", analysis_id: "ana-mock-pca" };
  },
  "analysis.list": () => [
    {
      id: "ana-mock-pca",
      descriptor_run_id: "run-dpa2",
      analysis_type: "pca",
      status: "COMPLETED",
      parameters: { mode: mockLatestPcaMode, preprocess: mockLatestPcaPreprocess },
      dataset_ids: ["ds-gaas"],
      input_run_ids: ["run-dpa2"],
      created_at: new Date(NOW - 58 * 60000).toISOString(),
    },
  ],
  "analysis.preview": () => mockOverviewPreview(),
  "analysis.chunk": (p) => {
    const array = String(p.array ?? "");
    const values = mockAnalysisArrays[array] ?? [];
    const offset = Math.max(0, Math.floor(Number(p.offset ?? 0) || 0));
    const limit = Math.min(20_000, Math.max(1, Math.floor(Number(p.limit ?? 2000) || 2000)));
    const data = values.slice(offset, offset + limit);
    const columns = Array.isArray(values[0]) ? (values[0] as unknown[]).length : undefined;
    return { analysis_id: mockLatestAnalysisId, array, offset, next_offset: offset + data.length, shape: columns == null ? [values.length] : [values.length, columns], dtype: "float64", data };
  },
  "analysis.umap": (_p) => {
    mockLatestAnalysisId = "ana-mock-analysis";
    mockLatestAnalysisKind = "projection";
    mockAnalysisArrays = {};
    window.setTimeout(() => mockEmit("job.finished", { job_id: "job-umap-live", status: "COMPLETED", result: { analysis_id: "ana-mock-analysis" }, error: null }), 800);
    return { job_id: "job-umap-live", analysis_id: "ana-mock-analysis", cache: null };
  },
  "analysis.tsne": (_p) => {
    return mockAnalysisSubmit("job-tsne-live");
  },
  "analysis.neighbors": (_p) => mockAnalysisSubmit("job-neighbors-live"),
  "analysis.similarity": (_p) => mockAnalysisSubmit("job-similarity-live", "ana-mock-similarity", "similarity"),
  "analysis.pairwise": (_p) => mockAnalysisSubmit("job-pairwise-live", "ana-mock-pairwise", "pairwise_similarity"),
  "analysis.cluster": (_p) => mockAnalysisSubmit("job-cluster-live", "ana-mock-clusters", "clusters"),
  "analysis.outlier": (_p) => mockAnalysisSubmit("job-outlier-live", "ana-mock-outliers", "outliers"),
  "analysis.sampling": (_p) => mockAnalysisSubmit("job-sampling-live", "ana-mock-sampling", "sampling"),
  "analysis.fps": (_p) => mockAnalysisSubmit("job-fps-live", "ana-mock-sampling", "sampling"),
  "analysis.coverage": (_p) => mockAnalysisSubmit("job-coverage-live", "ana-mock-coverage", "coverage"),
  "analysis.overlap": (_p) => mockAnalysisSubmit("job-overlap-live", "ana-mock-overlap", "overlap"),
  "analysis.acquisition": (p) => {
    mockLatestAcquisitionMethod = String(p.acquisition_method ?? "novelty_fps");
    return mockAnalysisSubmit("job-acquisition-live", "ana-mock-acquisition", "acquisition");
  },
  "analysis.compare": (_p) => mockAnalysisSubmit("job-compare-live", "ana-mock-compare", "compare"),
  "analysis.mantel": (_p) => mockAnalysisSubmit("job-mantel-live", "ana-mock-mantel", "mantel"),
  "analysis.feature_variance": (_p) => mockAnalysisSubmit("job-feature-variance-live", "ana-mock-feature-variance", "feature_variance"),
  "analysis.feature_correlation": (_p) => mockAnalysisSubmit("job-feature-correlation-live", "ana-mock-feature-correlation", "feature_correlation"),
  "analysis.effective_dimension": (_p) => mockAnalysisSubmit("job-effective-dimension-live", "ana-mock-effective-dimension", "effective_dimension"),
  "analysis.property_correlation": (_p) => mockAnalysisSubmit("job-property-live", "ana-mock-property", "property_correlation"),
  "analysis.local_diversity": (_p) => mockAnalysisSubmit("job-local-live", "ana-mock-local", "local_diversity"),
  "analysis.kernel": (_p) => mockAnalysisSubmit("job-kernel-live", "ana-mock-kernel", "kernel"),
  "analysis.trajectory": (_p) => mockAnalysisSubmit("job-trajectory-live", "ana-mock-trajectory", "trajectory"),
  "analysis.drift": (_p) => mockAnalysisSubmit("job-drift-live", "ana-mock-drift", "drift"),
  "analysis.sensitivity": (_p) => mockAnalysisSubmit("job-sensitivity-live", "ana-mock-sensitivity", "sensitivity"),
  "analysis.perturbation_sensitivity": (_p) => mockAnalysisSubmit("job-perturbation-live", "ana-mock-perturbation", "perturbation_sensitivity"),
  "analysis.export": (_p) => mockAnalysisSubmit("job-export-live", "ana-mock-export"),
  "result.get_pca": (p) => {
    const scale = mockLatestPcaPreprocess === "standardized" ? 1.35 : mockLatestPcaPreprocess === "center" ? 1 : 0.78;
    const rawShift = mockLatestPcaPreprocess === "raw" ? 1.6 : 0;
    const points = Array.from({ length: 250 }, (_, i) => {
      const cluster = i % 2;
      const noiseX = Math.sin(i * 1.71) * 0.32;
      const noiseY = Math.cos(i * 1.13) * 0.28;
      return {
        i,
        frame: i % 6320,
        pc1: Math.round((rawShift + scale * (cluster ? 3 : -3) + noiseX + Math.sin(i / 17) * 0.4) * 1e4) / 1e4,
        pc2: Math.round((scale * (cluster ? 1.5 : -1.5) + noiseY + Math.cos(i / 23) * 0.35) * 1e4) / 1e4,
        energy: cluster ? -3.2 : -4.4,
        force_max: Math.round((2.4 + Math.abs(Math.sin(i / 9))) * 1e4) / 1e4,
        volume: Math.round((650 + Math.sin(i / 11) * 70) * 10) / 10,
      };
    });
    return {
      analysis_id: "ana-mock-pca",
      run_id: p.run_id ?? "run-dpa2",
      mode: mockLatestPcaMode,
      n_points: points.length,
      points,
      explained_variance: [0.612, 0.221],
      x_label: "PC1 (61.2%)",
      y_label: "PC2 (22.1%)",
    };
  },
  "result.heatmap": (p) => {
    const atoms = Array.from({ length: 8 }, (_, i) => i);
    const features = Array.from({ length: 96 }, (_, i) => i);
    const frame = Number(p.frame_index ?? 0);
    const values = atoms.map((atom) =>
      features.map((feature) =>
        Number((Math.sin(atom * 0.8 + feature / 14 + frame / 10) * 0.45 + Math.cos(feature / 9) * 0.2).toFixed(6)),
      ),
    );
    return { atoms, features, values, atomOffset: 0 };
  },
};

// ---------- Tauri internals stub ----------
const eventListeners: { event: string; fn: (evt: unknown) => void }[] = [];

function mockEmit(event: string, data: Record<string, unknown>) {
  const line = JSON.stringify({ event, data });
  for (const l of [...eventListeners]) {
    if (l.event === "backend-message") l.fn({ event: "backend-message", payload: line });
  }
}

const RUNS = [
  {
    id: "run-dpa2",
    dataset_id: "ds-gaas",
    dataset_name: "GaAs Training Set",
    descriptor_name: "DPA-2",
    engine_version: "0.3.2",
    scope: "dataset",
    status: "COMPLETED",
    created_at: new Date(NOW - 3600_000).toISOString(),
    result_path: "mock",
    shape: "[12480, 256]",
  },
  {
    id: "run-soap",
    dataset_id: "ds-gaas",
    dataset_name: "GaAs Training Set",
    descriptor_name: "SOAP",
    engine_version: "0.3.2",
    scope: "dataset",
    status: "QUEUED",
    created_at: new Date(NOW - 2 * 60000).toISOString(),
    result_path: null,
    shape: null as string | null,
  },
  {
    id: "run-ace",
    dataset_id: "ds-gaas",
    dataset_name: "GaAs Training Set",
    descriptor_name: "ACE",
    engine_version: "0.3.2",
    scope: "dataset",
    status: "COMPLETED",
    created_at: new Date(NOW - 90 * 60000).toISOString(),
    result_path: "mock",
    shape: "[12480, 96]",
  },
];

// one-shot timeline for job-soap: the Results table should show QUEUED on
// mount, RUNNING within ~1.5s of the first job.progress, COMPLETED at ~4.5s
let jobPlaybookStarted = false;
function startJobPlaybook() {
  if (jobPlaybookStarted) return;
  jobPlaybookStarted = true;
  window.setTimeout(() => {
    RUNS[1].status = "RUNNING";
    mockEmit("job.progress", { job_id: "job-soap", progress: 0.5, completed: 3160, total: 6320, message: "Computing SOAP descriptors" });
  }, 1500);
  window.setTimeout(() => {
    mockEmit("job.progress", { job_id: "job-soap", progress: 0.85, completed: 5372, total: 6320, message: "Computing SOAP descriptors" });
  }, 3000);
  window.setTimeout(() => {
    RUNS[1].status = "COMPLETED";
    RUNS[1].result_path = "mock";
    RUNS[1].shape = "[6320, 432]";
    mockEmit("job.finished", { job_id: "job-soap", status: "COMPLETED", result: null, error: null });
  }, 4500);
}

// surface preview-only bootstrap errors on screen (dev aid)
(window as unknown as { __TAURI_EVENT_PLUGIN_INTERNALS__: unknown }).__TAURI_EVENT_PLUGIN_INTERNALS__ = {
  // @tauri-apps/api v2 event._unlisten calls this; nothing to clean up in the mock
  unregisterListener: () => {},
};
window.addEventListener("error", (e) => showPreviewError(e.message ?? String(e.error)));
window.addEventListener("unhandledrejection", (e) =>
  showPreviewError(String((e.reason as Error)?.stack ?? e.reason)),
);
const origConsoleError = console.error.bind(console);
console.error = (...args: unknown[]) => {
  showPreviewError(
    args
      .map((a) =>
        typeof a === "object" && a !== null
          ? JSON.stringify(a, Object.getOwnPropertyNames(a)).slice(0, 500)
          : String((a as Error)?.stack ?? a),
      )
      .join(" | "),
  );
  origConsoleError(...args);
};
function showPreviewError(text: string) {
  let box = document.getElementById("preview-error-box") as HTMLPreElement | null;
  if (!box) {
    box = document.createElement("pre");
    box.id = "preview-error-box";
    box.title = "Preview console errors (click to expand)";
    box.style.cssText =
      "position:fixed;bottom:0;left:0;right:0;max-height:18px;overflow:hidden;margin:0;background:#300a0a;color:#ffb4b4;font-size:11px;padding:1px 8px;z-index:99999;white-space:pre-wrap;cursor:pointer";
    box.onclick = () => {
      box!.style.maxHeight = box!.style.maxHeight === "18px" ? "40vh" : "18px";
    };
    document.body.appendChild(box);
  }
  box.textContent += `\n${text}`;
}

(window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = {
  // TitleBar calls getCurrentWindow() — give it a label so the Window handle
  // resolves; window commands (minimize/maximize/close) fall through to no-op.
  metadata: {
    currentWindow: { label: "main" },
    currentWebview: { windowLabel: "main", label: "main" },
  },
  transformCallback: (cb: (e: unknown) => void) => {
    // the event API wraps callbacks; we hand back the function for direct use
    return cb;
  },
  invoke: async (cmd: string, args?: Record<string, unknown>) => {
    if (cmd === "plugin:event|listen") {
      const fn = args?.handler as unknown as (evt: unknown) => void;
      eventListeners.push({ event: args?.event as string, fn });
      return eventListeners.length;
    }
    if (cmd === "plugin:event|unlisten") return null;
    if (cmd === "backend_ready_line") {
      return JSON.stringify({ event: "backend.ready", data: null });
    }
    if (cmd === "backend_send") {
      const frame = JSON.parse((args?.line as string) ?? "{}") as MockFrame;
      window.setTimeout(() => {
        const handler = METHODS[frame.method ?? ""];
        const payload = JSON.stringify(
          handler
            ? { id: frame.id, result: handler(frame.params ?? {}) }
            : { id: frame.id, error: { code: "NO_HANDLER", message: `preview mock lacks ${frame.method}` } },
        );
        // route strictly by event name, like the Tauri event system
        for (const l of [...eventListeners]) {
          if (l.event === "backend-message") l.fn({ event: "backend-message", payload });
        }
      }, 25);
      return null;
    }
    // backend_restart, plugin:dialog|*, anything else → no-op
    return null;
  },
};

async function bootstrap() {
  const React = await import("react");
  const ReactDOM = await import("react-dom/client");
  const antd = await import("antd");
  const [{ theme }, { default: enUS }, { default: zhCN }, { useI18n }] = await Promise.all([
    import("./theme"),
    import("antd/locale/en_US"),
    import("antd/locale/zh_CN"),
    import("./i18n"),
  ]);
  const { default: App } = await import("./App");
  // Applies the antd locale pack for the selected UI language around the app.
  const LocalizedApp = () => {
    const lang = useI18n((s) => s.lang);
    return React.createElement(
      antd.ConfigProvider,
      { theme, locale: lang === "zh" ? zhCN : enUS },
      React.createElement(antd.App, null, React.createElement(App)),
    );
  };
  ReactDOM.createRoot(document.getElementById("root")!).render(
    React.createElement(
      React.StrictMode,
      null,
      React.createElement(LocalizedApp),
    ),
  );
}

void bootstrap();

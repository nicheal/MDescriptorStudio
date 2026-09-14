// Dev-only browser preview of the real App without the Tauri shell.
// Open via `npm run dev` → http://localhost:1420/preview.html
// It stubs window.__TAURI_INTERNALS__ (invoke/transformCallback) and serves a
// tiny in-browser mock backend over the same NDJSON protocol. Not part of the
// production bundle (vite builds only index.html's entry).
import "./global.css";
import type { DatasetView, Hist } from "./types/protocol";
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
  {
    id: "ds-cho",
    name: "2026_Zhang_CHO train",
    format: "extxyz",
    source_path: "D:\\mlffkit\\mlffkit\\tests\\2026_Zhang_CHO\\train.xyz",
    number_of_frames: 3813,
    elements: ["C", "H", "O"],
    properties: { energy: { per_structure: true }, forces: { per_atom: true } },
    periodicity: { fully_periodic: false, isolated: true, mixed: false, flags: [] },
    fingerprint: "mock-cho",
    file_size: 66 * 1024 ** 2,
    created_at: "2026-04-12T10:00:00Z",
    last_scan_at: new Date(NOW - 60000).toISOString(),
    cache_valid: true,
  },
];

let MOCK_DATASET_VIEWS: DatasetView[] = [
  {
    id: "view-gaas-train",
    dataset_id: "ds-gaas",
    dataset_name: "GaAs Training Set",
    name: "Training split",
    role: "train",
    filter: { type: "split", seed: 42 },
    frame_indices: Array.from({ length: 80 }, (_, index) => index),
    number_of_frames: 9984,
    selection_hash: "mock-view-gaas-train",
    dataset_fingerprint: "mock-gaas",
    stale: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

// integer-aligned histogram from [value, count] pairs (half-integer edges)
function intHist(pairs: [number, number][]): Hist {
  const lo = pairs[0][0];
  const hi = pairs[pairs.length - 1][0];
  const map = new Map(pairs);
  const counts: number[] = [];
  for (let v = lo; v <= hi; v++) counts.push(map.get(v) ?? 0);
  const edges: number[] = [];
  for (let v = lo - 0.5; v <= hi + 0.5; v++) edges.push(v);
  return { edges, counts };
}

// Synthetic CHO stoichiometry: a fixed C140 backbone with arithmetically
// varied H/O counts across 794 species totalling the dataset's 3813
// structures. Pure arithmetic, so preview reloads stay deterministic.
function choStats(): { formulas: { formula: string; elements: string[]; count: number }[]; elementAtomCounts: Record<string, Hist> } {
  const formulas: { formula: string; elements: string[]; count: number }[] = [];
  const hCounts = new Map<number, number>();
  const oCounts = new Map<number, number>();
  let remaining = 3813;
  for (let i = 0; i < 794; i++) {
    const left = 794 - i;
    const h = i % 23;
    const o = ((i * 7) % 57) + 1;
    // decaying share of the remaining budget keeps abundant species on top
    const count = i === 793 ? remaining : Math.max(1, Math.min(Math.round((remaining / left) * 1.6), remaining - (left - 1)));
    formulas.push({ formula: h > 0 ? `C140H${h}O${o}` : `C140O${o}`, elements: h > 0 ? ["C", "H", "O"] : ["C", "O"], count });
    if (h > 0) hCounts.set(h, (hCounts.get(h) ?? 0) + count);
    oCounts.set(o, (oCounts.get(o) ?? 0) + count);
    remaining -= count;
  }
  const pairs = (m: Map<number, number>): [number, number][] => [...m.entries()].sort((a, b) => a[0] - b[0]);
  return { formulas, elementAtomCounts: { C: intHist([[140, 3813]]), H: intHist(pairs(hCounts)), O: intHist(pairs(oCounts)) } };
}

const CHO_STATS = choStats();

const STATS: Record<string, unknown> = {
  "ds-gaas": {
    structures: 12480,
    atoms_total: 798720,
    elements: [
      { symbol: "Ga", count: 397762 },
      { symbol: "As", count: 400958 },
    ],
    compositions: [
      { elements: ["Ga", "As"], count: 9600 },
      { elements: ["Ga"], count: 1600 },
      { elements: ["As"], count: 1280 },
    ],
    formulas: [
      { formula: "As32Ga32", elements: ["As", "Ga"], count: 9600 },
      { formula: "Ga64", elements: ["Ga"], count: 1600 },
      { formula: "As64", elements: ["As"], count: 1280 },
    ],
    element_atom_counts: {
      Ga: intHist([[32, 9600], [64, 1600]]),
      As: intHist([[32, 9600], [64, 1280]]),
    },
    atoms_per_structure: hist(56, 72, 16, 64, 2),
    atoms_per_structure_summary: { min: 64, max: 64, mean: 64, median: 64 },
    energy_per_atom: hist(-6, -1, 50, -3.8, 0.45),
    energy_per_atom_summary: { min: -5.6, max: -2.1, mean: -3.82, median: -3.8 },
    force_magnitude: hist(0, 10, 50, 2.6, 1.1),
    force_magnitude_summary: { min: 0.02, max: 9.4, mean: 2.63, median: 2.5 },
    max_force: hist(0, 10, 50, 2.8, 1.2),
    max_force_summary: { min: 0.3, max: 9.9, mean: 2.9, median: 2.7 },
    min_distance: hist(0.8, 4, 50, 2.0, 0.4),
    min_distance_summary: { min: 0.85, max: 3.9, mean: 2.05, median: 2.0 },
    volume: hist(400, 1000, 40, 650, 70),
    volume_summary: { min: 405, max: 995, mean: 651, median: 648 },
    properties: { energy: { per_structure: true, per_atom: true }, forces: { per_atom: true }, virial: { per_structure: true } },
    periodicity: { fully_periodic: true, isolated: false, mixed: false, flags: ["X", "Y", "Z"] },
    health: {
      missing_values: 5,
      missing_by_property: { energy: 2, virial: 5 },
      energy_anomaly: 2,
      invalid_cell: 0,
      duplicate_structures: 2,
      extreme_force: 3,
      extreme_force_threshold: 50,
      nonphysical_structures: 1,
      short_contact_coefficient: 0.7,
      net_force: 4,
      net_force_threshold: 0.001,
    },
    health_findings: {
      cap: 5000,
      missing_values: [3, 21, 204, 512, 866],
      energy_anomaly: [88, 902],
      invalid_cell: [],
      duplicate_structures: [17, 421],
      // parallel to duplicate_structures: the first-occurrence frames
      duplicate_structures_of: [4, 420],
      extreme_force: [3, 88, 902],
      nonphysical_structures: [155],
      net_force: [12, 47, 233, 519],
    },
    excluded_frames: { count: 0, indices: [] },
  },
  "ds-cho": {
    structures: 3813,
    atoms_total: 747348,
    elements: [
      { symbol: "C", count: 533820 },
      { symbol: "H", count: 48100 },
      { symbol: "O", count: 165428 },
    ],
    compositions: [
      { elements: ["C", "O"], count: 547 },
      { elements: ["C", "H", "O"], count: 3266 },
    ],
    formulas: CHO_STATS.formulas,
    element_atom_counts: CHO_STATS.elementAtomCounts,
    atoms_per_structure: hist(140, 250, 40, 196, 16),
    atoms_per_structure_summary: { min: 154, max: 240, mean: 196, median: 196 },
    energy_per_atom: hist(-9, -6, 50, -7.6, 0.4),
    energy_per_atom_summary: { min: -8.9, max: -6.2, mean: -7.62, median: -7.6 },
    force_magnitude: hist(0, 12, 50, 2.2, 1.0),
    force_magnitude_summary: { min: 0.01, max: 11.8, mean: 2.24, median: 2.1 },
    max_force: hist(0, 12, 50, 2.6, 1.2),
    max_force_summary: { min: 0.2, max: 11.9, mean: 2.7, median: 2.5 },
    min_distance: hist(0.7, 3, 50, 1.3, 0.35),
    min_distance_summary: { min: 0.72, max: 2.9, mean: 1.32, median: 1.3 },
    volume: hist(400, 2200, 40, 1200, 250),
    volume_summary: { min: 420, max: 2150, mean: 1198, median: 1195 },
    properties: { energy: { per_structure: true, per_atom: false }, forces: { per_atom: true }, virial: { per_structure: false } },
    periodicity: { fully_periodic: false, isolated: true, mixed: false, flags: [] },
    health: {
      missing_values: 0,
      missing_by_property: { energy: 0, forces: 0 },
      energy_anomaly: 0,
      invalid_cell: 0,
      duplicate_structures: 0,
      extreme_force: 0,
      extreme_force_threshold: 50,
      nonphysical_structures: 2,
      short_contact_coefficient: 0.7,
      net_force: 0,
      net_force_threshold: 0.001,
    },
    health_findings: {
      cap: 5000,
      missing_values: [],
      energy_anomaly: [],
      invalid_cell: [],
      duplicate_structures: [],
      extreme_force: [],
      nonphysical_structures: [27, 1180],
      net_force: [],
    },
    excluded_frames: { count: 1, indices: [27] },
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
let mockLatestEffectiveDimensionPreprocess = "standardized";
let mockLatestAcquisitionMethod = "novelty_fps";
let mockFeatureVarianceSettings = {
  near_zero_relative_threshold: 1e-4,
  low_variance_relative_threshold: 1e-2,
};
let mockFeatureCorrelationSettings = {
  method: "pearson",
  correlation_threshold: 0.95,
};

function mockAnalysisSubmit(jobId: string, analysisId = "ana-mock-analysis", kind = "projection", rowType: string | null = kind) {
  mockLatestAnalysisId = analysisId;
  mockLatestAnalysisKind = kind;
  mockAnalysisArrays = {};
  if (rowType) mockRecordAnalysisRow(analysisId, rowType);
  window.setTimeout(() => {
    mockEmit("job.finished", { job_id: jobId, status: "COMPLETED", result: { analysis_id: analysisId }, error: null });
  }, 800);
  return { job_id: jobId, analysis_id: analysisId, cache: null };
}

// Completed analysis rows the mock analysis.list serves; every submit
// registers one so computed results stay restorable and visible in history.
const mockAnalysisRows = new Map<string, Record<string, unknown>>();
function mockRecordAnalysisRow(id: string, type: string, parameters: Record<string, unknown> = {}) {
  mockAnalysisRows.set(id, {
    id,
    descriptor_run_id: "run-dpa2",
    analysis_type: type,
    status: "COMPLETED",
    parameters,
    dataset_ids: ["ds-gaas"],
    input_run_ids: ["run-dpa2"],
    created_at: new Date().toISOString(),
  });
}
mockRecordAnalysisRow("ana-mock-pca", "pca", { mode: mockLatestPcaMode, preprocess: mockLatestPcaPreprocess });

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
    const variances = Array.from({ length: 35 }, (_, index) => 0.00045 + Math.abs(Math.sin(index / 4.3)) * 0.0064);
    variances[0] = 0;
    variances[1] = 0.00000012;
    variances[2] = 0.000025;
    const maxVariance = Math.max(...variances);
    const features = variances.map((variance, index) => {
      const standardDeviation = Math.sqrt(variance);
      const sample = Array.from({ length: index === 34 ? 78 : 80 }, (_, sampleIndex) => (index === 0 ? 1 : Math.sin(sampleIndex / 7 + index) * standardDeviation * 1.7));
      const minimum = Math.min(...sample);
      const maximum = Math.max(...sample);
      const median = (minimum + maximum) / 2;
      const q25 = minimum + (maximum - minimum) * 0.25;
      const q75 = minimum + (maximum - minimum) * 0.75;
      const iqr = q75 - q25;
      const lowerFence = q25 - 1.5 * iqr;
      const upperFence = q75 + 1.5 * iqr;
      const outliers = sample.filter((value) => value < lowerFence || value > upperFence);
      const inliers = sample.filter((value) => value >= lowerFence && value <= upperFence);
      const relativeVariance = maxVariance > 0 ? variance / maxVariance : 0;
      const status = index === 0
        ? "constant"
        : relativeVariance < mockFeatureVarianceSettings.near_zero_relative_threshold
          ? "near_zero"
          : relativeVariance < mockFeatureVarianceSettings.low_variance_relative_threshold
            ? "low_variation"
            : "active";
      return {
        index,
        mean: index === 0 ? 1 : 0,
        variance,
        relative_variance: relativeVariance,
        std: standardDeviation,
        min: minimum,
        max: maximum,
        p05: minimum,
        p25: q25,
        median,
        p75: q75,
        p95: maximum,
        iqr,
        mad: iqr / 2,
        robust_sigma: (iqr / 2) * 1.4826,
        std_robust_ratio: standardDeviation > 0 ? standardDeviation / Math.max((iqr / 2) * 1.4826, 1e-12) : null,
        whisker_min: inliers.length ? Math.min(...inliers) : q25,
        whisker_max: inliers.length ? Math.max(...inliers) : q75,
        outlier_count: outliers.length,
        finite_count: sample.length,
        invalid_count: index === 34 ? 2 : 0,
        missing_count: index === 34 ? 2 : 0,
        distribution_sample_count: sample.length,
        status,
      };
    });
    const sampleFor = (feature: { std: number | null }, index: number) => Array.from({ length: index === 34 ? 78 : 80 }, (_, sampleIndex) => index === 0 ? 1 : Math.sin(sampleIndex / 7 + index) * (feature.std ?? 0) * 1.7);
    const histogramEdges = features.map((feature, index) => {
      const sample = sampleFor(feature, index);
      const minimum = Math.min(...sample);
      const maximum = Math.max(...sample);
      const width = maximum > minimum ? (maximum - minimum) / 16 : 0.5;
      return Array.from({ length: 17 }, (_, edge) => (maximum > minimum ? minimum : minimum - width / 2) + edge * width);
    });
    const histogramCounts = histogramEdges.map((edges, index) => {
      const feature = features[index];
      const sample = sampleFor(feature, index);
      const width = edges[1] - edges[0];
      const counts = Array.from({ length: 16 }, () => 0);
      sample.forEach((value) => counts[Math.min(15, Math.max(0, Math.floor((value - edges[0]) / Math.max(width, 1e-12))))] += 1);
      return counts;
    });
    mockAnalysisArrays = {
      variance: variances,
      relative_variance: variances.map((value) => value / Math.max(...variances)),
      std: variances.map(Math.sqrt),
      iqr: features.map((feature) => feature.iqr),
      mad: features.map((feature) => feature.mad),
      histogram_edges: histogramEdges,
      histogram_counts: histogramCounts,
      distribution_samples: features.map(sampleFor),
      distribution_sample_counts: features.map((feature) => feature.distribution_sample_count),
    };
    const order = variances.map((_, index) => index).sort((left, right) => variances[right] - variances[left]);
    return {
      analysis_id: mockLatestAnalysisId,
      kind: "feature_variance",
      schema_version: 2,
      sample_count: 80,
      feature_count: features.length,
      ddof: 0,
      settings: { ...mockFeatureVarianceSettings, constant_tolerance: 1e-12, histogram_bins: 16, distribution_sample_limit: 80 },
      summary: {
        max_variance: maxVariance,
        median_variance: [...variances].sort((left, right) => left - right)[Math.floor(variances.length / 2)],
        min_variance: Math.min(...variances),
        near_zero_count: features.filter((feature) => feature.status === "near_zero").length,
        constant_count: 1,
        low_variation_count: features.filter((feature) => feature.status === "low_variation").length,
        active_count: features.filter((feature) => feature.status === "active").length,
        invalid_count: 1,
        invalid_value_count: 2,
        effective_nonzero_dimensions: 33,
      },
      warnings: ["ignored 2 non-finite feature value(s) across 1 feature(s)"],
      top_k: 20,
      top_indices: order.slice(0, 20),
      top_values: order.slice(0, 20).map((index) => variances[index]),
      features,
    };
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
      schema_version: 3,
      correlation_metric: mockFeatureCorrelationSettings.method,
      correlation_threshold: mockFeatureCorrelationSettings.correlation_threshold,
      feature_count: 256,
      valid_feature_count: 253,
      zero_variance_count: 3,
      highly_correlated_pairs: 7,
      high_correlation_cluster_count: 3,
      involved_feature_count: 5,
      involved_feature_ratio: 0.0195,
      clustered_feature_order: [...featureIndices].reverse(),
      heatmap_feature_count: featureIndices.length,
      heatmap_limited: true,
      pairs: [
        { feature_a: 12, feature_b: 41, correlation: 0.97, absolute_correlation: 0.97 },
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
    const residuals = targets.map((value, i) => predictions[i] - value);
    const absoluteErrors = residuals.map(Math.abs);
    const oofDistances = targets.map((_, i) => 0.18 + (i % 40) * 0.018 + Math.abs(Math.sin(i / 9)) * 0.08);
    const featureIndices = Array.from({ length: 64 }, (_, i) => i);
    const pearson = featureIndices.map((i) => Math.cos(i * 1.7) * (0.56 - i * 0.005));
    const spearman = featureIndices.map((i) => Math.sin(i * 1.3) * (0.62 - i * 0.006));
    const mutualInformation = featureIndices.map((i) => Math.max(0.005, 0.42 * Math.exp(-i / 18) + Math.sin(i) * 0.025));
    const binCenter = [0.24, 0.34, 0.44, 0.54, 0.64, 0.76, 0.89];
    mockAnalysisArrays = {
      sample_indices: targets.map((_, i) => i),
      sample_frames: targets.map((_, i) => i),
      sample_rows: targets.map(() => -1),
      targets,
      predictions,
      residuals,
      absolute_errors: absoluteErrors,
      oof_distances: oofDistances,
      feature_indices: featureIndices,
      pearson_correlations: pearson,
      spearman_correlations: spearman,
      mutual_information: mutualInformation,
      reliability_bin_center: binCenter,
      reliability_bin_median: [0.035, 0.042, 0.047, 0.056, 0.071, 0.09, 0.12],
      reliability_bin_p90: [0.07, 0.08, 0.09, 0.105, 0.13, 0.16, 0.2],
      reliability_bin_p95: [0.085, 0.095, 0.11, 0.125, 0.15, 0.19, 0.23],
    };
    return {
      analysis_id: mockLatestAnalysisId,
      kind: "property_correlation",
      property: "energy_per_atom",
      property_unit: "eV/atom",
      sample_count: targets.length,
      feature_count: 256,
      valid_feature_count: 253,
      model: "Ridge",
      cv_folds: 5,
      cv_shuffle: true,
      cv_seed: 42,
      r2: 0.91,
      rmse: 0.084,
      mae: 0.067,
      baseline_r2: -0.012,
      baseline_rmse: 0.58,
      baseline_mae: 0.47,
      residual_mean: 0.001,
      residual_median: -0.002,
      residual_std: 0.084,
      p95_absolute_error: 0.118,
      max_abs_pearson: 0.56,
      max_abs_spearman: 0.61,
      max_mutual_information: 0.42,
      encoding_strength: "strong",
      information_pattern: "distributed",
      distance_metric: "euclidean",
      distance_standardized: true,
      reliability_k: 5,
      distance_error_pearson: 0.51,
      distance_error_spearman: 0.58,
      sparse_quantile: 0.9,
      ood_quantile: 0.99,
      sparse_threshold: 0.78,
      ood_threshold: 0.91,
      high_error_high_distance_count: 11,
      high_error_low_distance_count: 5,
    };
  }
  if (mockLatestAnalysisKind === "effective_dimension") {
    const head = [0.5, 0.2, 0.12, 0.1, 0.025, 0.01, 0.012, 0.009, 0.007, 0.005, 0.004];
    const tailBase = Array.from({ length: 50 }, (_, index) => Math.pow(0.9, index));
    const tailBaseTotal = tailBase.reduce((sum, value) => sum + value, 0);
    const explained = [...head, ...tailBase.map((value) => 0.008 * value / tailBaseTotal)];
    const cumulative: number[] = [];
    let cumulativeTotal = 0;
    for (const value of explained) {
      cumulativeTotal += value;
      cumulative.push(cumulativeTotal);
    }
    const thresholdAt = (target: number) => cumulative.findIndex((value) => value >= target) + 1;
    const participationRatio = 1 / explained.reduce((sum, value) => sum + value * value, 0);
    mockAnalysisArrays = { explained_variance: explained };
    return {
      analysis_id: mockLatestAnalysisId,
      kind: "effective_dimension",
      preprocess: mockLatestEffectiveDimensionPreprocess,
      pca_basis: mockLatestEffectiveDimensionPreprocess === "standardized" ? "correlation" : "covariance",
      sample_count: 6320,
      feature_count: 219,
      pca_feature_count: 219,
      component_count: explained.length,
      participation_ratio: participationRatio,
      components_for_threshold: { "0.9": thresholdAt(0.9), "0.95": thresholdAt(0.95), "0.99": thresholdAt(0.99) },
    };
  }
  if (mockLatestAnalysisKind === "trajectory") {
    const time = Array.from({ length: 180 }, (_, index) => index);
    const jump = (index: number) => (index > 118 && index < 132 ? 0.52 : 0);
    const stepDistance = time.map((index) => Number((0.08 + Math.abs(Math.sin(index / 13)) * 0.24 + jump(index)).toFixed(5)));
    const referenceDistance = time.map((_, index) => Math.abs(Math.sin(index / 31)) * 2.5 + index / 280);
    const coords = time.map((index) => [Math.sin(index / 12) * 3 + index / 90, Math.cos(index / 17) * 2 - jump(index) * 4]);
    const steps = stepDistance.slice(1);
    const sorted = steps.slice().sort((left, right) => left - right);
    const median = sorted[Math.floor(sorted.length / 2)];
    const mad = sorted.map((value) => Math.abs(value - median)).sort((left, right) => left - right)[Math.floor(sorted.length / 2)];
    const mean = steps.reduce((sum, value) => sum + value, 0) / steps.length;
    const std = Math.sqrt(steps.reduce((sum, value) => sum + (value - mean) ** 2, 0) / steps.length);
    const threshold = median + 3 * 1.4826 * mad;
    const eventIndices = time.filter((index) => index > 0 && stepDistance[index] > threshold);
    const points = mockAnalysisPoints("trajectory", time.length).map((point, index) => ({ ...point, x: coords[index][0], y: coords[index][1] }));
    mockAnalysisArrays = {
      time,
      frames: time,
      sample_indices: time,
      step_distance: stepDistance,
      reference_distance: referenceDistance,
      cumulative_distance: stepDistance.map((_, index) => stepDistance.slice(0, index + 1).reduce((sum, value) => sum + value, 0)),
      coords,
      pc_explained_variance: [0.62, 0.21],
      event_indices: eventIndices,
    };
    return {
      analysis_id: mockLatestAnalysisId,
      kind: "trajectory",
      frame_start: 0,
      frame_end: 179,
      frame_step: 1,
      time_unit: "frame",
      sample_count: time.length,
      total_distance: Number(steps.reduce((sum, value) => sum + value, 0).toFixed(4)),
      max_step_distance: Math.max(...steps),
      max_reference_distance: Math.max(...referenceDistance),
      median_step_distance: median,
      mean_step_distance: mean,
      step_mad: mad,
      step_robust_sigma: 1.4826 * mad,
      step_std: std,
      event_method: "mad",
      event_sensitivity: 3,
      event_threshold: threshold,
      event_count: eventIndices.length,
      event_rate: eventIndices.length / steps.length,
      event_space: "descriptor",
      pc1_explained_variance: 0.62,
      pc2_explained_variance: 0.21,
      pc_explained_variance_sum: 0.83,
      points,
    };
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
    return { analysis_id: mockLatestAnalysisId, kind: "perturbation_sensitivity", perturbation: "jitter", metric: "euclidean", response_unit: "scaled descriptor distance", sample_count: responseMatrix.length, available_structure_count: 6320, curve_count: amplitudes.length, baseline_included: true, warnings: ["sampled 36 of 6320 structures evenly across the run"] };
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

// mock missing-values findings for ds-gaas; must stay consistent with the
// health mock below (missing_by_property: energy 2, virial 5)
const MOCK_MISSING_INDICES = new Set([3, 21, 204, 512, 866]);

function mockFramePayload(index: number, bondCutoff = 2.4) {
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
    // 5-decimal rounding matches the backend's frame payload so the atom
    // table shows compact, fixed-width numbers.
    x: Number((x * 2 * a).toFixed(5)),
    y: Number((y * a).toFixed(5)),
    z: Number((z * a).toFixed(5)),
    fx: gauss(0, 0.3),
    fy: gauss(0, 0.3),
    fz: gauss(0, 0.3),
    f: null,
  }));
  for (const r of rows) r.f = Math.sqrt(r.fx ** 2 + r.fy ** 2 + r.fz ** 2);
  // Periodic images within the requested cutoff, mirroring the backend's
  // ghost rule so the Explore local shell shows cross-boundary neighbors.
  const cellDims = [2 * a, a, a];
  const cutoff = Math.max(0.1, Math.min(10, bondCutoff || 2.4));
  const ghosts: { el: string; x: number; y: number; z: number }[] = [];
  const ghostParents: number[] = [];
  const shiftRange = (dim: number) => {
    const lim = Math.max(1, Math.ceil(cutoff / dim));
    const values: number[] = [];
    for (let s = -lim; s <= lim; s++) values.push(s);
    return values;
  };
  const realPos = rows.map((r) => [r.x, r.y, r.z]);
  base.forEach(([el, fx, fy, fz], parent) => {
    const px = fx * 2 * a;
    const py = fy * a;
    const pz = fz * a;
    for (const sx of shiftRange(cellDims[0])) {
      for (const sy of shiftRange(cellDims[1])) {
        for (const sz of shiftRange(cellDims[2])) {
          if (!sx && !sy && !sz) continue;
          const gx = px + sx * cellDims[0];
          const gy = py + sy * cellDims[1];
          const gz = pz + sz * cellDims[2];
          let minD2 = Infinity;
          for (const [rx, ry, rz] of realPos) {
            const d2 = (gx - rx) ** 2 + (gy - ry) ** 2 + (gz - rz) ** 2;
            if (d2 < minD2) minD2 = d2;
          }
          if (minD2 > cutoff * cutoff || minD2 < 1e-6) continue;
          ghosts.push({ el, x: gx, y: gy, z: gz });
          ghostParents.push(parent);
        }
      }
    }
  });
  return {
    index,
    natoms: rows.length,
    formula: "Ga4As4",
    xyz: [
      String(rows.length + ghosts.length),
      `Lattice="11.3 0.0 0.0 0.0 5.65 0.0 0.0 0.0 5.65" Properties=species:S:1:pos:R:3`,
      ...rows.map((r) => `${r.el} ${r.x.toFixed(4)} ${r.y.toFixed(4)} ${r.z.toFixed(4)}`),
      ...ghosts.map((g) => `${g.el} ${g.x.toFixed(4)} ${g.y.toFixed(4)} ${g.z.toFixed(4)}`),
    ].join("\n"),
    atom_rows: rows,
    energy: index === 21 || index === 512 ? null : -28.42,
    energy_per_atom: index === 21 || index === 512 ? null : -3.55,
    force_max: Math.max(...rows.map((r) => r.f ?? 0)),
    virial_present: !MOCK_MISSING_INDICES.has(index),
    // symmetric near-zero-pressure virial (eV) for the 8-atom cell; null on
    // the frames the missing-values mock flags, matching virial_present
    virial: MOCK_MISSING_INDICES.has(index)
      ? null
      : [0.0123, 0.0008, -0.0004, 0.0008, 0.0091, 0.0005, -0.0004, 0.0005, 0.0147],
    volume: 2 * a ** 3,
    pbc: "XYZ",
    cell: [2 * a, 0, 0, 0, a, 0, 0, 0, a],
    ghost_count: ghosts.length,
    ghost_parents: ghostParents,
    bond_cutoff: Math.min(10, Math.max(0.1, bondCutoff || 2.4)),
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
  "dataset.view.list": (p) => p.dataset_id ? MOCK_DATASET_VIEWS.filter((view) => view.dataset_id === p.dataset_id) : MOCK_DATASET_VIEWS,
  "dataset.view.create": (p) => {
    const dataset = DS.find((item) => item.id === p.dataset_id) ?? DS[0];
    const indices = Array.isArray(p.indices) ? p.indices.map(Number) : [];
    const view: DatasetView = {
      id: `view-${nextMockId++}`,
      dataset_id: dataset.id,
      dataset_name: dataset.name,
      name: String(p.name ?? "Selection"),
      role: "filtered",
      filter: (p.filter as Record<string, unknown>) ?? {},
      frame_indices: indices,
      number_of_frames: indices.length,
      selection_hash: `mock-selection-${nextMockId}`,
      dataset_fingerprint: dataset.fingerprint,
      stale: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    MOCK_DATASET_VIEWS = [...MOCK_DATASET_VIEWS, view];
    return view;
  },
  "dataset.view.remove": (p) => {
    MOCK_DATASET_VIEWS = MOCK_DATASET_VIEWS.filter((view) => view.id !== p.id);
    return { ok: true };
  },
  "dataset.view.rename": (p) => {
    const view = MOCK_DATASET_VIEWS.find((item) => item.id === p.id);
    if (view) view.name = String(p.name ?? view.name);
    return view ?? null;
  },
  "dataset.view.split": (p) => {
    const dataset = DS.find((item) => item.id === p.dataset_id) ?? DS[0];
    const roles = ["train", "validation", "test"] as const;
    const views = roles.map((role, index) => ({
      id: `view-${role}-${nextMockId++}`,
      dataset_id: dataset.id,
      dataset_name: dataset.name,
      name: `${dataset.name} / ${role[0].toUpperCase()}${role.slice(1)}`,
      role,
      filter: { type: "split", seed: Number(p.seed ?? 42) },
      frame_indices: [index],
      number_of_frames: Math.floor(dataset.number_of_frames * ([0.8, 0.1, 0.1][index])),
      selection_hash: `mock-${role}-${nextMockId}`,
      dataset_fingerprint: dataset.fingerprint,
      stale: false,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }));
    MOCK_DATASET_VIEWS = [...MOCK_DATASET_VIEWS, ...views];
    return { views };
  },
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
  "dataset.frame": (p) => mockFramePayload(Number(p.index ?? 0), Number(p.bond_cutoff ?? 2.4)),
  "dataset.findings": (p) => {
    const stats = STATS[p.id as string] as { health_findings?: { [k: string]: number[] } } | undefined;
    let indices: number[];
    if (p.check) indices = stats?.health_findings?.[p.check as string] ?? [];
    else if (Array.isArray(p.indices)) indices = p.indices as number[];
    else indices = [];
    const limit = Math.min(Number(p.limit ?? 1000), 1000);
    const mockExcluded = new Set(STATS["ds-cho"] && p.id === "ds-cho" ? [27] : []);
    // per-frame missing properties, consistent with MOCK_MISSING_INDICES and
    // the ds-gaas health mock (energy 2, virial 5)
    const mockMissingProps = (i: number): string[] => {
      if (!MOCK_MISSING_INDICES.has(i)) return [];
      return i === 21 || i === 512 ? ["energy", "virial"] : ["virial"];
    };
    return {
      recalculating: false,
      job_id: null,
      total: indices.length,
      returned: Math.min(indices.length, limit),
      rows: indices.slice(0, limit).map((i: number) => ({
        index: i,
        natoms: 4 + (i % 6),
        formula: "Ga2As2",
        force_max: 0.4 + (i % 7) * 0.1,
        energy_per_atom: 0.01 + (i % 5) * 0.01,
        volume: 618.2,
        // plausible only on the non-physical tab (the sole consumer); a couple
        // of rows dip below the short-contact bound, the rest sit at ~2 Å
        min_distance: i % 9 === 0 ? 0.72 + (i % 3) * 0.05 : 1.9 + (i % 5) * 0.08,
        missing_props: mockMissingProps(i),
        excluded: mockExcluded.has(i),
      })),
    };
  },
  "dataset.excluded": (p) => ({
    indices: p.id === "ds-cho" ? [27] : [],
    number_of_frames: 3813,
  }),
  "dataset.exclude": (p) => {
    window.setTimeout(() => {
      mockEmit("job.finished", { job_id: "job-exclude", status: "COMPLETED", result: null, error: null });
    }, 600);
    return { job_id: "job-exclude", excluded: (p.indices as number[]).length };
  },
  "dataset.restore": (p) => {
    window.setTimeout(() => {
      mockEmit("job.finished", { job_id: "job-restore", status: "COMPLETED", result: null, error: null });
    }, 600);
    return { job_id: "job-restore", restored: (p.indices as number[]).length };
  },
  "dataset.export_cleaned": () => {
    window.setTimeout(() => {
      mockEmit("job.finished", { job_id: "job-export", status: "COMPLETED", result: { path: "D:\preview\cleaned.xyz", frames_written: 3800 }, error: null });
    }, 900);
    return { job_id: "job-export", dest_path: "D:\preview\cleaned.xyz" };
  },
  "dataset.register": () => {
    window.setTimeout(() => {
      mockEmit("job.finished", { job_id: "job-register", status: "COMPLETED", result: { dataset_id: "ds-cleaned" }, error: null });
    }, 800);
    return { job_id: "job-register" };
  },
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
  "settings.get": (p) => ({ value: p?.key === "workspace.activeDatasetId" ? "ds-gaas" : null }),
  "settings.set": () => ({}),
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
      execution: { devices: ["cpu", "cuda"], num_threads: true, cooperative_cancel: false },
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
  // mutable run rows + a scripted job lifecycle so the Results page can be
  // watched flipping QUEUED -> RUNNING -> COMPLETED without the real backend
  "result.list": (p) => {
    startJobPlaybook();
    return p.dataset_id ? RUNS.filter((run) => run.dataset_id === p.dataset_id) : RUNS;
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
    mockRecordAnalysisRow("ana-mock-pca", "pca", { mode: mockLatestPcaMode, preprocess: mockLatestPcaPreprocess });
    window.setTimeout(() => {
      mockEmit("job.finished", { job_id: "job-pca-live", status: "COMPLETED", result: { analysis_id: "ana-mock-pca" }, error: null });
    }, 800);
    return { job_id: "job-pca-live", analysis_id: "ana-mock-pca" };
  },
  "analysis.list": () => Array.from(mockAnalysisRows.values()),
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
  "analysis.umap": (p) => {
    mockLatestPcaMode = String(p.mode ?? mockLatestPcaMode);
    mockLatestPcaPreprocess = String(p.preprocess ?? mockLatestPcaPreprocess);
    return mockAnalysisSubmit("job-umap-live", "ana-mock-umap", "projection", "umap");
  },
  "analysis.tsne": (_p) => mockAnalysisSubmit("job-tsne-live", "ana-mock-tsne", "projection", "tsne"),
  "analysis.neighbors": (_p) => mockAnalysisSubmit("job-neighbors-live"),
  "analysis.similarity": (_p) => mockAnalysisSubmit("job-similarity-live", "ana-mock-similarity", "similarity"),
  "analysis.pairwise": (_p) => mockAnalysisSubmit("job-pairwise-live", "ana-mock-pairwise", "pairwise_similarity"),
  "analysis.cluster": (_p) => mockAnalysisSubmit("job-cluster-live", "ana-mock-clusters", "clusters"),
  "analysis.outlier": (_p) => mockAnalysisSubmit("job-outlier-live", "ana-mock-outliers", "outliers"),
  "analysis.sampling": (_p) => mockAnalysisSubmit("job-sampling-live", "ana-mock-sampling", "sampling"),
  "analysis.coverage": (_p) => mockAnalysisSubmit("job-coverage-live", "ana-mock-coverage", "coverage"),
  "analysis.overlap": (_p) => mockAnalysisSubmit("job-overlap-live", "ana-mock-overlap", "overlap"),
  "analysis.acquisition": (p) => {
    mockLatestAcquisitionMethod = String(p.acquisition_method ?? "novelty_fps");
    return mockAnalysisSubmit("job-acquisition-live", "ana-mock-acquisition", "acquisition");
  },
  "analysis.compare": (_p) => mockAnalysisSubmit("job-compare-live", "ana-mock-compare", "compare"),
  "analysis.mantel": (_p) => mockAnalysisSubmit("job-mantel-live", "ana-mock-mantel", "mantel"),
  "analysis.feature_variance": (p) => {
    const near = Number(p.near_zero_relative_threshold);
    const low = Number(p.low_variance_relative_threshold);
    const normalizedNear = Number.isFinite(near) ? Math.min(1, Math.max(0, near)) : 1e-4;
    mockFeatureVarianceSettings = {
      near_zero_relative_threshold: normalizedNear,
      low_variance_relative_threshold: Number.isFinite(low) ? Math.min(1, Math.max(normalizedNear, low)) : Math.max(normalizedNear, 1e-2),
    };
    const response = mockAnalysisSubmit("job-feature-variance-live", "ana-mock-feature-variance", "feature_variance");
    mockRecordAnalysisRow("ana-mock-feature-variance", "feature_variance", { ...mockFeatureVarianceSettings, feature_variance_schema: 2, top_k: p.top_k ?? 20 });
    return response;
  },
  "analysis.feature_correlation": (p) => {
    const threshold = Number(p.correlation_threshold);
    mockFeatureCorrelationSettings = {
      method: p.method === "spearman" ? "spearman" : "pearson",
      correlation_threshold: Number.isFinite(threshold) ? Math.min(1, Math.max(0, threshold)) : 0.95,
    };
    const response = mockAnalysisSubmit("job-feature-correlation-live", "ana-mock-feature-correlation", "feature_correlation");
    mockRecordAnalysisRow("ana-mock-feature-correlation", "feature_correlation", { ...mockFeatureCorrelationSettings, feature_correlation_schema: 3, top_k: p.top_k ?? 20 });
    return response;
  },
  "analysis.effective_dimension": (p) => {
    mockLatestEffectiveDimensionPreprocess = p.preprocess === "center" ? "center" : "standardized";
    const response = mockAnalysisSubmit("job-effective-dimension-live", "ana-mock-effective-dimension", "effective_dimension");
    mockRecordAnalysisRow("ana-mock-effective-dimension", "effective_dimension", { preprocess: mockLatestEffectiveDimensionPreprocess });
    return response;
  },
  "analysis.property_correlation": (_p) => mockAnalysisSubmit("job-property-live", "ana-mock-property", "property_correlation"),
  "analysis.local_diversity": (_p) => mockAnalysisSubmit("job-local-live", "ana-mock-local", "local_diversity"),
  "analysis.kernel": (_p) => mockAnalysisSubmit("job-kernel-live", "ana-mock-kernel", "kernel"),
  "analysis.trajectory": (_p) => mockAnalysisSubmit("job-trajectory-live", "ana-mock-trajectory", "trajectory"),
  "analysis.drift": (_p) => mockAnalysisSubmit("job-drift-live", "ana-mock-drift", "drift"),
  "analysis.sensitivity": (_p) => mockAnalysisSubmit("job-sensitivity-live", "ana-mock-sensitivity", "sensitivity"),
  "analysis.perturbation_sensitivity": (_p) => mockAnalysisSubmit("job-perturbation-live", "ana-mock-perturbation", "perturbation_sensitivity"),
  "analysis.export": (_p) => mockAnalysisSubmit("job-export-live", "ana-mock-export", "projection", null),
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
        energy_per_atom: cluster ? -3.2 : -4.4,
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
};

// ---------- Tauri internals stub ----------
const eventListeners: { event: string; fn: (evt: unknown) => void }[] = [];
let nextMockId = 1;

function mockEmit(event: string, data: Record<string, unknown>) {
  const line = JSON.stringify({ protocol_version: 1, event, data });
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
    device: "cuda",
    status: "COMPLETED",
    created_at: new Date(NOW - 3600_000).toISOString(),
    started_at: new Date(NOW - 3540_000).toISOString(),
    finished_at: new Date(NOW - 3480_000).toISOString(),
    result_path: "mock",
    shape: "[12480, 256]",
    feature_space_signature: "dpa2-compatible-feature-space",
    feature_count: 256,
    row_semantics: "structure",
  },
  {
    id: "run-dpa2-si",
    dataset_id: "ds-si",
    dataset_name: "Si Training Set",
    descriptor_name: "DPA-2",
    engine_version: "0.3.2",
    scope: "dataset",
    device: "cuda",
    status: "COMPLETED",
    created_at: new Date(NOW - 7200_000).toISOString(),
    started_at: new Date(NOW - 7140_000).toISOString(),
    finished_at: new Date(NOW - 7080_000).toISOString(),
    result_path: "mock",
    shape: "[6320, 256]",
    feature_space_signature: "dpa2-compatible-feature-space",
    feature_count: 256,
    row_semantics: "structure",
  },
  {
    id: "run-soap",
    dataset_id: "ds-gaas",
    dataset_name: "GaAs Training Set",
    descriptor_name: "SOAP",
    engine_version: "0.3.2",
    scope: "dataset",
    device: "cpu",
    status: "QUEUED",
    created_at: new Date(NOW - 2 * 60000).toISOString(),
    started_at: null,
    finished_at: null,
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
    device: "cpu",
    status: "COMPLETED",
    created_at: new Date(NOW - 90 * 60000).toISOString(),
    started_at: new Date(NOW - 5340_000).toISOString(),
    finished_at: new Date(NOW - 5280_000).toISOString(),
    result_path: "mock",
    shape: "[12480, 96]",
    feature_space_signature: "ace-feature-space",
    feature_count: 96,
    row_semantics: "structure",
  },
];

// one-shot timeline for job-soap: the Results table should show QUEUED on
// mount, RUNNING within ~1.5s of the first job.progress, COMPLETED at ~4.5s
let jobPlaybookStarted = false;
function startJobPlaybook() {
  if (jobPlaybookStarted) return;
  jobPlaybookStarted = true;
  window.setTimeout(() => {
    const soap = RUNS.find((run) => run.id === "run-soap");
    if (!soap) return;
    soap.status = "RUNNING";
    soap.started_at = new Date().toISOString();
    mockEmit("job.progress", { job_id: "job-soap", progress: 0.5, completed: 3160, total: 6320, message: "Computing SOAP descriptors" });
  }, 1500);
  window.setTimeout(() => {
    mockEmit("job.progress", { job_id: "job-soap", progress: 0.85, completed: 5372, total: 6320, message: "Computing SOAP descriptors" });
  }, 3000);
  window.setTimeout(() => {
    const soap = RUNS.find((run) => run.id === "run-soap");
    if (!soap) return;
    soap.status = "COMPLETED";
    soap.finished_at = new Date().toISOString();
    soap.result_path = "mock";
    soap.shape = "[6320, 432]";
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
      return JSON.stringify({ protocol_version: 1, event: "backend.ready", data: null });
    }
    if (cmd === "backend_request") {
      const frame: MockFrame = {
        protocol_version: 1,
        id: nextMockId++,
        method: args?.method as string,
        params: (args?.params as Record<string, unknown>) ?? {},
      };
      window.setTimeout(() => {
        const handler = METHODS[frame.method ?? ""];
        const payload = JSON.stringify(
          handler
            ? { protocol_version: 1, id: frame.id, result: handler(frame.params ?? {}) }
            : { protocol_version: 1, id: frame.id, error: { code: "NO_HANDLER", message: "Preview mock method is unavailable.", error_id: "preview" } },
        );
        // route strictly by event name, like the Tauri event system
        for (const l of [...eventListeners]) {
          if (l.event === "backend-message") l.fn({ event: "backend-message", payload });
        }
      }, 25);
      return frame.id;
    }
    // backend_restart, plugin:dialog|*, anything else → no-op
    return null;
  },
};

async function bootstrap() {
  // Same mounting path as main.tsx; loaded asynchronously so the mock backend
  // above is fully installed before the real app boots.
  const { mountApp } = await import("./AppMount");
  mountApp(document.getElementById("root")!);
}

void bootstrap();

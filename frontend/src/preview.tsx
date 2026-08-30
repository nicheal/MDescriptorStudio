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

function mockAnalysisSubmit(jobId: string, analysisId = "ana-mock-analysis") {
  window.setTimeout(() => {
    mockEmit("job.finished", { job_id: jobId, status: "COMPLETED", result: { analysis_id: analysisId }, error: null });
  }, 800);
  return { job_id: jobId, analysis_id: analysisId, cache: null };
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
  // mutable run rows + a scripted job lifecycle so the Analysis tab can be
  // watched flipping QUEUED -> RUNNING -> COMPLETED without the real backend
  "result.list": () => {
    startJobPlaybook();
    return RUNS;
  },
  "analysis.pca": (_p) => {
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
      dataset_ids: ["ds-gaas"],
      input_run_ids: ["run-dpa2"],
      created_at: new Date(NOW - 58 * 60000).toISOString(),
    },
  ],
  "analysis.preview": () => ({
    analysis_id: "ana-mock-analysis",
    kind: "projection",
    points: Array.from({ length: 250 }, (_, i) => ({
      i,
      frame: i % 6320,
      x: Math.sin(i / 17) * 3 + gauss(0, 0.25),
      y: Math.cos(i / 23) * 2 + gauss(0, 0.25),
      sample_id: `frame:${i}`,
    })),
  }),
  "analysis.umap": (_p) => {
    window.setTimeout(() => mockEmit("job.finished", { job_id: "job-umap-live", status: "COMPLETED", result: { analysis_id: "ana-mock-analysis" }, error: null }), 800);
    return { job_id: "job-umap-live", analysis_id: "ana-mock-analysis", cache: null };
  },
  "analysis.tsne": (_p) => {
    return mockAnalysisSubmit("job-tsne-live");
  },
  "analysis.neighbors": (_p) => mockAnalysisSubmit("job-neighbors-live"),
  "analysis.similarity": (_p) => mockAnalysisSubmit("job-similarity-live"),
  "analysis.cluster": (_p) => mockAnalysisSubmit("job-cluster-live"),
  "analysis.outlier": (_p) => mockAnalysisSubmit("job-outlier-live"),
  "analysis.sampling": (_p) => mockAnalysisSubmit("job-sampling-live"),
  "analysis.fps": (_p) => mockAnalysisSubmit("job-fps-live"),
  "analysis.coverage": (_p) => mockAnalysisSubmit("job-coverage-live"),
  "analysis.compare": (_p) => mockAnalysisSubmit("job-compare-live"),
  "analysis.feature_variance": (_p) => mockAnalysisSubmit("job-feature-variance-live"),
  "analysis.feature_correlation": (_p) => mockAnalysisSubmit("job-feature-correlation-live"),
  "analysis.effective_dimension": (_p) => mockAnalysisSubmit("job-effective-dimension-live"),
  "analysis.trajectory": (_p) => mockAnalysisSubmit("job-trajectory-live"),
  "analysis.drift": (_p) => mockAnalysisSubmit("job-drift-live"),
  "analysis.sensitivity": (_p) => mockAnalysisSubmit("job-sensitivity-live"),
  "analysis.export": (_p) => mockAnalysisSubmit("job-export-live", "ana-mock-export"),
  "result.get_pca": (p) => {
    const gauss2 = (mu: number, sigma: number) => {
      let u = 0, v = 0;
      while (u === 0) u = Math.random();
      while (v === 0) v = Math.random();
      return mu + sigma * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
    };
    const points = Array.from({ length: 250 }, (_, i) => {
      const cluster = i % 2;
      const energy = cluster ? gauss2(-3.2, 0.25) : gauss2(-4.4, 0.3);
      return {
        i,
        frame: i % 6320,
        pc1: cluster ? gauss2(3, 1.1) : gauss2(-3, 1.2),
        pc2: cluster ? gauss2(1.5, 1.3) : gauss2(-1.5, 1.2),
        energy: Math.round(energy * 1e4) / 1e4,
        force_max: Math.round(Math.abs(gauss2(2.6, 1)) * 1e4) / 1e4,
        volume: Math.round(Math.abs(gauss2(650, 70)) * 10) / 10,
      };
    });
    return {
      analysis_id: "ana-mock-pca",
      run_id: p.run_id ?? "run-dpa2",
      mode: p.mode ?? "structure",
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
];

// one-shot timeline for job-soap: the Analysis table should show QUEUED on
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
  const { theme } = await import("./theme");
  const { default: App } = await import("./App");
  ReactDOM.createRoot(document.getElementById("root")!).render(
    React.createElement(
      React.StrictMode,
      null,
      React.createElement(
        antd.ConfigProvider,
        { theme },
        React.createElement(antd.App, null, React.createElement(App)),
      ),
    ),
  );
}

void bootstrap();

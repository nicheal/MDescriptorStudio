/*
 * Analysis is the successor to the old Results page.  It owns one shared run
 * selector, one compact inspector, and a set of real backend-backed modules.
 * Descriptor run history lives on the separate Results page.
 * Plotly is deliberately scoped to this page; Overview remains ECharts and
 * Explore remains 3Dmol.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Plot from "react-plotly.js";
import type { Data, Layout } from "plotly.js";
import {
  App as AntApp,
  Button,
  Empty,
  Input,
  InputNumber,
  Progress,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import {
  ArrowRight16Regular,
  ArrowSync16Regular,
  ArrowDownload16Regular,
  CheckmarkCircle16Regular,
  Delete16Regular,
} from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { activeDataset, useWorkspace, type PcaMode } from "../stores/workspace";
import { trackJob, watchJob } from "../stores/jobs";
import StructurePreview from "../components/StructurePreview";
import { normalizePoints, selectedDisplayIndices, type AnalysisPoint } from "./analysisPreview";
import AnalysisResultVisualization, { type AnalysisArrays } from "./analysisVisualizations";
import type {
  AnalysisJobResponse,
  AnalysisChunk,
  AnalysisPreview,
  AnalysisRow,
  FramePayload,
  PcaAnalysisResponse,
  PcaPayload,
  RunRow,
} from "../types/protocol";

type TabKey = "overview" | "projection" | "similarity" | "clusters" | "outliers" | "sampling" | "coverage" | "compare" | "local" | "kernel";
type ProjectionName = "pca" | "umap" | "tsne";
type OverviewAnalysis = "feature_variance" | "feature_correlation" | "effective_dimension" | "property_correlation" | "trajectory" | "drift" | "sensitivity" | "perturbation_sensitivity";
type CompareMode = "geometry" | "mantel";

type Point = AnalysisPoint;
type NumericArrays = AnalysisArrays;

type CachedAnalysis = {
  preview: AnalysisPreview | null;
  points: Point[];
  selectedIndices: number[];
  arrays: NumericArrays;
};

type ProjectionOverrides = {
  mode?: PcaMode;
  preprocess?: string;
};

type Metric = {
  label: string;
  value: string;
};

const TAB_LABELS: Record<TabKey, string> = {
  overview: "Overview",
  projection: "Projection",
  similarity: "Similarity",
  clusters: "Clusters",
  outliers: "Outliers",
  sampling: "Sampling",
  coverage: "Coverage",
  compare: "Compare",
  local: "Local",
  kernel: "Kernel",
};

const OVERVIEW_KIND_LABELS: Record<string, string> = {
  feature_variance: "FEATURE VARIANCE",
  feature_correlation: "FEATURE CORRELATION",
  effective_dimension: "EFFECTIVE DIMENSION",
  trajectory: "TRAJECTORY",
  drift: "DATASET DRIFT",
  sensitivity: "PARAMETER SENSITIVITY",
  perturbation_sensitivity: "STRUCTURAL PERTURBATION SENSITIVITY",
};

const ARTIFACT_ARRAYS: Record<string, string[]> = {
  pairwise_similarity: ["similarity_matrix", "distance_matrix", "sample_indices"],
  compare: ["left_coords", "right_coords", "left_pair_distances", "right_pair_distances", "neighbor_overlap", "sample_indices"],
  mantel: ["left_pair_distances", "right_pair_distances", "null_distribution", "sample_indices"],
  feature_correlation: ["correlation_matrix", "correlation_feature_indices"],
  effective_dimension: ["explained_variance"],
  property_correlation: ["targets", "predictions", "residuals", "pair_distance", "pair_property_delta"],
  coverage: ["projection_coords", "projection_source", "projection_sample_indices", "labels", "distances"],
  overlap: ["projection_coords", "projection_source", "projection_sample_indices", "labels", "distances"],
  drift: ["projection_coords", "projection_source", "projection_sample_indices", "labels", "distances"],
  trajectory: ["time", "frames", "step_distance", "reference_distance", "cumulative_distance", "event_indices"],
  perturbation_sensitivity: ["amplitudes", "mean_response", "median_response", "p95_response", "max_response", "response_matrix", "sample_indices"],
  local_diversity: ["coords", "sample_indices", "labels", "scores", "cluster_labels", "elements", "coordination", "neighbor_offsets", "neighbor_indices", "neighbor_distances"],
  kernel: ["kernel_matrix", "eigenvalues", "sample_indices"],
};

function pcaPayloadPoints(payload: PcaPayload): Point[] {
  return payload.points.map((point) => ({
    i: point.i,
    frame: point.frame,
    row: point.atom,
    x: point.pc1,
    y: point.pc2,
    energy: point.energy,
    force_max: point.force_max,
    volume: point.volume,
  }));
}

function selectedIndicesFromPreview(result: AnalysisPreview): number[] {
  return (Array.isArray(result.selected) ? result.selected : [])
    .map((row) => Number(row.i ?? row.sample_index))
    .filter((index) => Number.isInteger(index) && index >= 0);
}

function tabForAnalysisType(analysisType: string): TabKey {
  if (["pca", "umap", "tsne"].includes(analysisType)) return "projection";
  if (["feature_variance", "feature_correlation", "effective_dimension", "property_correlation", "trajectory", "drift", "sensitivity", "perturbation_sensitivity"].includes(analysisType)) return "overview";
  if (["similarity", "neighbors", "pairwise", "pairwise_similarity"].includes(analysisType)) return "similarity";
  if (["kmeans", "dbscan", "hdbscan", "agglomerative", "hierarchical"].includes(analysisType)) return "clusters";
  if (["lof", "knn", "isolation_forest", "isolation-forest", "iforest", "mahalanobis", "mahalanobis_distance"].includes(analysisType)) return "outliers";
  if (["fps", "random", "stratified", "cluster_representative", "per_element", "acquisition", "novelty_fps"].includes(analysisType)) return "sampling";
  if (["coverage", "overlap"].includes(analysisType)) return "coverage";
  if (["compare", "mantel"].includes(analysisType)) return "compare";
  if (analysisType === "local_diversity") return "local";
  if (analysisType === "kernel") return "kernel";
  return "overview";
}

export default function Analysis() {
  const { message } = AntApp.useApp();
  const st = useWorkspace();
  const dataset = activeDataset(st);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [analyses, setAnalyses] = useState<AnalysisRow[]>([]);
  const [tab, setTab] = useState<TabKey>("overview");
  const [projection, setProjection] = useState<ProjectionName>("pca");
  const [mode, setMode] = useState<PcaMode>("structure");
  const [preprocess, setPreprocess] = useState("raw");
  const [points, setPoints] = useState<Point[]>([]);
  const [preview, setPreview] = useState<AnalysisPreview | null>(null);
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lastJobProgress, setLastJobProgress] = useState<number | null>(null);
  const [selectedIndices, setSelectedIndices] = useState<number[]>([]);
  const [inspectedPoint, setInspectedPoint] = useState<Point | null>(null);
  const [selectedFrame, setSelectedFrame] = useState<FramePayload | null>(null);
  const [selectedFrameBusy, setSelectedFrameBusy] = useState(false);
  const [colorBy, setColorBy] = useState<"none" | "energy" | "force_max" | "volume">("none");
  const [clusterAlgorithm, setClusterAlgorithm] = useState("kmeans");
  const [outlierAlgorithm, setOutlierAlgorithm] = useState("lof");
  const [samplingAlgorithm, setSamplingAlgorithm] = useState("fps");
  const [similarityMode, setSimilarityMode] = useState<"query" | "all_neighbors" | "pairwise">("query");
  const [compareMode, setCompareMode] = useState<CompareMode>("geometry");
  const [mantelMethod, setMantelMethod] = useState<"pearson" | "spearman">("pearson");
  const [mantelPermutations, setMantelPermutations] = useState(999);
  const [coverageMode, setCoverageMode] = useState<"coverage" | "overlap">("coverage");
  const [propertyName, setPropertyName] = useState("energy_per_atom");
  const [kernelName, setKernelName] = useState("rbf");
  const [localCutoff, setLocalCutoff] = useState(3.0);
  const [secondRun, setSecondRun] = useState<string | null>(null);
  const [exportFormat, setExportFormat] = useState("csv");
  const [exportPath, setExportPath] = useState("");
  const [k, setK] = useState(10);
  const [nClusters, setNClusters] = useState(6);
  const [nSamples, setNSamples] = useState(1000);
  const [uncertaintyK, setUncertaintyK] = useState(8);
  const [contamination, setContamination] = useState(0.01);
  const [queryIndex, setQueryIndex] = useState(0);
  const [overviewAnalysis, setOverviewAnalysis] = useState<OverviewAnalysis>("feature_variance");
  const [trajectoryStep, setTrajectoryStep] = useState(1);
  const [perturbationType, setPerturbationType] = useState<"jitter" | "strain">("jitter");
  const [perturbationCount, setPerturbationCount] = useState(8);
  const [perturbationMaximum, setPerturbationMaximum] = useState(0.2);
  const [perturbationMetric, setPerturbationMetric] = useState("euclidean");
  const [tsnePerplexity, setTsnePerplexity] = useState(30);
  const [overviewArrays, setOverviewArrays] = useState<NumericArrays>({});
  const [overviewArraysBusy, setOverviewArraysBusy] = useState(false);
  const [loadingAnalysisId, setLoadingAnalysisId] = useState<string | null>(null);
  const operationRef = useRef(0);
  const analysisCacheRef = useRef(new Map<string, CachedAnalysis>());

  const selectedRun = st.activeDescriptorRunId;
  const setSelectedRun = st.setActiveRun;
  const selectedPoint = inspectedPoint ?? points.find((point) => point.i === selectedIndices[0]) ?? null;

  const refresh = useCallback(async () => {
    if (!dataset) {
      setRuns([]);
      setAnalyses([]);
      return;
    }
    try {
      const [resultRows, analysisRows] = await Promise.all([
        ipc.request<RunRow[]>("result.list", { dataset_id: dataset.id }),
        ipc.request<AnalysisRow[]>("analysis.list", { }),
      ]);
      setRuns(resultRows);
      setAnalyses(analysisRows.filter((row) => (row.dataset_ids ?? []).includes(dataset.id) || row.descriptor_run_id && resultRows.some((run) => run.id === row.descriptor_run_id)));
      const current = useWorkspace.getState().activeDescriptorRunId;
      const completedRuns = resultRows.filter((row) => row.status === "COMPLETED");
      setSelectedRun(current && completedRuns.some((row) => row.id === current) ? current : completedRuns[0]?.id ?? null);
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "ANALYSIS"}: ${err.message ?? "Could not load analysis runs"}`);
    }
  }, [dataset, message, setSelectedRun]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    operationRef.current += 1;
    setBusy(false);
    setLastJobProgress(null);
    setPoints([]);
    setPreview(null);
    setAnalysisId(null);
    setSelectedIndices([]);
    setInspectedPoint(null);
    setSelectedFrame(null);
    setSecondRun(null);
    setOverviewArrays({});
    setLoadingAnalysisId(null);
  }, [dataset?.id, selectedRun]);

  useEffect(() => {
    const selectedRow = runs.find((run) => run.id === selectedRun);
    const secondRow = secondRun ? runs.find((run) => run.id === secondRun) : undefined;
    const sensitivityPair = tab === "overview" && overviewAnalysis === "sensitivity";
    const sensitivityDescriptorMismatch = sensitivityPair
      && selectedRow
      && secondRow
      && selectedRow.descriptor_name !== secondRow.descriptor_name;
    if (secondRun && (secondRun === selectedRun || !secondRow || secondRow.status !== "COMPLETED" || sensitivityDescriptorMismatch)) {
      setSecondRun(null);
    }
  }, [overviewAnalysis, runs, secondRun, selectedRun, tab]);

  useEffect(() => {
    const offFinished = ipc.on("job.finished", () => void refresh());
    return offFinished;
  }, [refresh]);

  useEffect(() => {
    if (!dataset || !selectedPoint) {
      setSelectedFrame(null);
      return;
    }
    let disposed = false;
    setSelectedFrameBusy(true);
    void ipc.request<FramePayload>("dataset.frame", { id: dataset.id, index: selectedPoint.frame, bond_cutoff: 2.4 })
      .then((frame) => { if (!disposed) setSelectedFrame(frame); })
      .catch(() => { if (!disposed) setSelectedFrame(null); })
      .finally(() => { if (!disposed) setSelectedFrameBusy(false); });
    return () => { disposed = true; };
  }, [dataset, selectedPoint]);

  useEffect(() => {
    const kind = String(preview?.kind ?? "");
    const arrayNames = ARTIFACT_ARRAYS[kind] ?? [];
    if (!analysisId || !arrayNames.length) {
      setOverviewArrays({});
      setOverviewArraysBusy(false);
      return;
    }

    const cached = analysisCacheRef.current.get(analysisId);
    const cachedArrays = cached?.arrays ?? {};
    const missingArrays = arrayNames.filter((name) => !Object.prototype.hasOwnProperty.call(cachedArrays, name));
    if (!missingArrays.length) {
      setOverviewArrays(cachedArrays);
      setOverviewArraysBusy(false);
      return;
    }

    let disposed = false;
    setOverviewArraysBusy(true);
    void Promise.all(missingArrays.map(async (name) => {
      try {
        const chunk = await ipc.request<AnalysisChunk>("analysis.chunk", {
          analysis_id: analysisId,
          array: name,
          offset: 0,
          limit: 20_000,
          column_end: 2_000,
        });
        return [name, chunk.data] as const;
      } catch {
        return [name, []] as const;
      }
    })).then((entries) => {
      if (disposed) return;
      const arrays = { ...cachedArrays, ...Object.fromEntries(entries) };
      const current = analysisCacheRef.current.get(analysisId);
      analysisCacheRef.current.set(analysisId, {
        preview: current?.preview ?? cached?.preview ?? preview,
        points: current?.points ?? cached?.points ?? normalizePoints(preview ?? { analysis_id: analysisId }),
        selectedIndices: current?.selectedIndices ?? cached?.selectedIndices ?? selectedIndicesFromPreview(preview ?? { analysis_id: analysisId }),
        arrays,
      });
      setOverviewArrays(arrays);
    }).finally(() => {
      if (!disposed) setOverviewArraysBusy(false);
    });

    return () => { disposed = true; };
  }, [analysisId, preview]);

  const runRequest = useCallback(async (method: string, params: Record<string, unknown>, label: string) => {
    if (!selectedRun) {
      message.warning("Select a completed descriptor run first");
      return null;
    }
    const requestRunId = selectedRun;
    const requestDatasetId = dataset?.id;
    const operation = ++operationRef.current;
    const isCurrent = () => operationRef.current === operation
      && useWorkspace.getState().activeDescriptorRunId === requestRunId
      && useWorkspace.getState().activeDatasetId === requestDatasetId;
    setLoadingAnalysisId(null);
    setBusy(true);
    setLastJobProgress(0);
    setPoints([]);
    setPreview(null);
    setAnalysisId(null);
    setOverviewArrays({});
    setSelectedIndices([]);
    setInspectedPoint(null);
    useWorkspace.getState().setSelectedSample(null);
    try {
      const response = await ipc.request<AnalysisJobResponse>(method, { ...params, run_id: requestRunId, seed: params.seed ?? 42 });
      let id = response.analysis_id;
      if (response.job_id) {
        trackJob(response.job_id, method);
        const offProgress = ipc.on("job.progress", (data) => {
          const event = data as { job_id: string; progress: number };
          if (event.job_id === response.job_id) setLastJobProgress(event.progress);
        });
        const done = await watchJob(response.job_id);
        offProgress();
        if (done.status !== "COMPLETED") {
          if (!isCurrent()) return null;
          message.error(`${label} ${done.status}: ${done.error?.message ?? ""}`);
          return null;
        }
        if (typeof done.result?.analysis_id === "string") id = done.result.analysis_id;
      }
      if (!id) throw new Error(`${method} returned no analysis_id`);
      if (!isCurrent()) return null;
      const frontendCached = response.job_id ? undefined : analysisCacheRef.current.get(id);
      if (frontendCached) {
        setAnalysisId(id);
        setPreview(frontendCached.preview);
        setPoints(frontendCached.points);
        setSelectedIndices(frontendCached.selectedIndices);
        setOverviewArrays(frontendCached.arrays);
        setLastJobProgress(1);
        message.success(`${label} loaded from cache`);
        return id;
      }
      const result = await ipc.request<AnalysisPreview>("analysis.preview", { analysis_id: id, limit: 20_000 });
      if (!isCurrent()) return null;
      const nextPoints = normalizePoints(result);
      const nextSelectedIndices = selectedIndicesFromPreview(result);
      const cached = analysisCacheRef.current.get(id);
      analysisCacheRef.current.set(id, {
        preview: result,
        points: nextPoints,
        selectedIndices: nextSelectedIndices,
        arrays: cached?.arrays ?? {},
      });
      setAnalysisId(id);
      setPreview(result);
      setPoints(nextPoints);
      setSelectedIndices(nextSelectedIndices);
      setLastJobProgress(1);
      message.success(response.job_id ? `${label} complete` : `${label} loaded from cache`);
      return id;
    } catch (error) {
      const err = error as { code?: string; message?: string };
      if (isCurrent()) message.error(`${err.code ?? label}: ${err.message ?? "analysis failed"}`);
      return null;
    } finally {
      if (operationRef.current === operation) setBusy(false);
    }
  }, [dataset?.id, message, selectedRun]);

  const runProjection = useCallback(async ({ mode: requestedMode, preprocess: requestedPreprocess }: ProjectionOverrides = {}) => {
    if (!selectedRun) return;
    const requestRunId = selectedRun;
    const requestDatasetId = dataset?.id;
    const activeMode = requestedMode ?? mode;
    const activePreprocess = requestedPreprocess ?? preprocess;
    setLoadingAnalysisId(null);
    if (projection === "pca") {
      const operation = ++operationRef.current;
      const isCurrent = () => operationRef.current === operation
        && useWorkspace.getState().activeDescriptorRunId === requestRunId
        && useWorkspace.getState().activeDatasetId === requestDatasetId;
      setBusy(true);
      setLastJobProgress(0);
      setPoints([]);
      setPreview(null);
      setAnalysisId(null);
      setOverviewArrays({});
      setSelectedIndices([]);
      setInspectedPoint(null);
      useWorkspace.getState().setSelectedSample(null);
      try {
        const response = await ipc.request<PcaAnalysisResponse>("analysis.pca", { run_id: requestRunId, mode: activeMode, seed: 42, preprocess: activePreprocess });
        let id = response.analysis_id;
        if (response.job_id) {
          trackJob(response.job_id, "analysis.pca");
          const offProgress = ipc.on("job.progress", (data) => {
            const event = data as { job_id: string; progress: number };
            if (event.job_id === response.job_id) setLastJobProgress(event.progress);
          });
          const done = await watchJob(response.job_id);
          offProgress();
          if (done.status !== "COMPLETED") {
            if (!isCurrent()) return;
            message.error(`PCA ${done.status}: ${done.error?.message ?? ""}`);
            return;
          }
          if (typeof done.result?.analysis_id === "string") id = done.result.analysis_id;
        }
        if (!isCurrent()) return;
        const frontendCached = response.job_id ? undefined : analysisCacheRef.current.get(id);
        if (frontendCached) {
          setAnalysisId(id);
          setPreview(frontendCached.preview);
          setPoints(frontendCached.points);
          setSelectedIndices(frontendCached.selectedIndices);
          setOverviewArrays(frontendCached.arrays);
          setLastJobProgress(1);
          message.success("PCA loaded from cache");
          return;
        }
        const payload = await ipc.request<PcaPayload>("result.get_pca", { analysis_id: id });
        if (!isCurrent()) return;
        const nextPoints = pcaPayloadPoints(payload);
        const cached = analysisCacheRef.current.get(id);
        analysisCacheRef.current.set(id, {
          preview: null,
          points: nextPoints,
          selectedIndices: [],
          arrays: cached?.arrays ?? {},
        });
        setAnalysisId(id);
        setPreview(null);
        setPoints(nextPoints);
        setSelectedIndices([]);
        setLastJobProgress(1);
        message.success(response.job_id ? "PCA complete" : "PCA loaded from cache");
      } catch (error) {
        const err = error as { code?: string; message?: string };
        if (isCurrent()) message.error(`${err.code ?? "PCA"}: ${err.message ?? "analysis failed"}`);
      } finally {
        if (operationRef.current === operation) setBusy(false);
      }
      return;
    }
    const projectionParams = projection === "umap"
      ? { mode: activeMode, preprocess: activePreprocess, n_neighbors: 15, min_dist: 0.1 }
      : { mode: activeMode, preprocess: activePreprocess, perplexity: tsnePerplexity === 30 ? undefined : tsnePerplexity, max_iter: 1000 };
    await runRequest(`analysis.${projection}`, projectionParams, projection.toUpperCase());
  }, [dataset?.id, message, mode, preprocess, projection, runRequest, selectedRun, tsnePerplexity]);

  const handlePreprocessChange = useCallback((value: string) => {
    setPreprocess(value);
    if (projection === "pca" && selectedRun && value !== preprocess) {
      void runProjection({ preprocess: value });
    }
  }, [preprocess, projection, runProjection, selectedRun]);

  const loadAnalysis = useCallback(async (row: AnalysisRow) => {
    if (!dataset || !selectedRun || row.status !== "COMPLETED") return;
    const inputRunIds = row.input_run_ids?.length ? row.input_run_ids : [row.descriptor_run_id];
    if (!inputRunIds.includes(selectedRun)) {
      message.warning("Select the source descriptor run before loading this analysis");
      return;
    }

    const operation = ++operationRef.current;
    const requestRunId = selectedRun;
    const requestDatasetId = dataset.id;
    const isCurrent = () => operationRef.current === operation
      && useWorkspace.getState().activeDescriptorRunId === requestRunId
      && useWorkspace.getState().activeDatasetId === requestDatasetId;
    const analysisType = row.analysis_type.toLowerCase();
    const analysisTab = tabForAnalysisType(analysisType);
    setTab(analysisTab);
    if (analysisTab === "overview" && ["feature_variance", "feature_correlation", "effective_dimension", "property_correlation", "trajectory", "drift", "sensitivity", "perturbation_sensitivity"].includes(analysisType)) {
      setOverviewAnalysis(analysisType as OverviewAnalysis);
    }
    if (analysisTab === "projection") {
      setProjection(analysisType as ProjectionName);
      if (analysisType === "pca") {
        const savedMode: PcaMode = row.parameters?.mode === "atom" ? "atom" : "structure";
        const savedPreprocess = row.parameters?.preprocess;
        const savedPreprocessValue = savedPreprocess === "raw" || savedPreprocess === "standardized" ? savedPreprocess : "center";
        setMode(savedMode);
        setPreprocess(savedPreprocessValue);
      }
    }
    if (analysisType === "pairwise" || analysisType === "pairwise_similarity") setSimilarityMode("pairwise");
    if (analysisType === "overlap") setCoverageMode("overlap");
    if (analysisType === "acquisition") setSamplingAlgorithm(row.parameters?.acquisition_method === "uncertainty_diversity" ? "uncertainty_diversity" : "novelty_fps");
    if (analysisType === "mantel") setCompareMode("mantel");

    setLoadingAnalysisId(row.id);
    setBusy(true);
    setLastJobProgress(null);
    setPoints([]);
    setPreview(null);
    setAnalysisId(null);
    setOverviewArrays({});
    setSelectedIndices([]);
    setInspectedPoint(null);
    setSelectedFrame(null);
    useWorkspace.getState().setSelectedSample(null);

    try {
      const cached = analysisCacheRef.current.get(row.id);
      if (cached) {
        if (!isCurrent()) return;
        setAnalysisId(row.id);
        setPreview(cached.preview);
        setPoints(cached.points);
        setSelectedIndices(cached.selectedIndices);
        setOverviewArrays(cached.arrays);
        setLastJobProgress(1);
        message.success(`Loaded cached ${analysisType.toUpperCase()}`);
        return;
      }

      if (analysisType === "pca") {
        const payload = await ipc.request<PcaPayload>("result.get_pca", { analysis_id: row.id });
        if (!isCurrent()) return;
        const nextPoints = pcaPayloadPoints(payload);
        const nextCached: CachedAnalysis = { preview: null, points: nextPoints, selectedIndices: [], arrays: {} };
        analysisCacheRef.current.set(row.id, nextCached);
        setAnalysisId(row.id);
        setPreview(null);
        setPoints(nextPoints);
        setSelectedIndices([]);
        setOverviewArrays({});
      } else {
        const result = await ipc.request<AnalysisPreview>("analysis.preview", { analysis_id: row.id, limit: 20_000 });
        if (!isCurrent()) return;
        const nextPoints = normalizePoints(result);
        const nextSelectedIndices = selectedIndicesFromPreview(result);
        const nextCached: CachedAnalysis = { preview: result, points: nextPoints, selectedIndices: nextSelectedIndices, arrays: {} };
        analysisCacheRef.current.set(row.id, nextCached);
        setAnalysisId(row.id);
        setPreview(result);
        setPoints(nextPoints);
        setSelectedIndices(nextSelectedIndices);
        setOverviewArrays({});
      }
      setLastJobProgress(1);
      message.success(`Loaded cached ${analysisType.toUpperCase()}`);
    } catch (error) {
      const err = error as { code?: string; message?: string };
      if (isCurrent()) message.error(`${err.code ?? "ANALYSIS"}: ${err.message ?? "could not load analysis"}`);
    } finally {
      if (operationRef.current === operation) {
        setBusy(false);
        setLoadingAnalysisId(null);
      }
    }
  }, [dataset, message, selectedRun]);

  const runTabAnalysis = useCallback(async () => {
    if (tab === "projection") return runProjection();
    if (tab === "similarity") {
      const method = similarityMode === "pairwise" ? "analysis.pairwise" : similarityMode === "all_neighbors" ? "analysis.neighbors" : "analysis.similarity";
      const parameters = similarityMode === "pairwise"
        ? { metric: "cosine", preprocess: "raw", mode, max_samples: 400 }
        : { k, query_index: queryIndex, metric: "cosine", preprocess: "raw", mode };
      await runRequest(method, parameters, similarityMode === "pairwise" ? "Pairwise similarity" : similarityMode === "all_neighbors" ? "Neighbor graph" : "Similarity");
    } else if (tab === "clusters") {
      await runRequest("analysis.cluster", { algorithm: clusterAlgorithm, n_clusters: nClusters, preprocess: "standardized", mode }, clusterAlgorithm.toUpperCase());
    } else if (tab === "outliers") {
      await runRequest("analysis.outlier", { algorithm: outlierAlgorithm, k, contamination, preprocess: "standardized", mode }, outlierAlgorithm.toUpperCase());
    } else if (tab === "sampling") {
      if (samplingAlgorithm === "novelty_fps" || samplingAlgorithm === "uncertainty_diversity") {
        if (!secondRun || !selectedRun) {
          message.warning("Select a reference run for acquisition");
          return;
        }
        const uncertainty = samplingAlgorithm === "uncertainty_diversity";
        await runRequest("analysis.acquisition", { reference_run_id: secondRun, query_run_id: selectedRun, n_samples: nSamples, mode, acquisition_method: samplingAlgorithm, novelty_weight: 0.65, uncertainty_weight: 0.65, uncertainty_k: uncertaintyK }, uncertainty ? "Uncertainty acquisition" : "Novelty acquisition");
      } else {
        await runRequest("analysis.sampling", { algorithm: samplingAlgorithm, n_samples: nSamples, mode }, "Sampling");
      }
    } else if (tab === "coverage") {
      if (!secondRun || !selectedRun) {
        message.warning("Select a reference/query run pair");
        return;
      }
      await runRequest(`analysis.${coverageMode}`, { reference_run_id: selectedRun, query_run_id: secondRun, metric: "euclidean", mode }, coverageMode === "coverage" ? "Coverage" : "Overlap");
    } else if (tab === "compare") {
      if (!secondRun || !selectedRun) {
        message.warning("Select a descriptor run pair");
        return;
      }
      const method = compareMode === "mantel" ? "analysis.mantel" : "analysis.compare";
      const parameters = compareMode === "mantel"
        ? { left_run_id: selectedRun, right_run_id: secondRun, mode, method: mantelMethod, permutations: mantelPermutations, max_samples: 600 }
        : { left_run_id: selectedRun, right_run_id: secondRun, mode };
      await runRequest(method, parameters, compareMode === "mantel" ? "Mantel test" : "Compare");
    } else if (tab === "local") {
      await runRequest("analysis.local_diversity", { mode: "atom", n_clusters: nClusters, k, cutoff: localCutoff, max_neighbors: 128 }, "Local diversity");
    } else if (tab === "kernel") {
      await runRequest("analysis.kernel", { kernel: kernelName, mode, max_samples: 400 }, "Kernel diagnostics");
    } else if (tab === "overview") {
      if ((overviewAnalysis === "drift" || overviewAnalysis === "sensitivity") && (!secondRun || !selectedRun)) {
        message.warning("Select a reference/query run pair");
        return;
      }
      if (overviewAnalysis === "sensitivity" && selectedRun && secondRun) {
        const referenceRow = runs.find((run) => run.id === selectedRun);
        const queryRow = runs.find((run) => run.id === secondRun);
        if (!referenceRow || !queryRow || referenceRow.descriptor_name !== queryRow.descriptor_name) {
          message.warning("Parameter sensitivity requires the same descriptor; use Compare for different descriptors");
          return;
        }
      }
      const overviewParams: Record<string, unknown> = overviewAnalysis === "drift"
        ? { reference_run_id: selectedRun, query_run_id: secondRun, metric: "euclidean" }
        : overviewAnalysis === "sensitivity"
          ? { run_ids: [selectedRun, secondRun] }
          : overviewAnalysis === "perturbation_sensitivity"
            ? { perturbation: perturbationType, n_amplitudes: perturbationCount, max_amplitude: perturbationMaximum, metric: perturbationMetric, max_structures: 64, preprocess: "standardized" }
          : overviewAnalysis === "trajectory"
            ? { frame_step: trajectoryStep }
            : overviewAnalysis === "property_correlation"
              ? { property: propertyName, folds: 5, top_k: 20, mode }
            : overviewAnalysis === "feature_correlation"
              ? { top_k: 20 }
              : overviewAnalysis === "effective_dimension"
                ? { preprocess: "center" }
                : { top_k: 20 };
      await runRequest(`analysis.${overviewAnalysis}`, overviewParams, overviewAnalysis.replaceAll("_", " "));
    }
  }, [clusterAlgorithm, contamination, coverageMode, k, kernelName, localCutoff, mantelMethod, mantelPermutations, mode, nClusters, nSamples, overviewAnalysis, perturbationCount, perturbationMaximum, perturbationMetric, perturbationType, propertyName, queryIndex, runProjection, runRequest, runs, samplingAlgorithm, secondRun, selectedRun, similarityMode, tab, trajectoryStep, uncertaintyK, message, compareMode]);

  const inspectPoint = useCallback((point: Point) => {
    setInspectedPoint(point);
    if (dataset && selectedRun) {
      useWorkspace.getState().setSelectedSample({ datasetId: dataset.id, runId: selectedRun, mode: point.row == null ? mode : "atom", frame: point.frame, atom: point.row });
      useWorkspace.getState().setActiveFrame(point.frame);
    }
  }, [dataset, mode, selectedRun]);

  const handlePoint = useCallback((point: Point) => {
    setSelectedIndices([point.i]);
    inspectPoint(point);
  }, [inspectPoint]);

  const plot = useMemo(() => {
    if (!points.length) return null;
    const values = points.map((point) => colorBy === "energy" ? point.energy : colorBy === "force_max" ? point.force_max : colorBy === "volume" ? point.volume : undefined);
    const hasColor = values.some((value) => value != null && Number.isFinite(value));
    const displayedSelected = selectedDisplayIndices(points, selectedIndices);
    return (
      <Plot
        key={`${analysisId ?? "pending"}:${projection}:${mode}:${preprocess}`}
        data={[{
          type: "scattergl",
          mode: "markers",
          x: points.map((point) => point.x),
          y: points.map((point) => point.y),
          text: points.map((point) => `frame ${point.frame}${point.row == null ? "" : ` · row ${point.row}`}`),
          customdata: points.map((point) => [point.i, point.frame, point.row ?? -1]),
          marker: hasColor ? { size: 7, color: values as number[], colorscale: "Viridis", showscale: true, colorbar: { title: { text: colorBy } } } : { size: 7, color: "#0F6CBD" },
           selectedpoints: displayedSelected,
          hovertemplate: "%{text}<br>x=%{x:.5g}<br>y=%{y:.5g}<extra></extra>",
        }]}
        layout={{ autosize: true, margin: { l: 56, r: 24, t: 16, b: 48 }, paper_bgcolor: "#FFFFFF", plot_bgcolor: "#FFFFFF", dragmode: "lasso", hovermode: "closest", xaxis: { title: projection === "pca" ? "PC1" : `${projection.toUpperCase()}-1`, gridcolor: "#F0F1F3" }, yaxis: { title: projection === "pca" ? "PC2" : `${projection.toUpperCase()}-2`, gridcolor: "#F0F1F3" }, showlegend: false }}
        config={{ responsive: true, displaylogo: false, modeBarButtonsToAdd: ["select2d", "lasso2d"], modeBarButtonsToRemove: ["toImage"] }}
        style={{ width: "100%", height: "100%" }}
        onClick={(event) => {
          const index = event.points?.[0]?.pointIndex;
          if (typeof index === "number" && points[index]) handlePoint(points[index]);
        }}
        onSelected={(event) => {
          const displayIndices = (event?.points ?? []).map((point) => point.pointIndex).filter((index): index is number => typeof index === "number");
          const indices = displayIndices.map((index) => points[index]?.i).filter((index): index is number => typeof index === "number");
          setSelectedIndices(indices);
          const first = displayIndices[0];
          if (first != null && points[first]) inspectPoint(points[first]);
          else {
            setInspectedPoint(null);
            useWorkspace.getState().setSelectedSample(null);
          }
        }}
      />
    );
  }, [analysisId, colorBy, handlePoint, inspectPoint, mode, points, preprocess, projection, selectedIndices]);

  const exportSelection = async () => {
    if (!selectedRun || !exportPath.trim()) {
      message.warning("Choose an output path first");
      return;
    }
    const indices = selectedIndices.length ? selectedIndices : points.map((point) => point.i);
    try {
      const response = await ipc.request<AnalysisJobResponse>("analysis.export", { run_id: selectedRun, indices, mode, format: exportFormat, output_path: exportPath.trim() });
      if (!response.job_id) {
        message.success("Export loaded from cache");
        return;
      }
      trackJob(response.job_id, "analysis.export");
      const done = await watchJob(response.job_id);
      if (done.status === "COMPLETED") message.success(`Export written to ${String(done.result?.output_path ?? exportPath)}`);
      else message.error(`Export ${done.status}: ${done.error?.message ?? ""}`);
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "EXPORT"}: ${err.message ?? "export failed"}`);
    }
  };

  const deleteAnalysis = async (row: AnalysisRow) => {
    try {
      await ipc.request("analysis.delete", { analysis_id: row.id });
      analysisCacheRef.current.delete(row.id);
      if (analysisId === row.id) {
        setAnalysisId(null);
        setPreview(null);
        setPoints([]);
        setSelectedIndices([]);
        setOverviewArrays({});
      }
      await refresh();
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(`${err.code ?? "ANALYSIS"}: ${err.message ?? "delete failed"}`);
    }
  };

  if (!dataset) return <Empty description="Register a dataset first" style={{ marginTop: 120 }} />;
  const selectedRunRow = runs.find((run) => run.id === selectedRun);
  const completedRuns = runs.filter((run) => run.status === "COMPLETED");
  const sensitivityRuns = completedRuns.filter((run) => run.descriptor_name === selectedRunRow?.descriptor_name);
  const sensitivityPair = tab === "overview" && overviewAnalysis === "sensitivity";
  const pairRuns = sensitivityPair ? sensitivityRuns : completedRuns;
  const visibleAnalyses = analyses.filter((row) => {
    const inputRunIds = row.input_run_ids?.length ? row.input_run_ids : [row.descriptor_run_id];
    return !selectedRun || inputRunIds.includes(selectedRun);
  });
  const overviewModuleControl = <Space wrap><Typography.Text>Module</Typography.Text><Select value={overviewAnalysis} onChange={setOverviewAnalysis} options={[{ value: "feature_variance", label: "Feature variance" }, { value: "feature_correlation", label: "Feature correlation" }, { value: "effective_dimension", label: "Effective dimension" }, { value: "property_correlation", label: "Property correlation" }, { value: "trajectory", label: "Trajectory" }, { value: "drift", label: "Dataset drift" }, { value: "sensitivity", label: "Parameter sensitivity" }, { value: "perturbation_sensitivity", label: "Structural perturbation" }]} /><Typography.Text type="secondary">{overviewAnalysis === "sensitivity" ? "Compare parameter variants of the same descriptor; use Compare for different descriptors." : overviewAnalysis === "perturbation_sensitivity" ? "Recompute the selected descriptor after controlled atomic jitter or strain." : "All results stay on the backend as bounded artifacts."}</Typography.Text></Space>;
  const legacyOverview = tab === "overview" && (preview?.kind === "feature_variance" || preview?.kind === "effective_dimension");

  return (
    <div className="analysis-page">
      <section className="analysis-toolbar">
        <Space wrap>
          <Typography.Text strong>Run</Typography.Text>
          <Select
            aria-label="Analysis descriptor run"
            value={selectedRun ?? undefined}
            placeholder="Select completed run"
            style={{ width: 250 }}
            disabled={busy}
            onChange={setSelectedRun}
            options={completedRuns.map((run) => ({ value: run.id, label: `${run.descriptor_name} · ${run.shape ?? "unknown shape"}` }))}
          />
          <Tag color={selectedRunRow?.status === "COMPLETED" ? "green" : "orange"}>{selectedRunRow?.status ?? "No run"}</Tag>
          <Button size="small" icon={<ArrowSync16Regular />} onClick={() => void refresh()}>Refresh</Button>
        </Space>
        {busy && <Progress percent={Math.round((lastJobProgress ?? 0) * 100)} size="small" style={{ width: 180, marginLeft: "auto" }} />}
      </section>

      <Tabs
        activeKey={tab}
        onChange={(value) => setTab(value as TabKey)}
        items={(Object.keys(TAB_LABELS) as TabKey[]).map((key) => ({ key, label: TAB_LABELS[key] }))}
      />

      <div className="analysis-workspace">
        <main className="analysis-main">
          <section className="analysis-card analysis-controls">
            {tab === "projection" && <ProjectionControls projection={projection} setProjection={setProjection} mode={mode} setMode={setMode} preprocess={preprocess} onPreprocessChange={handlePreprocessChange} tsnePerplexity={tsnePerplexity} setTsnePerplexity={setTsnePerplexity} />}
            {tab === "similarity" && <Space wrap><Typography.Text>View</Typography.Text><Select value={similarityMode} onChange={setSimilarityMode} options={[{ value: "query", label: "Query neighbors" }, { value: "all_neighbors", label: "All-neighbor graph" }, { value: "pairwise", label: "Pairwise matrix" }]} /><Typography.Text>Granularity</Typography.Text><Select value={mode} onChange={setMode} options={[{ value: "structure", label: "Structure" }, { value: "atom", label: "Atom / local" }]} /></Space>}
            {tab === "clusters" && <Space wrap><Typography.Text>Algorithm</Typography.Text><Select value={clusterAlgorithm} onChange={setClusterAlgorithm} options={["kmeans", "dbscan", "hdbscan", "agglomerative"].map((value) => ({ value, label: value.toUpperCase() }))} /><Typography.Text>Clusters</Typography.Text><InputNumber min={2} value={nClusters} onChange={(value) => setNClusters(value ?? 6)} /><Select value={mode} onChange={setMode} options={[{ value: "structure", label: "Structure" }, { value: "atom", label: "Atom / local" }]} /></Space>}
            {tab === "outliers" && <Space wrap><Typography.Text>Algorithm</Typography.Text><Select value={outlierAlgorithm} onChange={setOutlierAlgorithm} options={["lof", "knn", "isolation_forest", "mahalanobis"].map((value) => ({ value, label: value.toUpperCase() }))} /><Typography.Text>Contamination</Typography.Text><InputNumber min={0.001} max={0.5} step={0.001} value={contamination} onChange={(value) => setContamination(value ?? 0.01)} /><Select value={mode} onChange={setMode} options={[{ value: "structure", label: "Structure" }, { value: "atom", label: "Atom / local" }]} /></Space>}
            {tab === "sampling" && <Space wrap><Typography.Text>Method</Typography.Text><Select value={samplingAlgorithm} onChange={setSamplingAlgorithm} options={["fps", "novelty_fps", "uncertainty_diversity", "random", "stratified", "cluster_representative", "per_element"].map((value) => ({ value, label: value === "uncertainty_diversity" ? "uncertainty + diversity" : value.replaceAll("_", " ") }))} /><Typography.Text>Target</Typography.Text><InputNumber min={1} value={nSamples} onChange={(value) => setNSamples(value ?? 1000)} /><Select value={mode} onChange={setMode} options={[{ value: "structure", label: "Structure" }, { value: "atom", label: "Atom" }]} />{samplingAlgorithm === "uncertainty_diversity" && <><Typography.Text>kNN</Typography.Text><InputNumber min={2} value={uncertaintyK} onChange={(value) => setUncertaintyK(value ?? 8)} /></>}</Space>}
            {tab === "coverage" && <Space wrap><Typography.Text>Analysis</Typography.Text><Select value={coverageMode} onChange={setCoverageMode} options={[{ value: "coverage", label: "Coverage" }, { value: "overlap", label: "Train / test overlap" }]} /><Typography.Text>Granularity</Typography.Text><Select value={mode} onChange={setMode} options={[{ value: "structure", label: "Structure" }, { value: "atom", label: "Atom / local" }]} /></Space>}
            {tab === "local" && <Space wrap><Typography.Text>Clusters / element</Typography.Text><InputNumber min={2} value={nClusters} onChange={(value) => setNClusters(value ?? 6)} /><Typography.Text>Descriptor kNN</Typography.Text><InputNumber min={1} value={k} onChange={(value) => setK(value ?? 10)} /><Typography.Text>Neighbor cutoff</Typography.Text><InputNumber min={0.1} step={0.1} precision={2} value={localCutoff} onChange={(value) => setLocalCutoff(value ?? 3)} addonAfter="Å" /><Typography.Text type="secondary">Coordinates and periodic images determine coordination.</Typography.Text></Space>}
            {tab === "kernel" && <Space wrap><Typography.Text>Kernel</Typography.Text><Select value={kernelName} onChange={setKernelName} options={["rbf", "linear", "cosine", "polynomial"].map((value) => ({ value, label: value.toUpperCase() }))} /><Typography.Text>Granularity</Typography.Text><Select value={mode} onChange={setMode} options={[{ value: "structure", label: "Structure" }, { value: "atom", label: "Atom / local" }]} /></Space>}
            {tab === "overview" && overviewAnalysis === "sensitivity" && overviewModuleControl}
            {tab === "overview" && overviewAnalysis === "perturbation_sensitivity" && <Space wrap><Typography.Text>Perturbation</Typography.Text><Select value={perturbationType} onChange={setPerturbationType} options={[{ value: "jitter", label: "Atomic jitter (Å)" }, { value: "strain", label: "Isotropic strain" }]} /><Typography.Text>Steps</Typography.Text><InputNumber min={2} max={32} value={perturbationCount} onChange={(value) => setPerturbationCount(value ?? 8)} /><Typography.Text>Maximum</Typography.Text><InputNumber min={0.001} step={0.01} precision={3} value={perturbationMaximum} onChange={(value) => setPerturbationMaximum(value ?? 0.2)} /><Typography.Text>Metric</Typography.Text><Select value={perturbationMetric} onChange={setPerturbationMetric} options={["euclidean", "cosine", "manhattan"].map((value) => ({ value, label: value }))} /></Space>}
            {(tab === "coverage" || tab === "compare" || (tab === "sampling" && (samplingAlgorithm === "novelty_fps" || samplingAlgorithm === "uncertainty_diversity")) || (tab === "overview" && (overviewAnalysis === "drift" || overviewAnalysis === "sensitivity"))) && <Space wrap><Typography.Text>{tab === "compare" ? "Left" : tab === "sampling" ? "Query" : "Reference"}</Typography.Text><Select value={selectedRun ?? undefined} style={{ width: 220 }} disabled={busy} options={completedRuns.map((run) => ({ value: run.id, label: run.descriptor_name + " · " + run.id }))} onChange={setSelectedRun} /><Typography.Text>{tab === "compare" ? "Right" : tab === "sampling" ? "Reference" : "Query"}</Typography.Text><Select value={secondRun ?? undefined} style={{ width: 220 }} disabled={busy} notFoundContent={sensitivityPair ? "No other completed run for this descriptor" : undefined} options={pairRuns.filter((run) => run.id !== selectedRun).map((run) => ({ value: run.id, label: run.descriptor_name + " · " + run.id }))} onChange={setSecondRun} /></Space>}
            {tab === "compare" && <Space wrap><Typography.Text>Test</Typography.Text><Select value={compareMode} onChange={setCompareMode} options={[{ value: "geometry", label: "Geometry comparison" }, { value: "mantel", label: "Mantel permutation test" }]} />{compareMode === "mantel" && <><Typography.Text>Statistic</Typography.Text><Select value={mantelMethod} onChange={setMantelMethod} options={[{ value: "pearson", label: "Pearson" }, { value: "spearman", label: "Spearman" }]} /><Typography.Text>Permutations</Typography.Text><InputNumber min={1} max={5000} value={mantelPermutations} onChange={(value) => setMantelPermutations(value ?? 999)} /></>}</Space>}
            {tab === "similarity" && similarityMode !== "pairwise" && <Space wrap>{similarityMode === "query" && <><Typography.Text>Query index</Typography.Text><InputNumber min={0} value={queryIndex} onChange={(value) => setQueryIndex(value ?? 0)} /></>}<Typography.Text>k</Typography.Text><InputNumber min={1} value={k} onChange={(value) => setK(value ?? 10)} /></Space>}
            {tab === "overview" && overviewAnalysis !== "sensitivity" && overviewModuleControl}
            {tab === "overview" && overviewAnalysis === "trajectory" && <Space wrap><Typography.Text>Frame step</Typography.Text><InputNumber min={1} value={trajectoryStep} onChange={(value) => setTrajectoryStep(value ?? 1)} /></Space>}
            {tab === "overview" && overviewAnalysis === "property_correlation" && <Space wrap><Typography.Text>Property</Typography.Text><Select value={propertyName} onChange={(value) => { setPropertyName(value); if (value === "force_magnitude") setMode("atom"); }} options={[{ value: "energy_per_atom", label: "Energy / atom" }, { value: "energy", label: "Energy" }, { value: "force_max", label: "Max |F|" }, { value: "force_magnitude", label: "Atom |F|" }, { value: "volume", label: "Volume" }]} /><Select value={mode} onChange={setMode} options={[{ value: "structure", label: "Structure" }, { value: "atom", label: "Atom / local" }]} /></Space>}
            <Space wrap style={{ marginTop: 10 }}>
              <Button type="primary" icon={<CheckmarkCircle16Regular />} loading={busy} disabled={!selectedRun} onClick={() => void runTabAnalysis()}>{tab === "projection" ? `Run ${projection.toUpperCase()}` : tab === "overview" ? `Run ${overviewAnalysis.replaceAll("_", " ")}` : `Run ${TAB_LABELS[tab]}`}</Button>
              {points.length > 0 && <Select size="small" value={colorBy} onChange={setColorBy} options={[{ value: "none", label: "No color" }, { value: "energy", label: "Energy" }, { value: "force_max", label: "Max |F|" }, { value: "volume", label: "Volume" }]} />}
            </Space>
          </section>

          {tab === "projection" && <section className="analysis-card analysis-plot-card"><SectionHeading title="DESCRIPTOR SPACE" meta={`${points.length.toLocaleString()} preview points${selectedIndices.length ? ` · ${selectedIndices.length} selected` : ""}`} />{points.length ? <div className="analysis-plot-frame">{plot}</div> : <Empty description="Run PCA, UMAP, or t-SNE to populate the Plotly canvas." />}</section>}
          {legacyOverview && <OverviewResultVisualization preview={preview} arrays={overviewArrays} loading={overviewArraysBusy} />}
          {tab !== "projection" && !legacyOverview && <AnalysisResultVisualization preview={preview} arrays={overviewArrays} points={points} loading={overviewArraysBusy} selectedIndices={selectedIndices} onSelect={handlePoint} />}
          {tab !== "projection" && <ResultPanel preview={preview} points={points} onSelect={(row) => {
            if (row.i == null && row.sample_index == null && row.frame == null) return;
            const index = Number(row.i ?? row.sample_index ?? 0);
            const frame = Number(row.frame ?? index);
            handlePoint({ i: index, frame, row: row.row == null ? undefined : Number(row.row), sample_id: row.sample_id == null ? undefined : String(row.sample_id), x: 0, y: 0, label: row.labels == null ? undefined : Number(row.labels), score: row.scores == null ? undefined : Number(row.scores), distance: row.distances == null ? undefined : Number(row.distances), element: row.element == null ? undefined : Number(row.element), cluster: row.cluster_labels == null ? undefined : Number(row.cluster_labels), coordination: row.coordination == null ? undefined : Number(row.coordination), novelty: row.novelty == null ? undefined : Number(row.novelty), uncertainty: row.uncertainty == null ? undefined : Number(row.uncertainty), diversity: row.diversity == null ? undefined : Number(row.diversity) });
          }} />}

          {tab === "sampling" && <section className="analysis-card"><SectionHeading title="EXPORT SELECTED SET" meta="Source data is never modified" /><Space.Compact style={{ width: "100%" }}><Select value={exportFormat} onChange={setExportFormat} options={["json", "csv", "extxyz", "deepmd"].map((value) => ({ value, label: value.toUpperCase() }))} style={{ width: 120 }} /><Input placeholder="D:\\exports\\analysis_subset.csv" value={exportPath} onChange={(event) => setExportPath(event.target.value)} /><Button icon={<ArrowDownload16Regular />} onClick={() => void exportSelection()}>Export</Button></Space.Compact></section>}
        </main>

        <aside className="analysis-inspector">
          <section className="analysis-card"><SectionHeading title="INSPECTOR" meta={selectedPoint ? `Frame ${selectedPoint.frame}` : undefined} />{selectedPoint ? <><Row k="Sample" v={selectedPoint.sample_id ?? String(selectedPoint.i)} /><Row k="Frame" v={String(selectedPoint.frame)} />{selectedPoint.row != null && <Row k="Row" v={String(selectedPoint.row)} />}<Button size="small" icon={<ArrowRight16Regular />} onClick={() => { st.setActiveFrame(selectedPoint.frame); st.setPage("explore"); }}>Open in Explore</Button></> : <Typography.Text type="secondary">Click a point, or use box/lasso selection, to inspect a structure.</Typography.Text>}</section>
          <section className="analysis-card"><SectionHeading title="STRUCTURE PREVIEW" meta={selectedFrame ? `Frame ${selectedFrame.index}` : undefined} />{selectedFrame ? <StructurePreview frame={selectedFrame} selectedAtom={selectedPoint?.row} localCutoff={preview?.kind === "local_diversity" && selectedPoint?.row != null ? Number(preview.cutoff ?? 3) : undefined} onOpen={() => { st.setActiveFrame(selectedFrame.index); st.setPage("explore"); }} /> : <div className="analysis-empty-small">{selectedFrameBusy ? "Loading structure…" : "Select a sample to preview it."}</div>}</section>
          <section className="analysis-card"><SectionHeading title="ANALYSIS HISTORY" meta={`${visibleAnalyses.length}`} />{visibleAnalyses.length ? <div className="analysis-history-list">{visibleAnalyses.slice(0, 10).map((row) => <div className="analysis-history-row" key={row.id}><div><Typography.Text strong>{row.analysis_type}</Typography.Text><Typography.Text type="secondary" style={{ display: "block", fontSize: 11 }}>{new Date(row.created_at).toLocaleString()}</Typography.Text></div><Space size={4}><Tag color={row.status === "COMPLETED" ? "green" : row.status === "STALE" ? "orange" : undefined}>{row.status}</Tag><Button size="small" type="text" icon={<ArrowRight16Regular />} aria-label={`Load ${row.analysis_type} analysis`} title="Load cached analysis" loading={loadingAnalysisId === row.id} disabled={row.status !== "COMPLETED" || (loadingAnalysisId !== null && loadingAnalysisId !== row.id)} onClick={() => void loadAnalysis(row)} /><Button size="small" type="text" icon={<Delete16Regular />} aria-label={`Delete ${row.analysis_type} analysis`} disabled={row.status === "RUNNING" || row.status === "QUEUED"} onClick={() => void deleteAnalysis(row)} /></Space></div>)}</div> : <Typography.Text type="secondary">No analysis artifacts for this descriptor run yet.</Typography.Text>}</section>
        </aside>
      </div>
    </div>
  );
}

function OverviewResultVisualization({ preview, arrays, loading }: { preview: AnalysisPreview | null; arrays: NumericArrays; loading: boolean }) {
  if (!preview) return null;
  const kind = String(preview.kind ?? "");
  const title = OVERVIEW_KIND_LABELS[kind] ?? "ANALYSIS RESULT";
  if (loading) {
    return <section className="analysis-card analysis-visual-card"><SectionHeading title={title} meta="Loading chart data" /><div className="analysis-overview-empty"><Empty description="Loading the result arrays…" /></div></section>;
  }

  let content: ReactNode;
  if (kind === "feature_variance") content = <FeatureVarianceChart preview={preview} />;
  else if (kind === "feature_correlation") content = <FeatureCorrelationChart preview={preview} />;
  else if (kind === "effective_dimension") content = <EffectiveDimensionChart preview={preview} arrays={arrays} />;
  else if (kind === "trajectory") content = <TrajectoryChart preview={preview} arrays={arrays} />;
  else if (kind === "drift") content = <DriftChart preview={preview} />;
  else if (kind === "sensitivity") content = <SensitivityChart preview={preview} />;
  else return null;

  return <section className="analysis-card analysis-visual-card"><SectionHeading title={title} meta="Visual summary" />{content}</section>;
}

function FeatureVarianceChart({ preview }: { preview: AnalysisPreview }) {
  const indices = numericArray(preview.top_indices);
  const values = numericArray(preview.top_values);
  const count = Math.min(indices.length, values.length);
  if (!count) return <OverviewNoData message="No feature variance values were returned." />;
  const rows = Array.from({ length: count }, (_, index) => ({ feature: `Feature ${indices[index]}`, value: values[index] })).reverse();
  return <>
    <MetricStrip metrics={[{ label: "Features shown", value: String(count) }, { label: "Highest variance", value: formatNumber(values[0]) }]} />
    <OverviewPlot
      ariaLabel="Top descriptor feature variance"
      data={[{
        type: "bar",
        orientation: "h",
        x: rows.map((row) => row.value),
        y: rows.map((row) => row.feature),
        marker: { color: "#0F6CBD" },
        hovertemplate: "%{y}<br>variance=%{x:.5g}<extra></extra>",
      }]}
      layout={overviewLayout({ xaxis: { title: "Variance", zeroline: true }, yaxis: { automargin: true } })}
    />
    <ChartCaption>显示方差最大的 descriptor 维度；数值越大，跨样本变化越明显。</ChartCaption>
  </>;
}

function FeatureCorrelationChart({ preview }: { preview: AnalysisPreview }) {
  const pairs = recordArray(preview.pairs)
    .map((pair) => ({
      feature: `F${formatIndex(pair.feature_a)} ↔ F${formatIndex(pair.feature_b)}`,
      value: finiteNumber(pair.correlation),
    }))
    .filter((pair): pair is { feature: string; value: number } => pair.value !== null);
  if (!pairs.length) return <OverviewNoData message="No feature correlation pairs were returned." />;
  const rows = pairs.slice().reverse();
  const strongest = pairs.reduce((best, row) => Math.max(best, Math.abs(row.value)), 0);
  return <>
    <MetricStrip metrics={[{ label: "Pairs shown", value: String(pairs.length) }, { label: "Strongest |r|", value: strongest.toFixed(3) }]} />
    <OverviewPlot
      ariaLabel="Top descriptor feature correlations"
      data={[{
        type: "bar",
        orientation: "h",
        x: rows.map((row) => row.value),
        y: rows.map((row) => row.feature),
        marker: { color: rows.map((row) => row.value >= 0 ? "#0F6CBD" : "#D13438") },
        hovertemplate: "%{y}<br>correlation=%{x:.4f}<extra></extra>",
      }]}
      layout={overviewLayout({ xaxis: { title: "Pearson correlation", range: [-1, 1], zeroline: true }, yaxis: { automargin: true } })}
    />
    <ChartCaption>蓝色表示正相关，红色表示负相关；相关性绝对值越大，特征冗余越明显。</ChartCaption>
  </>;
}

function EffectiveDimensionChart({ preview, arrays }: { preview: AnalysisPreview; arrays: NumericArrays }) {
  const explained = numericArray(arrays.explained_variance);
  if (!explained.length) return <OverviewNoData message="The explained-variance array is not available for visualization." />;
  const cumulative: number[] = [];
  let total = 0;
  for (const value of explained) {
    total += value;
    cumulative.push(total);
  }
  const indices = sampledIndices(explained.length, 320);
  const metrics: Metric[] = [
    { label: "Participation ratio", value: formatNumber(preview.participation_ratio) },
    { label: "90% components", value: formatCount(componentThreshold(preview, "0.9")) },
    { label: "95% components", value: formatCount(componentThreshold(preview, "0.95")) },
    { label: "99% components", value: formatCount(componentThreshold(preview, "0.99")) },
  ];
  return <>
    <MetricStrip metrics={metrics} />
    <OverviewPlot
      ariaLabel="Explained and cumulative descriptor variance by component"
      data={[
        {
          type: "bar",
          x: indices.map((index) => index + 1),
          y: indices.map((index) => explained[index] * 100),
          name: "Explained variance",
          marker: { color: "#0F6CBD" },
          hovertemplate: "PC %{x}<br>explained=%{y:.2f}%<extra></extra>",
        },
        {
          type: "scatter",
          mode: "lines",
          x: indices.map((index) => index + 1),
          y: indices.map((index) => cumulative[index] * 100),
          name: "Cumulative",
          line: { color: "#F7630C", width: 2 },
          hovertemplate: "PC %{x}<br>cumulative=%{y:.2f}%<extra></extra>",
        },
      ]}
      layout={overviewLayout({
        xaxis: { title: "Component", type: "linear" },
        yaxis: { title: "Variance (%)", range: [0, 100] },
        legend: { orientation: "h", y: 1.12, x: 0 },
      })}
    />
    <ChartCaption>柱状图是单个主成分贡献率，橙线是累计贡献率；横轴过长时会进行等距抽样显示。</ChartCaption>
  </>;
}

function TrajectoryChart({ preview, arrays }: { preview: AnalysisPreview; arrays: NumericArrays }) {
  const time = numericArray(arrays.time);
  const xValues = time.length ? time : numericArray(arrays.frames);
  const distances = numericArray(arrays.step_distance);
  const count = Math.min(xValues.length, distances.length);
  if (count < 2) return <OverviewNoData message="At least two trajectory points are needed for visualization." />;
  const cumulative: number[] = [];
  let total = 0;
  for (let index = 0; index < count; index += 1) {
    total += distances[index];
    cumulative.push(total);
  }
  const indices = sampledIndices(count, 1000);
  return <>
    <MetricStrip metrics={[{ label: "Samples", value: formatCount(count) }, { label: "Total descriptor distance", value: formatNumber(preview.total_distance) }, { label: "Largest step", value: formatNumber(Math.max(...distances.slice(0, count))) }]} />
    <OverviewPlot
      ariaLabel="Descriptor trajectory step and cumulative distance"
      data={[
        {
          type: "scatter",
          mode: "lines",
          x: indices.map((index) => xValues[index]),
          y: indices.map((index) => distances[index]),
          name: "Step distance",
          line: { color: "#0F6CBD", width: 1.5 },
          hovertemplate: "t=%{x}<br>step=%{y:.5g}<extra></extra>",
        },
        {
          type: "scatter",
          mode: "lines",
          x: indices.map((index) => xValues[index]),
          y: indices.map((index) => cumulative[index]),
          name: "Cumulative distance",
          yaxis: "y2",
          line: { color: "#F7630C", width: 2 },
          hovertemplate: "t=%{x}<br>cumulative=%{y:.5g}<extra></extra>",
        },
      ]}
      layout={overviewLayout({
        xaxis: { title: String(preview.time_unit ?? "Frame") },
        yaxis: { title: "Step distance" },
        yaxis2: { title: "Cumulative distance", overlaying: "y", side: "right", showgrid: false },
        legend: { orientation: "h", y: 1.12, x: 0 },
      })}
    />
    <ChartCaption>蓝线显示相邻帧的 descriptor 变化，橙线显示从轨迹起点累计的变化距离。</ChartCaption>
  </>;
}

function DriftChart({ preview }: { preview: AnalysisPreview }) {
  const rows = recordArray(preview.rows)
    .map((row) => ({ label: finiteNumber(row.labels), distance: finiteNumber(row.distances) }))
    .filter((row): row is { label: number; distance: number } => row.label !== null && row.distance !== null);
  const categoryLabels = ["Covered", "Marginal", "Out of coverage"];
  const categoryColors = ["#107C10", "#F7630C", "#D13438"];
  const grouped = categoryLabels.map((_, category) => rows.filter((row) => row.label === category).map((row) => row.distance));
  const counts = [
    numberOr(preview.covered, grouped[0].length),
    numberOr(preview.marginal, grouped[1].length),
    numberOr(preview.out_of_coverage, grouped[2].length),
  ];
  const q95 = finiteNumber(preview.q95);
  const q99 = finiteNumber(preview.q99);
  const thresholdShapes = [q95, q99]
    .filter((value): value is number => value !== null)
    .map((value, index) => ({ type: "line" as const, x0: value, x1: value, y0: 0, y1: 1, yref: "paper" as const, line: { color: index === 0 ? "#F7630C" : "#D13438", dash: "dash" as const, width: 1.5 } }));
  const data: Data[] = rows.length
    ? grouped.map((values, index) => ({ type: "histogram" as const, x: values, name: categoryLabels[index], opacity: 0.78, marker: { color: categoryColors[index] }, nbinsx: 28, hovertemplate: `${categoryLabels[index]}<br>distance=%{x:.5g}<br>count=%{y}<extra></extra>` }))
    : [{ type: "bar", x: categoryLabels, y: counts, marker: { color: categoryColors }, hovertemplate: "%{x}<br>count=%{y}<extra></extra>" }];
  return <>
    <MetricStrip metrics={[{ label: "Mean distance", value: formatNumber(preview.mean_distance) }, { label: "Median distance", value: formatNumber(preview.median_distance) }, { label: "Max distance", value: formatNumber(preview.max_distance) }, { label: "Out of coverage", value: formatCount(preview.out_of_coverage) }]} />
    <OverviewPlot
      ariaLabel="Distribution of distances from query samples to the reference descriptor set"
      data={data}
      layout={overviewLayout({
        barmode: rows.length ? "stack" : "group",
        xaxis: { title: rows.length ? "Nearest-reference distance" : "Coverage category" },
        yaxis: { title: "Samples" },
        shapes: thresholdShapes,
        legend: { orientation: "h", y: 1.12, x: 0 },
      })}
    />
    <ChartCaption>分布越靠右表示 query 样本离 reference descriptor space 越远；虚线分别对应 q95 和 q99 阈值。</ChartCaption>
  </>;
}

function SensitivityChart({ preview }: { preview: AnalysisPreview }) {
  const records = recordArray(preview.runs);
  const geometryMode = records.some((run) => finiteNumber(run.mean_delta_norm) === null);
  const metricKey = geometryMode ? "pairwise_distance_spearman" : "mean_delta_norm";
  const metricLabel = geometryMode ? "Pairwise distance Spearman" : "Mean descriptor delta norm";
  const runs = records
    .map((run, index) => ({
      label: parameterLabel(run.parameters, index),
      value: finiteNumber(run[metricKey]),
      detail: parameterText(run.parameters),
    }))
    .filter((run): run is { label: string; value: number; detail: string } => run.value !== null);
  if (!runs.length) return <OverviewNoData message="No completed runs were returned for sensitivity analysis." />;
  const rows = runs.slice().reverse();
  const extremeValue = geometryMode ? Math.min(...runs.map((run) => run.value)) : Math.max(...runs.map((run) => run.value));
  return <>
    <MetricStrip metrics={[{ label: "Runs compared", value: String(runs.length) }, { label: geometryMode ? "Lowest geometry correlation" : "Largest mean delta", value: formatNumber(extremeValue) }]} />
    <OverviewPlot
      ariaLabel="Descriptor parameter sensitivity across completed runs"
      data={[{
        type: "bar",
        orientation: "h",
        x: rows.map((run) => run.value),
        y: rows.map((run) => run.label),
        customdata: rows.map((run) => run.detail),
        marker: { color: rows.map((_, index) => index === rows.length - 1 ? "#107C10" : "#0F6CBD") },
        hovertemplate: `%{y}<br>${metricLabel.toLowerCase()}=%{x:.5g}<br>%{customdata}<extra></extra>`,
      }]}
      layout={overviewLayout({ xaxis: { title: metricLabel, zeroline: true }, yaxis: { automargin: true } })}
    />
    <ChartCaption>{geometryMode ? "不同特征维度的描述符使用样本几何一致性比较；Spearman 越接近 1，样本排序越一致。" : "以第一个 Completed Run 为基准；柱越长，descriptor 参数变化带来的整体结果差异越大。"}</ChartCaption>
  </>;
}

function OverviewPlot({ data, layout, ariaLabel }: { data: Data[]; layout: Partial<Layout>; ariaLabel: string }) {
  return <div className="analysis-overview-chart-frame" aria-label={ariaLabel}><Plot data={data} layout={layout} config={{ responsive: true, displaylogo: false, modeBarButtonsToRemove: ["toImage"] }} style={{ width: "100%", height: "100%" }} /></div>;
}

function MetricStrip({ metrics }: { metrics: Metric[] }) {
  return <div className="analysis-metric-strip">{metrics.map((metric) => <div className="analysis-metric" key={metric.label}><Typography.Text type="secondary">{metric.label}</Typography.Text><Typography.Text strong>{metric.value}</Typography.Text></div>)}</div>;
}

function ChartCaption({ children }: { children: ReactNode }) {
  return <Typography.Text type="secondary" className="analysis-chart-caption">{children}</Typography.Text>;
}

function OverviewNoData({ message }: { message: string }) {
  return <div className="analysis-overview-empty"><Empty description={message} /></div>;
}

function overviewLayout(overrides: Partial<Layout> = {}): Partial<Layout> {
  return {
    autosize: true,
    margin: { l: 64, r: 28, t: 18, b: 52 },
    paper_bgcolor: "#FFFFFF",
    plot_bgcolor: "#FFFFFF",
    font: { family: "Segoe UI, sans-serif", size: 12, color: "#424242" },
    ...overrides,
  };
}

function numericArray(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return value.map(finiteNumber).filter((item): item is number => item !== null);
}

function recordArray(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null && !Array.isArray(item));
}

function finiteNumber(value: unknown): number | null {
  if (typeof value !== "number" && typeof value !== "string") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function sampledIndices(length: number, maxPoints: number): number[] {
  if (length <= 0) return [];
  if (length <= maxPoints) return Array.from({ length }, (_, index) => index);
  return Array.from({ length: maxPoints }, (_, index) => Math.round(index * (length - 1) / (maxPoints - 1)));
}

function formatNumber(value: unknown): string {
  const number = finiteNumber(value);
  if (number === null) return "—";
  return Math.abs(number) >= 1000 ? number.toLocaleString(undefined, { maximumFractionDigits: 2 }) : number.toPrecision(5);
}

function formatCount(value: unknown): string {
  const number = finiteNumber(value);
  return number === null ? "—" : Math.round(number).toLocaleString();
}

function formatIndex(value: unknown): string {
  const number = finiteNumber(value);
  return number === null ? "?" : String(Math.round(number));
}

function componentThreshold(preview: AnalysisPreview, key: string): number | null {
  const thresholds = preview.components_for_threshold;
  if (typeof thresholds !== "object" || thresholds === null || Array.isArray(thresholds)) return null;
  return finiteNumber((thresholds as Record<string, unknown>)[key]);
}

function numberOr(value: unknown, fallback: number): number {
  return finiteNumber(value) ?? fallback;
}

function parameterText(value: unknown): string {
  if (value == null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function parameterLabel(value: unknown, index: number): string {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>).slice(0, 2);
    if (entries.length) return entries.map(([key, item]) => `${key}=${String(item)}`).join(", ");
  }
  return `Run ${index + 1}`;
}

function ProjectionControls({ projection, setProjection, mode, setMode, preprocess, onPreprocessChange, tsnePerplexity, setTsnePerplexity }: { projection: ProjectionName; setProjection: (value: ProjectionName) => void; mode: PcaMode; setMode: (value: PcaMode) => void; preprocess: string; onPreprocessChange: (value: string) => void; tsnePerplexity: number; setTsnePerplexity: (value: number) => void }) {
  return <Space wrap><Typography.Text>Method</Typography.Text><Select value={projection} onChange={setProjection} options={[{ value: "pca", label: "PCA" }, { value: "umap", label: "UMAP" }, { value: "tsne", label: "t-SNE" }]} /><Typography.Text>Granularity</Typography.Text><Select value={mode} onChange={setMode} options={[{ value: "structure", label: "Structure" }, { value: "atom", label: "Atom / local" }]} /><Typography.Text>Preprocess</Typography.Text><Select value={preprocess} onChange={onPreprocessChange} options={[{ value: "raw", label: "Raw scale" }, { value: "center", label: "Centered" }, { value: "standardized", label: "Standardized" }]} />{projection === "tsne" && <><Typography.Text>Perplexity</Typography.Text><InputNumber min={2} step={1} value={tsnePerplexity} onChange={(value) => setTsnePerplexity(value ?? 30)} /></>}</Space>;
}

function ResultPanel({ preview, points, onSelect }: { preview: AnalysisPreview | null; points: Point[]; onSelect?: (row: Record<string, unknown>) => void }) {
  if (!preview && !points.length) return <section className="analysis-card"><Empty description="Run an analysis module to see its bounded result preview." /></section>;
  const rows = Array.isArray(preview?.rows)
    ? preview.rows
    : Array.isArray(preview?.selected)
      ? preview.selected
      : Array.isArray(preview?.pairs)
        ? preview.pairs as Record<string, unknown>[]
        : Array.isArray(preview?.runs)
          ? preview.runs as Record<string, unknown>[]
          : Array.isArray(preview?.top_indices)
            ? (preview.top_indices as unknown[]).map((index, position) => ({ rank: position + 1, feature: index, variance: (preview.top_values as unknown[] | undefined)?.[position] }))
            : [];
  if (rows.length) return <section className="analysis-card"><SectionHeading title={String(preview?.kind ?? "RESULT").toUpperCase()} meta={`${rows.length.toLocaleString()} rows`} /><Table size="small" pagination={{ pageSize: 12 }} rowKey={(row, index) => `${String(row.i ?? row.sample_id ?? row.run_id ?? index)}:${String(row.source_i ?? row.rank ?? index)}`} dataSource={rows} onRow={(row) => ({ onClick: () => onSelect?.(row) })} columns={Object.keys(rows[0]).slice(0, 7).map((key) => ({ title: key, dataIndex: key, key, render: (value: unknown) => typeof value === "number" ? value.toPrecision(6) : String(value ?? "—") }))} /></section>;
  return <section className="analysis-card"><SectionHeading title={String(preview?.kind ?? "RESULT").toUpperCase()} /><pre className="analysis-json-preview">{JSON.stringify(preview, null, 2)}</pre></section>;
}

function SectionHeading({ title, meta }: { title: string; meta?: string }) {
  return <div className="analysis-section-heading"><Typography.Text strong>{title}</Typography.Text>{meta && <Typography.Text type="secondary">{meta}</Typography.Text>}</div>;
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="analysis-inspector-row"><span>{k}</span><Typography.Text code>{v}</Typography.Text></div>;
}

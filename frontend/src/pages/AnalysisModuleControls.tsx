import { InputNumber, Select, Space, Tag, Tooltip, Typography } from "antd";
import type {
  AnalysisParams,
  OverviewAnalysis,
  ProjectionName,
  TabKey,
} from "../features/analysis";
import type { PcaMode } from "../stores/workspace";
import type { DatasetMeta, DatasetView, RunRow } from "../types/protocol";
import { useT } from "../i18n";
import {
  CrossDatasetPicker,
  ParamLabel,
  ProjectionControls,
  SamplingControls,
  type CacheOption,
  type CompareMode,
} from "./analysisShared";
import type { useAnalysisParameters } from "./useAnalysisParameters";

type AnalysisParameterState = ReturnType<typeof useAnalysisParameters>;

type AnalysisModuleControlSetters = Pick<AnalysisParameterState,
  | "setSimilarityMode"
  | "setTsnePerplexity"
  | "setClusterAlgorithm"
  | "setNClusters"
  | "setOutlierAlgorithm"
  | "setK"
  | "setContamination"
  | "setSamplingAlgorithm"
  | "setNSamples"
  | "setUncertaintyK"
  | "setSamplingStrategy"
  | "setSamplingStratificationSource"
  | "setSamplingScaling"
  | "setSamplingBlocks"
  | "setSamplingBudgetMode"
  | "setSamplingCoverage"
  | "setSamplingMinDistance"
  | "setSamplingExistingRunId"
  | "setReferenceDatasetId"
  | "setQueryDatasetId"
  | "setReferenceRunId"
  | "setQueryRunId"
  | "setReferenceViewId"
  | "setQueryViewId"
  | "setSecondRun"
  | "setCompareMode"
  | "setMantelMethod"
  | "setMantelPermutations"
  | "setQueryIndex"
  | "setLocalCutoff"
  | "setKernelName"
  | "setPropertyName"
  | "setPropertyFolds"
  | "setPropertyReliabilityK"
  | "setPropertyDistanceMetric"
  | "setPropertySparsePercentile"
  | "setPropertyOodPercentile"
  | "setPerturbationType"
  | "setPerturbationCount"
  | "setPerturbationMaximum"
  | "setPerturbationStructures"
  | "setPerturbationMetric"
> & {
  setProjection: (value: ProjectionName) => void;
  setMode: (value: PcaMode) => void;
  setEffectiveDimensionPreprocess: (value: AnalysisParams["effectiveDimensionPreprocess"]) => void;
  setNearZeroThreshold: (value: number) => void;
  setLowVariationThreshold: (value: number) => void;
  setFeatureCorrelationMethod: (value: AnalysisParams["featureCorrelationMethod"]) => void;
  setFeatureCorrelationThreshold: (value: number) => void;
};

export interface AnalysisModuleControlsProps {
  tab: TabKey;
  overviewAnalysis: OverviewAnalysis;
  params: AnalysisParams;
  effectiveTsnePerplexity?: number | null;
  secondRun: string | null;
  datasets: DatasetMeta[];
  referenceDatasetId: string | null;
  queryDatasetId: string | null;
  referenceRuns: RunRow[];
  queryRuns: RunRow[];
  referenceViews: DatasetView[];
  queryViews: DatasetView[];
  warmStartRuns: RunRow[];
  samplingQuota: AnalysisParameterState["samplingQuota"];
  samplingQuotaBusy: boolean;
  referenceInputsReady: boolean;
  crossDatasetModule: boolean;
  disabled: boolean;
  selectedRun: string | null;
  completedRuns: RunRow[];
  pairRuns: RunRow[];
  sensitivityPair: boolean;
  setters: AnalysisModuleControlSetters;
  setSelectedRun: (value: string) => void;
  onPreprocessChange: (value: string) => void;
  markOptions: (param: string, options: CacheOption[]) => CacheOption[];
  cachedParam: (param: keyof AnalysisParams) => boolean;
}

export default function AnalysisModuleControls({
  tab,
  overviewAnalysis,
  params,
  effectiveTsnePerplexity,
  secondRun,
  datasets,
  referenceDatasetId,
  queryDatasetId,
  referenceRuns,
  queryRuns,
  referenceViews,
  queryViews,
  warmStartRuns,
  samplingQuota,
  samplingQuotaBusy,
  referenceInputsReady,
  crossDatasetModule,
  disabled,
  selectedRun,
  completedRuns,
  pairRuns,
  sensitivityPair,
  setters,
  setSelectedRun,
  onPreprocessChange,
  markOptions,
  cachedParam,
}: AnalysisModuleControlsProps) {
  const { t } = useT();
  const { setMode } = setters;
  return <>
    {tab === "projection" && <ProjectionControls
      projection={params.projection}
      setProjection={setters.setProjection}
      mode={params.mode}
      setMode={setMode}
      preprocess={params.preprocess}
      onPreprocessChange={onPreprocessChange}
      tsnePerplexity={params.tsnePerplexity}
      effectiveTsnePerplexity={effectiveTsnePerplexity}
      setTsnePerplexity={setters.setTsnePerplexity}
      markOptions={markOptions}
      cachedParam={cachedParam}
    />}
    {tab === "similarity" && <Space wrap>
      <Typography.Text>{t("View")}</Typography.Text>
      <Select value={params.similarityMode} onChange={(value) => setters.setSimilarityMode(value as "query" | "all_neighbors" | "pairwise")} options={markOptions("similarityMode", [{ value: "query", label: t("Query neighbors") }, { value: "all_neighbors", label: t("All-neighbor graph") }, { value: "pairwise", label: t("Pairwise matrix") }])} />
      <Typography.Text>{t("Granularity")}</Typography.Text>
      <Select value={params.mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} />
    </Space>}
    {tab === "clusters" && <Space wrap>
      <Typography.Text>{t("Algorithm")}</Typography.Text>
      <Select value={params.clusterAlgorithm} onChange={setters.setClusterAlgorithm} options={markOptions("clusterAlgorithm", ["kmeans", "dbscan", "hdbscan", "agglomerative"].map((value) => ({ value, label: value.toUpperCase() })))} />
      <ParamLabel label={t("Clusters")} cached={cachedParam("nClusters")} />
      <InputNumber min={2} value={params.nClusters} onChange={(value) => setters.setNClusters(value ?? 6)} />
      <Select value={params.mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} />
    </Space>}
    {tab === "outliers" && <Space wrap>
      <Typography.Text>{t("Algorithm")}</Typography.Text>
      <Select value={params.outlierAlgorithm} onChange={setters.setOutlierAlgorithm} options={markOptions("outlierAlgorithm", ["lof", "knn", "isolation_forest", "mahalanobis"].map((value) => ({ value, label: value.toUpperCase() })))} />
      {(params.outlierAlgorithm === "lof" || params.outlierAlgorithm === "knn") && <>
        <ParamLabel label="k" cached={cachedParam("k")} />
        <InputNumber min={1} value={params.k} onChange={(value) => setters.setK(value ?? 10)} />
      </>}
      <ParamLabel label={t("Contamination")} cached={cachedParam("contamination")} />
      <InputNumber min={0.001} max={0.5} step={0.001} value={params.contamination} onChange={(value) => setters.setContamination(value ?? 0.01)} />
      <Select value={params.mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} />
    </Space>}
    {tab === "sampling" && <SamplingControls
      params={params}
      setters={{
        samplingAlgorithm: setters.setSamplingAlgorithm,
        nSamples: setters.setNSamples,
        mode: setMode,
        uncertaintyK: setters.setUncertaintyK,
        samplingStrategy: setters.setSamplingStrategy,
        samplingStratificationSource: setters.setSamplingStratificationSource,
        samplingScaling: setters.setSamplingScaling,
        samplingBlocks: setters.setSamplingBlocks,
        samplingBudgetMode: (value: string) => setters.setSamplingBudgetMode(value as "count" | "coverage"),
        samplingCoverage: setters.setSamplingCoverage,
        samplingMinDistance: setters.setSamplingMinDistance,
        samplingExistingRunId: setters.setSamplingExistingRunId,
      }}
      warmStartRuns={warmStartRuns}
      quota={samplingQuota}
      quotaBusy={samplingQuotaBusy}
      markOptions={markOptions}
      cachedParam={cachedParam}
    />}
    {tab === "coverage" && <Space wrap>
      <Typography.Text>{t("Granularity")}</Typography.Text>
      <Select value={params.mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} />
    </Space>}
    {tab === "local" && <Space wrap>
      <ParamLabel label={t("Clusters / element")} cached={cachedParam("nClusters")} />
      <InputNumber min={2} value={params.nClusters} onChange={(value) => setters.setNClusters(value ?? 6)} />
      <ParamLabel label={t("Descriptor kNN")} cached={cachedParam("k")} />
      <InputNumber min={1} value={params.k} onChange={(value) => setters.setK(value ?? 10)} />
      <ParamLabel label={t("Neighbor cutoff")} cached={cachedParam("localCutoff")} />
      <InputNumber min={0.1} max={10} step={0.1} precision={2} value={params.localCutoff} onChange={(value) => setters.setLocalCutoff(value == null ? 3 : Math.max(0.1, Math.min(10, value)))} addonAfter="Å" />
      <Typography.Text type="secondary">{t("Coordinates and periodic images determine coordination.")}</Typography.Text>
    </Space>}
    {tab === "kernel" && <Space wrap>
      <Typography.Text>{t("Kernel")}</Typography.Text>
      <Select value={params.kernelName} onChange={setters.setKernelName} options={markOptions("kernelName", ["rbf", "linear", "cosine", "polynomial"].map((value) => ({ value, label: value.toUpperCase() })))} />
      <Typography.Text>{t("Granularity")}</Typography.Text>
      <Select value={params.mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} />
    </Space>}
    {crossDatasetModule && <CrossDatasetPicker
      datasets={datasets}
      referenceDatasetId={referenceDatasetId}
      queryDatasetId={queryDatasetId}
      referenceRunId={params.referenceRunId}
      queryRunId={params.queryRunId}
      referenceViewId={params.referenceViewId}
      queryViewId={params.queryViewId}
      referenceRuns={referenceRuns}
      queryRuns={queryRuns}
      referenceViews={referenceViews}
      queryViews={queryViews}
      compatible={referenceInputsReady}
      disabled={disabled}
      onReferenceDataset={(value) => { setters.setReferenceDatasetId(value); setters.setReferenceRunId(null); setters.setReferenceViewId(null); }}
      onQueryDataset={(value) => { setters.setQueryDatasetId(value); setters.setQueryRunId(null); setters.setQueryViewId(null); }}
      onReferenceRun={setters.setReferenceRunId}
      onQueryRun={setters.setQueryRunId}
      onReferenceView={setters.setReferenceViewId}
      onQueryView={setters.setQueryViewId}
      onSwap={() => {
        const nextReferenceDataset = queryDatasetId;
        const nextReferenceRun = params.queryRunId;
        const nextReferenceView = params.queryViewId;
        setters.setQueryDatasetId(referenceDatasetId);
        setters.setQueryRunId(params.referenceRunId);
        setters.setQueryViewId(params.referenceViewId);
        setters.setReferenceDatasetId(nextReferenceDataset);
        setters.setReferenceRunId(nextReferenceRun);
        setters.setReferenceViewId(nextReferenceView);
      }}
    />}
    {tab === "overview" && overviewAnalysis === "feature_variance" && <Space wrap>
      <ParamLabel label={t("Near-zero threshold")} cached={cachedParam("nearZeroThreshold")} />
      <InputNumber min={0} max={1} step={0.0001} precision={6} value={params.nearZeroThreshold} onChange={(value) => setters.setNearZeroThreshold(value ?? 1e-4)} />
      <ParamLabel label={t("Low variation threshold")} cached={cachedParam("lowVariationThreshold")} />
      <InputNumber min={0} max={1} step={0.0001} precision={6} value={params.lowVariationThreshold} onChange={(value) => setters.setLowVariationThreshold(value ?? 1e-2)} />
    </Space>}
    {tab === "overview" && overviewAnalysis === "feature_correlation" && <Space wrap>
      <ParamLabel label={t("Correlation method")} cached={cachedParam("featureCorrelationMethod")} />
      <Select aria-label={t("Correlation method")} value={params.featureCorrelationMethod} onChange={setters.setFeatureCorrelationMethod} options={markOptions("featureCorrelationMethod", [{ value: "pearson", label: "Pearson" }, { value: "spearman", label: "Spearman" }])} />
      <ParamLabel label={t("High-correlation threshold")} cached={cachedParam("featureCorrelationThreshold")} />
      <InputNumber min={0.8} max={0.999} step={0.01} precision={2} value={params.featureCorrelationThreshold} onChange={(value) => setters.setFeatureCorrelationThreshold(Math.min(0.999, Math.max(0.8, value ?? 0.95)))} />
      <Typography.Text type="secondary">{t("High when |correlation| ≥ threshold")}</Typography.Text>
    </Space>}
    {tab === "overview" && overviewAnalysis === "effective_dimension" && <Space wrap>
      <ParamLabel label={t("PCA preprocessing")} cached={cachedParam("effectiveDimensionPreprocess")} />
      <Select aria-label={t("PCA preprocessing")} value={params.effectiveDimensionPreprocess} onChange={setters.setEffectiveDimensionPreprocess} options={markOptions("effectiveDimensionPreprocess", [{ value: "standardized", label: t("Standardized") }, { value: "center", label: t("Centered") }])} />
      <Typography.Text type="secondary">{params.effectiveDimensionPreprocess === "standardized" ? t("Correlation basis") : t("Covariance basis")}</Typography.Text>
    </Space>}
    {tab === "overview" && overviewAnalysis === "perturbation_sensitivity" && <Space wrap>
      <Typography.Text>{t("Perturbation")}</Typography.Text>
      <Select value={params.perturbationType} onChange={(value) => setters.setPerturbationType(value as "jitter" | "strain")} options={markOptions("perturbationType", [{ value: "jitter", label: t("Atomic jitter (Å)") }, { value: "strain", label: t("Isotropic strain") }])} />
      <ParamLabel label={t("Steps")} cached={cachedParam("perturbationCount")} />
      <InputNumber min={2} max={32} value={params.perturbationCount} onChange={(value) => setters.setPerturbationCount(value ?? 8)} />
      <ParamLabel label={t("Maximum")} cached={cachedParam("perturbationMaximum")} />
      <InputNumber min={0.001} step={0.01} precision={3} value={params.perturbationMaximum} onChange={(value) => setters.setPerturbationMaximum(value ?? 0.2)} />
      <ParamLabel label={t("Max structures")} cached={cachedParam("perturbationStructures")} />
      <Tooltip title={t("Structures sampled evenly across the run; every one is recomputed per amplitude.")} placement="top"><InputNumber aria-label={t("Max structures")} min={1} max={2048} step={8} value={params.perturbationStructures} onChange={(value) => setters.setPerturbationStructures(Math.max(1, Math.min(2048, Math.round(value ?? 64))))} /></Tooltip>
      <Typography.Text>{t("Metric")}</Typography.Text>
      <Select value={params.perturbationMetric} onChange={setters.setPerturbationMetric} options={markOptions("perturbationMetric", ["euclidean", "cosine", "manhattan"].map((value) => ({ value, label: value })))} />
    </Space>}
    {(tab === "compare" || (tab === "overview" && overviewAnalysis === "sensitivity")) && <Space wrap>
      <Typography.Text>{tab === "compare" ? t("Left") : t("Reference")}</Typography.Text>
      <Select value={selectedRun ?? undefined} style={{ width: 220 }} disabled={disabled} options={completedRuns.map((run) => ({ value: run.id, label: run.descriptor_name + " · " + run.id }))} onChange={setSelectedRun} />
      <Typography.Text>{tab === "compare" ? t("Right") : t("Query")}</Typography.Text>
      <Select value={secondRun ?? undefined} style={{ width: 220 }} disabled={disabled} notFoundContent={sensitivityPair ? t("No other completed run for this descriptor") : undefined} options={pairRuns.filter((run) => run.id !== selectedRun).map((run) => ({ value: run.id, label: run.descriptor_name + " · " + run.id }))} onChange={setters.setSecondRun} />
    </Space>}
    {tab === "compare" && <Space wrap>
      <Typography.Text>{t("Test")}</Typography.Text>
      <Select value={params.compareMode as CompareMode} onChange={setters.setCompareMode} options={markOptions("compareMode", [{ value: "geometry", label: t("Geometry comparison") }, { value: "mantel", label: t("Mantel permutation test") }])} />
      {params.compareMode === "mantel" && <>
        <Typography.Text>{t("Statistic")}</Typography.Text>
        <Select value={params.mantelMethod} onChange={(value) => setters.setMantelMethod(value as "pearson" | "spearman")} options={markOptions("mantelMethod", [{ value: "pearson", label: "Pearson" }, { value: "spearman", label: "Spearman" }])} />
        <ParamLabel label={t("Permutations")} cached={cachedParam("mantelPermutations")} />
        <InputNumber min={1} max={5000} value={params.mantelPermutations} onChange={(value) => setters.setMantelPermutations(value ?? 999)} />
      </>}
    </Space>}
    {tab === "similarity" && params.similarityMode !== "pairwise" && <Space wrap>
      {params.similarityMode === "query" && <><ParamLabel label={t("Query index")} cached={cachedParam("queryIndex")} /><InputNumber min={0} value={params.queryIndex} onChange={(value) => setters.setQueryIndex(value ?? 0)} /></>}
      <ParamLabel label="k" cached={cachedParam("k")} /><InputNumber min={1} value={params.k} onChange={(value) => setters.setK(value ?? 10)} />
    </Space>}
    {tab === "overview" && overviewAnalysis === "trajectory" && <Typography.Text type="secondary">{t("Frame range, trajectory sampling interval, event method, sensitivity, and coloring live in the trajectory result itself.")}</Typography.Text>}
    {/* Drift measures whichever matrix its two runs share, so granularity
        is one of its inputs and its identity key already carries it. With
        no control here, changing the mode elsewhere dropped the reference
        points with nothing on screen to explain or undo it. */}
    {tab === "overview" && overviewAnalysis === "drift" && <Space wrap>
      <ParamLabel label={t("Granularity")} cached={cachedParam("mode")} />
      <Select value={params.mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} />
    </Space>}
    {tab === "overview" && overviewAnalysis === "property_correlation" && <Space className="analysis-property-controls" wrap>
      <Typography.Text>{t("Property")}</Typography.Text><Select value={params.propertyName} onChange={(value) => { setters.setPropertyName(value); if (value === "force_magnitude") setMode("atom"); }} options={markOptions("propertyName", [{ value: "energy_per_atom", label: t("Energy / atom") }, { value: "energy", label: t("Energy") }, { value: "force_max", label: t("Max |F|") }, { value: "force_magnitude", label: t("Atom |F|") }, { value: "volume", label: t("Volume") }])} />
      <Typography.Text>{t("Granularity")}</Typography.Text><Select value={params.mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} />
      <Typography.Text>{t("Model")}</Typography.Text><Tag>Ridge</Tag>
      <Typography.Text>{t("CV folds")}</Typography.Text><InputNumber aria-label={t("CV folds")} min={2} max={20} value={params.propertyFolds} onChange={(value) => setters.setPropertyFolds(value ?? 5)} />
      <Typography.Text>kNN k</Typography.Text><InputNumber aria-label="kNN k" min={1} max={50} value={params.propertyReliabilityK} onChange={(value) => setters.setPropertyReliabilityK(value ?? 5)} />
      <Typography.Text>{t("Distance metric")}</Typography.Text><Select aria-label={t("Distance metric")} value={params.propertyDistanceMetric} onChange={(value) => setters.setPropertyDistanceMetric(value as "euclidean" | "cosine")} options={[{ value: "euclidean", label: "Euclidean" }, { value: "cosine", label: "Cosine" }]} />
      <Typography.Text>{t("Sparse threshold (%)")}</Typography.Text><InputNumber aria-label={t("Sparse threshold")} min={50} max={98} value={params.propertySparsePercentile} onChange={(value) => setters.setPropertySparsePercentile(Math.min(params.propertyOodPercentile - 1, value ?? 90))} />
      <Typography.Text>{t("OOD-like threshold (%)")}</Typography.Text><InputNumber aria-label={t("OOD-like threshold")} min={params.propertySparsePercentile + 1} max={99} value={params.propertyOodPercentile} onChange={(value) => setters.setPropertyOodPercentile(Math.max(params.propertySparsePercentile + 1, value ?? 99))} />
    </Space>}
  </>;
}

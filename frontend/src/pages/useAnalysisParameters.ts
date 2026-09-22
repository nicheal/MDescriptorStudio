import { useCallback, useMemo, useState } from "react";
import type { AnalysisParams } from "../features/analysis";
import type { CompareMode } from "./analysisShared";

export type AnalysisParameterView = Pick<
  AnalysisParams,
  | "projection"
  | "mode"
  | "preprocess"
  | "effectiveDimensionPreprocess"
  | "coverageMode"
  | "overviewAnalysis"
  | "nearZeroThreshold"
  | "lowVariationThreshold"
  | "featureCorrelationMethod"
  | "featureCorrelationThreshold"
>;

export interface SamplingQuota {
  groups: { group: string; structures: number; quota: number }[];
  n_candidates: number;
}

/**
 * Local controls and the canonical parameter snapshot used by Analysis.
 * Navigation values stay in useAnalysisUi; this hook owns only the values
 * that describe a run or its surrounding selectors.
 */
export function useAnalysisParameters(view: AnalysisParameterView) {
  const {
    projection,
    mode,
    preprocess,
    effectiveDimensionPreprocess,
    coverageMode,
    overviewAnalysis,
    nearZeroThreshold,
    lowVariationThreshold,
    featureCorrelationMethod,
    featureCorrelationThreshold,
  } = view;
  const [clusterAlgorithm, setClusterAlgorithm] = useState("kmeans");
  const [outlierAlgorithm, setOutlierAlgorithm] = useState("lof");
  const [samplingAlgorithm, setSamplingAlgorithm] = useState("fps");
  const [samplingStrategy, setSamplingStrategy] = useState("global");
  const [samplingScaling, setSamplingScaling] = useState("robust");
  const [samplingMinDistance, setSamplingMinDistance] = useState(0);
  const [samplingExistingRunId, setSamplingExistingRunId] = useState<string | null>(null);
  const [samplingBlocks, setSamplingBlocks] = useState<string[]>([]);
  const [samplingBudgetMode, setSamplingBudgetMode] = useState<"count" | "coverage">("count");
  const [samplingCoverage, setSamplingCoverage] = useState(95);
  const [samplingQuota, setSamplingQuota] = useState<SamplingQuota | null>(null);
  const [samplingQuotaBusy, setSamplingQuotaBusy] = useState(false);
  const [similarityMode, setSimilarityMode] = useState<"query" | "all_neighbors" | "pairwise">("query");
  const [compareMode, setCompareMode] = useState<CompareMode>("geometry");
  const [mantelMethod, setMantelMethod] = useState<"pearson" | "spearman">("pearson");
  const [mantelPermutations, setMantelPermutations] = useState(999);
  const [propertyName, setPropertyName] = useState("energy_per_atom");
  const [propertyFolds, setPropertyFolds] = useState(5);
  const [propertyReliabilityK, setPropertyReliabilityK] = useState(5);
  const [propertyDistanceMetric, setPropertyDistanceMetric] = useState<"euclidean" | "cosine">("euclidean");
  const [propertySparsePercentile, setPropertySparsePercentile] = useState(90);
  const [propertyOodPercentile, setPropertyOodPercentile] = useState(99);
  const [kernelName, setKernelName] = useState("rbf");
  const [localCutoff, setLocalCutoff] = useState(3.0);
  const [secondRun, setSecondRun] = useState<string | null>(null);
  const [referenceDatasetId, setReferenceDatasetId] = useState<string | null>(null);
  const [queryDatasetId, setQueryDatasetId] = useState<string | null>(null);
  const [referenceRunId, setReferenceRunId] = useState<string | null>(null);
  const [queryRunId, setQueryRunId] = useState<string | null>(null);
  const [referenceViewId, setReferenceViewId] = useState<string | null>(null);
  const [queryViewId, setQueryViewId] = useState<string | null>(null);
  const [viewId, setViewId] = useState<string | null>(null);
  const [exportFormat, setExportFormat] = useState("csv");
  const [exportPath, setExportPath] = useState("");
  const [k, setK] = useState(10);
  const [nClusters, setNClusters] = useState(6);
  const [nSamples, setNSamples] = useState(1000);
  const [uncertaintyK, setUncertaintyK] = useState(8);
  const [contamination, setContamination] = useState(0.01);
  const [queryIndex, setQueryIndex] = useState(0);
  const [methodGuideOpen, setMethodGuideOpen] = useState(false);
  const [perturbationType, setPerturbationType] = useState<"jitter" | "strain">("jitter");
  const [perturbationCount, setPerturbationCount] = useState(8);
  const [perturbationMaximum, setPerturbationMaximum] = useState(0.2);
  const [perturbationStructures, setPerturbationStructures] = useState(64);
  const [perturbationMetric, setPerturbationMetric] = useState("euclidean");
  const [tsnePerplexity, setTsnePerplexity] = useState(30);

  const analysisParams = useMemo<AnalysisParams>(() => ({
    projection,
    mode,
    preprocess,
    effectiveDimensionPreprocess,
    coverageMode,
    overviewAnalysis,
    nearZeroThreshold,
    lowVariationThreshold,
    featureCorrelationMethod,
    featureCorrelationThreshold,
    tsnePerplexity,
    similarityMode,
    k,
    queryIndex,
    clusterAlgorithm,
    nClusters,
    outlierAlgorithm,
    contamination,
    samplingAlgorithm,
    nSamples,
    uncertaintyK,
    samplingStrategy,
    samplingScaling,
    samplingMinDistance,
    samplingExistingRunId,
    samplingBlocks,
    samplingBudgetMode,
    samplingCoverage,
    compareMode,
    mantelMethod,
    mantelPermutations,
    localCutoff,
    kernelName,
    propertyName,
    propertyFolds,
    propertyReliabilityK,
    propertyDistanceMetric,
    propertySparsePercentile,
    propertyOodPercentile,
    perturbationType,
    perturbationCount,
    perturbationMaximum,
    perturbationStructures,
    perturbationMetric,
    referenceRunId,
    queryRunId,
    referenceViewId,
    queryViewId,
    viewId,
  }), [
    coverageMode,
    effectiveDimensionPreprocess,
    featureCorrelationMethod,
    featureCorrelationThreshold,
    lowVariationThreshold,
    mode,
    nearZeroThreshold,
    overviewAnalysis,
    preprocess,
    projection,
    clusterAlgorithm,
    compareMode,
    contamination,
    k,
    kernelName,
    localCutoff,
    mantelMethod,
    mantelPermutations,
    nClusters,
    nSamples,
    outlierAlgorithm,
    perturbationCount,
    perturbationMaximum,
    perturbationMetric,
    perturbationStructures,
    perturbationType,
    propertyDistanceMetric,
    propertyFolds,
    propertyName,
    propertyOodPercentile,
    propertyReliabilityK,
    propertySparsePercentile,
    queryIndex,
    queryRunId,
    queryViewId,
    referenceRunId,
    referenceViewId,
    samplingAlgorithm,
    samplingBlocks,
    samplingBudgetMode,
    samplingCoverage,
    samplingExistingRunId,
    samplingMinDistance,
    samplingScaling,
    samplingStrategy,
    similarityMode,
    tsnePerplexity,
    uncertaintyK,
    viewId,
  ]);

  const restoreParameters = useCallback((loaded: AnalysisParams) => {
    setTsnePerplexity(loaded.tsnePerplexity);
    setSimilarityMode(loaded.similarityMode as "query" | "all_neighbors" | "pairwise");
    setK(loaded.k);
    setQueryIndex(loaded.queryIndex);
    setClusterAlgorithm(loaded.clusterAlgorithm);
    setNClusters(loaded.nClusters);
    setOutlierAlgorithm(loaded.outlierAlgorithm);
    setContamination(loaded.contamination);
    setSamplingAlgorithm(loaded.samplingAlgorithm);
    setNSamples(loaded.nSamples);
    setUncertaintyK(loaded.uncertaintyK);
    setSamplingStrategy(loaded.samplingStrategy);
    setSamplingScaling(loaded.samplingScaling);
    setSamplingMinDistance(loaded.samplingMinDistance);
    setSamplingExistingRunId(loaded.samplingExistingRunId);
    setSamplingBlocks(loaded.samplingBlocks);
    setSamplingBudgetMode(loaded.samplingBudgetMode as "count" | "coverage");
    setSamplingCoverage(loaded.samplingCoverage);
    setCompareMode(loaded.compareMode as CompareMode);
    setMantelMethod(loaded.mantelMethod as "pearson" | "spearman");
    setMantelPermutations(loaded.mantelPermutations);
    setLocalCutoff(loaded.localCutoff);
    setKernelName(loaded.kernelName);
    setPropertyName(loaded.propertyName);
    setPropertyFolds(loaded.propertyFolds);
    setPropertyReliabilityK(loaded.propertyReliabilityK);
    setPropertyDistanceMetric(loaded.propertyDistanceMetric as "euclidean" | "cosine");
    setPropertySparsePercentile(loaded.propertySparsePercentile);
    setPropertyOodPercentile(loaded.propertyOodPercentile);
    setPerturbationType(loaded.perturbationType as "jitter" | "strain");
    setPerturbationCount(loaded.perturbationCount);
    setPerturbationMaximum(loaded.perturbationMaximum);
    setPerturbationStructures(loaded.perturbationStructures);
    setPerturbationMetric(loaded.perturbationMetric);
  }, []);

  return {
    analysisParams,
    restoreParameters,
    clusterAlgorithm, setClusterAlgorithm,
    outlierAlgorithm, setOutlierAlgorithm,
    samplingAlgorithm, setSamplingAlgorithm,
    samplingStrategy, setSamplingStrategy,
    samplingScaling, setSamplingScaling,
    samplingMinDistance, setSamplingMinDistance,
    samplingExistingRunId, setSamplingExistingRunId,
    samplingBlocks, setSamplingBlocks,
    samplingBudgetMode, setSamplingBudgetMode,
    samplingCoverage, setSamplingCoverage,
    samplingQuota, setSamplingQuota,
    samplingQuotaBusy, setSamplingQuotaBusy,
    similarityMode, setSimilarityMode,
    compareMode, setCompareMode,
    mantelMethod, setMantelMethod,
    mantelPermutations, setMantelPermutations,
    propertyName, setPropertyName,
    propertyFolds, setPropertyFolds,
    propertyReliabilityK, setPropertyReliabilityK,
    propertyDistanceMetric, setPropertyDistanceMetric,
    propertySparsePercentile, setPropertySparsePercentile,
    propertyOodPercentile, setPropertyOodPercentile,
    kernelName, setKernelName,
    localCutoff, setLocalCutoff,
    secondRun, setSecondRun,
    referenceDatasetId, setReferenceDatasetId,
    queryDatasetId, setQueryDatasetId,
    referenceRunId, setReferenceRunId,
    queryRunId, setQueryRunId,
    referenceViewId, setReferenceViewId,
    queryViewId, setQueryViewId,
    viewId, setViewId,
    exportFormat, setExportFormat,
    exportPath, setExportPath,
    k, setK,
    nClusters, setNClusters,
    nSamples, setNSamples,
    uncertaintyK, setUncertaintyK,
    contamination, setContamination,
    queryIndex, setQueryIndex,
    methodGuideOpen, setMethodGuideOpen,
    perturbationType, setPerturbationType,
    perturbationCount, setPerturbationCount,
    perturbationMaximum, setPerturbationMaximum,
    perturbationStructures, setPerturbationStructures,
    perturbationMetric, setPerturbationMetric,
    tsnePerplexity, setTsnePerplexity,
  };
}

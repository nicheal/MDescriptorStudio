import { analysisNavModuleForKey } from "./registry";
import type {
  AnalysisModuleKey,
  AnalysisNavModule,
  AnalysisParams,
  AnalysisSlot,
  TabKey,
} from "./types";

export const slotKey = (slot: Pick<AnalysisSlot, "moduleKey" | "inputKey" | "parameterKey">): string =>
  `${slot.moduleKey}|${slot.inputKey}|${slot.parameterKey}`;

export function buildParamsKey(tab: TabKey, p: AnalysisParams): string {
  switch (tab) {
    case "projection":
      return [p.projection, p.mode, p.preprocess, p.projection === "tsne" ? p.tsnePerplexity : "", p.viewId ?? "full"].join("|");
    case "similarity":
      // Pairwise similarity submits no k (it correlates every pair), so k must
      // not participate in its key: moving the slider would otherwise look like
      // a different analysis and re-run an identical one.
      return [p.similarityMode, p.mode, p.similarityMode === "pairwise" ? "" : p.k, p.similarityMode === "query" ? String(p.queryIndex) : "", p.viewId ?? "full"].join("|");
    case "clusters":
      return [p.clusterAlgorithm, p.nClusters, p.mode, p.viewId ?? "full"].join("|");
    case "outliers":
      return [p.outlierAlgorithm, p.k, p.contamination, p.mode, p.viewId ?? "full"].join("|");
    case "sampling":
      return [
        p.samplingAlgorithm,
        p.nSamples,
        p.mode,
        p.samplingAlgorithm === "uncertainty_diversity" ? p.uncertaintyK : "",
        p.samplingAlgorithm === "fps"
          ? [p.samplingStrategy, p.samplingScaling, p.samplingMinDistance, p.samplingExistingRunId ?? "none", p.samplingBlocks.length ? p.samplingBlocks.join("+") : "descriptor", p.samplingBudgetMode === "coverage" ? `cov${p.samplingCoverage}` : "count"].join(":")
          : "",
        p.samplingAlgorithm === "novelty_fps" || p.samplingAlgorithm === "uncertainty_diversity"
          ? [p.referenceRunId, p.referenceViewId ?? "full", p.queryRunId, p.queryViewId ?? "full"].join(":")
          : p.viewId ?? "full",
      ].join("|");
    case "coverage":
      return [p.coverageMode, p.mode, p.referenceRunId, p.referenceViewId ?? "full", p.queryRunId, p.queryViewId ?? "full"].join("|");
    case "compare":
      return [p.compareMode, p.mode, p.compareMode === "mantel" ? `${p.mantelMethod}|${p.mantelPermutations}` : ""].join("|");
    case "local":
      return [p.nClusters, p.k, p.localCutoff, p.viewId ?? "full"].join("|");
    case "kernel":
      return [p.kernelName, p.mode, p.viewId ?? "full"].join("|");
    case "overview": {
      const moduleParts = p.overviewAnalysis === "property_correlation"
        ? `${p.mode}|${p.propertyName}|${p.propertyFolds}|${p.propertyReliabilityK}|${p.propertyDistanceMetric}|${p.propertySparsePercentile}|${p.propertyOodPercentile}`
        : p.overviewAnalysis === "perturbation_sensitivity"
          ? `${p.perturbationType}|${p.perturbationCount}|${p.perturbationMaximum}|${p.perturbationMetric}|${p.perturbationStructures}`
          : p.overviewAnalysis === "feature_variance"
            ? `${p.nearZeroThreshold}|${p.lowVariationThreshold}`
            : p.overviewAnalysis === "feature_correlation"
              ? `${p.featureCorrelationMethod}|${p.featureCorrelationThreshold}`
              : p.overviewAnalysis === "effective_dimension" ? p.effectiveDimensionPreprocess : "";
      if (p.overviewAnalysis === "drift") return [p.overviewAnalysis, moduleParts, [p.referenceRunId, p.referenceViewId ?? "full", p.queryRunId, p.queryViewId ?? "full"].join(":")].join("|");
      return [p.overviewAnalysis, moduleParts, p.viewId ?? "full"].join("|");
    }
    default:
      return "";
  }
}

export function buildAnalysisInputKey(
  tab: TabKey,
  p: Pick<AnalysisParams, "overviewAnalysis" | "samplingAlgorithm" | "samplingExistingRunId" | "referenceRunId" | "queryRunId" | "referenceViewId" | "queryViewId" | "viewId">,
  selectedRun: string | null,
  secondRun: string | null,
): string {
  const crossDataset = tab === "coverage"
    || (tab === "sampling" && (p.samplingAlgorithm === "novelty_fps" || p.samplingAlgorithm === "uncertainty_diversity"))
    || (tab === "overview" && p.overviewAnalysis === "drift");
  if (crossDataset) return [p.referenceRunId ?? "none", p.referenceViewId ?? "full", p.queryRunId ?? "none", p.queryViewId ?? "full"].join("|");
  if (tab === "sampling" && p.samplingAlgorithm === "fps" && p.samplingExistingRunId) return [selectedRun ?? "none", p.viewId ?? "full", p.samplingExistingRunId, "full"].join("|");
  if (tab === "compare" || (tab === "overview" && p.overviewAnalysis === "sensitivity")) return [selectedRun ?? "none", secondRun ?? "none"].join("|");
  return [selectedRun ?? "none", p.viewId ?? "full"].join("|");
}

export function slotForParams(slots: Record<string, AnalysisSlot>, moduleKey: AnalysisModuleKey | null, inputKey: string, parameterKey: string): AnalysisSlot | null {
  if (!moduleKey || !inputKey) return null;
  return slots[slotKey({ moduleKey, inputKey, parameterKey })] ?? null;
}

function slotBelongsToModule(slot: AnalysisSlot, module: AnalysisNavModule): boolean {
  return slot.moduleKey === module.key;
}

export function analysisSlotMatchesModule(slot: AnalysisSlot, moduleKey: AnalysisModuleKey | string | null): boolean {
  const module = moduleKey ? analysisNavModuleForKey(moduleKey) : null;
  return module ? slotBelongsToModule(slot, module) : false;
}

export function latestSlotForModule(slots: Record<string, AnalysisSlot>, moduleKey: AnalysisModuleKey | string | null, inputKey: string | null): AnalysisSlot | null {
  if (!inputKey || !moduleKey) return null;
  const module = analysisNavModuleForKey(moduleKey);
  if (!module) return null;
  let best: AnalysisSlot | null = null;
  for (const slot of Object.values(slots)) {
    if (slot.inputKey !== inputKey || !slotBelongsToModule(slot, module)) continue;
    if (!best || slot.updatedAt > best.updatedAt || (slot.updatedAt === best.updatedAt && slot.seq > best.seq)) best = slot;
  }
  return best;
}

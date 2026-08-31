import Plot from "react-plotly.js";
import type { Data, Layout } from "plotly.js";
import { Empty, Table, Typography } from "antd";
import { useT, type Pair } from "../i18n";
import type { AnalysisPreview } from "../types/protocol";
import type { AnalysisPoint } from "./analysisPreview";

export type AnalysisArrays = Record<string, unknown[]>;

type Props = {
  preview: AnalysisPreview | null;
  arrays: AnalysisArrays;
  points: AnalysisPoint[];
  loading: boolean;
  selectedIndices: number[];
  onSelect: (point: AnalysisPoint) => void;
};

const TITLES: Record<string, Pair> = {
  similarity: { en: "NEAREST-NEIGHBOR SIMILARITY", zh: "最近邻相似度" },
  neighbors: { en: "NEAREST-NEIGHBOR GRAPH", zh: "最近邻图" },
  pairwise_similarity: { en: "PAIRWISE SIMILARITY MATRIX", zh: "两两相似度矩阵" },
  clusters: { en: "CLUSTER STRUCTURE", zh: "聚类结构" },
  outliers: { en: "OUTLIER LANDSCAPE", zh: "离群点分布" },
  sampling: { en: "REPRESENTATIVE SAMPLING", zh: "代表性采样" },
  acquisition: { en: "NOVELTY–DIVERSITY ACQUISITION", zh: "新颖性–多样性采样" },
  coverage: { en: "DATASET COVERAGE", zh: "数据集覆盖度" },
  overlap: { en: "TRAIN / TEST OVERLAP", zh: "训练 / 测试重叠" },
  compare: { en: "DESCRIPTOR COMPARISON", zh: "描述符对比" },
  mantel: { en: "MANTEL PERMUTATION TEST", zh: "Mantel 置换检验" },
  feature_correlation: { en: "FEATURE REDUNDANCY", zh: "特征冗余" },
  property_correlation: { en: "PROPERTY CORRELATION", zh: "属性相关性" },
  local_diversity: { en: "LOCAL ENVIRONMENT DIVERSITY", zh: "局部环境多样性" },
  trajectory: { en: "DESCRIPTOR TRAJECTORY", zh: "描述符轨迹" },
  drift: { en: "DATASET DRIFT", zh: "数据集漂移" },
  sensitivity: { en: "PARAMETER SENSITIVITY", zh: "参数敏感性" },
  perturbation_sensitivity: { en: "STRUCTURAL PERTURBATION SENSITIVITY", zh: "结构扰动敏感性" },
  kernel: { en: "KERNEL DIAGNOSTICS", zh: "核函数诊断" },
};

// Sensitivity metric legend names (generated the same way as before).
const SENSITIVITY_METRIC_LABELS: Record<string, Pair> = {
  pairwise_distance_pearson: { en: "pairwise distance pearson", zh: "两两距离 Pearson" },
  neighbor_overlap: { en: "neighbor overlap", zh: "近邻重叠" },
  clustering_stability: { en: "clustering stability", zh: "聚类稳定性" },
};

const COLORS = ["#0F6CBD", "#F7630C", "#107C10", "#8764B8", "#D13438", "#00B7C3", "#C239B3"];

export default function AnalysisResultVisualization(props: Props) {
  const { preview, loading } = props;
  const { t, tr } = useT();
  if (!preview) return null;
  const kind = String(preview.kind ?? "");
  if (!TITLES[kind]) return null;
  return (
    <section className="analysis-card analysis-visual-card">
      <div className="analysis-section-heading">
        <Typography.Text strong>{tr(TITLES[kind])}</Typography.Text>
        <Typography.Text type="secondary">{t("Purpose-built visual summary")}</Typography.Text>
      </div>
      {loading ? <NoData message={t("Loading bounded analysis arrays…")} /> : <Visualization {...props} kind={kind} />}
    </section>
  );
}

function Visualization({ kind, preview, arrays, points, selectedIndices, onSelect }: Props & { kind: string }) {
  if (!preview) return null;
  if (kind === "similarity" || kind === "neighbors") return <NeighborView preview={preview} />;
  if (kind === "pairwise_similarity") return <MatrixView preview={preview} matrix={matrix(arrays.similarity_matrix)} label="Similarity" />;
  if (kind === "clusters") return <ClusterView preview={preview} points={points} selectedIndices={selectedIndices} onSelect={onSelect} />;
  if (kind === "outliers") return <OutlierView preview={preview} points={points} selectedIndices={selectedIndices} onSelect={onSelect} />;
  if (kind === "sampling" || kind === "acquisition") return <SamplingView preview={preview} points={points} selectedIndices={selectedIndices} onSelect={onSelect} />;
  if (kind === "coverage" || kind === "overlap" || kind === "drift") return <CoverageView preview={preview} arrays={arrays} />;
  if (kind === "compare") return <CompareView preview={preview} arrays={arrays} />;
  if (kind === "mantel") return <MantelView preview={preview} arrays={arrays} />;
  if (kind === "feature_correlation") return <FeatureCorrelationView preview={preview} arrays={arrays} />;
  if (kind === "property_correlation") return <PropertyView preview={preview} arrays={arrays} />;
  if (kind === "local_diversity") return <LocalView preview={preview} arrays={arrays} points={points} selectedIndices={selectedIndices} onSelect={onSelect} />;
  if (kind === "trajectory") return <TrajectoryView preview={preview} arrays={arrays} points={points} selectedIndices={selectedIndices} onSelect={onSelect} />;
  if (kind === "sensitivity") return <SensitivityView preview={preview} />;
  if (kind === "perturbation_sensitivity") return <PerturbationView preview={preview} arrays={arrays} />;
  if (kind === "kernel") return <KernelView preview={preview} arrays={arrays} />;
  return null;
}

function NeighborView({ preview }: { preview: AnalysisPreview }) {
  const { t } = useT();
  const rows = records(preview.rows).slice(0, 60);
  if (!rows.length) return <NoData message={t("No neighbors were returned.")} />;
  const reversed = rows.slice().reverse();
  const hasSimilarity = reversed.some((row) => num(row.similarity) !== null);
  return <>
    <Metrics values={[{ k: t("Neighbors"), v: rows.length }, { k: t("Metric"), v: preview.metric }, { k: t("Query"), v: preview.query_index }]} />
    <PlotFrame ariaLabel={t("Ranked nearest descriptor samples")} data={[{
      type: "bar", orientation: "h",
      x: reversed.map((row) => num(hasSimilarity ? row.similarity : row.distance) ?? 0),
      y: reversed.map((row) => String(row.sample_id ?? t("sample {index}", { index: String(row.i ?? "?") }))),
      marker: { color: hasSimilarity ? "#107C10" : "#0F6CBD" },
      hovertemplate: `%{y}<br>${hasSimilarity ? t("similarity") : t("distance")}=%{x:.5g}<extra></extra>`,
    }]} layout={layout({ xaxis: { title: hasSimilarity ? t("Similarity") : t("Distance") }, yaxis: { automargin: true } })} />
    <DataTable rows={rows} />
  </>;
}

function MatrixView({ preview, matrix: values, label }: { preview: AnalysisPreview; matrix: number[][]; label: string }) {
  const { t } = useT();
  const labelT = t(label);
  if (!values.length) return <NoData message={t("{label} matrix is unavailable.", { label: labelT })} />;
  return <>
    <Metrics values={[{ k: t("Samples"), v: preview.sample_count }, { k: t("Metric"), v: preview.metric ?? preview.kernel }, { k: t("Minimum"), v: preview.kernel_min ?? preview.distance_min }, { k: t("Maximum"), v: preview.kernel_max ?? preview.distance_max }]} />
    <PlotFrame ariaLabel={t("{label} heatmap", { label: labelT })} data={[{ type: "heatmap", z: values, colorscale: "Viridis", colorbar: { title: { text: labelT } }, hovertemplate: `${t("row")}=%{y}<br>${t("column")}=%{x}<br>${t("value")}=%{z:.5g}<extra></extra>` }]} layout={layout({ xaxis: { title: t("Sample") }, yaxis: { title: t("Sample"), autorange: "reversed" } })} />
  </>;
}

function ClusterView({ preview, points, selectedIndices, onSelect }: Pick<Props, "preview" | "points" | "selectedIndices" | "onSelect">) {
  const { t } = useT();
  const counts = countBy(points.map((point) => point.label ?? -1));
  return <>
    <Metrics values={[{ k: t("Clusters"), v: preview?.cluster_count }, { k: t("Noise"), v: preview?.noise_count }, { k: t("Samples"), v: points.length }]} />
    <div className="analysis-chart-grid">
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color="label" ariaLabel={t("Descriptor clusters in PCA space")} />
      <PlotFrame compact ariaLabel={t("Samples per cluster")} data={[{ type: "bar", x: counts.map(([key]) => key === "-1" ? t("Noise") : `C${key}`), y: counts.map(([, value]) => value), marker: { color: counts.map((_, index) => COLORS[index % COLORS.length]) } }]} layout={layout({ xaxis: { title: t("Cluster") }, yaxis: { title: t("Samples") } })} />
    </div>
  </>;
}

function OutlierView({ preview, points, selectedIndices, onSelect }: Pick<Props, "preview" | "points" | "selectedIndices" | "onSelect">) {
  const { t } = useT();
  return <>
    <Metrics values={[{ k: t("Outliers"), v: preview?.outlier_count }, { k: t("Algorithm"), v: preview?.algorithm }, { k: t("Contamination"), v: preview?.contamination }]} />
    <div className="analysis-chart-grid">
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color="score" ariaLabel={t("Outlier scores in descriptor space")} />
      <PlotFrame compact ariaLabel={t("Outlier score distribution")} data={[{ type: "histogram", x: points.map((point) => point.score ?? 0), marker: { color: "#0F6CBD" } }]} layout={layout({ xaxis: { title: t("Outlier score") }, yaxis: { title: t("Samples") } })} />
    </div>
  </>;
}

function SamplingView({ preview, points, selectedIndices, onSelect }: Pick<Props, "preview" | "points" | "selectedIndices" | "onSelect">) {
  const { t } = useT();
  const kind = String(preview?.kind ?? "sampling");
  const uncertaintyDriven = preview?.algorithm === "uncertainty_diversity";
  return <>
    <Metrics values={[{ k: t("Selected"), v: preview?.selected_count }, { k: t("Candidates"), v: preview?.candidate_pool ?? points.length }, { k: t("Method"), v: preview?.algorithm }, { k: uncertaintyDriven ? t("Mean uncertainty") : t("Mean novelty"), v: uncertaintyDriven ? preview?.mean_selected_uncertainty : preview?.mean_selected_novelty }]} />
    <div className="analysis-chart-grid">
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color={kind === "acquisition" ? uncertaintyDriven ? "uncertainty" : "distance" : "selected"} ariaLabel={t("Selected representative samples in descriptor space")} />
      <PlotFrame compact ariaLabel={t("Selection score distribution")} data={[{ type: "histogram", x: points.map((point) => kind === "acquisition" ? uncertaintyDriven ? point.uncertainty ?? 0 : point.distance ?? 0 : point.x), marker: { color: uncertaintyDriven ? "#D13438" : "#8764B8" } }]} layout={layout({ xaxis: { title: kind === "acquisition" ? uncertaintyDriven ? t("kNN extrapolation uncertainty") : t("Novelty distance") : t("PC1 distribution") }, yaxis: { title: t("Samples") } })} />
    </div>
  </>;
}

function CoverageView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const coords = matrix(arrays.projection_coords);
  const source = nums(arrays.projection_source);
  const rows = records(preview.rows);
  const distances = rows.map((row) => num(row.distances ?? row.distance)).filter((value): value is number => value !== null);
  const categories = strings(preview.categories);
  const labels = nums(arrays.labels);
  const categoryCounts = categories.map((_, category) => labels.filter((label) => label === category).length);
  return <>
    <Metrics values={coverageMetrics(preview, t)} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Reference and query descriptor-space projection")} data={[0, 1].map((group) => ({ type: "scattergl", mode: "markers", name: group ? t("Query") : t("Reference"), x: coords.flatMap((point, index) => source[index] === group ? [point[0]] : []), y: coords.flatMap((point, index) => source[index] === group ? [point[1]] : []), marker: { size: group ? 7 : 5, color: group ? "#D13438" : "#0F6CBD", opacity: group ? 0.82 : 0.45 }, hovertemplate: `${group ? t("Query") : t("Reference")}<br>PC1=%{x:.4g}<br>PC2=%{y:.4g}<extra></extra>` })) as Data[]} layout={layout({ xaxis: { title: t("Joint PC1") }, yaxis: { title: t("Joint PC2") }, legend: { orientation: "h" } })} />
      <PlotFrame compact ariaLabel={t("Nearest-reference distance distribution")} data={distances.length ? [{ type: "histogram", x: distances, marker: { color: "#0F6CBD" } }] : [{ type: "bar", x: categories, y: categoryCounts, marker: { color: ["#107C10", "#F7630C", "#D13438"] } }]} layout={layout({ xaxis: { title: distances.length ? t("Nearest-reference distance") : t("Category") }, yaxis: { title: t("Samples") } })} />
    </div>
    <DataTable rows={rows} />
  </>;
}

function CompareView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const leftPairs = nums(arrays.left_pair_distances);
  const rightPairs = nums(arrays.right_pair_distances);
  const leftCoords = matrix(arrays.left_coords);
  const rightCoords = matrix(arrays.right_coords);
  const count = Math.min(leftPairs.length, rightPairs.length);
  return <>
    <Metrics values={[{ k: t("Pair-distance Pearson"), v: preview.pairwise_distance_pearson }, { k: t("Spearman"), v: preview.pairwise_distance_spearman }, { k: t("kNN overlap"), v: preview.neighbor_overlap }, { k: t("Cluster stability"), v: preview.clustering_stability }, { k: t("PCA topology error"), v: preview.pca_topology_error }, { k: t("Effective dimension"), v: `${fmt(preview.left_effective_dimension)} / ${fmt(preview.right_effective_dimension)}` }]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Pairwise descriptor distances comparison")} data={[{ type: "scattergl", mode: "markers", x: leftPairs.slice(0, count), y: rightPairs.slice(0, count), marker: { size: 5, color: "#0F6CBD", opacity: 0.45 }, hovertemplate: `${t("left")}=%{x:.5g}<br>${t("right")}=%{y:.5g}<extra></extra>` }]} layout={layout({ xaxis: { title: t("Left pair distance") }, yaxis: { title: t("Right pair distance") } })} />
      <PlotFrame compact ariaLabel={t("Descriptor PCA topology comparison")} data={[{ type: "scattergl", mode: "markers", name: t("Left"), x: leftCoords.map((row) => row[0]), y: leftCoords.map((row) => row[1]), marker: { size: 6, color: "#0F6CBD", opacity: 0.55 } }, { type: "scattergl", mode: "markers", name: t("Right"), x: rightCoords.map((row) => row[0]), y: rightCoords.map((row) => row[1]), marker: { size: 6, color: "#D13438", opacity: 0.55 } }]} layout={layout({ xaxis: { title: "PC1" }, yaxis: { title: "PC2" }, legend: { orientation: "h" } })} />
    </div>
  </>;
}

function MantelView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const leftPairs = nums(arrays.left_pair_distances);
  const rightPairs = nums(arrays.right_pair_distances);
  const nullDistribution = nums(arrays.null_distribution);
  const count = Math.min(leftPairs.length, rightPairs.length);
  const statistic = num(preview.statistic) ?? 0;
  return <>
    <Metrics values={[{ k: t("Mantel r"), v: statistic }, { k: t("p-value"), v: preview.p_value }, { k: t("Statistic"), v: preview.method }, { k: t("Permutations"), v: preview.permutations }, { k: t("Pairs"), v: preview.pair_count }, { k: t("Significant (α=.05)"), v: preview.significant_at_05 ? t("yes") : t("no") }]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Mantel paired descriptor distances")} data={[{ type: "scattergl", mode: "markers", x: leftPairs.slice(0, count), y: rightPairs.slice(0, count), marker: { size: 5, color: "#0F6CBD", opacity: 0.45 }, hovertemplate: `${t("left")}=%{x:.5g}<br>${t("right")}=%{y:.5g}<extra></extra>` }]} layout={layout({ xaxis: { title: t("Left pair distance") }, yaxis: { title: t("Right pair distance") } })} />
      <PlotFrame compact ariaLabel={t("Mantel permutation null distribution")} data={[{ type: "histogram", x: nullDistribution, marker: { color: "#8764B8" } }]} layout={layout({ xaxis: { title: `${preview.method ?? "Pearson"} ${t("null statistic")}` }, yaxis: { title: t("Permutations") }, shapes: [{ type: "line", x0: statistic, x1: statistic, y0: 0, y1: 1, yref: "paper", line: { color: "#D13438", width: 2, dash: "dash" } }] })} />
    </div>
    <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>{t("Two-sided permutation p-value with +1 correction; the red line marks the observed statistic.")}</Typography.Paragraph>
  </>;
}

function FeatureCorrelationView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const values = matrix(arrays.correlation_matrix);
  const featureIndices = nums(arrays.correlation_feature_indices);
  return <>
    <Metrics values={[{ k: t("Features"), v: preview.feature_count }, { k: t("Zero variance"), v: preview.zero_variance_count }, { k: t("Highly correlated pairs"), v: preview.highly_correlated_pairs }, { k: t("Redundant features"), v: preview.redundant_feature_count }, { k: t("Redundancy ratio"), v: preview.redundancy_ratio }]} />
    {values.length ? <PlotFrame ariaLabel={t("Descriptor feature correlation heatmap")} data={[{ type: "heatmap", z: values, x: featureIndices, y: featureIndices, zmin: -1, zmax: 1, colorscale: [[0, "#D13438"], [0.5, "#FFFFFF"], [1, "#0F6CBD"]], colorbar: { title: { text: "Pearson r" } }, hovertemplate: `F%{y} ↔ F%{x}<br>r=%{z:.4f}<extra></extra>` }]} layout={layout({ xaxis: { title: t("Feature") }, yaxis: { title: t("Feature"), autorange: "reversed" } })} /> : <NoData message={t("Correlation matrix is unavailable.")} />}
    <DataTable rows={records(preview.pairs)} />
  </>;
}

function PropertyView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const targets = nums(arrays.targets);
  const predictions = nums(arrays.predictions);
  const residuals = nums(arrays.residuals);
  const pairDistance = nums(arrays.pair_distance);
  const pairDelta = nums(arrays.pair_property_delta);
  const identityBounds = [...targets, ...predictions];
  const identityMin = identityBounds.length ? Math.min(...identityBounds) : 0;
  const identityMax = identityBounds.length ? Math.max(...identityBounds) : 1;
  return <>
    <Metrics values={[{ k: t("Property"), v: preview.property }, { k: t("Samples"), v: preview.sample_count }, { k: t("CV R²"), v: preview.r2 }, { k: t("CV RMSE"), v: preview.rmse }, { k: t("CV MAE"), v: preview.mae }, { k: t("Distance–property r"), v: preview.distance_property_correlation }]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Cross-validated property prediction")} data={[{ type: "scattergl", mode: "markers", x: targets, y: predictions, marker: { size: 6, color: "#0F6CBD", opacity: 0.65 }, hovertemplate: `${t("target")}=%{x:.5g}<br>${t("prediction")}=%{y:.5g}<extra></extra>` }]} layout={layout({ xaxis: { title: t("Target") }, yaxis: { title: t("Cross-validated prediction") }, shapes: [{ type: "line", x0: identityMin, y0: identityMin, x1: identityMax, y1: identityMax, line: { color: "#616161", dash: "dash" } }] })} />
      <PlotFrame compact ariaLabel={t("Property residual distribution")} data={[{ type: "histogram", x: residuals, marker: { color: "#F7630C" } }]} layout={layout({ xaxis: { title: t("Residual") }, yaxis: { title: t("Samples") } })} />
      <PlotFrame compact ariaLabel={t("Descriptor distance versus property difference")} data={[{ type: "scattergl", mode: "markers", x: pairDistance, y: pairDelta, marker: { size: 4, color: "#8764B8", opacity: 0.35 } }]} layout={layout({ xaxis: { title: t("Descriptor pair distance") }, yaxis: { title: t("Absolute property difference") } })} />
      <TopFeatureBars rows={records(preview.top_features)} />
    </div>
  </>;
}

function LocalView({ preview, arrays, points, selectedIndices, onSelect }: Pick<Props, "preview" | "arrays" | "points" | "selectedIndices" | "onSelect">) {
  const { t } = useT();
  const rows = records(preview?.element_summary);
  const coordination = nums(arrays.coordination);
  const neighborDistances = nums(arrays.neighbor_distances);
  return <>
    <Metrics values={[{ k: t("Local environments"), v: preview?.sample_count }, { k: t("Elements"), v: rows.length }, { k: t("Outliers"), v: rows.reduce((sum, row) => sum + (num(row.outliers) ?? 0), 0) }, { k: t("Cutoff (Å)"), v: preview?.cutoff }, { k: t("Mean coordination"), v: preview?.mean_coordination }, { k: t("Max coordination"), v: preview?.max_coordination }]} />
    <div className="analysis-chart-grid">
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color="element" ariaLabel={t("Atom-level local environment map")} />
      <PlotFrame compact ariaLabel={t("Local environment categories by element")} data={["distorted", "outliers"].map((key, index) => ({ type: "bar", name: t(key), x: rows.map((row) => `Z=${row.element}`), y: rows.map((row) => num(row[key]) ?? 0), marker: { color: COLORS[index + 1] } })) as Data[]} layout={layout({ barmode: "group", xaxis: { title: t("Element") }, yaxis: { title: t("Environments") }, legend: { orientation: "h" } })} />
      <PlotFrame compact ariaLabel={t("Coordination number distribution")} data={[{ type: "histogram", x: coordination, marker: { color: "#107C10" } }]} layout={layout({ xaxis: { title: t("Coordination number"), dtick: 1 }, yaxis: { title: t("Atoms") } })} />
    </div>
    {neighborDistances.length > 0 && <PlotFrame compact ariaLabel={t("Local neighbor distance distribution")} data={[{ type: "histogram", x: neighborDistances, marker: { color: "#F7630C" } }]} layout={layout({ xaxis: { title: t("Neighbor distance (Å)") }, yaxis: { title: t("Neighbor pairs") } })} />}
    <DataTable rows={rows} />
  </>;
}

function TrajectoryView({ preview, arrays, points, selectedIndices, onSelect }: Pick<Props, "preview" | "arrays" | "points" | "selectedIndices" | "onSelect">) {
  const { t } = useT();
  const time = nums(arrays.time);
  const stepDistance = nums(arrays.step_distance);
  const eventIndices = nums(arrays.event_indices).map(Math.round).filter((index) => index >= 0 && index < time.length);
  return <>
    <Metrics values={[{ k: t("Frames"), v: time.length }, { k: t("Path length"), v: preview?.total_distance }, { k: t("Max displacement"), v: preview?.max_reference_distance }, { k: t("Detected events"), v: preview?.event_count }]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Descriptor trajectory distances")} data={[{ type: "scatter", mode: "lines", name: t("Step"), x: time, y: stepDistance, line: { color: "#0F6CBD" } }, { type: "scatter", mode: "lines", name: t("From start"), x: time, y: nums(arrays.reference_distance), line: { color: "#D13438" } }, { type: "scatter", mode: "lines", name: t("Cumulative"), x: time, y: nums(arrays.cumulative_distance), line: { color: "#F7630C" } }, { type: "scatter", mode: "markers", name: t("Events"), x: eventIndices.map((index) => time[index]), y: eventIndices.map((index) => stepDistance[index]), marker: { color: "#D13438", size: 9, symbol: "diamond" } }]} layout={layout({ xaxis: { title: String(preview?.time_unit ?? t("Frame")) }, yaxis: { title: t("Descriptor distance") }, legend: { orientation: "h" } })} />
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color="path" ariaLabel={t("Descriptor trajectory path in PCA space")} lines />
    </div>
  </>;
}

function SensitivityView({ preview }: { preview: AnalysisPreview }) {
  const { t, tr } = useT();
  const rows = records(preview.runs);
  if (!rows.length) return <NoData message={t("No aligned runs were returned.")} />;
  const metrics = ["pairwise_distance_pearson", "neighbor_overlap", "clustering_stability"];
  return <>
    <Metrics values={[{ k: t("Runs"), v: rows.length }, { k: t("Baseline"), v: preview.baseline_run_id }]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Parameter sensitivity geometry metrics")} data={metrics.map((key, index) => ({ type: "bar", name: tr(SENSITIVITY_METRIC_LABELS[key] ?? { en: key, zh: key }), x: rows.map((row, runIndex) => runLabel(row, runIndex, t)), y: rows.map((row) => num(row[key]) ?? 0), marker: { color: COLORS[index] } })) as Data[]} layout={layout({ barmode: "group", yaxis: { title: t("Agreement (higher is better)"), range: [-0.05, 1.05] }, xaxis: { automargin: true }, legend: { orientation: "h" } })} />
      <PlotFrame compact ariaLabel={t("Descriptor compute peak memory")} data={[{ type: "bar", x: rows.map((row, index) => runLabel(row, index, t)), y: rows.map((row) => { const bytes = num(row.memory_peak_bytes); return bytes == null ? null : bytes / 1024 / 1024; }), marker: { color: "#D13438" }, hovertemplate: "%{x}<br>peak RSS=%{y:.2f} MB<extra></extra>" }]} layout={layout({ xaxis: { automargin: true }, yaxis: { title: t("Peak RSS (MB)") } })} />
    </div>
    <DataTable rows={rows} />
  </>;
}

function PerturbationView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const amplitudes = nums(arrays.amplitudes);
  const mean = nums(arrays.mean_response);
  const median = nums(arrays.median_response);
  const p95 = nums(arrays.p95_response);
  const max = nums(arrays.max_response);
  const responseMatrix = matrix(arrays.response_matrix);
  if (!amplitudes.length || !mean.length) return <NoData message={t("No structural perturbation response was returned.")} />;
  return <>
    <Metrics values={[{ k: t("Perturbation"), v: preview.perturbation }, { k: t("Metric"), v: preview.metric }, { k: t("Structures"), v: preview.sample_count }, { k: t("Steps"), v: preview.curve_count }, { k: t("Response"), v: preview.response_unit }]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Descriptor response versus structural perturbation")} data={[{ type: "scatter", mode: "lines+markers", name: t("Mean"), x: amplitudes, y: mean, line: { color: "#0F6CBD", width: 2 } }, { type: "scatter", mode: "lines", name: t("Median"), x: amplitudes.slice(0, median.length), y: median, line: { color: "#107C10", dash: "dash" } }, { type: "scatter", mode: "lines", name: t("P95"), x: amplitudes.slice(0, p95.length), y: p95, line: { color: "#D13438", dash: "dot" } }, { type: "scatter", mode: "lines", name: t("Max"), x: amplitudes.slice(0, max.length), y: max, line: { color: "#F7630C", dash: "dashdot" } }]} layout={layout({ xaxis: { title: preview.perturbation === "strain" ? t("Isotropic strain") : t("Jitter amplitude (Å)") }, yaxis: { title: String(preview.response_unit ?? t("Descriptor response")) }, legend: { orientation: "h" } })} />
      <PlotFrame compact ariaLabel={t("Per-structure perturbation response heatmap")} data={[{ type: "heatmap", z: responseMatrix, x: amplitudes, colorscale: "Viridis", colorbar: { title: { text: t("Response") } }, hovertemplate: `${t("amplitude")}=%{x:.4g}<br>${t("structure")}=%{y}<br>${t("response")}=%{z:.5g}<extra></extra>` }]} layout={layout({ xaxis: { title: preview.perturbation === "strain" ? t("Isotropic strain") : t("Jitter amplitude (Å)") }, yaxis: { title: t("Structure index"), autorange: "reversed" } })} />
    </div>
    <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>{t("The curve is generated by recomputing the selected descriptor on the same structures after a seeded perturbation sweep.")}</Typography.Paragraph>
  </>;
}

function KernelView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const eigenvalues = nums(arrays.eigenvalues);
  const kernelMatrix = matrix(arrays.kernel_matrix);
  return <>
    <Metrics values={[{ k: t("Kernel"), v: preview.kernel }, { k: t("Samples"), v: preview.sample_count }, { k: t("Effective rank"), v: preview.effective_rank }, { k: t("Top eigenvalue fraction"), v: preview.top_eigenvalue_fraction }]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Kernel heatmap")} data={[{ type: "heatmap", z: kernelMatrix, colorscale: "Viridis", colorbar: { title: { text: t("Kernel") } }, hovertemplate: `${t("row")}=%{y}<br>${t("column")}=%{x}<br>${t("value")}=%{z:.5g}<extra></extra>` }]} layout={layout({ xaxis: { title: t("Sample") }, yaxis: { title: t("Sample"), autorange: "reversed" } })} />
      <PlotFrame compact ariaLabel={t("Kernel eigenspectrum")} data={[{ type: "bar", x: eigenvalues.slice(0, 100).map((_, index) => index + 1), y: eigenvalues.slice(0, 100), marker: { color: "#8764B8" } }]} layout={layout({ xaxis: { title: t("Component") }, yaxis: { title: t("Centered eigenvalue") } })} />
    </div>
  </>;
}

const POINT_COLOR_LABELS: Record<string, Pair> = {
  label: { en: "label", zh: "簇标签" },
  score: { en: "score", zh: "分数" },
  selected: { en: "selected", zh: "是否选中" },
  distance: { en: "distance", zh: "距离" },
  uncertainty: { en: "uncertainty", zh: "不确定性" },
  element: { en: "element", zh: "元素" },
  path: { en: "path", zh: "路径" },
};

function PointPlot({ points, selectedIndices, onSelect, color, ariaLabel, lines = false }: { points: AnalysisPoint[]; selectedIndices: number[]; onSelect: (point: AnalysisPoint) => void; color: "label" | "score" | "selected" | "distance" | "uncertainty" | "element" | "path"; ariaLabel: string; lines?: boolean }) {
  const { t, tr } = useT();
  if (!points.length) return <NoData message={t("No projected samples are available.")} />;
  const selected = new Set(selectedIndices);
  const colorValues = points.map((point) => color === "label" ? point.label ?? -1 : color === "score" ? point.score ?? 0 : color === "distance" ? point.distance ?? 0 : color === "uncertainty" ? point.uncertainty ?? 0 : color === "element" ? point.element ?? 0 : color === "selected" ? selected.has(point.i) ? 1 : 0 : point.i);
  return <PlotFrame compact ariaLabel={ariaLabel} onClick={(index) => points[index] && onSelect(points[index])} data={[{ type: "scattergl", mode: lines ? "lines+markers" : "markers", x: points.map((point) => point.x), y: points.map((point) => point.y), text: points.map((point) => `${point.sample_id ?? t("sample {index}", { index: point.i })}${point.row == null ? "" : t(" · atom {row}", { row: point.row })}`), marker: { size: color === "selected" ? points.map((point) => selected.has(point.i) ? 10 : 5) : 7, color: colorValues, colorscale: color === "selected" ? [[0, "#C8CDD4"], [1, "#D13438"]] : "Viridis", showscale: color !== "selected" && color !== "path", colorbar: { title: { text: tr(POINT_COLOR_LABELS[color] ?? { en: color, zh: color }) } }, opacity: 0.8 }, line: lines ? { color: "#0F6CBD", width: 1.5 } : undefined, hovertemplate: "%{text}<br>x=%{x:.5g}<br>y=%{y:.5g}<extra></extra>" }]} layout={layout({ xaxis: { title: "PC1" }, yaxis: { title: "PC2" }, showlegend: false })} />;
}

function TopFeatureBars({ rows }: { rows: Record<string, unknown>[] }) {
  const { t } = useT();
  const shown = rows.slice(0, 20).reverse();
  return <PlotFrame compact ariaLabel={t("Most property-correlated descriptor features")} data={[{ type: "bar", orientation: "h", x: shown.map((row) => num(row.correlation) ?? 0), y: shown.map((row) => `F${row.feature}`), marker: { color: shown.map((row) => (num(row.correlation) ?? 0) >= 0 ? "#0F6CBD" : "#D13438") } }]} layout={layout({ xaxis: { title: t("Pearson correlation"), range: [-1, 1] }, yaxis: { automargin: true } })} />;
}

function PlotFrame({ data, layout: plotLayout, ariaLabel, compact = false, onClick }: { data: Data[]; layout: Partial<Layout>; ariaLabel: string; compact?: boolean; onClick?: (index: number) => void }) {
  return <div className={compact ? "analysis-purpose-chart compact" : "analysis-purpose-chart"} aria-label={ariaLabel}><Plot data={data} layout={plotLayout} config={{ responsive: true, displaylogo: false, modeBarButtonsToRemove: ["toImage"] }} style={{ width: "100%", height: "100%" }} onClick={(event) => { const index = event.points?.[0]?.pointIndex; if (typeof index === "number") onClick?.(index); }} /></div>;
}

function Metrics({ values }: { values: { k: string; v: unknown }[] }) {
  const visible = values.filter(({ v }) => v !== undefined && v !== null);
  return <div className="analysis-metric-strip">{visible.map(({ k, v }) => <div className="analysis-metric" key={k}><Typography.Text type="secondary">{k}</Typography.Text><Typography.Text strong>{fmt(v)}</Typography.Text></div>)}</div>;
}

function DataTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return null;
  const keys = Object.keys(rows[0]).filter((key) => key !== "parameters" && key !== "warnings").slice(0, 8);
  return <Table className="analysis-data-table" size="small" pagination={{ pageSize: 8, hideOnSinglePage: true }} rowKey={(row, index) => `${row.sample_id ?? row.run_id ?? index}`} dataSource={rows} columns={keys.map((key) => ({ title: key.replaceAll("_", " "), dataIndex: key, key, render: (value: unknown) => fmt(value) }))} />;
}

function NoData({ message }: { message: string }) {
  return <div className="analysis-overview-empty"><Empty description={message} /></div>;
}

function layout(overrides: Partial<Layout> = {}): Partial<Layout> {
  return { autosize: true, margin: { l: 62, r: 24, t: 20, b: 52 }, paper_bgcolor: "#FFFFFF", plot_bgcolor: "#FFFFFF", font: { family: "Segoe UI, sans-serif", size: 11, color: "#424242" }, ...overrides };
}

function coverageMetrics(preview: AnalysisPreview, t: (key: string) => string): { k: string; v: unknown }[] {
  if (preview.kind === "overlap") return [{ k: t("Near duplicates"), v: preview.near_duplicates }, { k: t("Highly similar"), v: preview.highly_similar }, { k: t("Independent"), v: preview.independent }, { k: t("Overlap fraction"), v: preview.overlap_fraction }, { k: t("Mean distance"), v: preview.mean_distance }];
  const values = [{ k: t("Covered"), v: preview.covered }, { k: t("Marginal"), v: preview.marginal }, { k: t("Out of coverage"), v: preview.out_of_coverage }, { k: t("Mean distance"), v: preview.mean_distance }];
  if (preview.kind === "drift") values.push({ k: "MMD", v: preview.mmd }, { k: t("Centroid shift"), v: preview.centroid_distance }, { k: t("Covariance shift"), v: preview.covariance_shift });
  return values;
}

function nums(value: unknown): number[] {
  return Array.isArray(value) ? value.map(num).filter((item): item is number => item !== null) : [];
}

function matrix(value: unknown): number[][] {
  return Array.isArray(value) ? value.map(nums).filter((row) => row.length) : [];
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null && !Array.isArray(item)) : [];
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function num(value: unknown): number | null {
  const parsed = typeof value === "number" || typeof value === "string" ? Number(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

function fmt(value: unknown): string {
  const numeric = num(value);
  if (numeric !== null) return Math.abs(numeric) >= 1_000 ? numeric.toLocaleString(undefined, { maximumFractionDigits: 2 }) : numeric.toPrecision(5);
  if (typeof value === "string") return value;
  if (value == null) return "—";
  try { return JSON.stringify(value); } catch { return String(value); }
}

function countBy(values: number[]): [string, number][] {
  const counts = new Map<number, number>();
  values.forEach((value) => counts.set(value, (counts.get(value) ?? 0) + 1));
  return [...counts.entries()].sort(([left], [right]) => left - right).map(([key, value]) => [String(key), value]);
}

function runLabel(row: Record<string, unknown>, index: number, t: (key: string, vars?: Record<string, string | number>) => string): string {
  const parameters = row.parameters;
  if (typeof parameters === "object" && parameters !== null && !Array.isArray(parameters)) {
    const entry = Object.entries(parameters as Record<string, unknown>)[0];
    if (entry) return `${entry[0]}=${String(entry[1])}`;
  }
  return t("Run {index}", { index: index + 1 });
}

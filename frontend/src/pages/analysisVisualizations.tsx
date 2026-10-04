import { memo, useState } from "react";
import type { Data } from "plotly.js";
import { Select, Space, Switch, Table, Tag, Typography } from "antd";
import { useT, type Pair } from "../i18n";
import { formatLabel } from "../util/format";
import type { AnalysisPreview } from "../types/protocol";
import { SAMPLE_COLUMN_LABELS, matrixExtent } from "./analysisPreview";
import type { AnalysisPoint } from "./analysisPreview";
import TrajectoryView from "./trajectoryView";
import { HIGH_CONTRAST_COLORSCALE, Metrics, NoData, PlotFrame, fmt, formatCount, formatFixed, formatPercent, layout, matrix, num, nums, quantile, records, strings } from "./analysisChartKit";

export type AnalysisArrays = Record<string, unknown[]>;

type Props = {
  preview: AnalysisPreview | null;
  arrays: AnalysisArrays;
  /** arrays that arrived with fewer columns than the view asked for */
  narrowed?: string[];
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
  feature_correlation: { en: "FEATURE CORRELATION", zh: "特征相关性" },
  property_correlation: { en: "PROPERTY INFORMATION", zh: "属性信息分析" },
  local_diversity: { en: "LOCAL ENVIRONMENT DIVERSITY", zh: "局部环境多样性" },
  trajectory: { en: "DESCRIPTOR TRAJECTORY", zh: "描述符轨迹" },
  drift: { en: "DATASET DRIFT", zh: "数据集漂移" },
  sensitivity: { en: "PARAMETER SENSITIVITY", zh: "参数敏感性" },
  perturbation_sensitivity: { en: "STRUCTURAL PERTURBATION SENSITIVITY", zh: "结构扰动敏感性" },
  kernel: { en: "KERNEL DIAGNOSTICS", zh: "核函数诊断" },
  spectral_effective_dimension: { en: "SPECTRAL EFFECTIVE DIMENSION", zh: "谱有效维度" },
  two_nn_intrinsic_dimension: { en: "INTRINSIC DIMENSION (TWONN)", zh: "内禀维度 (TWONN)" },
  information_imbalance: { en: "INFORMATION IMBALANCE", zh: "信息不平衡" },
  formal_invariance: { en: "FORMAL INVARIANCE", zh: "形式不变性" },
  cutoff_smoothness: { en: "CUTOFF SMOOTHNESS", zh: "截断平滑性" },
  environment_jacobian: { en: "ENVIRONMENT JACOBIAN RANK", zh: "环境雅可比秩" },
  degeneracy_search: { en: "DEGENERACY SEARCH", zh: "退化搜索" },
  distance_consistency: { en: "DISTANCE CONSISTENCY", zh: "距离一致性" },
};

// Sensitivity metric legend names (generated the same way as before).
const SENSITIVITY_METRIC_LABELS: Record<string, Pair> = {
  pairwise_distance_pearson: { en: "pairwise distance pearson", zh: "两两距离 Pearson" },
  neighbor_overlap: { en: "neighbor overlap", zh: "近邻重叠" },
  clustering_stability: { en: "clustering stability", zh: "聚类稳定性" },
};

const COLORS = ["#0F6CBD", "#F7630C", "#107C10", "#8764B8", "#D13438", "#00B7C3", "#C239B3"];

export default memo(function AnalysisResultVisualization(props: Props) {
  const { preview, loading, narrowed } = props;
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
      {/* An array that arrived narrowed - fewer columns, or fewer rows than the
          artifact holds - looks exactly like a genuinely narrow or short one,
          and the charts built from it would be read as complete. */}
      {/* The algorithm's own report about what it dropped or could not measure.
          It used to be shown for one analysis only, from inside that panel;
          every result carries it now, so it belongs where a user reads any
          caveat about the picture below. */}
      {Array.isArray(preview.warnings) && preview.warnings.length > 0 && (
        <Typography.Text type="warning" style={{ display: "block", marginBottom: 8 }}>
          {preview.warnings.filter((warning): warning is string => typeof warning === "string").join(" · ")}
        </Typography.Text>
      )}
      {narrowed && narrowed.length > 0 && (
        <Typography.Text type="warning" style={{ display: "block", marginBottom: 8 }}>
          {t("Some arrays were narrowed to fit one response: {names}", { names: narrowed.join(", ") })}
        </Typography.Text>
      )}
      {loading ? <NoData message={t("Loading bounded analysis arrays…")} /> : <Visualization {...props} kind={kind} />}
    </section>
  );
});

function Visualization({ kind, preview, arrays, points, selectedIndices, onSelect }: Props & { kind: string }) {
  if (!preview) return null;
  if (kind === "similarity" || kind === "neighbors") return <NeighborView preview={preview} />;
  if (kind === "pairwise_similarity") return <MatrixView preview={preview} matrix={matrix(arrays.similarity_matrix)} label="Similarity" />;
  if (kind === "clusters") return <ClusterView preview={preview} points={points} selectedIndices={selectedIndices} onSelect={onSelect} />;
  if (kind === "outliers") return <OutlierView preview={preview} points={points} selectedIndices={selectedIndices} onSelect={onSelect} />;
  if (kind === "sampling" || kind === "acquisition") return <SamplingView preview={preview} arrays={arrays} points={points} selectedIndices={selectedIndices} onSelect={onSelect} />;
  if (kind === "coverage" || kind === "overlap" || kind === "drift") return <CoverageView preview={preview} arrays={arrays} />;
  if (kind === "compare") return <CompareView preview={preview} arrays={arrays} />;
  if (kind === "mantel") return <MantelView preview={preview} arrays={arrays} />;
  if (kind === "feature_correlation") return <FeatureCorrelationView preview={preview} arrays={arrays} />;
  if (kind === "property_correlation") return <PropertyView preview={preview} arrays={arrays} onSelect={onSelect} />;
  if (kind === "local_diversity") return <LocalView preview={preview} arrays={arrays} points={points} selectedIndices={selectedIndices} onSelect={onSelect} />;
  if (kind === "trajectory") return <TrajectoryView key={String(preview.analysis_id ?? "trajectory")} preview={preview} arrays={arrays} points={points} selectedIndices={selectedIndices} onSelect={onSelect} />;
  if (kind === "sensitivity") return <SensitivityView preview={preview} />;
  if (kind === "perturbation_sensitivity") return <PerturbationView preview={preview} arrays={arrays} />;
  if (kind === "kernel") return <KernelView preview={preview} arrays={arrays} />;
  if (kind === "two_nn_intrinsic_dimension") return <TwoNNView preview={preview} arrays={arrays} />;
  if (kind === "information_imbalance") return <InformationImbalanceView preview={preview} arrays={arrays} />;
  if (kind === "formal_invariance") return <FormalInvarianceView preview={preview} />;
  if (kind === "cutoff_smoothness") return <CutoffSmoothnessView preview={preview} arrays={arrays} />;
  if (kind === "environment_jacobian") return <EnvironmentJacobianView preview={preview} arrays={arrays} />;
  if (kind === "degeneracy_search") return <DegeneracySearchView preview={preview} arrays={arrays} />;
  if (kind === "distance_consistency") return <DistanceConsistencyView preview={preview} arrays={arrays} />;
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
    }]} layout={layout({ xaxis: { title: { text: hasSimilarity ? t("Similarity") : t("Distance") } }, yaxis: { automargin: true } })} />
  </>;
}

function MatrixView({ preview, matrix: values, label }: { preview: AnalysisPreview; matrix: number[][]; label: string }) {
  const { t } = useT();
  const labelT = t(label);
  if (!values.length) return <NoData message={t("{label} matrix is unavailable.", { label: labelT })} />;
  // What the strip states is what the grid holds: the pairwise view's preview
  // numbers are distances, and labelling a similarity grid with them reported a
  // maximum the drawn values never reach.
  const extent = matrixExtent(values);
  return <>
    <Metrics values={[{ k: t("Samples"), v: preview.sample_count }, { k: t("Metric"), v: preview.metric ?? preview.kernel }, { k: t("Minimum"), v: extent.min }, { k: t("Maximum"), v: extent.max }]} />
    <PlotFrame ariaLabel={t("{label} heatmap", { label: labelT })} data={[{ type: "heatmap", z: values, colorscale: "Viridis", colorbar: { title: { text: labelT } }, hovertemplate: `${t("row")}=%{y}<br>${t("column")}=%{x}<br>${t("value")}=%{z:.5g}<extra></extra>` }]} layout={layout({ xaxis: { title: { text: t("Sample") } }, yaxis: { title: { text: t("Sample") }, autorange: "reversed" } })} />
  </>;
}

function ClusterView({ preview, points, selectedIndices, onSelect }: Pick<Props, "preview" | "points" | "selectedIndices" | "onSelect">) {
  const { t } = useT();
  const counts = countBy(points.map((point) => point.label ?? -1));
  return <>
    <Metrics values={[{ k: t("Clusters"), v: preview?.cluster_count }, { k: t("Noise"), v: preview?.noise_count }, { k: t("Samples"), v: points.length }]} />
    <div className="analysis-chart-grid">
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color="label" ariaLabel={t("Descriptor clusters in PCA space")} />
      <PlotFrame compact ariaLabel={t("Samples per cluster")} data={[{ type: "bar", x: counts.map(([key]) => key === "-1" ? t("Noise") : `C${key}`), y: counts.map(([, value]) => value), marker: { color: counts.map((_, index) => COLORS[index % COLORS.length]) } }]} layout={layout({ xaxis: { title: { text: t("Cluster") } }, yaxis: { title: { text: t("Samples") } } })} />
    </div>
  </>;
}

function OutlierView({ preview, points, selectedIndices, onSelect }: Pick<Props, "preview" | "points" | "selectedIndices" | "onSelect">) {
  const { t } = useT();
  return <>
    <Metrics values={[{ k: t("Outliers"), v: preview?.outlier_count }, { k: t("Algorithm"), v: preview?.algorithm }, { k: t("Contamination"), v: preview?.contamination }]} />
    <div className="analysis-chart-grid">
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color="score" ariaLabel={t("Outlier scores in descriptor space")} />
      <PlotFrame compact ariaLabel={t("Outlier score distribution")} data={[{ type: "histogram", x: points.map((point) => point.score ?? 0), marker: { color: "#0F6CBD" } }]} layout={layout({ xaxis: { title: { text: t("Outlier score") } }, yaxis: { title: { text: t("Samples") } } })} />
    </div>
  </>;
}

function SamplingView({ preview, arrays, points, selectedIndices, onSelect }: Pick<Props, "preview" | "arrays" | "points" | "selectedIndices" | "onSelect">) {
  const { t } = useT();
  const kind = String(preview?.kind ?? "sampling");
  const uncertaintyDriven = preview?.algorithm === "uncertainty_diversity";
  const fps = preview?.algorithm === "fps" && kind === "sampling";
  const radiusCurve = fps ? nums(arrays.coverage_radius_curve) : [];
  const meanCurve = fps ? nums(arrays.coverage_mean_curve) : [];
  const r2Curve = fps ? nums(arrays.coverage_r2_curve) : [];
  // Acquisition: the objective value each pick actually saw.  `scores` is a
  // final-state ranking and does not decrease along selected_indices, so it
  // cannot answer "why this one" on its own (deep review P1-17).  Saved
  // results predate the array and keep the distribution plot.
  const pickScores = kind === "acquisition" && !fps ? nums(arrays.pick_scores) : [];
  const stopReason = fps
    ? preview?.stop_reason === "min_distance"
      ? t("Minimum descriptor distance")
      : preview?.stop_reason === "exhausted"
        ? t("All candidates used")
        : preview?.stop_reason === "coverage"
          ? t("Target coverage reached")
          : preview?.stop_reason === "target"
            ? t("Target reached")
            : undefined
    : undefined;
  const coverageStopMessage = fps && typeof preview?.target_coverage === "number"
    ? preview.stop_reason === "coverage"
      ? t("Stopped on target coverage of {percent}.", { percent: formatPercent(preview.target_coverage) })
      : preview.stop_reason === "target"
        ? t("Reached sample budget before target coverage.")
        : preview.stop_reason === "min_distance"
          ? t("Stopped by minimum-distance criterion.")
          : preview.stop_reason === "exhausted"
            ? t("Candidate set exhausted before target coverage.")
            : null
    : null;
  const explained = fps ? nums(preview?.pc_explained_variance) : [];
  // Only the distance-based algorithms publish a scaling, so this is absent for
  // random/stratified rather than claimed.
  const scaling = typeof preview?.scaling === "string" ? preview.scaling : null;
  const allocation = fps && preview?.strategy === "grouped" ? records(preview.allocation) : [];
  const blocks = fps ? records(preview?.blocks) : [];
  const metricValues = fps
    ? [
        { k: t("Selected"), text: preview?.n_candidates != null ? `${formatCount(preview.selected_count)} / ${formatCount(preview.n_candidates)}` : formatCount(preview.selected_count) },
        { k: t("Coverage radius"), v: preview?.coverage_radius },
        { k: t("Coverage R²"), v: preview?.coverage_r2 },
        { k: t("Mean residual"), v: preview?.mean_residual },
        { k: t("P95 residual"), v: preview?.p95_residual },
        { k: t("Stop reason"), text: stopReason },
        { k: t("Feature scaling"), v: scaling },
      ]
    : [
        { k: t("Selected"), v: preview?.selected_count },
        { k: t("Candidates"), v: preview?.candidate_pool ?? points.length },
        { k: t("Method"), v: preview?.algorithm },
        { k: t("Feature scaling"), v: scaling },
        { k: uncertaintyDriven ? t("Mean uncertainty") : t("Mean novelty"), v: uncertaintyDriven ? preview?.mean_selected_uncertainty : preview?.mean_selected_novelty },
      ];
  return <>
    <Metrics values={metricValues} />
    {blocks.length > 0 && <>
      <Typography.Text type="secondary">{t("Composite sampling space (each block scaled, weighted 1/√D)")}</Typography.Text>
      <Table
        size="small"
        style={{ marginTop: 8, maxWidth: 560 }}
        rowKey={(row) => String(row.name)}
        pagination={false}
        dataSource={blocks}
        columns={[
          { title: t("Block"), dataIndex: "name", render: (value) => String(value) },
          { title: t("Dimensions"), dataIndex: "dimension", align: "right" as const, render: (value) => formatCount(value) },
          { title: t("Weight"), dataIndex: "weight", align: "right" as const, render: (value) => formatFixed(value, 3) },
          { title: t("Scaling"), dataIndex: "scaling", render: (value) => String(value) },
        ]}
      />
    </>}
    {allocation.length > 0 && <>
      <Typography.Text type="secondary">{t("Group allocation (√N per element set)")}</Typography.Text>
      <Table
        size="small"
        style={{ marginTop: 8, maxWidth: 480 }}
        rowKey={(row) => String(row.group)}
        pagination={false}
        dataSource={allocation}
        columns={[
          { title: t("Element set"), dataIndex: "group", render: (value) => String(value) },
          { title: t("Structures"), dataIndex: "structures", align: "right" as const, render: (value) => formatCount(value) },
          { title: t("Sampling quota"), dataIndex: "quota", align: "right" as const, render: (value) => formatCount(value) },
        ]}
      />
    </>}
    <div className="analysis-chart-grid">
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color={kind === "acquisition" ? uncertaintyDriven ? "uncertainty" : "distance" : "selected"} ariaLabel={t("Selected representative samples in descriptor space")} />
      {fps && radiusCurve.length
        ? <PlotFrame compact ariaLabel={t("Coverage curve")} data={[
            { type: "scatter", mode: "lines", x: radiusCurve.map((_, index) => index + 1), y: radiusCurve, name: t("Coverage radius"), line: { color: "#0F6CBD", width: 2 } },
            { type: "scatter", mode: "lines", x: meanCurve.map((_, index) => index + 1), y: meanCurve, name: t("Mean residual"), line: { color: "#F7630C", width: 2, dash: "dot" } },
          ]} layout={layout({ xaxis: { title: { text: t("Selected samples") } }, yaxis: { title: { text: t("Descriptor distance") } }, showlegend: true })} />
        : pickScores.length
        ? <PlotFrame compact ariaLabel={t("Score at the moment of each pick")} data={[{ type: "bar", x: pickScores.map((_, index) => index + 1), y: pickScores, marker: { color: uncertaintyDriven ? "#D13438" : "#0F6CBD" } }]} layout={layout({ xaxis: { title: { text: t("Pick order") }, dtick: pickScores.length <= 20 ? 1 : undefined }, yaxis: { title: { text: t("Score when picked") }, rangemode: "tozero" } })} />
        : <PlotFrame compact ariaLabel={t("Selection score distribution")} data={[{ type: "histogram", x: points.map((point) => kind === "acquisition" ? uncertaintyDriven ? point.uncertainty ?? 0 : point.distance ?? 0 : point.x), marker: { color: uncertaintyDriven ? "#D13438" : "#8764B8" } }]} layout={layout({ xaxis: { title: { text: kind === "acquisition" ? uncertaintyDriven ? t("kNN extrapolation uncertainty") : t("Novelty distance") : t("PC1 distribution") } }, yaxis: { title: { text: t("Samples") } } })} />}
    </div>
    {pickScores.length > 0 && <Typography.Text type="secondary">{t("Bars show the objective each pick maximised; the stored scores rank every candidate by the loop's final state.")}</Typography.Text>}
    {fps && r2Curve.length > 0 && <PlotFrame compact ariaLabel={t("Coverage R² curve")} data={[{ type: "scatter", mode: "lines", x: r2Curve.map((_, index) => index + 1), y: r2Curve, line: { color: "#107C10", width: 2 }, hovertemplate: `${t("Selected samples")}=%{x}<br>R²=%{y:.4f}<extra></extra>` }]} layout={layout({ xaxis: { title: { text: t("Selected samples") } }, yaxis: { title: { text: "R²" }, range: [Math.min(0, ...r2Curve), 1] }, shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 0, y1: 0, line: { color: "#616161", dash: "dash" } }] })} />}
    {coverageStopMessage && <Typography.Text type="secondary">{coverageStopMessage}</Typography.Text>}
    {fps && explained.length === 2 && <Typography.Text type="secondary">{t("FPS ran in the full scaled descriptor space; the plot is only a PC1–PC2 projection ({percent} variance).", { percent: formatPercent(explained[0] + explained[1]) })}</Typography.Text>}
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
      <PlotFrame compact ariaLabel={t("Reference and query descriptor-space projection")} data={[0, 1].map((group) => ({ type: "scattergl", mode: "markers", name: group ? t("Query") : t("Reference"), x: coords.flatMap((point, index) => source[index] === group ? [point[0]] : []), y: coords.flatMap((point, index) => source[index] === group ? [point[1]] : []), marker: { size: group ? 7 : 5, color: group ? "#D13438" : "#0F6CBD", opacity: group ? 0.82 : 0.45 }, hovertemplate: `${group ? t("Query") : t("Reference")}<br>PC1=%{x:.4g}<br>PC2=%{y:.4g}<extra></extra>` })) as Data[]} layout={layout({ xaxis: { title: { text: t("Joint PC1") } }, yaxis: { title: { text: t("Joint PC2") } }, legend: { orientation: "h" } })} />
      <PlotFrame compact ariaLabel={t("Nearest-reference distance distribution")} data={distances.length ? [{ type: "histogram", x: distances, marker: { color: "#0F6CBD" } }] : [{ type: "bar", x: categories, y: categoryCounts, marker: { color: ["#107C10", "#F7630C", "#D13438"] } }]} layout={layout({ xaxis: { title: { text: distances.length ? t("Nearest-reference distance") : t("Category") } }, yaxis: { title: { text: t("Samples") } } })} />
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
      <PlotFrame compact ariaLabel={t("Pairwise descriptor distances comparison")} data={[{ type: "scattergl", mode: "markers", x: leftPairs.slice(0, count), y: rightPairs.slice(0, count), marker: { size: 5, color: "#0F6CBD", opacity: 0.45 }, hovertemplate: `${t("left")}=%{x:.5g}<br>${t("right")}=%{y:.5g}<extra></extra>` }]} layout={layout({ xaxis: { title: { text: t("Left pair distance") } }, yaxis: { title: { text: t("Right pair distance") } } })} />
      <PlotFrame compact ariaLabel={t("Descriptor PCA topology comparison")} data={[{ type: "scattergl", mode: "markers", name: t("Left"), x: leftCoords.map((row) => row[0]), y: leftCoords.map((row) => row[1]), marker: { size: 6, color: "#0F6CBD", opacity: 0.55 } }, { type: "scattergl", mode: "markers", name: t("Right"), x: rightCoords.map((row) => row[0]), y: rightCoords.map((row) => row[1]), marker: { size: 6, color: "#D13438", opacity: 0.55 } }]} layout={layout({ xaxis: { title: { text: "PC1" } }, yaxis: { title: { text: "PC2" } }, legend: { orientation: "h" } })} />
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
      <PlotFrame compact ariaLabel={t("Mantel paired descriptor distances")} data={[{ type: "scattergl", mode: "markers", x: leftPairs.slice(0, count), y: rightPairs.slice(0, count), marker: { size: 5, color: "#0F6CBD", opacity: 0.45 }, hovertemplate: `${t("left")}=%{x:.5g}<br>${t("right")}=%{y:.5g}<extra></extra>` }]} layout={layout({ xaxis: { title: { text: t("Left pair distance") } }, yaxis: { title: { text: t("Right pair distance") } } })} />
      <PlotFrame compact ariaLabel={t("Mantel permutation null distribution")} data={[{ type: "histogram", x: nullDistribution, marker: { color: "#8764B8" } }]} layout={layout({ xaxis: { title: { text: `${preview.method ?? "Pearson"} ${t("null statistic")}` } }, yaxis: { title: { text: t("Permutations") } }, shapes: [{ type: "line", x0: statistic, x1: statistic, y0: 0, y1: 1, yref: "paper", line: { color: "#D13438", width: 2, dash: "dash" } }] })} />
    </div>
    <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>{t("Two-sided permutation p-value with +1 correction; the red line marks the observed statistic.")}</Typography.Paragraph>
  </>;
}

function FeatureCorrelationView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const values = matrix(arrays.correlation_matrix);
  const featureIndices = nums(arrays.correlation_feature_indices);
  const threshold = num(preview.correlation_threshold ?? preview.redundancy_threshold) ?? 0.95;
  const clusteredFeatureOrder = nums(preview.clustered_feature_order);
  const hasClusteredOrder = clusteredFeatureOrder.length === featureIndices.length
    && clusteredFeatureOrder.every((feature) => featureIndices.includes(feature));
  const correlationMethod = preview.correlation_metric === "spearman" ? "spearman" : "pearson";
  const [displayMode, setDisplayMode] = useState<"signed" | "absolute">("absolute");
  const [featureOrder, setFeatureOrder] = useState<"original" | "clustered">("original");
  const [highOnly, setHighOnly] = useState(false);
  const order = featureOrder === "clustered" && hasClusteredOrder ? clusteredFeatureOrder : featureIndices;
  const positions = order.map((feature) => featureIndices.indexOf(feature)).filter((position) => position >= 0);
  const orderedValues = positions.map((rowPosition, rowIndex) => positions.map((columnPosition, columnIndex) => {
    const raw = values[rowPosition]?.[columnPosition];
    if (raw == null || rowIndex >= columnIndex || (highOnly && Math.abs(raw) < threshold)) return null;
    return displayMode === "absolute" ? Math.abs(raw) : raw;
  }));
  const orderedSignedValues = positions.map((rowPosition) => positions.map((columnPosition) => values[rowPosition]?.[columnPosition] ?? null));
  const pairRows = records(preview.pairs)
    .map((row, index) => {
      const correlation = num(row.correlation);
      if (correlation === null) return null;
      const absoluteCorrelation = num(row.absolute_correlation) ?? Math.abs(correlation);
      return {
        key: `${row.feature_a ?? "?"}-${row.feature_b ?? "?"}-${index}`,
        feature_a: num(row.feature_a),
        feature_b: num(row.feature_b),
        correlation,
        absolute_correlation: absoluteCorrelation,
        status: absoluteCorrelation >= threshold ? "High" : "Moderate",
      };
    })
    .filter((row): row is CorrelationPairRow => row !== null)
    .sort((left, right) => right.absolute_correlation - left.absolute_correlation);
  const colorScale: Array<[number, string]> = displayMode === "absolute"
    ? [[0, "#F5F7FA"], [0.5, "#8DB8E5"], [1, "#0F6CBD"]]
    : [[0, "#D13438"], [0.5, "#FFFFFF"], [1, "#0F6CBD"]];
  const signedMetricLabel = correlationMethod === "spearman" ? "Spearman ρ" : "Pearson r";
  const absoluteMetricLabel = correlationMethod === "spearman" ? "|ρ|" : "|r|";
  const metricLabel = displayMode === "absolute" ? absoluteMetricLabel : signedMetricLabel;
  const heatmapLimited = preview.heatmap_limited === true;
  return <>
    <div className="analysis-correlation-toolbar">
      <Space wrap size={[8, 8]}>
        <Typography.Text>{t("Correlation view")}</Typography.Text>
        <Select
          className="analysis-correlation-view-select"
          aria-label={t("Correlation view")}
          value={displayMode}
          onChange={setDisplayMode}
          options={[{ value: "absolute", label: t("Absolute correlation") }, { value: "signed", label: t("Signed correlation") }]}
        />
        <Typography.Text>{t("Feature order")}</Typography.Text>
        <Select
          aria-label={t("Feature order")}
          value={featureOrder === "clustered" && !hasClusteredOrder ? "original" : featureOrder}
          onChange={setFeatureOrder}
          options={[{ value: "original", label: t("Original order") }, { value: "clustered", label: t("Clustered order"), disabled: !hasClusteredOrder }]}
        />
        <Switch aria-label={t("Show only high correlations")} checked={highOnly} onChange={setHighOnly} />
        <Typography.Text>{t("Show only high correlations")}</Typography.Text>
      </Space>
    </div>
    <Metrics values={[
      { k: t("Features"), v: formatCount(preview.feature_count) },
      { k: t("Zero variance"), v: formatCount(preview.zero_variance_count) },
      { k: t(heatmapLimited ? "High-correlation pairs (heatmap subset)" : "Highly correlated pairs"), v: formatCount(preview.highly_correlated_pairs) },
      { k: t("High-correlation clusters"), v: formatCount(preview.high_correlation_cluster_count) },
      { k: t("Involved high-correlation features"), v: formatCount(preview.involved_feature_count ?? preview.redundant_feature_count) },
      { k: t("High-correlation feature ratio"), v: formatPercent(preview.involved_feature_ratio ?? preview.redundancy_ratio) },
    ]} />
    {heatmapLimited && <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>{t("The heatmap and pair summary use the {n} highest-variance valid features.", { n: formatCount(preview.heatmap_feature_count) })}</Typography.Paragraph>}
    {values.length ? <PlotFrame ariaLabel={t("Descriptor feature correlation heatmap")} data={[{ type: "heatmap", z: orderedValues, customdata: orderedSignedValues, x: order, y: order, zmin: displayMode === "absolute" ? 0 : -1, zmax: 1, colorscale: colorScale, colorbar: { title: { text: metricLabel } }, hovertemplate: displayMode === "absolute" ? `F%{y} ↔ F%{x}<br>${absoluteMetricLabel}=%{z:.4f}<br>${signedMetricLabel}=%{customdata:.4f}<extra></extra>` : `F%{y} ↔ F%{x}<br>${signedMetricLabel}=%{z:.4f}<extra></extra>` }]} layout={layout({ xaxis: { title: { text: `${t("Feature")} · ${featureOrder === "clustered" && hasClusteredOrder ? t("Clustered order") : t("Original order")}` } }, yaxis: { title: { text: t("Feature") }, autorange: "reversed" } })} /> : <NoData message={t("Correlation matrix is unavailable.")} />}
    {pairRows.length ? <Table<CorrelationPairRow>
      className="analysis-data-table feature-correlation-table"
      size="small"
      tableLayout="fixed"
      pagination={{ pageSize: 6, hideOnSinglePage: true, showSizeChanger: false }}
      rowKey="key"
      dataSource={pairRows}
      columns={[
        { title: t("Feature A"), dataIndex: "feature_a", key: "feature_a", width: 96, render: (value: number | null) => formatIndex(value) },
        { title: t("Feature B"), dataIndex: "feature_b", key: "feature_b", width: 96, render: (value: number | null) => formatIndex(value) },
        { title: signedMetricLabel, dataIndex: "correlation", key: "correlation", width: 128, align: "right", render: formatCorrelation },
        { title: absoluteMetricLabel, dataIndex: "absolute_correlation", key: "absolute_correlation", width: 96, align: "right", render: (value: number) => value.toFixed(4) },
        { title: t("Status"), dataIndex: "status", key: "status", width: 104, render: (value: string) => <Tag color={value === "High" ? "red" : "default"}>{t(value)}</Tag> },
      ]}
    /> : <NoData message={t("No feature correlation pairs were returned.")} />}
  </>;
}

type CorrelationPairRow = {
  key: string;
  feature_a: number | null;
  feature_b: number | null;
  correlation: number;
  absolute_correlation: number;
  status: "High" | "Moderate";
};

function PropertyView({ preview, arrays, onSelect }: { preview: AnalysisPreview; arrays: AnalysisArrays; onSelect: (point: AnalysisPoint) => void }) {
  const { t } = useT();
  const [predictionView, setPredictionView] = useState<"scatter" | "density">("scatter");
  const [residualView, setResidualView] = useState<"all" | "center99">("all");
  const [associationMethod, setAssociationMethod] = useState<"pearson" | "spearman" | "mutual_information">("pearson");
  const [topN, setTopN] = useState<10 | 20 | 50>(20);
  const targets = nums(arrays.targets);
  const predictions = nums(arrays.predictions);
  const residuals = nums(arrays.residuals);
  const absoluteErrors = nums(arrays.absolute_errors);
  const distances = nums(arrays.oof_distances);
  const sampleIndices = nums(arrays.sample_indices);
  const sampleFrames = nums(arrays.sample_frames);
  const sampleRows = nums(arrays.sample_rows);
  const binCenters = nums(arrays.reliability_bin_center);
  const binMedian = nums(arrays.reliability_bin_median);
  const binP90 = nums(arrays.reliability_bin_p90);
  const binP95 = nums(arrays.reliability_bin_p95);
  const identityBounds = [...targets, ...predictions];
  const identityMin = identityBounds.length ? Math.min(...identityBounds) : 0;
  const identityMax = identityBounds.length ? Math.max(...identityBounds) : 1;
  const unit = String(preview.property_unit ?? "");
  const unitSuffix = unit ? ` (${unit})` : "";
  const requestedReliabilityK = num(preview.requested_reliability_k);
  const effectiveReliabilityKMin = num(preview.effective_reliability_k_min);
  const effectiveReliabilityKMax = num(preview.effective_reliability_k_max);
  const effectiveReliabilityK = effectiveReliabilityKMin != null && effectiveReliabilityKMax != null
    ? effectiveReliabilityKMin === effectiveReliabilityKMax
      ? String(Math.round(effectiveReliabilityKMin))
      : `${Math.round(effectiveReliabilityKMin)}–${Math.round(effectiveReliabilityKMax)}`
    : formatCount(preview.reliability_k);
  const residualLimit = quantile(residuals.map(Math.abs), 0.99);
  const visibleResiduals = residualView === "center99" && residualLimit !== null
    ? residuals.filter((value) => Math.abs(value) <= residualLimit)
    : residuals;
  const associationValues = associationMethod === "spearman"
    ? nums(arrays.spearman_correlations)
    : associationMethod === "mutual_information"
      ? nums(arrays.mutual_information)
      : nums(arrays.pearson_correlations);
  const featureIndices = nums(arrays.feature_indices);
  const associationRows = associationValues.length && featureIndices.length === associationValues.length
    ? associationValues.map((value, index) => ({ feature: featureIndices[index], value }))
    : associationMethod === "pearson"
      ? records(preview.top_features).map((row) => ({ feature: num(row.feature) ?? 0, value: num(row.correlation) ?? 0 }))
      : [];
  const rankedAssociations = associationRows
    .sort((left, right) => associationMethod === "mutual_information" ? right.value - left.value : Math.abs(right.value) - Math.abs(left.value))
    .slice(0, topN);
  const predictionData: Data[] = predictionView === "density"
    ? [{ type: "histogram2dcontour", x: targets, y: predictions, colorscale: "Blues", contours: { coloring: "fill" }, colorbar: { title: { text: t("Density") } }, hovertemplate: `${t("Ground truth")}=%{x:.5g}<br>${t("OOF prediction")}=%{y:.5g}<extra></extra>` } as Data]
    : [{ type: "scattergl", mode: "markers", x: targets, y: predictions, marker: { size: 6, color: "#0F6CBD", opacity: 0.62 }, hovertemplate: `${t("Ground truth")}=%{x:.5g}<br>${t("OOF prediction")}=%{y:.5g}<extra></extra>` }];
  const encodingStrength = formatLabel(t(String(preview.encoding_strength ?? "unknown")));
  const informationPattern = String(preview.information_pattern ?? "mixed");
  return <div className="property-analysis">
    <section className="property-analysis-section">
      <PropertySectionHeading number="01" title={t("Property Encoding")} question={t("Does the descriptor encode the property?")} />
      <div className="property-method-strip">
        <Tag color="blue">{String(preview.model ?? "Ridge")}</Tag>
        <Typography.Text>{t("{folds}-fold CV · shuffled · seed {seed}", { folds: formatCount(preview.cv_folds), seed: formatCount(preview.cv_seed) })}</Typography.Text>
        <Typography.Text type="secondary">{t("Baseline: training-fold mean")}</Typography.Text>
      </div>
      <Metrics values={[
        { k: t("Samples"), v: formatCount(preview.sample_count) },
        { k: t("Descriptor dimensions"), v: formatCount(preview.feature_count) },
        { k: t("CV R²"), v: formatFixed(preview.r2, 4) },
        { k: t("CV RMSE"), v: formatWithUnit(preview.rmse, unit) },
        { k: t("CV MAE"), v: formatWithUnit(preview.mae, unit) },
      ]} />
      <div className="property-insight" role="status">
        <Typography.Text strong>{t("{strength} property encoding", { strength: encodingStrength })}</Typography.Text>
        <Typography.Text>{propertyInsight(informationPattern, t)}</Typography.Text>
      </div>
      <div className="property-view-toolbar">
        <Typography.Text>{t("Prediction view")}</Typography.Text>
        <Select aria-label={t("Prediction view")} value={predictionView} onChange={setPredictionView} options={[{ value: "scatter", label: t("Scatter") }, { value: "density", label: t("Density") }]} />
        <Typography.Text>{t("Residual range")}</Typography.Text>
        <Select aria-label={t("Residual range")} value={residualView} onChange={setResidualView} options={[{ value: "all", label: t("All") }, { value: "center99", label: t("Central 99%") }]} />
      </div>
      <div className="analysis-chart-grid">
        <div className="property-chart-panel">
          <Typography.Text strong>{t("OOF Prediction vs. Ground Truth")}</Typography.Text>
          <PlotFrame compact ariaLabel={t("OOF Prediction vs. Ground Truth")} data={predictionData} layout={layout({ xaxis: { title: { text: `${t("Ground truth")}${unitSuffix}` } }, yaxis: { title: { text: `${t("OOF prediction")}${unitSuffix}` } }, shapes: [{ type: "line", x0: identityMin, y0: identityMin, x1: identityMax, y1: identityMax, line: { color: "#616161", dash: "dash" } }], annotations: [{ x: identityMax, y: identityMax, text: "y = x", showarrow: false, xanchor: "right", yanchor: "bottom", font: { color: "#616161" } }] })} />
        </div>
        <div className="property-chart-panel">
          <Typography.Text strong>{t("OOF Residual Distribution")}</Typography.Text>
          <div className="property-inline-stats">
            <span>{t("Mean")} {formatWithUnit(preview.residual_mean, unit)}</span>
            <span>{t("Median")} {formatWithUnit(preview.residual_median, unit)}</span>
            <span>P95(|error|) {formatWithUnit(preview.p95_absolute_error, unit)}</span>
            <span>{t("Std")} {formatWithUnit(preview.residual_std, unit)}</span>
          </div>
          <PlotFrame compact ariaLabel={t("OOF Residual Distribution")} data={[{ type: "histogram", x: visibleResiduals, marker: { color: "#F7630C" }, hovertemplate: `${t("Residual")}=%{x:.5g}<br>${t("Samples")}=%{y}<extra></extra>` }]} layout={layout({ xaxis: { title: { text: `${t("Residual (prediction − truth)")}${unitSuffix}` } }, yaxis: { title: { text: t("Samples") } }, shapes: [{ type: "line", x0: 0, x1: 0, y0: 0, y1: 1, yref: "paper", line: { color: "#616161", dash: "dash" } }] })} />
        </div>
      </div>
      <Typography.Text type="secondary" className="property-baseline-note">{t("Mean baseline: R² {r2}, RMSE {rmse}, MAE {mae}", { r2: formatFixed(preview.baseline_r2, 4), rmse: formatWithUnit(preview.baseline_rmse, unit), mae: formatWithUnit(preview.baseline_mae, unit) })}</Typography.Text>
    </section>

    <section className="property-analysis-section">
      <PropertySectionHeading number="02" title={t("Information Localization")} question={t("Which descriptor dimensions carry the information?")} />
      <div className="property-view-toolbar">
        <Typography.Text>{t("Association method")}</Typography.Text>
        <Select aria-label={t("Association method")} value={associationMethod} onChange={setAssociationMethod} options={[{ value: "pearson", label: "Pearson" }, { value: "spearman", label: "Spearman" }, { value: "mutual_information", label: t("Mutual Information") }]} />
        <Typography.Text>{t("Top N")}</Typography.Text>
        <Select aria-label={t("Top N")} value={topN} onChange={setTopN} options={[10, 20, 50].map((value) => ({ value, label: String(value) }))} />
      </div>
      <Metrics values={[
        { k: "Max |Pearson r|", v: formatFixed(preview.max_abs_pearson, 3) },
        { k: "Max |Spearman ρ|", v: formatFixed(preview.max_abs_spearman, 3) },
        { k: t("Max mutual information"), v: formatFixed(preview.max_mutual_information, 3) },
      ]} />
      {rankedAssociations.length ? <AssociationBars rows={rankedAssociations} method={associationMethod} /> : <NoData message={t("Association data is unavailable for this saved result. Rerun the analysis to compute it.")} />}
      <Typography.Text type="secondary">{t("Bars are ranked by association magnitude. Signed methods encode direction by both bar position and a visible value label.")}</Typography.Text>
    </section>

    <section className="property-analysis-section">
      <PropertySectionHeading number="03" title={t("Representation Reliability")} question={t("Where does the descriptor representation become unreliable?")} />
      <div className="property-method-strip">
        <Tag>{String(preview.distance_metric ?? "euclidean")}</Tag>
        <Typography.Text>{t("Mean kNN distance to the OOF training fold · k={k}", { k: effectiveReliabilityK })}</Typography.Text>
        {requestedReliabilityK != null && effectiveReliabilityKMin != null && <Typography.Text type="secondary">{t("Requested k={k}", { k: Math.round(requestedReliabilityK) })}</Typography.Text>}
        <Typography.Text type="secondary">{preview.distance_standardized ? t("Descriptor standardized within each fold") : t("Descriptor not standardized")}</Typography.Text>
      </div>
      <Metrics values={[
        { k: "Spearman ρ(distance, |error|)", v: formatFixed(preview.distance_error_spearman, 3) },
        { k: "Pearson r(distance, |error|)", v: formatFixed(preview.distance_error_pearson, 3) },
        { k: t("High error + high distance"), v: formatCount(preview.high_error_high_distance_count) },
        { k: t("High error + low distance"), v: formatCount(preview.high_error_low_distance_count) },
      ]} />
      <div className="property-wide-chart">
        <PlotFrame ariaLabel={t("Descriptor-space coverage versus OOF prediction error")} onClick={(index, curve) => {
          if (curve !== 0 || !Number.isFinite(sampleFrames[index])) return;
          const frame = Math.round(sampleFrames[index]);
          const row = sampleRows[index] >= 0 ? Math.round(sampleRows[index]) : undefined;
          onSelect({ i: Math.round(sampleIndices[index] ?? index), frame, row, sample_id: row == null ? `frame:${frame}` : `frame:${frame}:atom:${row}`, x: distances[index] ?? 0, y: absoluteErrors[index] ?? 0 });
        }} data={[
          { type: "scattergl", mode: "markers", name: t("Samples"), x: distances, y: absoluteErrors, marker: { size: 5, color: "#8764B8", opacity: 0.28 }, hovertemplate: `${t("OOF kNN distance")}=%{x:.5g}<br>|error|=%{y:.5g}${unit ? ` ${unit}` : ""}<extra></extra>` },
          { type: "scatter", mode: "lines+markers", name: t("Binned median"), x: binCenters, y: binMedian, line: { color: "#0F6CBD", width: 3 }, marker: { size: 7 } },
          { type: "scatter", mode: "lines", name: "P90 |error|", x: binCenters, y: binP90, line: { color: "#F7630C", width: 2, dash: "dash" } },
          { type: "scatter", mode: "lines", name: "P95 |error|", x: binCenters, y: binP95, line: { color: "#D13438", width: 2, dash: "dot" } },
        ]} layout={layout({ xaxis: { title: { text: t("Mean OOF training-fold kNN distance") } }, yaxis: { title: { text: `|${t("OOF prediction error")}|${unitSuffix}` } }, legend: { orientation: "h", y: 1.12 }, shapes: [
          { type: "line", x0: num(preview.sparse_threshold) ?? 0, x1: num(preview.sparse_threshold) ?? 0, y0: 0, y1: 1, yref: "paper", line: { color: "#F7630C", dash: "dash" } },
          { type: "line", x0: num(preview.ood_threshold) ?? 0, x1: num(preview.ood_threshold) ?? 0, y0: 0, y1: 1, yref: "paper", line: { color: "#D13438", dash: "dot" } },
          { type: "line", x0: 0, x1: 1, xref: "paper", y0: num(preview.high_error_threshold) ?? 0, y1: num(preview.high_error_threshold) ?? 0, line: { color: "#616161", dash: "dash" } },
        ], annotations: [
          { x: num(preview.sparse_threshold) ?? 0, y: 1, yref: "paper", text: t("Sparse P{p}", { p: Math.round((num(preview.sparse_quantile) ?? 0.9) * 100) }), showarrow: false, xanchor: "right", yanchor: "bottom", font: { color: "#C75B00" } },
          { x: num(preview.ood_threshold) ?? 0, y: 1, yref: "paper", text: t("OOD-like P{p}", { p: Math.round((num(preview.ood_quantile) ?? 0.99) * 100) }), showarrow: false, xanchor: "left", yanchor: "bottom", font: { color: "#D13438" } },
        ] })} />
      </div>
      <div className="property-reliability-legend" aria-label={t("Reliability region interpretation")}>
        <span><b>{t("Low distance · low error")}</b>{t("Covered")}</span>
        <span><b>{t("High distance · low error")}</b>{t("Sparse but stable")}</span>
        <span><b>{t("High distance · high error")}</b>{t("Coverage gap")}</span>
        <span><b>{t("Low distance · high error")}</b>{t("Possible representation degeneracy")}</span>
      </div>
    </section>
  </div>;
}

function PropertySectionHeading({ number, title, question }: { number: string; title: string; question: string }) {
  return <div className="property-section-heading"><span>{number}</span><div><Typography.Text strong>{title}</Typography.Text><Typography.Text type="secondary">{question}</Typography.Text></div></div>;
}

function LocalView({ preview, arrays, points, selectedIndices, onSelect }: Pick<Props, "preview" | "arrays" | "points" | "selectedIndices" | "onSelect">) {
  const { t } = useT();
  const rows = records(preview?.element_summary);
  const coordination = nums(arrays.coordination);
  const neighborDistances = nums(arrays.neighbor_distances);
  return <>
    <Metrics values={[{ k: t("Local environments"), v: preview?.sample_count }, { k: t("Elements"), v: rows.length }, { k: t("Outliers"), v: rows.reduce((sum, row) => sum + (num(row.outliers) ?? 0), 0) }, { k: t("Cutoff (Å)"), v: preview?.cutoff }, { k: t("Mean coordination"), v: preview?.mean_coordination }, { k: t("Max coordination"), v: preview?.max_coordination }, { k: t("Feature scale"), v: preview?.preprocess }]} />
    <div className="analysis-chart-grid">
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color="element" ariaLabel={t("Atom-level local environment map")} />
      <PlotFrame compact ariaLabel={t("Local environment categories by element")} data={["distorted", "outliers"].map((key, index) => ({ type: "bar", name: t(key), x: rows.map((row) => `Z=${row.element}`), y: rows.map((row) => num(row[key]) ?? 0), marker: { color: COLORS[index + 1] } })) as Data[]} layout={layout({ barmode: "group", xaxis: { title: { text: t("Element") } }, yaxis: { title: { text: t("Environments") } }, legend: { orientation: "h" } })} />
      <PlotFrame compact ariaLabel={t("Coordination number distribution")} data={[{ type: "histogram", x: coordination, marker: { color: "#107C10" } }]} layout={layout({ xaxis: { title: { text: t("Coordination number") }, dtick: 1 }, yaxis: { title: { text: t("Atoms") } } })} />
    </div>
    {neighborDistances.length > 0 && <PlotFrame compact ariaLabel={t("Local neighbor distance distribution")} data={[{ type: "histogram", x: neighborDistances, marker: { color: "#F7630C" } }]} layout={layout({ xaxis: { title: { text: t("Neighbor distance (Å)") } }, yaxis: { title: { text: t("Neighbor pairs") } } })} />}
    {num(preview?.coordination_capped_atoms) ? <Typography.Text type="secondary">{t("Atoms with more contacts than the neighbour list stores: {count}", { count: String(preview?.coordination_capped_atoms) })}</Typography.Text> : null}
    <DataTable rows={rows} />
  </>;
}

function SensitivityView({ preview }: { preview: AnalysisPreview }) {
  const { t, tr } = useT();
  const rows = records(preview.runs);
  if (!rows.length) return <NoData message={t("No aligned runs were returned.")} />;
  const metrics = ["pairwise_distance_pearson", "neighbor_overlap", "clustering_stability"];
  return <>
    <Metrics values={[{ k: t("Runs"), v: rows.length }, { k: t("Baseline"), v: preview.baseline_run_id }, { k: t("Feature scale"), v: preview.preprocess }]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Parameter sensitivity geometry metrics")} data={metrics.map((key, index) => ({ type: "bar", name: tr(SENSITIVITY_METRIC_LABELS[key] ?? { en: key, zh: key }), x: rows.map((row, runIndex) => runLabel(row, runIndex, t)), y: rows.map((row) => num(row[key]) ?? 0), marker: { color: COLORS[index] } })) as Data[]} layout={layout({ barmode: "group", yaxis: { title: { text: t("Agreement (higher is better)") }, range: [-0.05, 1.05] }, xaxis: { automargin: true }, legend: { orientation: "h" } })} />
      <PlotFrame compact ariaLabel={t("Descriptor compute peak memory")} data={[{ type: "bar", x: rows.map((row, index) => runLabel(row, index, t)), y: rows.map((row) => { const bytes = num(row.memory_peak_bytes); return bytes == null ? null : bytes / 1024 / 1024; }), marker: { color: "#D13438" }, hovertemplate: "%{x}<br>peak RSS=%{y:.2f} MB<extra></extra>" }]} layout={layout({ xaxis: { automargin: true }, yaxis: { title: { text: t("Peak RSS (MB)") } } })} />
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
  // The response summarizes the sampled structures only, so the KPI reads
  // "sampled / available" whenever the run offered more structures.
  const sampled = num(preview.sample_count);
  const available = num(preview.available_structure_count);
  const subsampled = sampled !== null && available !== null && available > sampled;
  return <>
    <Metrics values={[{ k: t("Perturbation"), v: preview.perturbation }, { k: t("Metric"), v: preview.metric }, { k: t("Structures"), text: sampled === null ? "—" : subsampled ? `${formatCount(sampled)} / ${formatCount(available)}` : formatCount(sampled) }, { k: t("Steps"), v: preview.curve_count }, { k: t("Response"), v: preview.response_unit }]} />
    {subsampled && <div className="property-method-strip"><Typography.Text type="warning">{t("Sampled {sampled} of {available} structures, evenly spaced across the run; increase Max structures for wider coverage.", { sampled: formatCount(sampled), available: formatCount(available) })}</Typography.Text></div>}
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Descriptor response versus structural perturbation")} data={[{ type: "scatter", mode: "lines+markers", name: t("Mean"), x: amplitudes, y: mean, line: { color: "#0F6CBD", width: 2 } }, { type: "scatter", mode: "lines", name: t("Median"), x: amplitudes.slice(0, median.length), y: median, line: { color: "#107C10", dash: "dash" } }, { type: "scatter", mode: "lines", name: t("P95"), x: amplitudes.slice(0, p95.length), y: p95, line: { color: "#D13438", dash: "dot" } }, { type: "scatter", mode: "lines", name: t("Max"), x: amplitudes.slice(0, max.length), y: max, line: { color: "#F7630C", dash: "dashdot" } }]} layout={layout({ xaxis: { title: { text: preview.perturbation === "strain" ? t("Isotropic strain") : t("Jitter amplitude (Å)") } }, yaxis: { title: { text: String(preview.response_unit ?? t("Descriptor response")) } }, legend: { orientation: "h" } })} />
      <PlotFrame compact ariaLabel={t("Per-structure perturbation response heatmap")} data={[{ type: "heatmap", z: responseMatrix, x: amplitudes, colorscale: "Viridis", colorbar: { title: { text: t("Response") } }, hovertemplate: `${t("amplitude")}=%{x:.4g}<br>${t("structure")}=%{y}<br>${t("response")}=%{z:.5g}<extra></extra>` }]} layout={layout({ xaxis: { title: { text: preview.perturbation === "strain" ? t("Isotropic strain") : t("Jitter amplitude (Å)") } }, yaxis: { title: { text: t("Structure index") }, autorange: "reversed" } })} />
    </div>
    <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>{t("The curve is generated by recomputing the selected descriptor on the selected structures after a seeded perturbation sweep.")}</Typography.Paragraph>
  </>;
}

function TwoNNView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const lnMu = nums(arrays.ln_mu);
  const bootstrap = nums(arrays.bootstrap_estimates);
  if (!lnMu.length) return <NoData message={t("No neighbor ratios were returned.")} />;
  const ci = records(preview.bootstrap ? [preview.bootstrap] : [])[0] ?? {};
  return <>
    <Metrics values={[
      { k: t("Intrinsic dimension"), v: preview.intrinsic_dimension },
      { k: t("Bootstrap"), text: ci.mean != null ? `${formatFixed(Number(ci.mean), 2)} ± ${formatFixed(Number(ci.sd ?? 0), 2)} (95% CI ${formatFixed(Number((ci.ci95 as number[] | undefined)?.[0] ?? 0), 2)}–${formatFixed(Number((ci.ci95 as number[] | undefined)?.[1] ?? 0), 2)})` : t("disabled") },
      { k: t("Points"), text: `${formatCount(Number(preview.points_used ?? 0))} / ${formatCount(Number(preview.points_total ?? 0))}` },
      { k: t("Duplicates excluded"), v: preview.duplicates_excluded },
      { k: t("Feature scale"), v: preview.preprocess },
    ]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Log neighbor-ratio distribution")} data={[{ type: "histogram", x: lnMu, marker: { color: "#0F6CBD" } }]} layout={layout({ xaxis: { title: { text: t("ln(r2/r1)") } }, yaxis: { title: { text: t("Points") } }, shapes: (() => { const estimate = num(preview.intrinsic_dimension); const meanLn = estimate && estimate > 0 ? 1 / estimate : null; return meanLn == null ? [] : [{ type: "line" as const, x0: meanLn, x1: meanLn, y0: 0, y1: 1, yref: "paper", line: { color: "#D13438", dash: "dash" } }]; })() })} />
      {bootstrap.length > 0 && <PlotFrame compact ariaLabel={t("Bootstrap dimension estimates")} data={[{ type: "histogram", x: bootstrap, marker: { color: "#107C10" } }]} layout={layout({ xaxis: { title: { text: t("Resampled intrinsic dimension") } }, yaxis: { title: { text: t("Draws") } } })} />}
    </div>
    <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>{t("TwoNN estimates the manifold dimension from first/second neighbor distance ratios (Facco et al. 2017); the spectral effective dimension measures PCA variance spread - a large gap between the two numbers means strong redundancy, not a lossless low-dimensional projection.")}</Typography.Paragraph>
  </>;
}

function InformationImbalanceView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const contributionsAb = nums(arrays.contribution_a_to_b);
  const contributionsBa = nums(arrays.contribution_b_to_a);
  const ks = nums(arrays.ks);
  const overlap = nums(arrays.overlap);
  if (!contributionsAb.length || !ks.length) return <NoData message={t("No imbalance contributions were returned.")} />;
  return <>
    <Metrics values={[
      { k: t("Δ(left→right)"), v: preview.delta_a_to_b },
      { k: t("Δ(right→left)"), v: preview.delta_b_to_a },
      { k: t("Samples"), v: preview.n_samples },
      { k: t("Metric"), v: preview.metric },
      { k: t("Feature scale"), v: preview.preprocess },
    ]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Neighborhood overlap versus k")} data={[{ type: "scatter", mode: "lines+markers", name: t("Top-k overlap"), x: ks, y: overlap, line: { color: "#0F6CBD", width: 3 }, marker: { size: 8 } }]} layout={layout({ xaxis: { title: { text: "k" }, type: "log", dtick: 1 }, yaxis: { title: { text: t("Mean top-k overlap") }, range: [0, 1.02] } })} />
      <PlotFrame compact ariaLabel={t("Per-point rank contribution distributions")} data={[
        { type: "histogram", name: t("left → right"), x: contributionsAb, marker: { color: "#0F6CBD", opacity: 0.6 }, nbinsx: 24 },
        { type: "histogram", name: t("right → left"), x: contributionsBa, marker: { color: "#F7630C", opacity: 0.6 }, nbinsx: 24 },
      ]} layout={layout({ barmode: "overlay", xaxis: { title: { text: t("Normalized neighbor rank (0 = reproduced, 0.5 = independent)") } }, yaxis: { title: { text: t("Points") } }, legend: { orientation: "h" } })} />
    </div>
    <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>{t("Δ reads how well one descriptor space reproduces the other's nearest-neighbor choices: 0 = reproduced, ~0.5 = independent. The asymmetry says which side loses information when compressed into the other.")}</Typography.Paragraph>
  </>;
}

function FormalInvarianceView({ preview }: { preview: AnalysisPreview }) {
  const { t } = useT();
  const checks = Object.entries((preview.checks ?? {}) as Record<string, Record<string, unknown>>);
  if (!checks.length) return <NoData message={t("No invariance checks were returned.")} />;
  const checkLabels: Record<string, string> = {
    translation: t("Translation"),
    rotation: t("Rotation"),
    reflection: t("Reflection"),
    permutation: t("Permutation (same species)"),
    precision: t("Float32 precision"),
  };
  const rows = checks.map(([name, summary]) => ({
    check: checkLabels[name] ?? name,
    worst_epsilon: num(summary.worst_epsilon),
    mean_epsilon: num(summary.mean_epsilon),
    failures: num(summary.failures) ?? 0,
    frames_measured: num(summary.frames_measured) ?? 0,
    passed: summary.passed === true ? t("Pass") : t("FAIL"),
  }));
  return <>
    <Metrics values={[
      { k: t("Verdict"), text: preview.passed === true ? t("All checks passed") : t("Failures detected") },
      { k: t("Granularity"), v: preview.granularity },
      { k: t("Tolerance"), v: preview.tolerance },
      { k: t("Rotations"), v: preview.n_rotations },
      { k: t("Structures"), v: preview.frame_count },
    ]} />
    {preview.chirality_sensitive === true && <div className="property-method-strip"><Typography.Text type="warning">{t("Reflection deviates while translation/rotation/permutation pass: the descriptor appears chirality-sensitive.")}</Typography.Text></div>}
    <DataTable rows={rows} />
    <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>{t("Each check rebuilds the structure (shift, rotation, mirror, same-species relabeling, float32 round-trip) and recomputes the descriptor; epsilon is the relative descriptor deviation from the original.")}</Typography.Paragraph>
  </>;
}

function CutoffSmoothnessView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const deltas = nums(arrays.deltas);
  const curves = matrix(arrays.response_curves);
  if (!deltas.length || !curves.length) return <NoData message={t("No cutoff response curves were returned.")} />;
  const frameRows = records(preview.frames);
  const flagged = frameRows.filter((row) => row.flagged === true);
  const zero = deltas.findIndex((value) => Math.abs(value) < 1e-12);
  const shown = frameRows
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => row.flagged === true)
    .slice(0, 8)
    .map(({ index }) => index);
  const data: Data[] = shown.map((index) => ({
    type: "scatter", mode: "lines",
    name: frameRows[index]?.sample_id ? String(frameRows[index].sample_id) : t("Structure {index}", { index: String(index) }),
    x: deltas, y: curves[index] ?? [],
    line: { color: "#D13438", width: 2 },
  }));
  data.unshift({
    type: "scatter", mode: "lines", name: t("All structures (mean)"),
    x: deltas,
    y: deltas.map((_, step) => curves.reduce((sum, curve) => sum + (curve[step] ?? 0), 0) / curves.length),
    line: { color: "#0F6CBD", width: 2 },
  });
  return <>
    <Metrics values={[
      { k: t("Structures"), v: preview.frame_count },
      { k: t("Grid steps"), v: preview.n_steps },
      { k: t("Max delta (Å)"), v: preview.max_delta },
      { k: t("Worst jump ratio"), v: preview.worst_jump_ratio },
      { k: t("Flagged"), v: preview.n_flagged },
    ]} />
    {Number(preview.n_flagged) > 0 && <div className="property-method-strip"><Typography.Text type="warning">{t("{count} structure(s) show a discontinuity at the cutoff: pairs sitting exactly on rcut appear or vanish for an infinitesimal parameter change.", { count: String(preview.n_flagged) })}</Typography.Text></div>}
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Descriptor response versus cutoff offset")} data={data} layout={layout({ xaxis: { title: { text: t("Cutoff offset from rcut (Å)") } }, yaxis: { title: { text: t("Relative descriptor response") }, type: "log" }, legend: { orientation: "h" }, ...(zero >= 0 ? { shapes: [{ type: "line", x0: deltas[zero], x1: deltas[zero], y0: 0, y1: 1, yref: "paper", line: { color: "#616161", dash: "dash", width: 1 } }] } : {}) })} />
    </div>
    {flagged.length > 0 && <DataTable rows={flagged.map((row) => ({ frame: row.frame_index, sample_id: row.sample_id, jump_ratio: row.jump_ratio, worst_relative_response: row.worst_relative_response, max_dd_dr: row.max_dd_dr }))} />}
    <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>{t("The descriptor is rebuilt at rcut + offset over a symmetric grid; the jump ratio contrasts the two grid steps adjacent to rcut against the interior steps.")}</Typography.Paragraph>
  </>;
}

function EnvironmentJacobianView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const expected = nums(arrays.expected_rank);
  const observed = nums(arrays.observed_rank);
  const deficiency = nums(arrays.rank_deficiency);
  if (!expected.length) return <NoData message={t("No per-atom Jacobian ranks were returned.")} />;
  const shown = Math.min(expected.length, 60);
  const atomLabels = expected.slice(0, shown).map((_, index) => t("Atom {index}", { index: String(index + 1) }));
  const worstAtoms = records(preview.worst_atoms);
  return <>
    <Metrics values={[
      { k: t("Atoms analyzed"), v: preview.atoms_analyzed },
      { k: t("Rank-deficient"), v: preview.n_deficient },
      { k: t("Isolated atoms"), v: preview.isolated_atoms },
      { k: t("Neighbor cutoff (Å)"), v: preview.cutoff },
      { k: t("Max rotational residual"), v: preview.max_rotational_residual },
      { k: t("Descriptor evaluations"), v: preview.total_descriptor_evaluations },
    ]} />
    {Number(preview.n_deficient) > 0 && <div className="property-method-strip"><Typography.Text type="warning">{t("{count} environment(s) cannot see some displacement direction inside the neighbor sphere: descriptor distance ~ 0 no longer guarantees physical similarity.", { count: String(preview.n_deficient) })}</Typography.Text></div>}
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Per-atom expected versus observed Jacobian rank")} data={[
        { type: "bar", name: t("Expected rank"), x: atomLabels, y: expected.slice(0, shown), marker: { color: "#0F6CBD" } },
        { type: "bar", name: t("Observed rank"), x: atomLabels, y: observed.slice(0, shown), marker: { color: "#D13438" } },
      ]} layout={layout({ barmode: "group", xaxis: { title: { text: t("Atom (first {count}, by index)", { count: String(shown) }) } }, yaxis: { title: { text: t("Rank") }, dtick: 1 }, legend: { orientation: "h" } })} />
      <PlotFrame compact ariaLabel={t("Rank deficiency histogram")} data={[{ type: "histogram", x: deficiency, marker: { color: "#F7630C" } }]} layout={layout({ xaxis: { title: { text: t("Rank deficiency (expected − observed)") }, dtick: 1 }, yaxis: { title: { text: t("Atoms") } } })} />
    </div>
    {worstAtoms.length > 0 && <DataTable rows={worstAtoms.slice(0, 24).map((row) => ({
      structure: row.structure_index, atom: row.atom_index, species: row.species, neighbors: row.neighbor_count,
      expected_rank: row.expected_rank, observed_rank: row.observed_rank, rank_deficiency: row.rank_deficiency,
      condition_number: row.condition_number, rotational_residual: row.rotational_residual, verdict: row.verdict,
    }))} />}
    <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>{t("The per-environment Jacobian is differentiated by finite differences with the center atom fixed; the three rotational zero modes are projected out before the rank readout. Expected rank is 3 × neighbors − 3.")}</Typography.Paragraph>
  </>;
}

function DegeneracySearchView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const descriptorDistances = nums(arrays.descriptor_distance);
  const structuralDistances = nums(arrays.structural_distance);
  const energyDeltas = nums(arrays.delta_energy);
  if (!descriptorDistances.length) return <NoData message={t("No candidate pairs were returned.")} />;
  const finitePairs = descriptorDistances
    .map((distance, index) => ({ distance, structural: structuralDistances[index], energy: energyDeltas[index] }))
    .filter((pair) => Number.isFinite(pair.structural));
  const energyColored = preview.energy_used === true && finitePairs.every((pair) => Number.isFinite(pair.energy));
  const infiniteCount = descriptorDistances.length - finitePairs.length;
  const thresholds = records(preview.thresholds ? [preview.thresholds] : [])[0] ?? {};
  const pairs = records(preview.pairs);
  const reasonLabels: Record<string, string> = {
    structure: t("Structure differs"),
    atom_count: t("Different atom count"),
    energy: t("Energy differs"),
    "structure+energy": t("Structure + energy differ"),
  };
  return <>
    <Metrics values={[
      { k: t("Samples"), v: preview.sample_count },
      { k: t("Candidate pairs"), v: preview.candidate_pairs },
      { k: t("Dangerous pairs"), v: preview.n_dangerous },
      { k: t("d_D threshold"), v: thresholds.descriptor_distance },
      { k: t("d_struct threshold"), v: thresholds.structural_distance },
      { k: t("Energy signal"), text: preview.energy_used === true ? t("used") : t("not available") },
    ]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Descriptor distance versus structural distance of candidate pairs")} data={[
        { type: "scattergl", mode: "markers", name: t("Candidate pairs"), x: finitePairs.map((pair) => pair.distance), y: finitePairs.map((pair) => pair.structural), marker: energyColored ? { size: 5, color: finitePairs.map((pair) => pair.energy ?? 0), colorscale: "Viridis", opacity: 0.5, colorbar: { title: { text: t("|ΔE|") } } } : { size: 4, color: "#0F6CBD", opacity: 0.35 }, hovertemplate: `${t("d_D")}=%{x:.4g}<br>${t("d_struct")}=%{y:.4g}${energyColored ? `<br>${t("|ΔE|")}=%{marker.color:.4g}` : ""}<extra></extra>` },
      ]} layout={layout({ xaxis: { title: { text: t("Descriptor distance (raw)") } }, yaxis: { title: { text: t("Structural fingerprint distance") } } })} />
    </div>
    {infiniteCount > 0 && <Typography.Text type="secondary">{t("{count} candidate pair(s) span different atom counts and are structurally incomparable by construction.", { count: String(infiniteCount) })}</Typography.Text>}
    {pairs.length > 0 && <DataTable rows={pairs.map((row) => ({
      sample_a: row.sample_a, sample_b: row.sample_b,
      descriptor_distance: row.descriptor_distance, structural_distance: row.structural_distance,
      delta_energy: row.delta_energy, reason: reasonLabels[String(row.reason)] ?? String(row.reason ?? ""),
    }))} />}
    <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>{t("Candidate pairs are k-nearest neighbors in raw descriptor space; a pair is dangerous when it sits in the closest quantile of d_D while its structural fingerprint or energy difference is in the far quantile.")}</Typography.Paragraph>
  </>;
}

function DistanceConsistencyView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const dDescriptor = nums(arrays.d_descriptor);
  const dStructural = nums(arrays.d_structural);
  const energyDeltas = nums(arrays.delta_energy);
  if (!dDescriptor.length) return <NoData message={t("No distance pairs were returned.")} />;
  const finitePairs = dDescriptor
    .map((distance, index) => ({ distance, structural: dStructural[index], energy: energyDeltas[index] }))
    .filter((pair) => Number.isFinite(pair.structural));
  const energyColored = Boolean(preview.energy) && energyDeltas.length === dDescriptor.length && finitePairs.every((pair) => Number.isFinite(pair.energy));
  const binned = records(preview.binned).filter((row) => row.structural_mean != null);
  const collapse = records(preview.collapse ? [preview.collapse] : [])[0] ?? {};
  const dangerousPairs = records(preview.dangerous_pairs);
  const energy = records(preview.energy ? [preview.energy] : [])[0] ?? null;
  return <>
    <Metrics values={[
      { k: t("Samples"), v: preview.sample_count },
      { k: t("Comparable pairs"), v: preview.comparable_pairs },
      { k: t("Pearson"), v: preview.pearson_descriptor_structural },
      { k: t("Spearman"), v: preview.spearman_descriptor_structural },
      { k: t("Collapse fraction"), v: collapse.fraction },
      { k: t("Collapse enrichment"), v: collapse.enrichment },
    ]} />
    {Number(collapse.count) > 0 && <div className="property-method-strip"><Typography.Text type="warning">{t("{count} pair(s) sit in the dangerous corner: descriptor-close but structurally far (enrichment {enrichment}× over independence).", { count: String(collapse.count ?? 0), enrichment: formatFixed(Number(collapse.enrichment ?? 0), 1) })}</Typography.Text></div>}
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Descriptor distance versus structural distance")} data={[
        { type: "scattergl", mode: "markers", name: t("All pairs"), x: finitePairs.map((pair) => pair.distance), y: finitePairs.map((pair) => pair.structural), marker: energyColored ? { size: 5, color: finitePairs.map((pair) => pair.energy ?? 0), colorscale: "Viridis", opacity: 0.45, colorbar: { title: { text: t("|ΔE|") } } } : { size: 4, color: "#8764B8", opacity: 0.3 }, hovertemplate: `${t("d_D")}=%{x:.4g}<br>${t("d_struct")}=%{y:.4g}${energyColored ? `<br>${t("|ΔE|")}=%{marker.color:.4g}` : ""}<extra></extra>` },
        { type: "scatter", mode: "lines+markers", name: t("Binned mean structural distance"), x: binned.map((row) => num(row.d_descriptor_max) ?? 0), y: binned.map((row) => num(row.structural_mean) ?? 0), line: { color: "#0F6CBD", width: 3 }, marker: { size: 7 } },
        { type: "scatter", mode: "lines", name: "P90", x: binned.map((row) => num(row.d_descriptor_max) ?? 0), y: binned.map((row) => num(row.structural_p90) ?? 0), line: { color: "#F7630C", width: 2, dash: "dash" } },
      ]} layout={layout({ xaxis: { title: { text: t("Descriptor distance (raw)") } }, yaxis: { title: { text: t("Structural fingerprint distance") } }, legend: { orientation: "h" } })} />
    </div>
    {energy && num(energy.pearson) != null && <Typography.Text type="secondary">{t("Descriptor distance vs |ΔE|: Pearson {pearson}, Spearman {spearman} ({pairs} pairs).", { pearson: formatFixed(Number(energy.pearson), 3), spearman: formatFixed(Number(energy.spearman ?? 0), 3), pairs: String(energy.pairs ?? 0) })}</Typography.Text>}
    {dangerousPairs.length > 0 && <DataTable rows={dangerousPairs.map((row) => ({
      sample_a: row.sample_a, sample_b: row.sample_b,
      descriptor_distance: row.descriptor_distance, structural_distance: row.structural_distance, delta_energy: row.delta_energy,
    }))} />}
    <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>{t("Both distances enter on their own metric: raw descriptor space against the structural fingerprint. The binned curve shows how structural distance grows with descriptor distance - a flat left edge is the collapse signature.")}</Typography.Paragraph>
  </>;
}

function KernelView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const { t } = useT();
  const eigenvalues = nums(arrays.eigenvalues);
  const kernelMatrix = matrix(arrays.kernel_matrix);
  return <>
    <Metrics values={[{ k: t("Kernel"), v: preview.kernel }, { k: t("Samples"), v: preview.sample_count }, { k: t("Effective rank"), v: preview.effective_rank }, { k: t("Top eigenvalue fraction"), v: preview.top_eigenvalue_fraction }]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel={t("Kernel heatmap")} data={[{ type: "heatmap", z: kernelMatrix, colorscale: "Viridis", colorbar: { title: { text: t("Kernel") } }, hovertemplate: `${t("row")}=%{y}<br>${t("column")}=%{x}<br>${t("value")}=%{z:.5g}<extra></extra>` }]} layout={layout({ xaxis: { title: { text: t("Sample") } }, yaxis: { title: { text: t("Sample") }, autorange: "reversed" } })} />
      <PlotFrame compact ariaLabel={t("Kernel eigenspectrum")} data={[{ type: "bar", x: eigenvalues.slice(0, 100).map((_, index) => index + 1), y: eigenvalues.slice(0, 100), marker: { color: "#8764B8" } }]} layout={layout({ xaxis: { title: { text: t("Component") } }, yaxis: { title: { text: t("Centered eigenvalue") } } })} />
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
};

function PointPlot({ points, selectedIndices, onSelect, color, ariaLabel }: { points: AnalysisPoint[]; selectedIndices: number[]; onSelect: (point: AnalysisPoint) => void; color: "label" | "score" | "selected" | "distance" | "uncertainty" | "element"; ariaLabel: string }) {
  const { t, tr } = useT();
  if (!points.length) return <NoData message={t("No projected samples are available.")} />;
  const selected = new Set(selectedIndices);
  const colorValues = points.map((point) => color === "label" ? point.label ?? -1 : color === "score" ? point.score ?? 0 : color === "distance" ? point.distance ?? 0 : color === "uncertainty" ? point.uncertainty ?? 0 : color === "element" ? point.element ?? 0 : selected.has(point.i) ? 1 : 0);
  return <PlotFrame compact ariaLabel={ariaLabel} onClick={(index) => points[index] && onSelect(points[index])} data={[{ type: "scattergl", mode: "markers", x: points.map((point) => point.x), y: points.map((point) => point.y), text: points.map((point) => `${point.sample_id ?? t("sample {index}", { index: point.i })}${point.row == null ? "" : t(" · atom {row}", { row: point.row })}`), marker: { size: color === "selected" ? points.map((point) => selected.has(point.i) ? 10 : 5) : 7, color: colorValues, colorscale: color === "selected" ? [[0, "#C8CDD4"], [1, "#D13438"]] : HIGH_CONTRAST_COLORSCALE, showscale: color !== "selected", colorbar: { title: { text: tr(POINT_COLOR_LABELS[color]) } }, opacity: 0.8 }, hovertemplate: "%{text}<br>x=%{x:.5g}<br>y=%{y:.5g}<extra></extra>" }]} layout={layout({ xaxis: { title: { text: "PC1" } }, yaxis: { title: { text: "PC2" } }, showlegend: false })} />;
}

function AssociationBars({ rows, method }: { rows: { feature: number; value: number }[]; method: "pearson" | "spearman" | "mutual_information" }) {
  const { t } = useT();
  const shown = rows.slice().reverse();
  const signed = method !== "mutual_information";
  const axisTitle = method === "spearman" ? "Spearman ρ" : method === "mutual_information" ? t("Mutual Information") : "Pearson r";
  const maxValue = Math.max(...shown.map((row) => Math.abs(row.value)), 0.01);
  const axisRange: [number, number] = signed ? [-1, 1] : [0, maxValue * 1.18];
  return <PlotFrame ariaLabel={t("Feature–Property Association")} data={[{
    type: "bar",
    orientation: "h",
    x: shown.map((row) => row.value),
    y: shown.map((row) => `F${Math.round(row.feature)}`),
    text: shown.map((row) => `${row.value >= 0 && signed ? "+" : ""}${row.value.toFixed(3)}`),
    textposition: "outside",
    cliponaxis: false,
    marker: { color: shown.map((row) => !signed ? "#8764B8" : row.value >= 0 ? "#0F6CBD" : "#D13438") },
    hovertemplate: `%{y}<br>${axisTitle}=%{x:.5g}<extra></extra>`,
  }]} layout={layout({ margin: { l: 62, r: 64, t: 20, b: 52 }, xaxis: { title: { text: axisTitle }, range: axisRange, zeroline: true, zerolinecolor: "#616161" }, yaxis: { automargin: true } })} />;
}

function DataTable({ rows }: { rows: Record<string, unknown>[] }) {
  const { t } = useT();
  if (!rows.length) return null;
  const keys = Object.keys(rows[0]).filter((key) => key !== "parameters" && key !== "warnings").slice(0, 8);
  return <Table className="analysis-data-table" size="small" pagination={{ pageSize: 8, hideOnSinglePage: true }} rowKey={(row, index) => `${row.sample_id ?? row.run_id ?? index}`} dataSource={rows} columns={keys.map((key) => ({ title: t(SAMPLE_COLUMN_LABELS[key] ?? key.replaceAll("_", " ")), dataIndex: key, key, render: (value: unknown) => fmt(value) }))} />;
}

function coverageMetrics(preview: AnalysisPreview, t: (key: string) => string): { k: string; v: unknown }[] {
  // Which scale the distances were measured on: the three cross-dataset
  // methods share one default and record it (deep review P1-14).
  const scale = typeof preview.preprocess === "string" ? preview.preprocess : undefined;
  if (preview.kind === "overlap") return [{ k: t("Near duplicates"), v: preview.near_duplicates }, { k: t("Highly similar"), v: preview.highly_similar }, { k: t("Independent"), v: preview.independent }, { k: t("Overlap fraction"), v: preview.overlap_fraction }, { k: t("Mean distance"), v: preview.mean_distance }, { k: t("Feature scale"), v: scale }];
  const values = [{ k: t("Covered"), v: preview.covered }, { k: t("Marginal"), v: preview.marginal }, { k: t("Out of coverage"), v: preview.out_of_coverage }, { k: t("Mean distance"), v: preview.mean_distance }];
  if (preview.kind === "drift") values.push({ k: "MMD", v: preview.mmd }, { k: t("Centroid shift"), v: preview.centroid_distance }, { k: t("Covariance shift"), v: preview.covariance_shift }, { k: t("MMD reference rows"), v: preview.mmd_reference_rows }, { k: t("MMD query rows"), v: preview.mmd_query_rows });
  values.push({ k: t("Feature scale"), v: scale });
  return values;
}

function formatIndex(value: unknown): string {
  const numeric = num(value);
  return numeric === null ? "?" : String(Math.round(numeric));
}

function formatCorrelation(value: unknown): string {
  const numeric = num(value);
  return numeric === null ? "—" : `${numeric >= 0 ? "+" : ""}${numeric.toFixed(4)}`;
}

function formatWithUnit(value: unknown, unit: string): string {
  const numeric = num(value);
  return numeric === null ? "—" : `${numeric.toPrecision(4)}${unit ? ` ${unit}` : ""}`;
}

function propertyInsight(pattern: string, t: (key: string) => string): string {
  if (pattern === "distributed") return t("Strong multivariate predictability with moderate single-feature association indicates distributed encoding across descriptor dimensions.");
  if (pattern === "dominant_features") return t("Strong multivariate predictability and strong single-feature association indicate a small set of dominant dimensions.");
  if (pattern === "insufficient") return t("Both multivariate predictability and single-feature association are weak; the descriptor carries limited information about this property.");
  return t("The descriptor contains mixed property information; inspect feature associations and reliability regions together.");
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

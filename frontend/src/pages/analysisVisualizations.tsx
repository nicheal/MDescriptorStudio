import Plot from "react-plotly.js";
import type { Data, Layout } from "plotly.js";
import { Empty, Table, Typography } from "antd";
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

const TITLES: Record<string, string> = {
  similarity: "NEAREST-NEIGHBOR SIMILARITY",
  neighbors: "NEAREST-NEIGHBOR GRAPH",
  pairwise_similarity: "PAIRWISE SIMILARITY MATRIX",
  clusters: "CLUSTER STRUCTURE",
  outliers: "OUTLIER LANDSCAPE",
  sampling: "REPRESENTATIVE SAMPLING",
  acquisition: "NOVELTY–DIVERSITY ACQUISITION",
  coverage: "DATASET COVERAGE",
  overlap: "TRAIN / TEST OVERLAP",
  compare: "DESCRIPTOR COMPARISON",
  feature_correlation: "FEATURE REDUNDANCY",
  property_correlation: "PROPERTY CORRELATION",
  local_diversity: "LOCAL ENVIRONMENT DIVERSITY",
  trajectory: "DESCRIPTOR TRAJECTORY",
  drift: "DATASET DRIFT",
  sensitivity: "PARAMETER SENSITIVITY",
  kernel: "KERNEL DIAGNOSTICS",
};

const COLORS = ["#0F6CBD", "#F7630C", "#107C10", "#8764B8", "#D13438", "#00B7C3", "#C239B3"];

export default function AnalysisResultVisualization(props: Props) {
  const { preview, loading } = props;
  if (!preview) return null;
  const kind = String(preview.kind ?? "");
  if (!TITLES[kind]) return null;
  return (
    <section className="analysis-card analysis-visual-card">
      <div className="analysis-section-heading">
        <Typography.Text strong>{TITLES[kind]}</Typography.Text>
        <Typography.Text type="secondary">Purpose-built visual summary</Typography.Text>
      </div>
      {loading ? <NoData message="Loading bounded analysis arrays…" /> : <Visualization {...props} kind={kind} />}
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
  if (kind === "feature_correlation") return <FeatureCorrelationView preview={preview} arrays={arrays} />;
  if (kind === "property_correlation") return <PropertyView preview={preview} arrays={arrays} />;
  if (kind === "local_diversity") return <LocalView preview={preview} points={points} selectedIndices={selectedIndices} onSelect={onSelect} />;
  if (kind === "trajectory") return <TrajectoryView preview={preview} arrays={arrays} points={points} selectedIndices={selectedIndices} onSelect={onSelect} />;
  if (kind === "sensitivity") return <SensitivityView preview={preview} />;
  if (kind === "kernel") return <KernelView preview={preview} arrays={arrays} />;
  return null;
}

function NeighborView({ preview }: { preview: AnalysisPreview }) {
  const rows = records(preview.rows).slice(0, 60);
  if (!rows.length) return <NoData message="No neighbors were returned." />;
  const reversed = rows.slice().reverse();
  const hasSimilarity = reversed.some((row) => num(row.similarity) !== null);
  return <>
    <Metrics values={[{ k: "Neighbors", v: rows.length }, { k: "Metric", v: preview.metric }, { k: "Query", v: preview.query_index }]} />
    <PlotFrame ariaLabel="Ranked nearest descriptor samples" data={[{
      type: "bar", orientation: "h",
      x: reversed.map((row) => num(hasSimilarity ? row.similarity : row.distance) ?? 0),
      y: reversed.map((row) => String(row.sample_id ?? `sample ${row.i ?? "?"}`)),
      marker: { color: hasSimilarity ? "#107C10" : "#0F6CBD" },
      hovertemplate: `%{y}<br>${hasSimilarity ? "similarity" : "distance"}=%{x:.5g}<extra></extra>`,
    }]} layout={layout({ xaxis: { title: hasSimilarity ? "Similarity" : "Distance" }, yaxis: { automargin: true } })} />
    <DataTable rows={rows} />
  </>;
}

function MatrixView({ preview, matrix: values, label }: { preview: AnalysisPreview; matrix: number[][]; label: string }) {
  if (!values.length) return <NoData message={`${label} matrix is unavailable.`} />;
  return <>
    <Metrics values={[{ k: "Samples", v: preview.sample_count }, { k: "Metric", v: preview.metric ?? preview.kernel }, { k: "Minimum", v: preview.kernel_min ?? preview.distance_min }, { k: "Maximum", v: preview.kernel_max ?? preview.distance_max }]} />
    <PlotFrame ariaLabel={`${label} heatmap`} data={[{ type: "heatmap", z: values, colorscale: "Viridis", colorbar: { title: { text: label } }, hovertemplate: "row=%{y}<br>column=%{x}<br>value=%{z:.5g}<extra></extra>" }]} layout={layout({ xaxis: { title: "Sample" }, yaxis: { title: "Sample", autorange: "reversed" } })} />
  </>;
}

function ClusterView({ preview, points, selectedIndices, onSelect }: Pick<Props, "preview" | "points" | "selectedIndices" | "onSelect">) {
  const counts = countBy(points.map((point) => point.label ?? -1));
  return <>
    <Metrics values={[{ k: "Clusters", v: preview?.cluster_count }, { k: "Noise", v: preview?.noise_count }, { k: "Samples", v: points.length }]} />
    <div className="analysis-chart-grid">
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color="label" ariaLabel="Descriptor clusters in PCA space" />
      <PlotFrame compact ariaLabel="Samples per cluster" data={[{ type: "bar", x: counts.map(([key]) => key === "-1" ? "Noise" : `C${key}`), y: counts.map(([, value]) => value), marker: { color: counts.map((_, index) => COLORS[index % COLORS.length]) } }]} layout={layout({ xaxis: { title: "Cluster" }, yaxis: { title: "Samples" } })} />
    </div>
  </>;
}

function OutlierView({ preview, points, selectedIndices, onSelect }: Pick<Props, "preview" | "points" | "selectedIndices" | "onSelect">) {
  return <>
    <Metrics values={[{ k: "Outliers", v: preview?.outlier_count }, { k: "Algorithm", v: preview?.algorithm }, { k: "Contamination", v: preview?.contamination }]} />
    <div className="analysis-chart-grid">
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color="score" ariaLabel="Outlier scores in descriptor space" />
      <PlotFrame compact ariaLabel="Outlier score distribution" data={[{ type: "histogram", x: points.map((point) => point.score ?? 0), marker: { color: "#0F6CBD" } }]} layout={layout({ xaxis: { title: "Outlier score" }, yaxis: { title: "Samples" } })} />
    </div>
  </>;
}

function SamplingView({ preview, points, selectedIndices, onSelect }: Pick<Props, "preview" | "points" | "selectedIndices" | "onSelect">) {
  const kind = String(preview?.kind ?? "sampling");
  return <>
    <Metrics values={[{ k: "Selected", v: preview?.selected_count }, { k: "Candidates", v: preview?.candidate_pool ?? points.length }, { k: "Method", v: preview?.algorithm }, { k: "Mean novelty", v: preview?.mean_selected_novelty }]} />
    <div className="analysis-chart-grid">
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color={kind === "acquisition" ? "distance" : "selected"} ariaLabel="Selected representative samples in descriptor space" />
      <PlotFrame compact ariaLabel="Selection score distribution" data={[{ type: "histogram", x: points.map((point) => kind === "acquisition" ? point.distance ?? 0 : point.x), marker: { color: "#8764B8" } }]} layout={layout({ xaxis: { title: kind === "acquisition" ? "Novelty distance" : "PC1 distribution" }, yaxis: { title: "Samples" } })} />
    </div>
  </>;
}

function CoverageView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const coords = matrix(arrays.projection_coords);
  const source = nums(arrays.projection_source);
  const rows = records(preview.rows);
  const distances = rows.map((row) => num(row.distances ?? row.distance)).filter((value): value is number => value !== null);
  const categories = strings(preview.categories);
  const labels = nums(arrays.labels);
  const categoryCounts = categories.map((_, category) => labels.filter((label) => label === category).length);
  return <>
    <Metrics values={coverageMetrics(preview)} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel="Reference and query descriptor-space projection" data={[0, 1].map((group) => ({ type: "scattergl", mode: "markers", name: group ? "Query" : "Reference", x: coords.flatMap((point, index) => source[index] === group ? [point[0]] : []), y: coords.flatMap((point, index) => source[index] === group ? [point[1]] : []), marker: { size: group ? 7 : 5, color: group ? "#D13438" : "#0F6CBD", opacity: group ? 0.82 : 0.45 }, hovertemplate: `${group ? "Query" : "Reference"}<br>PC1=%{x:.4g}<br>PC2=%{y:.4g}<extra></extra>` })) as Data[]} layout={layout({ xaxis: { title: "Joint PC1" }, yaxis: { title: "Joint PC2" }, legend: { orientation: "h" } })} />
      <PlotFrame compact ariaLabel="Nearest-reference distance distribution" data={distances.length ? [{ type: "histogram", x: distances, marker: { color: "#0F6CBD" } }] : [{ type: "bar", x: categories, y: categoryCounts, marker: { color: ["#107C10", "#F7630C", "#D13438"] } }]} layout={layout({ xaxis: { title: distances.length ? "Nearest-reference distance" : "Category" }, yaxis: { title: "Samples" } })} />
    </div>
    <DataTable rows={rows} />
  </>;
}

function CompareView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const leftPairs = nums(arrays.left_pair_distances);
  const rightPairs = nums(arrays.right_pair_distances);
  const leftCoords = matrix(arrays.left_coords);
  const rightCoords = matrix(arrays.right_coords);
  const count = Math.min(leftPairs.length, rightPairs.length);
  return <>
    <Metrics values={[{ k: "Pair-distance Pearson", v: preview.pairwise_distance_pearson }, { k: "Spearman", v: preview.pairwise_distance_spearman }, { k: "kNN overlap", v: preview.neighbor_overlap }, { k: "Cluster stability", v: preview.clustering_stability }, { k: "PCA topology error", v: preview.pca_topology_error }, { k: "Effective dimension", v: `${fmt(preview.left_effective_dimension)} / ${fmt(preview.right_effective_dimension)}` }]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel="Pairwise descriptor distances comparison" data={[{ type: "scattergl", mode: "markers", x: leftPairs.slice(0, count), y: rightPairs.slice(0, count), marker: { size: 5, color: "#0F6CBD", opacity: 0.45 }, hovertemplate: "left=%{x:.5g}<br>right=%{y:.5g}<extra></extra>" }]} layout={layout({ xaxis: { title: "Left pair distance" }, yaxis: { title: "Right pair distance" } })} />
      <PlotFrame compact ariaLabel="Descriptor PCA topology comparison" data={[{ type: "scattergl", mode: "markers", name: "Left", x: leftCoords.map((row) => row[0]), y: leftCoords.map((row) => row[1]), marker: { size: 6, color: "#0F6CBD", opacity: 0.55 } }, { type: "scattergl", mode: "markers", name: "Right", x: rightCoords.map((row) => row[0]), y: rightCoords.map((row) => row[1]), marker: { size: 6, color: "#D13438", opacity: 0.55 } }]} layout={layout({ xaxis: { title: "PC1" }, yaxis: { title: "PC2" }, legend: { orientation: "h" } })} />
    </div>
  </>;
}

function FeatureCorrelationView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const values = matrix(arrays.correlation_matrix);
  const featureIndices = nums(arrays.correlation_feature_indices);
  return <>
    <Metrics values={[{ k: "Features", v: preview.feature_count }, { k: "Zero variance", v: preview.zero_variance_count }, { k: "Highly correlated pairs", v: preview.highly_correlated_pairs }, { k: "Redundant features", v: preview.redundant_feature_count }, { k: "Redundancy ratio", v: preview.redundancy_ratio }]} />
    {values.length ? <PlotFrame ariaLabel="Descriptor feature correlation heatmap" data={[{ type: "heatmap", z: values, x: featureIndices, y: featureIndices, zmin: -1, zmax: 1, colorscale: [[0, "#D13438"], [0.5, "#FFFFFF"], [1, "#0F6CBD"]], colorbar: { title: { text: "Pearson r" } }, hovertemplate: "F%{y} ↔ F%{x}<br>r=%{z:.4f}<extra></extra>" }]} layout={layout({ xaxis: { title: "Feature" }, yaxis: { title: "Feature", autorange: "reversed" } })} /> : <NoData message="Correlation matrix is unavailable." />}
    <DataTable rows={records(preview.pairs)} />
  </>;
}

function PropertyView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const targets = nums(arrays.targets);
  const predictions = nums(arrays.predictions);
  const residuals = nums(arrays.residuals);
  const pairDistance = nums(arrays.pair_distance);
  const pairDelta = nums(arrays.pair_property_delta);
  const identityBounds = [...targets, ...predictions];
  const identityMin = identityBounds.length ? Math.min(...identityBounds) : 0;
  const identityMax = identityBounds.length ? Math.max(...identityBounds) : 1;
  return <>
    <Metrics values={[{ k: "Property", v: preview.property }, { k: "Samples", v: preview.sample_count }, { k: "CV R²", v: preview.r2 }, { k: "CV RMSE", v: preview.rmse }, { k: "CV MAE", v: preview.mae }, { k: "Distance–property r", v: preview.distance_property_correlation }]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel="Cross-validated property prediction" data={[{ type: "scattergl", mode: "markers", x: targets, y: predictions, marker: { size: 6, color: "#0F6CBD", opacity: 0.65 }, hovertemplate: "target=%{x:.5g}<br>prediction=%{y:.5g}<extra></extra>" }]} layout={layout({ xaxis: { title: "Target" }, yaxis: { title: "Cross-validated prediction" }, shapes: [{ type: "line", x0: identityMin, y0: identityMin, x1: identityMax, y1: identityMax, line: { color: "#616161", dash: "dash" } }] })} />
      <PlotFrame compact ariaLabel="Property residual distribution" data={[{ type: "histogram", x: residuals, marker: { color: "#F7630C" } }]} layout={layout({ xaxis: { title: "Residual" }, yaxis: { title: "Samples" } })} />
      <PlotFrame compact ariaLabel="Descriptor distance versus property difference" data={[{ type: "scattergl", mode: "markers", x: pairDistance, y: pairDelta, marker: { size: 4, color: "#8764B8", opacity: 0.35 } }]} layout={layout({ xaxis: { title: "Descriptor pair distance" }, yaxis: { title: "Absolute property difference" } })} />
      <TopFeatureBars rows={records(preview.top_features)} />
    </div>
  </>;
}

function LocalView({ preview, points, selectedIndices, onSelect }: Pick<Props, "preview" | "points" | "selectedIndices" | "onSelect">) {
  const rows = records(preview?.element_summary);
  return <>
    <Metrics values={[{ k: "Local environments", v: preview?.sample_count }, { k: "Elements", v: rows.length }, { k: "Outliers", v: rows.reduce((sum, row) => sum + (num(row.outliers) ?? 0), 0) }]} />
    <div className="analysis-chart-grid">
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color="element" ariaLabel="Atom-level local environment map" />
      <PlotFrame compact ariaLabel="Local environment categories by element" data={["distorted", "outliers"].map((key, index) => ({ type: "bar", name: key, x: rows.map((row) => `Z=${row.element}`), y: rows.map((row) => num(row[key]) ?? 0), marker: { color: COLORS[index + 1] } })) as Data[]} layout={layout({ barmode: "group", xaxis: { title: "Element" }, yaxis: { title: "Environments" }, legend: { orientation: "h" } })} />
    </div>
    <DataTable rows={rows} />
  </>;
}

function TrajectoryView({ preview, arrays, points, selectedIndices, onSelect }: Pick<Props, "preview" | "arrays" | "points" | "selectedIndices" | "onSelect">) {
  const time = nums(arrays.time);
  const stepDistance = nums(arrays.step_distance);
  const eventIndices = nums(arrays.event_indices).map(Math.round).filter((index) => index >= 0 && index < time.length);
  return <>
    <Metrics values={[{ k: "Frames", v: time.length }, { k: "Path length", v: preview?.total_distance }, { k: "Max displacement", v: preview?.max_reference_distance }, { k: "Detected events", v: preview?.event_count }]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel="Descriptor trajectory distances" data={[{ type: "scatter", mode: "lines", name: "Step", x: time, y: stepDistance, line: { color: "#0F6CBD" } }, { type: "scatter", mode: "lines", name: "From start", x: time, y: nums(arrays.reference_distance), line: { color: "#D13438" } }, { type: "scatter", mode: "lines", name: "Cumulative", x: time, y: nums(arrays.cumulative_distance), line: { color: "#F7630C" } }, { type: "scatter", mode: "markers", name: "Events", x: eventIndices.map((index) => time[index]), y: eventIndices.map((index) => stepDistance[index]), marker: { color: "#D13438", size: 9, symbol: "diamond" } }]} layout={layout({ xaxis: { title: String(preview?.time_unit ?? "Frame") }, yaxis: { title: "Descriptor distance" }, legend: { orientation: "h" } })} />
      <PointPlot points={points} selectedIndices={selectedIndices} onSelect={onSelect} color="path" ariaLabel="Descriptor trajectory path in PCA space" lines />
    </div>
  </>;
}

function SensitivityView({ preview }: { preview: AnalysisPreview }) {
  const rows = records(preview.runs);
  if (!rows.length) return <NoData message="No aligned runs were returned." />;
  const metrics = ["pairwise_distance_pearson", "neighbor_overlap", "clustering_stability"];
  return <>
    <Metrics values={[{ k: "Runs", v: rows.length }, { k: "Baseline", v: preview.baseline_run_id }]} />
    <PlotFrame ariaLabel="Parameter sensitivity geometry metrics" data={metrics.map((key, index) => ({ type: "bar", name: key.replaceAll("_", " "), x: rows.map((row, runIndex) => runLabel(row, runIndex)), y: rows.map((row) => num(row[key]) ?? 0), marker: { color: COLORS[index] } })) as Data[]} layout={layout({ barmode: "group", yaxis: { title: "Agreement (higher is better)", range: [-0.05, 1.05] }, xaxis: { automargin: true }, legend: { orientation: "h" } })} />
    <DataTable rows={rows} />
  </>;
}

function KernelView({ preview, arrays }: { preview: AnalysisPreview; arrays: AnalysisArrays }) {
  const eigenvalues = nums(arrays.eigenvalues);
  const kernelMatrix = matrix(arrays.kernel_matrix);
  return <>
    <Metrics values={[{ k: "Kernel", v: preview.kernel }, { k: "Samples", v: preview.sample_count }, { k: "Effective rank", v: preview.effective_rank }, { k: "Top eigenvalue fraction", v: preview.top_eigenvalue_fraction }]} />
    <div className="analysis-chart-grid">
      <PlotFrame compact ariaLabel="Kernel heatmap" data={[{ type: "heatmap", z: kernelMatrix, colorscale: "Viridis", colorbar: { title: { text: "Kernel" } }, hovertemplate: "row=%{y}<br>column=%{x}<br>value=%{z:.5g}<extra></extra>" }]} layout={layout({ xaxis: { title: "Sample" }, yaxis: { title: "Sample", autorange: "reversed" } })} />
      <PlotFrame compact ariaLabel="Kernel eigenspectrum" data={[{ type: "bar", x: eigenvalues.slice(0, 100).map((_, index) => index + 1), y: eigenvalues.slice(0, 100), marker: { color: "#8764B8" } }]} layout={layout({ xaxis: { title: "Component" }, yaxis: { title: "Centered eigenvalue" } })} />
    </div>
  </>;
}

function PointPlot({ points, selectedIndices, onSelect, color, ariaLabel, lines = false }: { points: AnalysisPoint[]; selectedIndices: number[]; onSelect: (point: AnalysisPoint) => void; color: "label" | "score" | "selected" | "distance" | "element" | "path"; ariaLabel: string; lines?: boolean }) {
  if (!points.length) return <NoData message="No projected samples are available." />;
  const selected = new Set(selectedIndices);
  const colorValues = points.map((point) => color === "label" ? point.label ?? -1 : color === "score" ? point.score ?? 0 : color === "distance" ? point.distance ?? 0 : color === "element" ? point.element ?? 0 : color === "selected" ? selected.has(point.i) ? 1 : 0 : point.i);
  return <PlotFrame compact ariaLabel={ariaLabel} onClick={(index) => points[index] && onSelect(points[index])} data={[{ type: "scattergl", mode: lines ? "lines+markers" : "markers", x: points.map((point) => point.x), y: points.map((point) => point.y), text: points.map((point) => `${point.sample_id ?? `sample ${point.i}`}${point.row == null ? "" : ` · atom ${point.row}`}`), marker: { size: color === "selected" ? points.map((point) => selected.has(point.i) ? 10 : 5) : 7, color: colorValues, colorscale: color === "selected" ? [[0, "#C8CDD4"], [1, "#D13438"]] : "Viridis", showscale: color !== "selected" && color !== "path", colorbar: { title: { text: color } }, opacity: 0.8 }, line: lines ? { color: "#0F6CBD", width: 1.5 } : undefined, hovertemplate: "%{text}<br>x=%{x:.5g}<br>y=%{y:.5g}<extra></extra>" }]} layout={layout({ xaxis: { title: "PC1" }, yaxis: { title: "PC2" }, showlegend: false })} />;
}

function TopFeatureBars({ rows }: { rows: Record<string, unknown>[] }) {
  const shown = rows.slice(0, 20).reverse();
  return <PlotFrame compact ariaLabel="Most property-correlated descriptor features" data={[{ type: "bar", orientation: "h", x: shown.map((row) => num(row.correlation) ?? 0), y: shown.map((row) => `F${row.feature}`), marker: { color: shown.map((row) => (num(row.correlation) ?? 0) >= 0 ? "#0F6CBD" : "#D13438") } }]} layout={layout({ xaxis: { title: "Pearson correlation", range: [-1, 1] }, yaxis: { automargin: true } })} />;
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

function coverageMetrics(preview: AnalysisPreview): { k: string; v: unknown }[] {
  if (preview.kind === "overlap") return [{ k: "Near duplicates", v: preview.near_duplicates }, { k: "Highly similar", v: preview.highly_similar }, { k: "Independent", v: preview.independent }, { k: "Overlap fraction", v: preview.overlap_fraction }, { k: "Mean distance", v: preview.mean_distance }];
  const values = [{ k: "Covered", v: preview.covered }, { k: "Marginal", v: preview.marginal }, { k: "Out of coverage", v: preview.out_of_coverage }, { k: "Mean distance", v: preview.mean_distance }];
  if (preview.kind === "drift") values.push({ k: "MMD", v: preview.mmd }, { k: "Centroid shift", v: preview.centroid_distance }, { k: "Covariance shift", v: preview.covariance_shift });
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

function runLabel(row: Record<string, unknown>, index: number): string {
  const parameters = row.parameters;
  if (typeof parameters === "object" && parameters !== null && !Array.isArray(parameters)) {
    const entry = Object.entries(parameters as Record<string, unknown>)[0];
    if (entry) return `${entry[0]}=${String(entry[1])}`;
  }
  return `Run ${index + 1}`;
}

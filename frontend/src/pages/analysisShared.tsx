/*
 * Leaf components and value types shared by the Analysis page and its panels.
 * Split out of Analysis so the page component keeps run selection, the async
 * run layer and module orchestration — and nothing else.
 */
import type { ReactNode } from "react";
import { Button, Collapse, Empty, Input, InputNumber, Modal, Select, Space, Spin, Table, Tag, Typography } from "antd";
import { ArrowDownload16Regular, ArrowSync16Regular } from "@fluentui/react-icons";
import { useT } from "../i18n";
import { OVERVIEW_KIND_LABELS, SAMPLING_LABELS, type AnalysisParams, type ProjectionName } from "../features/analysis";
import type { PcaMode } from "../stores/workspace";
import type {
  AnalysisPreview,
  DatasetMeta,
  DatasetView,
  PcaPayload,
  RunRow,
} from "../types/protocol";
import { previewTableColumns, previewTableRows, type AnalysisPoint } from "./analysisPreview";
import type { AnalysisArrays } from "./analysisVisualizations";
import type { AnalysisMethodGuide } from "./analysisMethodGuides";
import { FeatureVarianceChart } from "./featureVariance";
import { EffectiveDimensionChart } from "./analysisEffectiveDimension";

export type CompareMode = "geometry" | "mantel";

export type Point = AnalysisPoint;
export type NumericArrays = AnalysisArrays;

// Module-level so computed charts survive leaving the Analysis page. After an
// app restart the backend re-serves generic artifacts through analysis.preview;
// result.get_pca is only used while opening legacy PCA rows.
export type ProjectionOverrides = {
  mode?: PcaMode;
  preprocess?: string;
};

export type CacheOption = { value: string; label: ReactNode };

export function AnalysisRunLabel({ name, shape }: { name: string; shape: string }) {
  return (
    <span className="analysis-run-label">
      <span className="analysis-run-name">{name}</span>
      <span className="analysis-run-shape">{shape}</span>
    </span>
  );
}

export function CrossDatasetPicker({
  datasets,
  referenceDatasetId,
  queryDatasetId,
  referenceRunId,
  queryRunId,
  referenceViewId,
  queryViewId,
  referenceRuns,
  queryRuns,
  referenceViews,
  queryViews,
  compatible,
  disabled,
  onReferenceDataset,
  onQueryDataset,
  onReferenceRun,
  onQueryRun,
  onReferenceView,
  onQueryView,
  onSwap,
}: {
  datasets: DatasetMeta[];
  referenceDatasetId: string | null;
  queryDatasetId: string | null;
  referenceRunId: string | null;
  queryRunId: string | null;
  referenceViewId: string | null;
  queryViewId: string | null;
  referenceRuns: RunRow[];
  queryRuns: RunRow[];
  referenceViews: DatasetView[];
  queryViews: DatasetView[];
  compatible: boolean;
  disabled: boolean;
  onReferenceDataset: (value: string) => void;
  onQueryDataset: (value: string) => void;
  onReferenceRun: (value: string) => void;
  onQueryRun: (value: string) => void;
  onReferenceView: (value: string | null) => void;
  onQueryView: (value: string | null) => void;
  onSwap: () => void;
}) {
  const { t } = useT();
  const datasetOptions = datasets.map((item) => ({ value: item.id, label: item.name }));
  const scopeOptions = (views: DatasetView[]) => [
    { value: "__full__", label: t("Full dataset") },
    ...views.map((view) => ({ value: view.id, label: `${view.name} · ${view.number_of_frames.toLocaleString()}` })),
  ];
  const runOptions = (rows: RunRow[]) => rows.map((run) => ({
    value: run.id,
    label: <AnalysisRunLabel name={run.descriptor_name} shape={run.shape ?? t("unknown shape")} />,
  }));
  const row = (
    label: string,
    datasetId: string | null,
    runId: string | null,
    viewId: string | null,
    runs: RunRow[],
    views: DatasetView[],
    onDataset: (value: string) => void,
    onRun: (value: string) => void,
    onView: (value: string | null) => void,
  ) => <div className="analysis-cross-input-row">
    <Typography.Text strong className="analysis-cross-input-label">{label}</Typography.Text>
    <Select aria-label={`${label} ${t("Dataset")}`} value={datasetId ?? undefined} disabled={disabled} options={datasetOptions} onChange={onDataset} />
    <Select aria-label={`${label} ${t("Scope")}`} value={viewId ?? "__full__"} disabled={disabled || !datasetId} options={scopeOptions(views)} onChange={(value) => onView(value === "__full__" ? null : value)} />
    <Select aria-label={`${label} ${t("Descriptor run")}`} value={runId ?? undefined} disabled={disabled || !datasetId} placeholder={runs.length ? t("Select completed run") : t("No compatible run")} options={runOptions(runs)} onChange={onRun} />
  </div>;
  return <div className="analysis-cross-inputs">
    {row(t("Reference"), referenceDatasetId, referenceRunId, referenceViewId, referenceRuns, referenceViews, onReferenceDataset, onReferenceRun, onReferenceView)}
    <Button className="analysis-cross-swap" size="small" icon={<ArrowSync16Regular />} disabled={disabled || !referenceDatasetId || !queryDatasetId} onClick={onSwap}>{t("Swap")}</Button>
    {row(t("Query"), queryDatasetId, queryRunId, queryViewId, queryRuns, queryViews, onQueryDataset, onQueryRun, onQueryView)}
    <div className="analysis-cross-status">
      <Tag color={compatible ? "green" : "orange"}>{compatible ? t("Compatible feature space") : t("Select a compatible feature space")}</Tag>
    </div>
  </div>;
}

// Green dot marking a parameter value whose analysis result is already
// computed (and can be re-displayed without rerunning).
function CacheDot() {
  return <span aria-hidden title="cached result available" style={{ display: "inline-block", width: 6, height: 6, borderRadius: "50%", background: "#107C10", marginInlineEnd: 6, flex: "none" }} />;
}

// Parameter label with the cache dot when its current value is computed.
export function ParamLabel({ label, cached }: { label: string; cached: boolean }) {
  return <Typography.Text>{cached ? <CacheDot /> : null}{label}</Typography.Text>;
}

// Prepend the cache dot to every select option that has a computed result.
export function withCacheMarks(isCached: (value: string) => boolean, options: CacheOption[]): CacheOption[] {
  return options.map((option) => (isCached(option.value) ? { ...option, label: <><CacheDot />{option.label}</> } : option));
}

export function pcaPayloadPoints(payload: PcaPayload): Point[] {
  return payload.points.map((point) => ({
    i: point.i,
    frame: point.frame,
    row: point.atom,
    x: point.pc1,
    y: point.pc2,
    energy_per_atom: point.energy_per_atom,
    force_max: point.force_max,
    volume: point.volume,
  }));
}

export function selectedIndicesFromPreview(result: AnalysisPreview): number[] {
  return (Array.isArray(result.selected) ? result.selected : [])
    .map((row) => Number(row.i ?? row.sample_index))
    .filter((index) => Number.isInteger(index) && index >= 0);
}

export function SectionHeading({ title, meta }: { title: string; meta?: string }) {
  return <div className="analysis-section-heading"><Typography.Text strong>{title}</Typography.Text>{meta && <Typography.Text type="secondary">{meta}</Typography.Text>}</div>;
}

export function Row({ k, v }: { k: string; v: string }) {
  return <div className="analysis-inspector-row"><span>{k}</span><Typography.Text code>{v}</Typography.Text></div>;
}

export function OverviewResultVisualization({ preview, arrays, loading, analysisId }: { preview: AnalysisPreview | null; arrays: NumericArrays; loading: boolean; analysisId: string | null }) {
  const { t, tr } = useT();
  if (!preview) return null;
  const kind = String(preview.kind ?? "");
  const kindLabel = OVERVIEW_KIND_LABELS[kind];
  const title = kindLabel ? tr(kindLabel) : t("ANALYSIS RESULT");
  if (loading) {
    return <section className="analysis-card analysis-visual-card"><SectionHeading title={title} meta={t("Loading chart data")} /><div className="analysis-overview-empty"><Empty description={t("Loading the result arrays…")} /></div></section>;
  }

  let content: ReactNode;
  if (kind === "feature_variance") content = <FeatureVarianceChart preview={preview} analysisId={analysisId} />;
  else if (kind === "effective_dimension") content = <EffectiveDimensionChart preview={preview} arrays={arrays} />;
  else return null;

  return <section className="analysis-card analysis-visual-card"><SectionHeading title={title} meta={t("Visual summary")} />{content}</section>;
}

export function ResultPanel({ preview, points, onSelect }: { preview: AnalysisPreview | null; points: Point[]; onSelect?: (row: Record<string, unknown>) => void }) {
  const { t } = useT();
  if (preview?.kind === "feature_variance" || preview?.kind === "feature_correlation" || preview?.kind === "effective_dimension" || preview?.kind === "property_correlation") return null;
  if (!preview && !points.length) return <section className="analysis-card"><Empty description={t("Run an analysis module to see its bounded result preview.")} /></section>;
  const rows = previewTableRows(preview);
  if (rows.length) return <section className="analysis-card"><SectionHeading title={String(preview?.kind ?? "RESULT").toUpperCase()} meta={t("{n} rows", { n: rows.length.toLocaleString() })} /><Table size="small" pagination={{ pageSize: 12 }} rowKey={(row, index) => `${String(row.i ?? row.sample_id ?? row.run_id ?? index)}:${String(row.source_i ?? row.rank ?? index)}`} dataSource={rows} onRow={(row) => ({ onClick: () => onSelect?.(row) })} columns={previewTableColumns(rows[0]).map((key) => ({ title: key, dataIndex: key, key, render: (value: unknown) => typeof value === "number" ? value.toPrecision(6) : String(value ?? "—") }))} /></section>;
  return <section className="analysis-card"><SectionHeading title={String(preview?.kind ?? "RESULT").toUpperCase()} /><Collapse ghost size="small" items={[{ key: "raw", label: t("Raw result output"), children: <pre className="analysis-json-preview">{JSON.stringify(preview, null, 2)}</pre> }]} /></section>;
}

export function AnalysisMethodGuideModal({ guide, open, onClose }: { guide: AnalysisMethodGuide; open: boolean; onClose: () => void }) {
  const { t, tr } = useT();
  return (
    <Modal
      className="analysis-method-guide-modal"
      title={`${t("Method guide")} · ${tr(guide.title)}`}
      open={open}
      onCancel={onClose}
      footer={null}
      width={720}
      destroyOnHidden
    >
      <div className="analysis-method-guide-content">
        <section className="analysis-method-guide-section">
          <Typography.Title level={5}>{t("Theory")}</Typography.Title>
          <Typography.Paragraph>{tr(guide.theory)}</Typography.Paragraph>
        </section>
        <section className="analysis-method-guide-section">
          <Typography.Title level={5}>{t("Applications")}</Typography.Title>
          <Typography.Paragraph>{tr(guide.application)}</Typography.Paragraph>
        </section>
      </div>
    </Modal>
  );
}

export function ProjectionControls({ projection, setProjection, mode, setMode, preprocess, onPreprocessChange, tsnePerplexity, setTsnePerplexity, markOptions, cachedParam }: { projection: ProjectionName; setProjection: (value: ProjectionName) => void; mode: PcaMode; setMode: (value: PcaMode) => void; preprocess: string; onPreprocessChange: (value: string) => void; tsnePerplexity: number; setTsnePerplexity: (value: number) => void; markOptions: (param: string, options: CacheOption[]) => CacheOption[]; cachedParam: (param: keyof AnalysisParams) => boolean }) {
  const { t } = useT();
  return <Space wrap><Typography.Text>{t("Method")}</Typography.Text><Select value={projection} onChange={setProjection} options={markOptions("projection", [{ value: "pca", label: "PCA" }, { value: "umap", label: "UMAP" }, { value: "tsne", label: "t-SNE" }])} /><Typography.Text>{t("Granularity")}</Typography.Text><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom / local") }])} /><Typography.Text>{t("Preprocess")}</Typography.Text><Select value={preprocess} onChange={onPreprocessChange} options={markOptions("preprocess", [{ value: "raw", label: t("Raw scale") }, { value: "center", label: t("Centered") }, { value: "standardized", label: t("Standardized") }])} />{projection === "tsne" && <><ParamLabel label={t("Perplexity")} cached={cachedParam("tsnePerplexity")} /><InputNumber min={2} step={1} value={tsnePerplexity} onChange={(value) => setTsnePerplexity(value ?? 30)} /></>}</Space>;
}

export interface SamplingQuota {
  groups: { group: string; structures: number; quota: number }[];
  n_candidates: number;
}

/** Grouped-FPS element budget, shown only when it can apply. */
export function SamplingQuotaPreview({ quota, busy }: { quota: SamplingQuota | null; busy: boolean }) {
  const { t } = useT();
  return (
    <div style={{ marginTop: 12 }} aria-busy={busy}>
      <Typography.Text type="secondary">{t("Sampling quota preview (√N per element set)")}</Typography.Text>
      {busy ? <Spin size="small" style={{ marginLeft: 8 }} /> : quota && quota.groups.length ? (
        <Table
          size="small"
          style={{ marginTop: 8, maxWidth: 480 }}
          rowKey="group"
          pagination={false}
          dataSource={quota.groups}
          columns={[
            { title: t("Element set"), dataIndex: "group" },
            { title: t("Structures"), dataIndex: "structures", align: "right" as const, render: (value: number) => value.toLocaleString() },
            { title: t("Sampling quota"), dataIndex: "quota", align: "right" as const },
          ]}
        />
      ) : !busy ? <Typography.Text type="secondary" style={{ marginLeft: 8 }}>{t("Element metadata is unavailable for this run")}</Typography.Text> : null}
    </div>
  );
}

export function SamplingExportCard({ format, onFormat, destination, onChoose, onExport }: {
  format: string;
  onFormat: (value: string) => void;
  destination: string;
  onChoose: () => void;
  onExport: () => void;
}) {
  const { t } = useT();
  return (
    <section className="analysis-card">
      <SectionHeading title={t("EXPORT SELECTED SET")} meta={t("Source data is never modified")} />
      <Space.Compact style={{ width: "100%" }}>
        <Select value={format} onChange={onFormat} options={["json", "csv", "extxyz", "deepmd", "indices", "report"].map((value) => ({ value, label: value.toUpperCase() }))} style={{ width: 120 }} />
        <Input readOnly placeholder={t("Choose an export destination")} value={destination} aria-label={t("Export destination")} />
        <Button onClick={onChoose}>{t("Choose…")}</Button>
        <Button icon={<ArrowDownload16Regular />} onClick={onExport}>{t("Export")}</Button>
      </Space.Compact>
    </section>
  );
}

/** Sampling method row: algorithm, budget and the FPS knobs that apply to it.
 *
 * Takes the memoised parameter object plus one setter table instead of 25
 * positional props, so the JSX below stays identical to what the page used to
 * render inline.
 */
export function SamplingControls({ params, setters, warmStartRuns, quota, quotaBusy, markOptions, cachedParam }: {
  params: AnalysisParams;
  setters: {
    samplingAlgorithm: (value: string) => void;
    nSamples: (value: number) => void;
    mode: (value: PcaMode) => void;
    uncertaintyK: (value: number) => void;
    samplingStrategy: (value: string) => void;
    samplingScaling: (value: string) => void;
    samplingBlocks: (value: string[]) => void;
    samplingBudgetMode: (value: string) => void;
    samplingCoverage: (value: number) => void;
    samplingMinDistance: (value: number) => void;
    samplingExistingRunId: (value: string | null) => void;
  };
  warmStartRuns: RunRow[];
  quota: SamplingQuota | null;
  quotaBusy: boolean;
  markOptions: (param: string, options: CacheOption[]) => CacheOption[];
  cachedParam: (param: keyof AnalysisParams) => boolean;
}) {
  const { t, tr } = useT();
  const {
    samplingAlgorithm, nSamples, mode, uncertaintyK, samplingStrategy, samplingScaling,
    samplingBlocks, samplingBudgetMode, samplingCoverage, samplingMinDistance, samplingExistingRunId,
  } = params;
  const {
    samplingAlgorithm: setSamplingAlgorithm, nSamples: setNSamples, mode: setMode,
    uncertaintyK: setUncertaintyK, samplingStrategy: setSamplingStrategy, samplingScaling: setSamplingScaling,
    samplingBlocks: setSamplingBlocks, samplingBudgetMode: setSamplingBudgetMode,
    samplingCoverage: setSamplingCoverage, samplingMinDistance: setSamplingMinDistance,
    samplingExistingRunId: setSamplingExistingRunId,
  } = setters;
  return <><Space wrap><Typography.Text>{t("Method")}</Typography.Text><Select value={samplingAlgorithm} onChange={setSamplingAlgorithm} options={markOptions("samplingAlgorithm", ["fps", "novelty_fps", "uncertainty_diversity", "random", "stratified", "cluster_representative", "per_element"].map((value) => ({ value, label: tr(SAMPLING_LABELS[value] ?? { en: value, zh: value }) })))} /><ParamLabel label={t("Maximum samples")} cached={cachedParam("nSamples")} /><InputNumber min={1} value={nSamples} onChange={(value) => setNSamples(value ?? 1000)} /><Select value={mode} onChange={setMode} options={markOptions("mode", [{ value: "structure", label: t("Structure") }, { value: "atom", label: t("Atom") }])} />{samplingAlgorithm === "uncertainty_diversity" && <><ParamLabel label="kNN" cached={cachedParam("uncertaintyK")} /><InputNumber min={2} value={uncertaintyK} onChange={(value) => setUncertaintyK(value ?? 8)} /></>}{samplingAlgorithm === "fps" && <><ParamLabel label={t("Strategy")} cached={cachedParam("samplingStrategy")} /><Select value={samplingStrategy} onChange={setSamplingStrategy} options={markOptions("samplingStrategy", [{ value: "global", label: t("Global FPS") }, { value: "grouped", label: t("Grouped FPS") }])} /><ParamLabel label={t("Feature scaling")} cached={cachedParam("samplingScaling")} /><Select value={samplingScaling} onChange={setSamplingScaling} options={markOptions("samplingScaling", [{ value: "robust", label: t("Robust") }, { value: "standardized", label: t("Standardized") }, { value: "raw", label: t("Raw") }])} /><ParamLabel label={t("Sampling space")} cached={cachedParam("samplingBlocks")} /><Select mode="multiple" allowClear value={samplingBlocks} onChange={(value) => setSamplingBlocks(value ?? [])} style={{ minWidth: 240 }} placeholder={t("Descriptor only")} options={[{"value":"descriptor","label":t("Descriptor")},{"value":"descriptor_summary","label":t("Descriptor summary")},{"value":"lattice","label":t("Lattice")},{"value":"composition","label":t("Composition")},{"value":"energy","label":t("Energy")},{"value":"force","label":t("Force statistics")}]} /><ParamLabel label={t("Sample budget")} cached={cachedParam("samplingBudgetMode")} /><Select value={samplingBudgetMode} onChange={setSamplingBudgetMode} options={markOptions("samplingBudgetMode", [{ value: "count", label: t("Fixed sample count") }, { value: "coverage", label: t("Target coverage") }])} />{samplingBudgetMode === "coverage" && <><ParamLabel label={t("Target coverage")} cached={cachedParam("samplingCoverage")} /><InputNumber min={1} max={99} value={samplingCoverage} onChange={(value) => setSamplingCoverage(Math.min(99, Math.max(1, Math.round(value ?? 95))))} addonAfter="%" /></>}<ParamLabel label={t("Min descriptor distance")} cached={cachedParam("samplingMinDistance")} /><InputNumber min={0} step={0.001} value={samplingMinDistance} onChange={(value) => setSamplingMinDistance(Math.max(0, value ?? 0))} /><Typography.Text type="secondary">{t("Initialization: Center")}</Typography.Text><ParamLabel label={t("Existing dataset")} cached={cachedParam("samplingExistingRunId")} /><Select allowClear placeholder={t("None")} value={samplingExistingRunId} onChange={(value) => setSamplingExistingRunId(value ?? null)} style={{ minWidth: 180 }} options={warmStartRuns.map((run) => ({ value: run.id, label: `${run.descriptor_name} · ${run.dataset_name ?? run.dataset_id}` }))} /></>}</Space>
            {samplingAlgorithm === "fps" && samplingStrategy === "grouped" && <SamplingQuotaPreview quota={quota} busy={quotaBusy} />}</>;
}

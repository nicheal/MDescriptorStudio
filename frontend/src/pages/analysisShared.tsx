/*
 * Leaf components and value types shared by the Analysis page and its panels.
 * Split out of Analysis so the page component keeps run selection, the async
 * run layer and module orchestration — and nothing else.
 */
import type { ReactNode } from "react";
import { Button, Collapse, Empty, InputNumber, Modal, Select, Space, Table, Tag, Typography } from "antd";
import { ArrowSync16Regular } from "@fluentui/react-icons";
import { useT } from "../i18n";
import { OVERVIEW_KIND_LABELS, type AnalysisParams, type ProjectionName } from "../features/analysis";
import type { PcaMode } from "../stores/workspace";
import type {
  AnalysisPreview,
  DatasetMeta,
  DatasetView,
  PcaPayload,
  RunRow,
} from "../types/protocol";
import type { AnalysisPoint } from "./analysisPreview";
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
  if (rows.length) return <section className="analysis-card"><SectionHeading title={String(preview?.kind ?? "RESULT").toUpperCase()} meta={t("{n} rows", { n: rows.length.toLocaleString() })} /><Table size="small" pagination={{ pageSize: 12 }} rowKey={(row, index) => `${String(row.i ?? row.sample_id ?? row.run_id ?? index)}:${String(row.source_i ?? row.rank ?? index)}`} dataSource={rows} onRow={(row) => ({ onClick: () => onSelect?.(row) })} columns={Object.keys(rows[0]).slice(0, 7).map((key) => ({ title: key, dataIndex: key, key, render: (value: unknown) => typeof value === "number" ? value.toPrecision(6) : String(value ?? "—") }))} /></section>;
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

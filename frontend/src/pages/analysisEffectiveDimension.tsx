import { formatNumber as formatNumericValue } from "../util/format";
/*
 * The effective-dimension (PCA spectrum) panel.  Split out of Analysis so the
 * page component keeps only run selection and module orchestration.
 */
import { useEffect, useState, type ReactNode } from "react";
import type { Data } from "plotly.js";
import { Select, Space, Tooltip, Typography } from "antd";
import { Info16Regular } from "@fluentui/react-icons";
import { useT } from "../i18n";
import type { AnalysisPreview } from "../types/protocol";
import {
  ChartCaption,
  NoData as OverviewNoData,
  OverviewPlot,
  formatCount,
  num as finiteNumber,
  nums as numericArray,
  overviewLayout,
} from "./analysisChartKit";

export type Metric = {
  label: ReactNode;
  value: string;
};

type SpectrumRange = "20" | "50" | "all";

export function EffectiveDimensionChart({ preview, arrays }: { preview: AnalysisPreview; arrays: Record<string, unknown[]> }) {
  const { t } = useT();
  const [spectrumRange, setSpectrumRange] = useState<SpectrumRange>("20");
  useEffect(() => setSpectrumRange("20"), [preview.analysis_id]);

  const explained = numericArray(arrays.explained_variance);
  if (!explained.length) return <OverviewNoData message={t("The explained-variance array is not available for visualization.")} />;
  const cumulative: number[] = [];
  let total = 0;
  for (const value of explained) {
    total += value;
    cumulative.push(total);
  }

  const declaredComponentCount = integerCount(preview.component_count) ?? explained.length;
  const availableComponentCount = Math.min(declaredComponentCount, explained.length);
  const shownComponentCount = spectrumRange === "all"
    ? availableComponentCount
    : Math.min(Number(spectrumRange), availableComponentCount);
  const indices = sampledIndices(shownComponentCount, 320);
  const pcaFeatureCount = integerCount(preview.pca_feature_count);
  const featureCount = integerCount(preview.feature_count) ?? pcaFeatureCount;
  const preprocess = preview.preprocess === "standardized" || preview.preprocess === "raw" ? preview.preprocess : "center";
  const scalingLabel = preprocess === "standardized" ? t("Standardized") : preprocess === "raw" ? t("Raw scale") : t("Centered");
  const basisLabel = preprocess === "standardized" ? t("Correlation") : preprocess === "raw" ? t("Uncentered second moment") : t("Covariance");
  const thresholdRows = [
    { key: "0.9", target: "90%", color: "#107C10" },
    { key: "0.95", target: "95%", color: "#8764B8" },
    { key: "0.99", target: "99%", color: "#D13438" },
  ]
    .map((row) => ({ ...row, component: componentThreshold(preview, row.key) }))
    .filter((row): row is typeof row & { component: number } => row.component != null && row.component >= 1 && row.component <= availableComponentCount)
    .map((row) => ({ ...row, cumulative: cumulative[row.component - 1] }));
  const thresholdShapes = thresholdRows.map((row) => ({
    type: "line" as const,
    x0: row.component,
    x1: row.component,
    y0: 0,
    y1: 1,
    yref: "paper" as const,
    line: { color: row.color, dash: "dash" as const, width: 1.5 },
  }));
  const thresholdAnnotations = thresholdRows.map((row) => ({
    x: row.component,
    y: 1,
    xref: "x" as const,
    yref: "paper" as const,
    text: `${row.target} · PC${row.component}`,
    showarrow: false,
    yshift: 16,
    font: { size: 10, color: row.color },
    bgcolor: "#FFFFFF",
    bordercolor: row.color,
    borderwidth: 1,
    borderpad: 2,
  }));
  const thresholdTrace: Data | null = thresholdRows.length
    ? {
        type: "scatter",
        mode: "markers",
        x: thresholdRows.map((row) => row.component),
        y: thresholdRows.map((row) => row.cumulative * 100),
        text: thresholdRows.map((row) => `${row.target} · PC${row.component}`),
        marker: { color: thresholdRows.map((row) => row.color), size: 8, line: { color: "#FFFFFF", width: 1 } },
        hovertemplate: `%{text}<br>${t("Cumulative explained variance ratio")}=%{y:.2f}%<extra></extra>`,
        showlegend: false,
      }
    : null;
  const thresholdMetric = (key: string) => {
    const component = componentThreshold(preview, key);
    if (component == null) return "—";
    return pcaFeatureCount == null ? formatCount(component) : `${formatCount(component)} / ${formatCount(pcaFeatureCount)}`;
  };
  const participationRatio = finiteNumber(preview.participation_ratio);
  const prLabel = (
    <Tooltip title={t("Participation Ratio = (sum of eigenvalues)^2 / sum of squared eigenvalues: how many principal directions the variance is spread over. It can be fractional and is not a count of PCA components.")} placement="top">
      <span className="analysis-metric-label" tabIndex={0}>
        {t("PR effective dimension")} <Info16Regular aria-hidden="true" />
      </span>
    </Tooltip>
  );
  const hiddenThresholds = ["0.9", "0.95", "0.99"]
    .map((key) => componentThreshold(preview, key))
    .filter((component): component is number => component != null && component >= 1 && component > shownComponentCount);
  const conclusion = participationRatio != null && featureCount != null && pcaFeatureCount != null
    && componentThreshold(preview, "0.9") != null && componentThreshold(preview, "0.95") != null && componentThreshold(preview, "0.99") != null
    ? t("Variance is concentrated in this dataset: of {featureCount} original features, the {pcaFeatureCount} that entered the PCA are explained 90%, 95% and 99% of their total variance by the first {pc90}, {pc95} and {pc99} components, and the participation-ratio effective dimension is {participationRatio}. These figures are computed on {scaling} preprocessing and are not an optimal component count for a downstream model.", {
        featureCount: formatCount(featureCount),
        pcaFeatureCount: formatCount(pcaFeatureCount),
        pc90: formatCount(componentThreshold(preview, "0.9")),
        pc95: formatCount(componentThreshold(preview, "0.95")),
        pc99: formatCount(componentThreshold(preview, "0.99")),
        participationRatio: formatNumber(participationRatio),
        scaling: scalingLabel,
      })
    : null;
  const metrics: Metric[] = [
    { label: prLabel, value: formatNumber(participationRatio) },
    { label: t("90% effective dimension"), value: thresholdMetric("0.9") },
    { label: t("95% effective dimension"), value: thresholdMetric("0.95") },
    { label: t("99% effective dimension"), value: thresholdMetric("0.99") },
  ];
  return <>
    <MetricStrip metrics={metrics} />
    <div className="analysis-method-meta" aria-label={t("PCA method details")}>
      <span><Typography.Text type="secondary">{t("Scaling")}: </Typography.Text><Typography.Text strong>{scalingLabel}</Typography.Text></span>
      <span><Typography.Text type="secondary">{t("PCA basis")}: </Typography.Text><Typography.Text strong>{basisLabel}</Typography.Text></span>
      <span><Typography.Text type="secondary">{t("PCA features")}: </Typography.Text><Typography.Text strong>{pcaFeatureCount == null ? "—" : `${formatCount(pcaFeatureCount)} / ${formatCount(featureCount)}`}</Typography.Text></span>
      <span><Typography.Text type="secondary">{t("Components")}: </Typography.Text><Typography.Text strong>{formatCount(declaredComponentCount)}</Typography.Text></span>
    </div>
    <Typography.Text type="secondary" className="analysis-spectrum-note">{t("The threshold dimension is the number of principal components needed to reach that share of the total variance; the denominator is the number of features that actually entered the PCA.")}</Typography.Text>
    <div className="analysis-spectrum-toolbar">
      <Space wrap size={8}>
        <Typography.Text strong>{t("Spectrum range")}</Typography.Text>
        <Select
          aria-label={t("Spectrum range")}
          value={spectrumRange}
          onChange={(value) => setSpectrumRange(value as SpectrumRange)}
          options={[
            { value: "20", label: t("First 20") },
            { value: "50", label: t("First 50") },
            { value: "all", label: t("All components") },
          ]}
        />
        <Typography.Text type="secondary">{t("{shown} of {total} components shown", { shown: formatCount(shownComponentCount), total: formatCount(declaredComponentCount) })}</Typography.Text>
      </Space>
    </div>
    <OverviewPlot
      ariaLabel={t("Explained and cumulative descriptor variance by component")}
      data={[
        {
          type: "bar",
          x: indices.map((index) => index + 1),
          y: indices.map((index) => explained[index] * 100),
          name: t("Single-component explained variance ratio"),
          marker: { color: "#0F6CBD" },
          hovertemplate: `PC %{x}<br>${t("Single-component explained variance ratio")}=%{y:.2f}%<extra></extra>`,
        },
        {
          type: "scatter",
          mode: "lines",
          x: indices.map((index) => index + 1),
          y: indices.map((index) => cumulative[index] * 100),
          name: t("Cumulative explained variance ratio"),
          line: { color: "#F7630C", width: 2 },
          hovertemplate: `PC %{x}<br>${t("Cumulative explained variance ratio")}=%{y:.2f}%<extra></extra>`,
        },
        ...(thresholdTrace ? [thresholdTrace] : []),
      ]}
      layout={overviewLayout({
        margin: { l: 68, r: 28, t: 52, b: 52 },
        xaxis: { title: { text: t("Principal component") }, type: "linear" },
        yaxis: { title: { text: t("Explained variance ratio (%)") }, range: [0, 100] },
        shapes: thresholdShapes,
        annotations: thresholdAnnotations,
        legend: { orientation: "h", y: 1.18, x: 0 },
      })}
    />
    {conclusion && <Typography.Paragraph className="analysis-effective-conclusion">{conclusion}</Typography.Paragraph>}
    <ChartCaption>
      {t("Bars show the single-component explained variance ratio and the orange line shows the cumulative explained variance ratio.")}
      {shownComponentCount > 320 ? ` ${t("When more than 320 components are selected, the chart samples evenly for rendering while preserving the selected range.")}` : ""}
      {hiddenThresholds.length ? ` ${t("Some threshold markers are outside the selected range.")}` : ""}
    </ChartCaption>
  </>;
}

function MetricStrip({ metrics }: { metrics: Metric[] }) {
  return <div className="analysis-metric-strip">{metrics.map((metric, index) => <div className="analysis-metric" key={index}><Typography.Text type="secondary">{metric.label}</Typography.Text><Typography.Text strong>{metric.value}</Typography.Text></div>)}</div>;
}

function sampledIndices(length: number, maxPoints: number): number[] {
  if (length <= 0) return [];
  if (length <= maxPoints) return Array.from({ length }, (_, index) => index);
  return Array.from({ length: maxPoints }, (_, index) => Math.round(index * (length - 1) / (maxPoints - 1)));
}

function formatNumber(value: unknown): string {
  const number = finiteNumber(value);
  if (number === null) return "—";
  return formatNumericValue(number);
}

function integerCount(value: unknown): number | null {
  const number = finiteNumber(value);
  return number !== null && Number.isInteger(number) && number >= 0 ? number : null;
}

function componentThreshold(preview: AnalysisPreview, key: string): number | null {
  const thresholds = preview.components_for_threshold;
  if (typeof thresholds !== "object" || thresholds === null || Array.isArray(thresholds)) return null;
  return finiteNumber((thresholds as Record<string, unknown>)[key]);
}

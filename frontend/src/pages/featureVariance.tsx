import { useEffect, useMemo, useState } from "react";
import type { Data, PlotMouseEvent } from "plotly.js";
import { InputNumber, Select, Tabs, Tag, Typography } from "antd";
import { ipc } from "../ipc/client";
import { useT, type Pair } from "../i18n";
import {
  ChartCaption,
  NoData as OverviewNoData,
  OverviewPlot,
  formatCount,
  num as finiteNumber,
  nums as numericArray,
  overviewLayout,
  records as recordArray,
} from "./analysisChartKit";
import type { AnalysisChunk, AnalysisPreview } from "../types/protocol";

type FeatureVarianceMetric = "variance" | "relative_variance" | "std" | "iqr" | "mad";
type FeatureVarianceSort = "variance_desc" | "variance_asc" | "index";
type FeatureVarianceDisplay = "all" | "top" | "bottom" | "near_zero" | "low_variation" | "constant";
type FeatureVariancePane = "chart" | "stats";
type FeatureVarianceStatus = "constant" | "near_zero" | "low_variation" | "active" | "invalid";

type FeatureVarianceRow = {
  index: number;
  mean: number | null;
  variance: number | null;
  relative_variance: number | null;
  std: number | null;
  min: number | null;
  max: number | null;
  p05: number | null;
  p25: number | null;
  median: number | null;
  p75: number | null;
  p95: number | null;
  iqr: number | null;
  mad: number | null;
  robust_sigma: number | null;
  std_robust_ratio: number | null;
  whisker_min: number | null;
  whisker_max: number | null;
  outlier_count: number;
  finite_count: number;
  invalid_count: number;
  distribution_sample_count: number;
  status: FeatureVarianceStatus;
};

type FeatureVarianceDistribution = {
  loading: boolean;
  edges: number[];
  counts: number[];
  samples: number[];
};

const FEATURE_VARIANCE_STATUS_LABELS: Record<FeatureVarianceStatus, Pair> = {
  constant: { en: "Constant", zh: "常量" },
  near_zero: { en: "Near-zero", zh: "近零方差" },
  low_variation: { en: "Low variation", zh: "低变化" },
  active: { en: "Active", zh: "活跃" },
  invalid: { en: "Invalid", zh: "无效数据" },
};

const FEATURE_VARIANCE_STATUS_COLORS: Record<FeatureVarianceStatus, string> = {
  constant: "#8A8886",
  near_zero: "#8764B8",
  low_variation: "#F7630C",
  active: "#0F6CBD",
  invalid: "#D13438",
};

export function FeatureVarianceChart({ preview, analysisId }: { preview: AnalysisPreview; analysisId: string | null }) {
  const { t, tr } = useT();
  const features = useMemo(() => parseFeatureVarianceRows(preview), [preview]);
  const positiveVariances = useMemo(
    () => features.map((feature) => feature.variance).filter((value): value is number => value !== null && value > 0),
    [features],
  );
  const varianceSpan = positiveVariances.length ? Math.max(...positiveVariances) / Math.min(...positiveVariances) : 0;
  const recommendedScale: "linear" | "log" = varianceSpan >= 100 ? "log" : "linear";
  const [metric, setMetric] = useState<FeatureVarianceMetric>("variance");
  const [sort, setSort] = useState<FeatureVarianceSort>("variance_desc");
  const [display, setDisplay] = useState<FeatureVarianceDisplay>(() => features.length <= 100 ? "all" : "top");
  const [topK, setTopK] = useState(20);
  const [scale, setScale] = useState<"linear" | "log">(() => recommendedScale);
  const [selectedFeatureIndex, setSelectedFeatureIndex] = useState<number | null>(null);
  const [distribution, setDistribution] = useState<FeatureVarianceDistribution | null>(null);
  const [activePane, setActivePane] = useState<FeatureVariancePane>("chart");

  useEffect(() => {
    setDisplay(features.length <= 100 ? "all" : "top");
    setScale(recommendedScale);
    setSelectedFeatureIndex(null);
    setDistribution(null);
    setActivePane("chart");
  }, [analysisId, features.length, recommendedScale]);

  useEffect(() => {
    if (!analysisId || selectedFeatureIndex == null) {
      setDistribution(null);
      return;
    }
    let disposed = false;
    setDistribution({ loading: true, edges: [], counts: [], samples: [] });
    const loadRow = async (array: string): Promise<number[]> => {
      const chunk = await ipc.request<AnalysisChunk>("analysis.chunk", {
        analysis_id: analysisId,
        array,
        offset: selectedFeatureIndex,
        limit: 1,
        column_end: 20_000,
      });
      return numericArray(chunk.data[0]);
    };
    void Promise.all([
      loadRow("histogram_edges"),
      loadRow("histogram_counts"),
      loadRow("distribution_samples"),
    ]).then(([edges, counts, samples]) => {
      if (!disposed) setDistribution({ loading: false, edges, counts, samples });
    }).catch(() => {
      if (!disposed) setDistribution({ loading: false, edges: [], counts: [], samples: [] });
    });
    return () => { disposed = true; };
  }, [analysisId, selectedFeatureIndex]);

  const summary = recordValue(preview.summary);
  const maxVariance = finiteNumber(summary?.max_variance) ?? Math.max(0, ...features.map((feature) => feature.variance ?? 0));
  const medianVariance = finiteNumber(summary?.median_variance) ?? medianOf(features.map((feature) => feature.variance).filter((value): value is number => value !== null));
  const varianceValues = features.map((feature) => feature.variance).filter((value): value is number => value !== null);
  const minVariance = finiteNumber(summary?.min_variance) ?? (varianceValues.length ? Math.min(...varianceValues) : 0);
  const nearZeroCount = finiteNumber(summary?.near_zero_count) ?? features.filter((feature) => feature.status === "near_zero").length;
  const constantCount = finiteNumber(summary?.constant_count) ?? features.filter((feature) => feature.status === "constant").length;
  const featureCount = finiteNumber(preview.feature_count) ?? features.length;
  const sampleCount = finiteNumber(preview.sample_count);
  const settings = recordValue(preview.settings);
  const nearZeroThreshold = finiteNumber(settings?.near_zero_relative_threshold) ?? 1e-4;
  const lowVariationThreshold = finiteNumber(settings?.low_variance_relative_threshold) ?? 1e-2;
  const constantTolerance = finiteNumber(settings?.constant_tolerance) ?? 1e-12;

  const rankedRows = useMemo(() => {
    const filtered = features.filter((feature) => {
      if (display === "near_zero") return feature.status === "near_zero";
      if (display === "low_variation") return feature.status === "low_variation";
      if (display === "constant") return feature.status === "constant";
      return true;
    });
    const compare = (left: FeatureVarianceRow, right: FeatureVarianceRow) => {
      if (sort === "index") return left.index - right.index;
      const leftValue = left.variance ?? -Infinity;
      const rightValue = right.variance ?? -Infinity;
      return sort === "variance_asc" ? leftValue - rightValue : rightValue - leftValue;
    };
    if (display === "top" || display === "bottom") {
      const varianceRanked = filtered
        .filter((feature) => feature.variance !== null)
        .sort((left, right) => (right.variance ?? -Infinity) - (left.variance ?? -Infinity));
      const selected = display === "top"
        ? varianceRanked.slice(0, Math.max(1, topK))
        : varianceRanked.slice(Math.max(0, varianceRanked.length - Math.max(1, topK)));
      return selected.sort(compare);
    }
    return filtered.slice().sort(compare);
  }, [display, features, sort, topK]);

  const plotRows = useMemo(
    () => rankedRows.filter((row) => featureVarianceMetricValue(row, metric) !== null),
    [metric, rankedRows],
  );
  const chartRows = plotRows.slice().reverse();
  const selectedFeature = selectedFeatureIndex == null ? null : features.find((feature) => feature.index === selectedFeatureIndex) ?? null;
  const selectedSamples = useMemo(() => {
    if (!selectedFeature || !distribution) return [];
    return distribution.samples.slice(0, Math.max(0, selectedFeature.distribution_sample_count || distribution.samples.length));
  }, [distribution, selectedFeature]);
  const selectedHistogram = useMemo(
    () => buildFeatureHistogram(distribution?.edges ?? [], distribution?.counts ?? [], selectedSamples),
    [distribution?.counts, distribution?.edges, selectedSamples],
  );
  const selectedKde = useMemo(
    () => buildKde(selectedSamples, selectedHistogram.edges, selectedHistogram.countScale),
    [selectedHistogram.countScale, selectedHistogram.edges, selectedSamples],
  );
  if (!features.length) return <OverviewNoData message={t("No feature variance values were returned.")} />;

  const chooseFilter = (next: FeatureVarianceDisplay) => {
    setDisplay((current) => current === next ? "all" : next);
  };
  const statusText = (status: FeatureVarianceStatus) => tr(FEATURE_VARIANCE_STATUS_LABELS[status]);
  const topKEnabled = display === "top" || display === "bottom";
  const hasZeroVariance = features.some((feature) => feature.variance === 0);
  const featureOptions = plotRows.map((row) => ({ value: row.index, label: `${t("Feature {index}", { index: row.index })} · ${formatNumber(featureVarianceMetricValue(row, metric))}` }));
  const chartLabels = chartRows.map((row) => t("Feature {index}", { index: row.index }));
  const chartPositions = chartRows.map((_, index) => index);
  const chartHeight = Math.max(360, chartRows.length * 22 + 96);

  return <>
    <div className="analysis-metric-strip feature-variance-summary">
      <div className="analysis-metric"><Typography.Text type="secondary">{t("Features")}</Typography.Text><Typography.Text strong>{formatCount(featureCount)}</Typography.Text></div>
      <div className="analysis-metric"><Typography.Text type="secondary">{t("Max absolute variance")}</Typography.Text><Typography.Text strong>{formatNumber(maxVariance)}</Typography.Text></div>
      <div className="analysis-metric"><Typography.Text type="secondary">{t("Median absolute variance")}</Typography.Text><Typography.Text strong>{formatNumber(medianVariance)}</Typography.Text></div>
      <div className="analysis-metric"><Typography.Text type="secondary">{t("Min absolute variance")}</Typography.Text><Typography.Text strong>{formatNumber(minVariance)}</Typography.Text></div>
      <button type="button" className={`analysis-metric analysis-metric-action${display === "near_zero" ? " is-selected" : ""}`} aria-pressed={display === "near_zero"} onClick={() => chooseFilter("near_zero")}>
        <Typography.Text type="secondary">{t("Near-zero")}</Typography.Text><Typography.Text strong>{formatCount(nearZeroCount)}</Typography.Text>
      </button>
      <button type="button" className={`analysis-metric analysis-metric-action${display === "constant" ? " is-selected" : ""}`} aria-pressed={display === "constant"} onClick={() => chooseFilter("constant")}>
        <Typography.Text type="secondary">{t("Constant")}</Typography.Text><Typography.Text strong>{formatCount(constantCount)}</Typography.Text>
      </button>
    </div>
    <Typography.Text type="secondary" className="feature-variance-summary-note">{t("Summary cards always show absolute variance, independent of the selected metric.")}</Typography.Text>

    <div className="feature-variance-toolbar" role="group" aria-label={t("Feature variance controls")}>
      <label className="feature-variance-control"><span>{t("Metric")}</span><Select aria-label={t("Variance metric")} value={metric} onChange={setMetric} options={[{ value: "variance", label: t("Absolute variance") }, { value: "relative_variance", label: t("Normalized variance") }, { value: "std", label: t("Standard deviation") }, { value: "iqr", label: t("IQR") }, { value: "mad", label: t("MAD") }]} /></label>
      <label className="feature-variance-control"><span>{t("Sort")}</span><Select aria-label={t("Variance sort order")} value={sort} onChange={setSort} options={[{ value: "variance_desc", label: t("Variance descending") }, { value: "variance_asc", label: t("Variance ascending") }, { value: "index", label: t("Feature index") }]} /></label>
      <label className="feature-variance-control"><span>{t("Display")}</span><Select aria-label={t("Feature display filter")} value={display} onChange={setDisplay} options={[{ value: "all", label: t("All features") }, { value: "top", label: t("Highest variation") }, { value: "bottom", label: t("Lowest variation") }, { value: "near_zero", label: t("Near-zero") }, { value: "low_variation", label: t("Low variation") }, { value: "constant", label: t("Constant") }]} /></label>
      <label className={`feature-variance-control feature-variance-k-control${topKEnabled ? "" : " is-disabled"}`} title={topKEnabled ? undefined : t("Top/Bottom K applies only to highest/lowest variation views.")}><span>{t("Top/Bottom K")}</span><InputNumber aria-label={t("Top/Bottom K")} disabled={!topKEnabled} min={1} max={Math.max(1, features.length)} value={topK} onChange={(value) => setTopK(Math.min(Math.max(1, value ?? 20), Math.max(1, features.length)))} /></label>
      <label className="feature-variance-control"><span>{t("Coordinate scale")}</span><Select aria-label={t("Coordinate scale")} value={scale} onChange={setScale} options={[{ value: "linear", label: t("Linear") }, { value: "log", label: t("Log") }]} /></label>
    </div>
    <div className="feature-variance-thresholds">
      <Typography.Text type="secondary">{t("{n} samples · Near-zero < {near} · Low variation < {low} · Constant tolerance ≤ {constant}", { n: sampleCount ?? 0, near: nearZeroThreshold, low: lowVariationThreshold, constant: constantTolerance })}</Typography.Text>
      <Typography.Text type="secondary">{t("Near-zero and Low variation thresholds use normalized variance.")}</Typography.Text>
      {varianceSpan >= 100 && <Typography.Text type="warning">{t("Variance spans multiple orders of magnitude; log scale is recommended by default.")}</Typography.Text>}
      {scale === "log" && hasZeroVariance && <Typography.Text type="secondary">{t("Zero-variance features are omitted from the log axis.")}</Typography.Text>}
      {Array.isArray(preview.warnings) && preview.warnings.filter((warning): warning is string => typeof warning === "string").map((warning, index) => <Typography.Text type="warning" key={`${index}-${warning}`}>{warning}</Typography.Text>)}
    </div>

    <div className="feature-variance-layout">
      <div className="feature-variance-overview">
        <Tabs
          className="feature-variance-pane-tabs"
          type="card"
          activeKey={activePane}
          onChange={(key) => setActivePane(key as FeatureVariancePane)}
          items={[
            {
              key: "chart",
              label: t("Variance distribution"),
              children: <>
                {chartRows.length ? <div className="feature-variance-chart-scroll">
                  <OverviewPlot
                    className="feature-variance-overview-chart"
                    style={{ height: `${chartHeight}px` }}
                    ariaLabel={t("Descriptor feature variance distribution")}
                    data={[{
                      type: "bar",
                      orientation: "h",
                      x: chartRows.map((row) => {
                        const value = featureVarianceMetricValue(row, metric);
                        return scale === "log" && value !== null && value <= 0 ? null : value;
                      }),
                      y: chartPositions,
                      customdata: chartRows.map((row) => [
                        row.index,
                        formatNumber(row.variance),
                        formatNumber(row.std),
                        formatNumber(row.mean),
                        formatNumber(row.median),
                        formatNumber(row.p05),
                        formatNumber(row.p95),
                        formatNumber(row.relative_variance),
                        statusText(row.status),
                      ]),
                      marker: { color: chartRows.map((row) => FEATURE_VARIANCE_STATUS_COLORS[row.status]) },
                      hovertemplate: `${t("Feature")} %{customdata[0]}<br>${t("Absolute variance")}=%{customdata[1]}<br>${t("Standard deviation")}=%{customdata[2]}<br>${t("Mean")}=%{customdata[3]}<br>${t("Median")}=%{customdata[4]}<br>${t("P05–P95")}=%{customdata[5]} – %{customdata[6]}<br>${t("Normalized variance")}=%{customdata[7]}<br>${t("Status")}=%{customdata[8]}<extra></extra>`,
                    }]}
                    layout={overviewLayout({
                      xaxis: { title: { text: t(metricLabel(metric)) }, type: scale === "log" ? "log" : "linear", zeroline: true },
                      yaxis: {
                        automargin: true,
                        range: [-0.5, Math.max(0.5, chartRows.length - 0.5)],
                        tickmode: "array",
                        tickvals: chartPositions,
                        ticktext: chartLabels,
                      },
                    })}
                    onClick={(event: Readonly<PlotMouseEvent>) => {
                      const index = event.points?.[0]?.pointIndex;
                      if (typeof index === "number") {
                        const nextFeatureIndex = chartRows[index]?.index ?? null;
                        if (nextFeatureIndex !== null) {
                          setSelectedFeatureIndex(nextFeatureIndex);
                          setActivePane("stats");
                        }
                      }
                    }}
                  />
                </div> : <OverviewNoData message={t("No feature values are available for this metric.")} />}
              </>,
            },
            {
              key: "stats",
              label: t("Feature statistics"),
              children: selectedFeature ? <FeatureVarianceStats feature={selectedFeature} /> : <OverviewNoData message={t("Select a feature for detail")} />,
            },
          ]}
        />
      </div>
      <div className="feature-variance-detail-column">
        <div className="feature-variance-feature-picker feature-variance-detail-picker">
          <Typography.Text type="secondary">{t("Feature detail")}</Typography.Text>
          <Select
            allowClear
            aria-label={t("Select feature for detail")}
            placeholder={t("Select a feature for detail")}
            value={selectedFeatureIndex ?? undefined}
            options={featureOptions}
            onChange={(value) => setSelectedFeatureIndex(value ?? null)}
          />
        </div>
        {selectedFeature ? <FeatureVarianceDetail feature={selectedFeature} distribution={distribution} histogram={selectedHistogram} kde={selectedKde} statusText={statusText(selectedFeature.status)} /> : <div className="feature-variance-detail feature-variance-detail-empty"><div className="analysis-section-heading"><Typography.Text strong>{t("Feature detail")}</Typography.Text></div><OverviewNoData message={t("Select a feature for detail")} /></div>}
      </div>
    </div>
  </>;
}

function FeatureVarianceDetail({ feature, distribution, histogram, kde, statusText }: { feature: FeatureVarianceRow; distribution: FeatureVarianceDistribution | null; histogram: { edges: number[]; counts: number[] }; kde: { x: number[]; y: number[] }; statusText: string }) {
  const { t } = useT();
  const histogramCenters = histogram.counts.map((_, index) => (histogram.edges[index] + histogram.edges[index + 1]) / 2);
  const histogramWidths = histogram.counts.map((_, index) => histogram.edges[index + 1] - histogram.edges[index]);
  const whiskerMin = feature.whisker_min ?? feature.min;
  const whiskerMax = feature.whisker_max ?? feature.max;
  const boxData = feature.p25 !== null && feature.median !== null && feature.p75 !== null && whiskerMin !== null && whiskerMax !== null
    ? [{ type: "box", orientation: "h", q1: [feature.p25], median: [feature.median], q3: [feature.p75], lowerfence: [whiskerMin], upperfence: [whiskerMax], y: [t("Feature {index}", { index: feature.index })], name: t("Tukey whiskers"), boxpoints: false, marker: { color: "#8764B8" } } as Data]
    : null;
  const sampledOutliers = distribution && !distribution.loading && whiskerMin !== null && whiskerMax !== null
    ? distribution.samples.filter((value) => value < whiskerMin || value > whiskerMax)
    : [];
  const robustRatio = feature.std_robust_ratio;
  const robustDiagnostic = robustRatio === null
    ? null
    : robustRatio > 2
      ? { label: t("Long-tail / outlier-sensitive"), color: "red" }
      : robustRatio >= 1.5
        ? { label: t("Possible skew / long-tail"), color: "orange" }
        : { label: t("Within robust-scale baseline"), color: "green" };
  return <div className="feature-variance-detail">
    <div className="analysis-section-heading"><Typography.Text strong>{t("Feature detail")}</Typography.Text><Typography.Text type="secondary">{t("Feature {index}", { index: feature.index })}</Typography.Text></div>
    <div className="feature-variance-detail-meta"><Tag color={FEATURE_VARIANCE_STATUS_COLORS[feature.status]}>{statusText}</Tag>{robustDiagnostic && <Tag color={robustDiagnostic.color}>{robustDiagnostic.label}</Tag>}{feature.invalid_count > 0 && <Typography.Text type="warning">{t("{n} invalid values were excluded.", { n: feature.invalid_count })}</Typography.Text>}</div>
    {feature.status === "invalid" ? <OverviewNoData message={t("This feature has no finite values.")} /> : <div className="feature-variance-detail-charts">
      {histogram.counts.length ? <OverviewPlot compact ariaLabel={t("Feature value histogram and KDE")} data={[{ type: "bar", x: histogramCenters, y: histogram.counts, width: histogramWidths, marker: { color: "#0F6CBD", opacity: 0.7 }, hovertemplate: `${t("Value")}=%{x:.5g}<br>${t("Samples")}=%{y}<extra></extra>` }, ...(kde.x.length ? [{ type: "scatter", mode: "lines", x: kde.x, y: kde.y, name: "KDE", line: { color: "#D13438", width: 2 }, hovertemplate: `${t("KDE")}=%{y:.5g}<extra></extra>` }] : [])] as Data[]} layout={overviewLayout({ xaxis: { title: { text: t("Value") } }, yaxis: { title: { text: t("Samples") } }, legend: { orientation: "h" } })} /> : <OverviewNoData message={t("Distribution data is unavailable.")} />}
      {boxData ? <OverviewPlot compact ariaLabel={t("Feature value box plot")} data={[...boxData, ...(sampledOutliers.length ? [{ type: "scatter", mode: "markers", x: sampledOutliers, y: sampledOutliers.map(() => t("Feature {index}", { index: feature.index })), name: t("Outliers"), marker: { color: "#D13438", size: 7, symbol: "circle-open" }, hovertemplate: `${t("Outliers")}=%{x:.5g}<extra></extra>` } as Data] : [])]} layout={overviewLayout({ xaxis: { title: { text: t("Value") } }, yaxis: { automargin: true }, legend: { orientation: "h" } })} /> : <OverviewNoData message={t("Box plot data is unavailable.")} />}
    </div>}
    {distribution?.loading && <Typography.Text type="secondary">{t("Loading feature distribution…")}</Typography.Text>}
    {feature.status === "constant" && <ChartCaption>{t("Constant feature; KDE omitted.")}</ChartCaption>}
  </div>;
}

function FeatureVarianceStats({ feature }: { feature: FeatureVarianceRow }) {
  const { t } = useT();
  const whiskerMin = feature.whisker_min ?? feature.min;
  const whiskerMax = feature.whisker_max ?? feature.max;
  const stats: [string, string][] = [
    [t("Samples"), formatCount(feature.finite_count)],
    [t("Invalid values"), formatCount(feature.invalid_count)],
    [t("Mean"), formatNumber(feature.mean)],
    [t("Absolute variance"), formatNumber(feature.variance)],
    [t("Normalized variance"), formatNumber(feature.relative_variance)],
    [t("Standard deviation"), formatNumber(feature.std)],
    [t("Min"), formatNumber(feature.min)],
    [t("Whisker min"), formatNumber(whiskerMin)],
    [t("P05"), formatNumber(feature.p05)],
    [t("P25"), formatNumber(feature.p25)],
    [t("Median"), formatNumber(feature.median)],
    [t("P75"), formatNumber(feature.p75)],
    [t("P95"), formatNumber(feature.p95)],
    [t("Whisker max"), formatNumber(whiskerMax)],
    [t("Max"), formatNumber(feature.max)],
    [t("Outliers"), formatCount(feature.outlier_count)],
    [t("IQR"), formatNumber(feature.iqr)],
    [t("MAD"), formatNumber(feature.mad)],
    [t("Robust sigma"), formatNumber(feature.robust_sigma)],
    [t("Std / Robust sigma"), formatNumber(feature.std_robust_ratio)],
  ];
  return <div className="feature-variance-stat-grid">{stats.map(([label, value]) => <div className="feature-variance-stat" key={label}><span>{label}</span><Typography.Text code>{value}</Typography.Text></div>)}</div>;
}

function parseFeatureVarianceRows(preview: AnalysisPreview): FeatureVarianceRow[] {
  const records = recordArray(preview.features);
  if (records.length) {
    return records.map((record) => {
      const rawStatus = String(record.status ?? "active");
      const status: FeatureVarianceStatus = rawStatus === "constant" || rawStatus === "near_zero" || rawStatus === "low_variation" || rawStatus === "invalid" ? rawStatus : "active";
      return {
        index: Math.round(finiteNumber(record.index) ?? 0),
        mean: finiteNumber(record.mean),
        variance: finiteNumber(record.variance),
        relative_variance: finiteNumber(record.relative_variance),
        std: finiteNumber(record.std),
        min: finiteNumber(record.min),
        max: finiteNumber(record.max),
        p05: finiteNumber(record.p05),
        p25: finiteNumber(record.p25),
        median: finiteNumber(record.median),
        p75: finiteNumber(record.p75),
        p95: finiteNumber(record.p95),
        iqr: finiteNumber(record.iqr),
        mad: finiteNumber(record.mad),
        robust_sigma: finiteNumber(record.robust_sigma),
        std_robust_ratio: finiteNumber(record.std_robust_ratio),
        whisker_min: finiteNumber(record.whisker_min),
        whisker_max: finiteNumber(record.whisker_max),
        outlier_count: Math.max(0, Math.round(finiteNumber(record.outlier_count) ?? 0)),
        finite_count: Math.max(0, Math.round(finiteNumber(record.finite_count) ?? 0)),
        invalid_count: Math.max(0, Math.round(finiteNumber(record.invalid_count ?? record.missing_count) ?? 0)),
        distribution_sample_count: Math.max(0, Math.round(finiteNumber(record.distribution_sample_count) ?? 0)),
        status,
      };
    }).filter((row, index) => row.index >= 0 && index === records.findIndex((record) => Math.round(finiteNumber(record.index) ?? 0) === row.index));
  }

  // Legacy artifacts remain displayable while users transition to the full
  // schema. They do not contain enough information for the detail panel.
  const indices = Array.isArray(preview.top_indices) ? preview.top_indices.map(finiteNumber) : [];
  const values = Array.isArray(preview.top_values) ? preview.top_values.map(finiteNumber) : [];
  return indices.map((index, position) => {
    const variance = values[position] ?? null;
    return {
      index: Math.round(index ?? position),
      mean: null,
      variance,
      relative_variance: null,
      std: variance === null ? null : Math.sqrt(Math.max(variance, 0)),
      min: null,
      max: null,
      p05: null,
      p25: null,
      median: null,
      p75: null,
      p95: null,
      iqr: null,
      mad: null,
      robust_sigma: null,
      std_robust_ratio: null,
      whisker_min: null,
      whisker_max: null,
      outlier_count: 0,
      finite_count: Math.round(finiteNumber(preview.sample_count) ?? 0),
      invalid_count: 0,
      distribution_sample_count: 0,
      status: "active" as const,
    };
  }).filter((row) => row.variance !== null);
}

function featureVarianceMetricValue(row: FeatureVarianceRow, metric: FeatureVarianceMetric): number | null {
  if (metric === "relative_variance") return row.relative_variance;
  if (metric === "std") return row.std;
  if (metric === "iqr") return row.iqr;
  if (metric === "mad") return row.mad;
  return row.variance;
}

function metricLabel(metric: FeatureVarianceMetric): string {
  if (metric === "relative_variance") return "Normalized variance";
  if (metric === "std") return "Standard deviation";
  if (metric === "iqr") return "IQR";
  if (metric === "mad") return "MAD";
  return "Absolute variance";
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function medianOf(values: number[]): number {
  if (!values.length) return 0;
  const sorted = values.slice().sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function buildFeatureHistogram(edges: number[], counts: number[], samples: number[]): { edges: number[]; counts: number[]; countScale: number } {
  if (edges.length === counts.length + 1 && counts.length > 0) {
    return { edges, counts, countScale: counts.reduce((sum, count) => sum + Math.max(0, count), 0) };
  }
  if (!samples.length) return { edges: [], counts: [], countScale: 0 };
  const bins = 32;
  const minimum = samples.reduce((value, sample) => Math.min(value, sample), samples[0]);
  const maximum = samples.reduce((value, sample) => Math.max(value, sample), samples[0]);
  const width = maximum > minimum ? (maximum - minimum) / bins : Math.max(Math.abs(minimum) * 0.01, 0.5);
  const start = maximum > minimum ? minimum : minimum - width / 2;
  const computedEdges = Array.from({ length: bins + 1 }, (_, index) => start + width * index);
  const computedCounts = Array.from({ length: bins }, () => 0);
  samples.forEach((sample) => {
    const index = Math.min(bins - 1, Math.max(0, Math.floor((sample - start) / width)));
    computedCounts[index] += 1;
  });
  return { edges: computedEdges, counts: computedCounts, countScale: samples.length };
}

function buildKde(samples: number[], edges: number[], totalCount: number): { x: number[]; y: number[] } {
  if (samples.length < 2) return { x: [], y: [] };
  const minimum = samples.reduce((value, sample) => Math.min(value, sample), samples[0]);
  const maximum = samples.reduce((value, sample) => Math.max(value, sample), samples[0]);
  const range = maximum - minimum;
  if (!(range > Number.EPSILON)) return { x: [], y: [] };
  const mean = samples.reduce((sum, sample) => sum + sample, 0) / samples.length;
  const variance = samples.reduce((sum, sample) => sum + (sample - mean) ** 2, 0) / samples.length;
  const standardDeviation = Math.sqrt(variance);
  const bandwidth = Math.max(1.06 * standardDeviation * samples.length ** -0.2, range / 80, Number.EPSILON);
  const gridCount = 80;
  const start = minimum - 3 * bandwidth;
  const end = maximum + 3 * bandwidth;
  const step = (end - start) / (gridCount - 1);
  const binWidth = edges.length > 1 ? Math.max(Math.abs(edges[1] - edges[0]), Number.EPSILON) : range / 32;
  const histogramCount = Number.isFinite(totalCount) && totalCount > 0 ? totalCount : samples.length;
  const normalizer = samples.length * bandwidth * Math.sqrt(2 * Math.PI);
  const x = Array.from({ length: gridCount }, (_, index) => start + index * step);
  const y = x.map((position) => {
    const density = samples.reduce((sum, sample) => {
      const z = (position - sample) / bandwidth;
      return sum + Math.exp(-0.5 * z * z);
    }, 0) / normalizer;
    return density * histogramCount * binWidth;
  });
  return { x, y };
}

function formatNumber(value: unknown): string {
  const number = finiteNumber(value);
  if (number === null) return "—";
  return Math.abs(number) >= 1000 ? number.toLocaleString(undefined, { maximumFractionDigits: 2 }) : number.toPrecision(5);
}

// Descriptor-trajectory result view.
//
// The module answers three questions in order: does the descriptor evolve
// continuously or in jumps (step distance + event threshold), where and how
// strong are the jumps (event markers, table, before/after highlight), and is
// the trajectory resampling a known region or drifting into a new one
// (reference distance, cumulative path length, PCA path).  Event detection
// always runs on descriptor-space step distances; the PCA panel is a
// visualization whose explained variance is printed on both axes.
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Button, Checkbox, InputNumber, Select, Slider, Space, Table, Typography } from "antd";
import { useT } from "../i18n";
import type { AnalysisPreview } from "../types/protocol";
import type { AnalysisPoint } from "./analysisPreview";
import type { AnalysisArrays } from "./analysisVisualizations";
import { HIGH_CONTRAST_COLORSCALE, Metrics, NoData, PlotFrame, layout, matrix, num, nums, sortedQuantile } from "./analysisChartKit";
import { decimate, METHOD_DEFAULT_SENSITIVITY, stepPercentiles, stepStats, trajectoryThreshold, EVENT_METHODS, type EventMethod, type StepStats } from "./trajectoryMath";

type Props = {
  preview: AnalysisPreview | null;
  arrays: AnalysisArrays;
  points: AnalysisPoint[];
  selectedIndices: number[];
  onSelect: (point: AnalysisPoint) => void;
};

type DisplayMode = "points" | "trajectory" | "events";
type ColorBy = "frame" | "step" | "event";

interface TrajectoryEvent {
  index: number;
  frame: number;
  time: number;
  step: number;
  ratio: number | null;
  percentile: number;
  reference: number;
  pc1: number | null;
  pc2: number | null;
  pcDisplacement: number | null;
}

/** The central 95% of the visible PCA cloud, in projection units. */
type ProjectionBox = { x: [number, number]; y: [number, number] };

export default function TrajectoryView({ preview, arrays, points, selectedIndices, onSelect }: Props) {
  const { t } = useT();
  // Convert the fetched payload once. Built in the render body, these views were
  // a new identity every render, so every useMemo below re-ran - dragging the
  // range slider re-sorted the whole series for the percentiles each time.
  const { time, frames, sampleIndices, series, reference, cumulative, coords } = useMemo(() => ({
    time: nums(arrays.time),
    frames: nums(arrays.frames).map(Math.round),
    sampleIndices: nums(arrays.sample_indices).map(Math.round),
    series: nums(arrays.step_distance),
    reference: nums(arrays.reference_distance),
    cumulative: nums(arrays.cumulative_distance),
    coords: matrix(arrays.coords),
  }), [arrays]);
  const count = Math.min(time.length, frames.length, series.length, coords.length);
  const steps = useMemo(() => series.slice(1, count), [count, series]);
  const ranks = useMemo(() => stepPercentiles(steps), [steps]);

  const initialMethod: EventMethod = EVENT_METHODS.includes(String(preview?.event_method) as EventMethod)
    ? (String(preview?.event_method) as EventMethod)
    : "mad";
  const [method, setMethod] = useState<EventMethod>(initialMethod);
  const [sensitivity, setSensitivity] = useState<number>(() => num(preview?.event_sensitivity) ?? METHOD_DEFAULT_SENSITIVITY[initialMethod]);
  const [range, setRange] = useState<[number, number] | null>(null);
  const [sampling, setSampling] = useState(10);
  const [colorBy, setColorBy] = useState<ColorBy>("frame");
  const [mode, setMode] = useState<DisplayMode>("trajectory");
  const [overlays, setOverlays] = useState<string[]>([]);
  const [focusMain, setFocusMain] = useState(false);
  const [selectedFrame, setSelectedFrame] = useState<number | null>(null);

  const stats = useMemo<StepStats>(() => {
    const provided = {
      median: num(preview?.median_step_distance),
      mad: num(preview?.step_mad),
      mean: num(preview?.mean_step_distance),
      std: num(preview?.step_std),
    };
    // Artifacts written before the robust statistics existed still render.
    return provided.median === null || provided.mad === null || provided.mean === null || provided.std === null
      ? stepStats(steps)
      : { median: provided.median, mad: provided.mad, mean: provided.mean, std: provided.std };
  }, [preview?.mean_step_distance, preview?.median_step_distance, preview?.step_mad, preview?.step_std, steps]);

  const threshold = trajectoryThreshold(steps, stats, method, sensitivity);
  const events = useMemo<TrajectoryEvent[]>(() => {
    const list: TrajectoryEvent[] = [];
    for (let index = 1; index < count; index += 1) {
      const value = series[index];
      if (!(value > threshold)) continue;
      const previous = coords[index - 1];
      const current = coords[index];
      const pcDisplacement = previous && current ? Math.hypot(current[0] - previous[0], current[1] - previous[1]) : null;
      list.push({
        index,
        frame: frames[index],
        time: time[index],
        step: value,
        ratio: threshold > 0 ? value / threshold : null,
        percentile: ranks[index - 1] ?? 1,
        reference: reference[index] ?? 0,
        pc1: current ? current[0] : null,
        pc2: current ? current[1] : null,
        pcDisplacement,
      });
    }
    return list;
  }, [count, coords, frames, ranks, reference, series, threshold, time]);

  const firstFrame = count ? frames[0] : 0;
  const lastFrame = count ? frames[count - 1] : 0;
  const [low, high] = range ?? [firstFrame, lastFrame];
  const visibleIndices = useMemo(() => {
    const indices: number[] = [];
    for (let index = 0; index < count; index += 1) {
      if (frames[index] >= low && frames[index] <= high) indices.push(index);
    }
    return indices;
  }, [count, frames, high, low]);
  const visibleEvents = useMemo(() => events.filter((event) => event.frame >= low && event.frame <= high), [events, high, low]);
  const selectedEvent = useMemo(
    () => visibleEvents.find((event) => event.frame === selectedFrame) ?? null,
    [selectedFrame, visibleEvents],
  );

  const windowHalf = Math.max(10, Math.round(Math.max(1, high - low) / 200));
  // Everything here walks the visible window. In the render body it also re-ran
  // on unrelated state - colour-by, display mode, which point is selected - and
  // the range slider reports on every mouse-move, so it re-runs on each pixel of
  // a drag. Measured on a node replay of the same maths at 100 000 frames:
  // 103 ms per pass before, 50 ms after, because the four quantiles were
  // sorting four copies of the same two projections and now sort two.
  const { box, outliers, pathLength, maxStep } = useMemo<{ box: ProjectionBox | null; outliers: number; pathLength: number; maxStep: number }>(() => {
    let walked = 0;
    let largest = 0;
    for (const index of visibleIndices) {
      walked += series[index];
      largest = Math.max(largest, series[index]);
    }
    if (!focusMain || !visibleIndices.length) return { box: null, outliers: 0, pathLength: walked, maxStep: largest };
    const xs = visibleIndices.map((index) => coords[index][0]).sort((left, right) => left - right);
    const ys = visibleIndices.map((index) => coords[index][1]).sort((left, right) => left - right);
    const focused: ProjectionBox = {
      x: [sortedQuantile(xs, 0.025) ?? 0, sortedQuantile(xs, 0.975) ?? 0],
      y: [sortedQuantile(ys, 0.025) ?? 0, sortedQuantile(ys, 0.975) ?? 0],
    };
    let outside = 0;
    for (let position = 0; position < visibleIndices.length; position += 1) {
      const [x, y] = coords[visibleIndices[position]];
      if (x < focused.x[0] || x > focused.x[1] || y < focused.y[0] || y > focused.y[1]) outside += 1;
    }
    return { box: focused, outliers: outside, pathLength: walked, maxStep: largest };
  }, [coords, focusMain, series, visibleIndices]);
  const timeUnit = String(preview?.time_unit ?? t("Frame"));
  const pc1Variance = num(preview?.pc1_explained_variance) ?? nums(arrays.pc_explained_variance)[0] ?? null;
  const pc2Variance = num(preview?.pc2_explained_variance) ?? nums(arrays.pc_explained_variance)[1] ?? null;
  const axisTitle = (axis: string, variance: number | null): string => variance === null ? axis : `${axis} (${(variance * 100).toFixed(1)}%)`;

  if (count < 2) return <NoData message={t("At least two trajectory points are needed for visualization.")} />;

  const showReference = overlays.includes("reference");
  const showCumulative = overlays.includes("cumulative");
  // Descriptor distances in standardized space are often far below 1, so small
  // values keep significant digits instead of rounding to 0.00.
  const metric = (value: number): string => {
    const magnitude = Math.abs(value);
    if (magnitude >= 1000) return value.toLocaleString("en-US", { useGrouping: false, maximumFractionDigits: 2 });
    if (magnitude >= 1) return value.toFixed(2);
    return value.toPrecision(3);
  };

  // Left panel: the detected peaks carry the event markers, so a red marker
  // always sits on the step distance that triggered it.
  const lineIndices = decimate(visibleIndices.length, sampling);
  const timelineData: Data[] = [
    {
      type: "scatter",
      mode: "lines",
      name: t("Step distance"),
      x: lineIndices.map((position) => time[visibleIndices[position]]),
      y: lineIndices.map((position) => series[visibleIndices[position]]),
      line: { color: "#0F6CBD", width: 1.5 },
      hovertemplate: `${t("Frame")} %{x}<br>${t("Step distance")}=%{y:.5g}<extra></extra>`,
    },
    ...(showReference ? [{
      type: "scatter" as const,
      mode: "lines" as const,
      name: t("Distance to reference"),
      x: lineIndices.map((position) => time[visibleIndices[position]]),
      y: lineIndices.map((position) => reference[visibleIndices[position]] ?? 0),
      yaxis: "y2",
      line: { color: "#D13438", width: 1.5, dash: "dot" as const },
      hovertemplate: `${t("Frame")} %{x}<br>${t("Distance to reference")}=%{y:.5g}<extra></extra>`,
    }] : []),
    ...(showCumulative ? [{
      type: "scatter" as const,
      mode: "lines" as const,
      name: t("Cumulative path length"),
      x: lineIndices.map((position) => time[visibleIndices[position]]),
      y: lineIndices.map((position) => cumulative[visibleIndices[position]] ?? 0),
      yaxis: "y2",
      line: { color: "#F7630C", width: 1.5 },
      hovertemplate: `${t("Frame")} %{x}<br>${t("Cumulative path length")}=%{y:.5g}<extra></extra>`,
    }] : []),
    {
      type: "scatter",
      mode: "markers",
      name: t("Transitions"),
      x: visibleEvents.map((event) => event.time),
      y: visibleEvents.map((event) => event.step),
      marker: { color: "#D13438", size: 9, symbol: "diamond" as const, line: { color: "#FFFFFF", width: 1 } },
      customdata: visibleEvents.map((event) => event.frame),
      hovertemplate: `${t("Frame")} %{customdata}<br>${t("Step distance")}=%{y:.5g}<extra></extra>`,
    },
  ];
  const xBounds: [number, number] = visibleIndices.length ? [time[visibleIndices[0]], time[visibleIndices[visibleIndices.length - 1]]] : [0, 1];
  const timelineRange: [number, number] | undefined = selectedEvent ? [selectedEvent.time - windowHalf, selectedEvent.time + windowHalf] : undefined;
  const timelineShapes = [
    { type: "line" as const, x0: xBounds[0], x1: xBounds[1], y0: threshold, y1: threshold, line: { color: "#D13438", width: 1.5, dash: "dash" as const } },
    ...(selectedEvent ? [
      { type: "rect" as const, x0: selectedEvent.time - windowHalf, x1: selectedEvent.time + windowHalf, y0: 0, y1: 1, yref: "paper" as const, fillcolor: "rgba(209,52,56,0.08)", line: { width: 0 } },
      { type: "line" as const, x0: selectedEvent.time, x1: selectedEvent.time, y0: 0, y1: 1, yref: "paper" as const, line: { color: "#D13438", width: 1, dash: "dot" as const } },
    ] : []),
  ];
  const timelineLayout = layout({
    xaxis: { title: { text: timeUnit }, range: timelineRange },
    yaxis: { title: { text: t("Step distance") } },
    ...(showReference || showCumulative ? { yaxis2: { title: { text: t("Distance from reference") }, overlaying: "y", side: "right", showgrid: false, tickmode: "auto" } } : {}),
    shapes: timelineShapes,
    annotations: [
      { x: xBounds[0], y: threshold, text: t("Threshold {value}", { value: metric(threshold) }), showarrow: false, xanchor: "left", yanchor: "bottom", font: { color: "#D13438" } },
      ...(selectedEvent ? [
        { x: selectedEvent.time - windowHalf, y: 0.97, yref: "paper" as const, text: t("before {frame}", { frame: selectedEvent.frame - 1 }), showarrow: false, xanchor: "left" as const, yanchor: "top" as const, font: { color: "#616161" } },
        { x: selectedEvent.time + windowHalf, y: 0.97, yref: "paper" as const, text: t("after {frame}", { frame: selectedEvent.frame }), showarrow: false, xanchor: "right" as const, yanchor: "top" as const, font: { color: "#616161" } },
      ] : []),
    ],
    legend: { orientation: "h", y: 1.14 },
  });

  // Right panel: PCA is the visualization, never the detector.
  const linePositions = mode === "points" ? [] : decimate(visibleIndices.length, sampling);
  const eventFlags = new Set(events.map((event) => event.index));
  const selectedSample = selectedIndices[0];
  const selectedSeriesIndex = selectedSample === undefined
    ? -1
    : sampleIndices.length === count ? sampleIndices.indexOf(selectedSample) : selectedSample;
  const selectedIndex = selectedSeriesIndex >= 0 && selectedSeriesIndex < count ? selectedSeriesIndex : -1;
  const eventColorscale: Array<[number, string]> = [[0, "#C8CDD4"], [1, "#D13438"]];
  const colorValues = visibleIndices.map((index) => colorBy === "frame" ? frames[index] : colorBy === "step" ? series[index] : eventFlags.has(index) ? 1 : 0);
  const pcData: Data[] = [
    ...(linePositions.length ? [{
      type: "scattergl" as const,
      mode: "lines" as const,
      name: t("Trajectory path"),
      x: linePositions.map((position) => coords[visibleIndices[position]][0]),
      y: linePositions.map((position) => coords[visibleIndices[position]][1]),
      line: { color: "#9AA5B1", width: 1 },
      hoverinfo: "skip" as const,
    }] : []),
    {
      type: "scattergl",
      mode: "markers",
      name: colorBy === "event" ? t("Transitions") : t("Frames"),
      x: visibleIndices.map((index) => coords[index][0]),
      y: visibleIndices.map((index) => coords[index][1]),
      customdata: visibleIndices.map((index) => [frames[index], series[index]]),
      marker: {
        size: mode === "events" ? visibleIndices.map((index) => eventFlags.has(index) ? 11 : 5) : 6,
        color: colorValues,
        colorscale: colorBy === "event" ? eventColorscale : HIGH_CONTRAST_COLORSCALE,
        showscale: colorBy !== "event",
        colorbar: { title: { text: colorBy === "frame" ? t("Frame") : colorBy === "step" ? t("Step distance") : "" }, thickness: 12 },
        opacity: mode === "events" ? 0.9 : 0.75,
      },
      hovertemplate: `${t("Frame")} %{customdata[0]}<br>PC1=%{x:.5g}<br>PC2=%{y:.5g}<br>${t("Step distance")}=%{customdata[1]:.5g}<extra></extra>`,
    },
    ...(colorBy === "event" ? [] : [{
      type: "scattergl" as const,
      mode: "markers" as const,
      name: t("Transitions"),
      x: visibleEvents.map((event) => event.pc1 ?? 0),
      y: visibleEvents.map((event) => event.pc2 ?? 0),
      customdata: visibleEvents.map((event) => event.frame),
      marker: { color: "#D13438", size: 10, symbol: "diamond" as const, line: { color: "#FFFFFF", width: 1 } },
      hovertemplate: `${t("Frame")} %{customdata}<br>${t("Transition")}<extra></extra>`,
    }]),
    ...(selectedIndex >= 0 ? [{
      type: "scattergl" as const,
      mode: "markers" as const,
      name: t("Selected sample"),
      x: [coords[selectedIndex][0]],
      y: [coords[selectedIndex][1]],
      marker: { color: "#0F6CBD", size: 14, symbol: "circle-open" as const, line: { color: "#0F6CBD", width: 2 } },
      hovertemplate: `${t("Frame")} ${frames[selectedIndex]}<extra></extra>`,
    }] : []),
    ...(selectedEvent && selectedEvent.pc1 !== null && selectedEvent.pc2 !== null ? [
      {
        type: "scattergl" as const,
        mode: "text+markers" as const,
        name: t("Selected transition"),
        x: [coords[selectedEvent.index - 1]?.[0] ?? selectedEvent.pc1, selectedEvent.pc1],
        y: [coords[selectedEvent.index - 1]?.[1] ?? selectedEvent.pc2, selectedEvent.pc2],
        text: [t("before"), t("after")],
        textposition: "top center" as const,
        textfont: { size: 10, color: "#A4262C" },
        marker: { color: ["#8764B8", "#D13438"], size: [9, 12], symbol: ["circle", "diamond"] as ("circle" | "diamond")[], line: { color: "#FFFFFF", width: 1 } },
        hovertemplate: `${t("Frame")} ${selectedEvent.frame}<extra></extra>`,
      },
      {
        type: "scattergl" as const,
        mode: "lines" as const,
        name: t("Selected transition"),
        showlegend: false,
        x: [coords[selectedEvent.index - 1]?.[0] ?? selectedEvent.pc1, selectedEvent.pc1],
        y: [coords[selectedEvent.index - 1]?.[1] ?? selectedEvent.pc2, selectedEvent.pc2],
        line: { color: "#D13438", width: 1.5, dash: "dot" as const },
        hoverinfo: "skip" as const,
      },
    ] : []),
  ];
  const pcLayout = layout({
    xaxis: { title: { text: axisTitle("PC1", pc1Variance) }, range: box?.x },
    yaxis: { title: { text: axisTitle("PC2", pc2Variance) }, range: box?.y },
    legend: { orientation: "h", y: 1.14 },
  });

  const byId = new Map(points.map((point) => [point.i, point]));
  const pickPoint = (position: number) => {
    const index = visibleIndices[position];
    if (index === undefined) return;
    const logical = sampleIndices[index] ?? index;
    onSelect({ ...(byId.get(logical) ?? {}), i: logical, frame: frames[index], x: coords[index][0], y: coords[index][1] });
  };

  return <>
    <Metrics values={[
      { k: t("Frames"), text: visibleIndices.length.toLocaleString("en-US", { useGrouping: false }) },
      { k: t("Total path length"), text: metric(pathLength) },
      { k: t("Max step distance"), text: metric(maxStep) },
      { k: t("Detected transitions"), text: String(visibleEvents.length) },
      { k: t("Event rate"), text: `${((visibleEvents.length / Math.max(1, visibleIndices.length - 1)) * 100).toFixed(2)}%` },
      // Which scale the step distances above were measured on: results written
      // before the backend recorded it omit the chip rather than guess.
      { k: t("Feature scale"), v: preview?.preprocess },
    ]} />
    <div className="property-method-strip">
      <Typography.Text>{t("Event detection runs on descriptor-space step distances; the PCA panel is a visualization only.")}</Typography.Text>
      <Typography.Text type="secondary">{t("Threshold and statistics are computed over the full trajectory; the frame range only filters what is displayed.")}</Typography.Text>
    </div>
    <div className="property-view-toolbar">
      <Typography.Text>{t("Frame range")}</Typography.Text>
      <Slider
        className="trajectory-range-slider"
        range
        min={firstFrame}
        max={lastFrame}
        value={[low, high]}
        onChange={(value) => setRange([value[0], value[1]])}
        tooltip={{ formatter: (value) => String(value) }}
      />
      <Typography.Text type="secondary">{t("{a} – {b} · {n} frames", { a: low, b: high, n: visibleIndices.length.toLocaleString("en-US", { useGrouping: false }) })}</Typography.Text>
      <Button size="small" onClick={() => setRange(null)} disabled={low === firstFrame && high === lastFrame}>{t("Full range")}</Button>
    </div>
    <div className="property-view-toolbar">
      <Space size={4}>
        <Typography.Text>{t("Trajectory sampling interval")}</Typography.Text>
        <Select aria-label={t("Trajectory sampling interval")} value={sampling} onChange={setSampling} style={{ width: 88 }} options={[1, 2, 5, 10, 20, 50].map((value) => ({ value, label: String(value) }))} />
      </Space>
      <Space size={4}>
        <Typography.Text>{t("Event detection")}</Typography.Text>
        <Select
          aria-label={t("Event detection")}
          value={method}
          style={{ width: 132 }}
          onChange={(value: EventMethod) => { setMethod(value); setSensitivity(METHOD_DEFAULT_SENSITIVITY[value]); }}
          options={[
            { value: "mad", label: t("MAD (robust)") },
            { value: "zscore", label: t("Z-score") },
            { value: "percentile", label: t("Percentile") },
          ]}
        />
      </Space>
      <Space size={4}>
        <Typography.Text>{t("Sensitivity")}</Typography.Text>
        <InputNumber
          aria-label={t("Sensitivity")}
          value={sensitivity}
          min={method === "percentile" ? 0.1 : 0.5}
          max={method === "percentile" ? 20 : 10}
          step={method === "percentile" ? 0.1 : 0.5}
          addonAfter={method === "percentile" ? "%" : "σ"}
          onChange={(value) => setSensitivity(value ?? METHOD_DEFAULT_SENSITIVITY[method])}
        />
      </Space>
      <Space size={4}>
        <Typography.Text>{t("Color by")}</Typography.Text>
        <Select aria-label={t("Color by")} value={colorBy} style={{ width: 132 }} onChange={setColorBy} options={[{ value: "frame", label: t("Frame") }, { value: "step", label: t("Step distance") }, { value: "event", label: t("Transition") }]} />
      </Space>
      <Space size={4}>
        <Typography.Text>{t("Display")}</Typography.Text>
        <Select aria-label={t("Display")} value={mode} style={{ width: 132 }} onChange={setMode} options={[{ value: "points", label: t("Points") }, { value: "trajectory", label: t("Trajectory") }, { value: "events", label: t("Events") }]} />
      </Space>
      <Checkbox.Group
        value={overlays}
        onChange={(values) => setOverlays(values.map(String))}
        options={[{ value: "reference", label: t("Distance to reference") }, { value: "cumulative", label: t("Cumulative path length") }]}
      />
    </div>
    <div className="property-insight" role="status">
      <Typography.Text strong>{t("Threshold = {value}", { value: metric(threshold) })}</Typography.Text>
      <Typography.Text>{t("Transitions = {count}", { count: visibleEvents.length })}</Typography.Text>
      <Typography.Text type="secondary">{t("median {median} · MAD {mad} · mean {mean} · σ {std}", { median: metric(stats.median), mad: metric(stats.mad), mean: metric(stats.mean), std: metric(stats.std) })}</Typography.Text>
    </div>
    <div className="analysis-chart-grid trajectory-chart-grid">
      <PlotFrame
        compact
        ariaLabel={t("Descriptor step distance versus frame")}
        data={timelineData}
        onClick={(index, curve) => { if (curve === timelineData.length - 1 && visibleEvents[index]) setSelectedFrame(visibleEvents[index].frame); }}
        layout={timelineLayout}
      />
      <div>
        <PlotFrame compact ariaLabel={t("Descriptor trajectory path in PCA space")} data={pcData} onClick={(position, curve) => { if (curve === (linePositions.length ? 1 : 0)) pickPoint(position); }} layout={pcLayout} />
        <Space size={8} wrap style={{ marginTop: 6 }}>
          <Button size="small" onClick={() => setFocusMain(!focusMain)}>{focusMain ? t("Show all frames") : t("Focus main population (95%)")}</Button>
          <Typography.Text type="secondary">
            {axisTitle("PC1", pc1Variance)} + {axisTitle("PC2", pc2Variance)} · {pc1Variance !== null && pc2Variance !== null ? t("explained variance {value}%", { value: ((pc1Variance + pc2Variance) * 100).toFixed(1) }) : ""}
          </Typography.Text>
          {box && <Typography.Text type="secondary">{t("{n} frames outside the central 95% region", { n: outliers })}</Typography.Text>}
        </Space>
      </div>
    </div>
    {selectedEvent && <div className="property-method-strip">
      <Typography.Text strong>{t("Frame {frame}", { frame: selectedEvent.frame })}</Typography.Text>
      <Typography.Text>{t("Step distance {value}", { value: metric(selectedEvent.step) })}</Typography.Text>
      <Typography.Text>{t("Threshold ratio {value}×", { value: selectedEvent.ratio === null ? "—" : selectedEvent.ratio.toFixed(2) })}</Typography.Text>
      <Typography.Text>{t("Percentile {value}", { value: `${(selectedEvent.percentile * 100).toFixed(1)}%` })}</Typography.Text>
      <Typography.Text>{t("PC displacement {value}", { value: selectedEvent.pcDisplacement === null ? "—" : selectedEvent.pcDisplacement.toFixed(2) })}</Typography.Text>
      <Typography.Text type="secondary">{t("Window {a} → {b}", { a: selectedEvent.frame - windowHalf, b: selectedEvent.frame + windowHalf })}</Typography.Text>
      <Button size="small" onClick={() => setSelectedFrame(null)}>{t("Reset zoom")}</Button>
    </div>}
    {visibleEvents.length ? <Table
      className="analysis-data-table"
      size="small"
      rowKey={(row) => String(row.frame)}
      dataSource={visibleEvents.slice().sort((left, right) => right.step - left.step)}
      pagination={{ pageSize: 8, hideOnSinglePage: true, showSizeChanger: false }}
      onRow={(row) => ({ onClick: () => setSelectedFrame(row.frame), className: row.frame === selectedFrame ? "trajectory-event-row-selected" : undefined })}
      columns={[
        { title: t("Frame"), dataIndex: "frame", key: "frame", width: 88, render: (value: number) => value.toLocaleString("en-US", { useGrouping: false }) },
        { title: t("Step distance"), dataIndex: "step", key: "step", align: "right", render: (value: number) => metric(value) },
        { title: t("Threshold ratio"), dataIndex: "ratio", key: "ratio", align: "right", render: (value: number | null) => value === null ? "—" : `${value.toFixed(2)}×` },
        { title: t("Percentile"), dataIndex: "percentile", key: "percentile", align: "right", render: (value: number) => `${(value * 100).toFixed(1)}%` },
        { title: "PC1", dataIndex: "pc1", key: "pc1", align: "right", render: (value: number | null) => value === null ? "—" : value.toFixed(2) },
        { title: "PC2", dataIndex: "pc2", key: "pc2", align: "right", render: (value: number | null) => value === null ? "—" : value.toFixed(2) },
        { title: t("Distance to reference"), dataIndex: "reference", key: "reference", align: "right", render: (value: number) => metric(value) },
      ]}
    /> : <NoData message={t("No transition exceeded the current threshold.")} />}
  </>;
}

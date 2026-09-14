// Shared ECharts histogram renderer (plot style per design doc §97–99).
// Height follows its flex container so Overview can fit the viewport exactly.
import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import type { Hist } from "../types/protocol";
import { createCartesianDataZoom } from "./chartInteraction";
import { useT } from "../i18n";

const GRID_LEFT = 58;
const GRID_RIGHT = 12;

export function formatHistogramTooltip(
  hist: Hist | null,
  point: { data: unknown; dataIndex: number } | undefined,
  xLabel: string,
  countLabel: string,
): string {
  if (!hist || !point) return "";
  const lo = hist.edges[point.dataIndex];
  const hi = hist.edges[point.dataIndex + 1];
  const count = Array.isArray(point.data) ? point.data[1] : point.data;
  return `${xLabel}: ${lo.toFixed(2)} – ${hi.toFixed(2)}<br/>${countLabel}: <b>${count}</b>`;
}

export default function Histogram({
  title,
  unit,
  hist,
}: {
  title: string;
  unit?: string;
  hist: Hist | null;
}) {
  const { t } = useT();
  const option = useMemo(
    () => {
      const xLabel = unit ? `${title} (${unit})` : title;
      const countLabel = t("count");
      return {
        grid: { left: GRID_LEFT, right: GRID_RIGHT, top: 8, bottom: 38 },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          formatter: (params: unknown) => {
            return formatHistogramTooltip(hist, (params as { data: unknown; dataIndex: number }[])[0], xLabel, countLabel);
          },
        },
        xAxis: {
          type: "value",
          name: xLabel,
          nameLocation: "middle",
          nameGap: 26,
          nameTextStyle: { fontSize: 10, color: "#616161" },
          min: hist?.edges[0],
          max: hist?.edges[hist.edges.length - 1],
          axisLabel: { fontSize: 11, color: "#616161" },
          axisLine: { lineStyle: { color: "#E1E4E8" } },
          splitLine: { show: false },
        },
        yAxis: {
          type: "value",
          name: countLabel,
          nameLocation: "middle",
          nameGap: 42,
          nameTextStyle: { fontSize: 10, color: "#616161" },
          axisLabel: { fontSize: 11, color: "#616161" },
          splitLine: { lineStyle: { color: "#F0F1F3" } },
        },
        dataZoom: createCartesianDataZoom(),
        series: [
          {
            type: "bar",
            // value-axis pairs so bars land inside [edges0, edgesN]
            data:
              hist?.counts.map((c, i) => [
                (hist.edges[i] + hist.edges[i + 1]) / 2,
                c,
              ]) ?? [],
            itemStyle: { color: "#0F6CBD", borderRadius: [1, 1, 0, 0] },
            barCategoryGap: "8%",
          },
        ],
        animation: false,
      };
    },
    [hist, t, title, unit],
  );

  return (
    <div style={{ height: "100%", minHeight: 0, display: "flex", flexDirection: "column" }}>
      <div style={{ marginLeft: GRID_LEFT, marginRight: GRID_RIGHT, display: "flex", justifyContent: "center", alignItems: "center", fontSize: 12.5, fontWeight: 600, color: "#242424", padding: "0 2px 2px", textAlign: "center" }}>
        {unit ? `${title} (${unit})` : title}
      </div>
      <ReactECharts
        option={option}
        style={{ flex: 1, minHeight: 0, width: "100%" }}
        notMerge
        lazyUpdate
      />
    </div>
  );
}

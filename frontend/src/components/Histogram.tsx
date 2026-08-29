// Shared ECharts histogram renderer (plot style per design doc §97–99).
import ReactECharts from "echarts-for-react";
import type { Hist } from "../types/protocol";

export default function Histogram({
  title,
  unit,
  hist,
  color = "#0F6CBD",
}: {
  title: string;
  unit?: string;
  hist: Hist | null;
  color?: string;
}) {
  const option = {
    grid: { left: 46, right: 12, top: 26, bottom: 26 },
    title: {
      text: unit ? `${title} (${unit})` : title,
      left: 0,
      top: 0,
      textStyle: { fontSize: 12, fontWeight: 600, color: "#242424" },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const p = (params as { data: number; dataIndex: number }[])[0];
        if (!hist) return "";
        const lo = hist.edges[p.dataIndex];
        const hi = hist.edges[p.dataIndex + 1];
        return `${lo.toFixed(2)} – ${hi.toFixed(2)}<br/>count: <b>${p.data}</b>`;
      },
    },
    xAxis: {
      type: "value",
      min: hist?.edges[0],
      max: hist?.edges[hist.edges.length - 1],
      axisLabel: { fontSize: 11, color: "#616161" },
      axisLine: { lineStyle: { color: "#E1E4E8" } },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      axisLabel: { fontSize: 11, color: "#616161" },
      splitLine: { lineStyle: { color: "#F0F1F3" } },
    },
    series: [
      {
        type: "bar",
        data: hist?.counts ?? [],
        itemStyle: { color, borderRadius: [1, 1, 0, 0] },
        barCategoryGap: "8%",
      },
    ],
    animation: false,
  };
  return <ReactECharts option={option} style={{ height: 180 }} notMerge lazyUpdate />;
}

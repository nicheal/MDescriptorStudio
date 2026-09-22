/** UI charts are ECharts by convention (settings, histograms, drill-downs).
 *
 * Scientific scatter/projection views use ScientificPlot instead. Keeping the
 * ECharts wrapper here makes the renderer choice explicit at the call site.
 */
import type { ComponentProps } from "react";
import ReactECharts from "echarts-for-react";
import { CHART_COLORS, CHART_FONT_FAMILY, SCIENTIFIC_PALETTE } from "./chartTheme";

type UIChartProps = ComponentProps<typeof ReactECharts>;

const SCIENTIFIC_ECHARTS_THEME = {
  color: [...SCIENTIFIC_PALETTE],
  textStyle: {
    color: CHART_COLORS.text,
    fontFamily: CHART_FONT_FAMILY,
  },
  title: {
    textStyle: {
      color: CHART_COLORS.text,
      fontFamily: CHART_FONT_FAMILY,
    },
  },
  legend: {
    textStyle: {
      color: CHART_COLORS.secondaryText,
      fontFamily: CHART_FONT_FAMILY,
    },
  },
  tooltip: {
    backgroundColor: CHART_COLORS.paper,
    borderColor: CHART_COLORS.tooltipBorder,
    borderWidth: 1,
    confine: true,
    textStyle: {
      color: CHART_COLORS.text,
      fontFamily: CHART_FONT_FAMILY,
    },
  },
  categoryAxis: {
    axisLine: { lineStyle: { color: CHART_COLORS.axis } },
    axisLabel: { color: CHART_COLORS.secondaryText },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { lineStyle: { color: CHART_COLORS.axis } },
    axisLabel: { color: CHART_COLORS.secondaryText },
    splitLine: { lineStyle: { color: CHART_COLORS.grid } },
  },
};

export default function UIChart(props: UIChartProps) {
  return <ReactECharts theme={SCIENTIFIC_ECHARTS_THEME} {...props} />;
}

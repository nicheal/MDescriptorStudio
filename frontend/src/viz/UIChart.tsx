/** UI charts are ECharts by convention (settings, histograms, drill-downs).
 *
 * Scientific scatter/projection views use ScientificPlot instead. Keeping the
 * ECharts wrapper here makes the renderer choice explicit at the call site.
 */
import type { ComponentProps } from "react";
import ReactECharts from "echarts-for-react";

type UIChartProps = ComponentProps<typeof ReactECharts>;

export default function UIChart(props: UIChartProps) {
  return <ReactECharts {...props} />;
}

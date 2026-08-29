// M5: run history, PCA plot-first page, heatmap, selected-sample inspector.
import { Empty, Typography } from "antd";

export default function Results() {
  return (
    <Empty
      style={{ marginTop: 120 }}
      description={<Typography.Text type="secondary">Results & analysis land in M4/M5.</Typography.Text>}
    />
  );
}

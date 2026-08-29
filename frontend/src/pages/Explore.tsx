// M2: 3Dmol viewer + frame navigation + Structure Inspector + Atom Table.
// Placeholder until the Explore milestone lands.
import { Empty, Typography } from "antd";
import { activeDataset, useWorkspace } from "../stores/workspace";

export default function Explore() {
  const d = activeDataset(useWorkspace());
  return (
    <Empty
      style={{ marginTop: 120 }}
      description={
        <Typography.Text type="secondary">
          Structure Browser lands in M2{d ? ` (${d.name})` : ""}.
        </Typography.Text>
      }
    />
  );
}

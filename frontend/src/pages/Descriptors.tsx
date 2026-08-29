// M3: registry list + describe inspector + schema-driven form + scope + submit.
import { Empty, Typography } from "antd";
import { activeDataset, useWorkspace } from "../stores/workspace";

export default function Descriptors() {
  const d = activeDataset(useWorkspace());
  return (
    <Empty
      style={{ marginTop: 120 }}
      description={
        <Typography.Text type="secondary">
          Descriptor workbench lands in M3{d ? ` (${d.name})` : ""}.
        </Typography.Text>
      }
    />
  );
}

import { Badge, Space, Tag, Tooltip, Typography } from "antd";
import { CheckCircleFilled } from "@ant-design/icons";
import { Database16Regular } from "@fluentui/react-icons";
import { activeDataset, useWorkspace } from "../../stores/workspace";

export default function ContextBar() {
  const st = useWorkspace();
  const d = activeDataset(st);
  if (!d) {
    return (
      <div style={{ padding: "10px 20px", background: "#FFFFFF", borderBottom: "1px solid #EAECF0" }}>
        <Typography.Text type="secondary">No active dataset</Typography.Text>
      </div>
    );
  }
  const has = (v: boolean | undefined) => v === true;
  return (
    <div
      style={{
        padding: "10px 20px",
        background: "#FFFFFF",
        borderBottom: "1px solid #EAECF0",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 16,
      }}
    >
      <Space size={12} wrap>
        <Database16Regular style={{ color: "#0F6CBD" }} />
        <Typography.Text strong style={{ fontSize: 16 }}>
          {d.name}
        </Typography.Text>
        <Tag>{d.format.toUpperCase()}</Tag>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {d.number_of_frames.toLocaleString()} structures
        </Typography.Text>
        <Space size={2}>
          {d.elements.map((el) => (
            <Tag key={el} style={{ fontSize: 12 }}>
              {el}
            </Tag>
          ))}
        </Space>
        <Tag>PBC {d.periodicity.flags.join("/")}</Tag>
        <Space size={6}>
          <Tooltip title="Energy available">
            <Badge
              count={<CheckCircleFilled style={{ color: has(d.properties.energy?.per_structure) ? "#107C10" : "#D9D9D9" }} />}
            />
          </Tooltip>
          <Typography.Text style={{ fontSize: 12, color: "#616161" }}>Energy</Typography.Text>
          <Badge
            count={<CheckCircleFilled style={{ color: has(d.properties.forces?.per_atom) ? "#107C10" : "#D9D9D9" }} />}
          />
          <Typography.Text style={{ fontSize: 12, color: "#616161" }}>Force</Typography.Text>
          <Badge
            count={<CheckCircleFilled style={{ color: has(d.properties.virial?.per_structure) ? "#107C10" : "#D9D9D9" }} />}
          />
          <Typography.Text style={{ fontSize: 12, color: "#616161" }}>Virial</Typography.Text>
        </Space>
      </Space>
      <Typography.Text code style={{ fontSize: 12, color: "#8A8A8A" }}>
        {d.source_path}
      </Typography.Text>
    </div>
  );
}

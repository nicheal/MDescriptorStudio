// Jobs entry point: top-right badge + right Drawer (ADR-6: no Jobs tab).
// Drawer content (progress/cancel) fills in M4; badge + shell exist from M0.
import { useState } from "react";
import { Badge, Button, Drawer, Empty } from "antd";
import { Clock16Regular, Settings16Regular } from "@fluentui/react-icons";
import { useWorkspace } from "../../stores/workspace";

export default function JobsBadge() {
  const { runningJobs } = useWorkspace();
  const [open, setOpen] = useState(false);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <Button type="text" onClick={() => setOpen(true)}>
        <Badge count={runningJobs} size="small" offset={[2, -2]}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Clock16Regular /> Jobs
          </span>
        </Badge>
      </Button>
      <Button type="text" icon={<Settings16Regular />} disabled title="Settings (M5)" />
      <Drawer
        title="JOBS"
        placement="right"
        width={360}
        open={open}
        onClose={() => setOpen(false)}
      >
        <Empty description="No jobs yet" style={{ marginTop: 48 }} />
      </Drawer>
    </div>
  );
}

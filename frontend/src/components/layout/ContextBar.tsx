// Context header (UI.png layout pass): large dataset title, chip row with
// property availability pills, source path + overflow menu on the right.
import { useState } from "react";
import { App as AntApp, Button, Dropdown, Space, Tag, Tooltip, Typography } from "antd";
import { CheckmarkCircle16Filled, MoreHorizontal16Regular } from "@fluentui/react-icons";
import { ipc } from "../../ipc/client";
import { RenameDatasetModal, useDatasetDelete } from "../datasetActions";
import { useActiveDataset, useWorkspace } from "../../stores/workspace";
import { formatLabel } from "../../util/format";
import { describeError } from "../../util/errors";
import { useT } from "../../i18n";

function PropertyChip({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        border: "1px solid #E1E4E8",
        borderRadius: 6,
        padding: "2px 10px",
        fontSize: 12,
        color: "#242424",
        background: "#FFFFFF",
      }}
    >
      {label}
      <CheckmarkCircle16Filled style={{ color: ok ? "#107C10" : "#D9D9D9", fontSize: 12 }} />
    </span>
  );
}

export default function ContextBar() {
  const { message } = AntApp.useApp();
  const confirmDelete = useDatasetDelete();
  const [renameOpen, setRenameOpen] = useState(false);
  const { t } = useT();
  const d = useActiveDataset();
  if (!d) {
    return (
      <div style={{ padding: "10px 24px", background: "#FFFFFF", borderBottom: "1px solid #EAECF0" }}>
        <Typography.Text type="secondary">{t("No active dataset")}</Typography.Text>
      </div>
    );
  }
  const has = (v: boolean | undefined) => v === true;
  const pbcLabel = d.periodicity.flags.join("") || "—";
  const copyPath = async () => {
    try {
      await navigator.clipboard.writeText(d.source_path);
      message.success(t("Path copied"));
    } catch {
      message.error(t("Could not access the clipboard"));
    }
  };
  return (
    <div
      style={{
        padding: "10px 24px 8px",
        background: "#FFFFFF",
        borderBottom: "1px solid #EAECF0",
      }}
    >
      <div style={{ fontSize: 17, fontWeight: 600, color: "#242424", marginBottom: 6, lineHeight: 1.3 }}>
        {d.name}
        {!d.cache_valid && (
          <Typography.Text type="warning" style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
            {t("changed on disk")}
          </Typography.Text>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Space size={8} wrap style={{ flex: 1, minWidth: 0 }}>
          <Tag style={{ marginRight: 0 }}>{formatLabel(d.format)}</Tag>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {t("{n} structures", { n: d.number_of_frames.toLocaleString() })}
          </Typography.Text>
          <Space size={2}>
            {d.elements.map((el) => (
              <Tag key={el} style={{ fontSize: 12, marginRight: 0 }}>
                {el}
              </Tag>
            ))}
          </Space>
          <Tag style={{ marginRight: 0 }}>PBC {pbcLabel}</Tag>
          <Space size={6} style={{ marginLeft: 4 }}>
            <PropertyChip label={t("Energy")} ok={has(d.properties.energy?.per_structure)} />
            <PropertyChip label={t("Force")} ok={has(d.properties.forces?.per_atom)} />
            <PropertyChip label={t("Virial")} ok={has(d.properties.virial?.per_structure)} />
          </Space>
        </Space>
        <Space size={2} style={{ flex: "0 0 auto" }}>
          <Tooltip title={d.source_path}>
            <Typography.Text
              code
              style={{ fontSize: 12, color: "#8A8A8A", maxWidth: 300, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {d.source_path}
            </Typography.Text>
          </Tooltip>
          <Dropdown
            menu={{
              items: [
                { key: "copy-path", label: t("Copy dataset path") },
                { key: "refresh-stats", label: t("Refresh statistics") },
                { type: "divider" },
                { key: "rename", label: t("Rename dataset") },
                { key: "delete", label: t("Delete dataset"), danger: true },
              ],
              onClick: ({ key }) => {
                if (key === "copy-path") void copyPath();
                if (key === "rename") setRenameOpen(true);
                if (key === "delete") confirmDelete(d);
                if (key === "refresh-stats") {
                  void ipc
                    .request("dataset.statistics", { id: d.id })
                    .then(() => {
                      useWorkspace.getState().bumpStatsTick();
                      message.info(t("Statistics refreshed"));
                    })
                    // describeError, not a hand-built `${code}: ${message}`: that
                    // is the format the shared helper exists to own, and the two
                    // fields it adds are the error_id that joins this complaint to
                    // a backend log line. It also survives a rejection that is not
                    // an ErrorFrame, which used to render "undefined: undefined".
                    .catch((e) => message.error(describeError(e, "DATASET", "could not refresh statistics")));
                }
              },
            }}
            trigger={["click"]}
          >
            <Button type="text" size="small" icon={<MoreHorizontal16Regular />} />
          </Dropdown>
        </Space>
      </div>
      <RenameDatasetModal dataset={d} open={renameOpen} onClose={() => setRenameOpen(false)} />
    </div>
  );
}

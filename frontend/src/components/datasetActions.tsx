// Shared dataset actions used by the ContextBar overflow menu and the Sidebar
// dataset list: rename modal + delete confirmation. Deleting removes the
// registry entry, cached statistics and descriptor runs/results; source files
// on disk are never touched.
import { useCallback, useEffect, useState } from "react";
import { App as AntApp, Input, Modal, Typography } from "antd";
import { ipc } from "../ipc/client";
import { refetchDatasets, useWorkspace } from "../stores/workspace";
import { useT } from "../i18n";
import type { DatasetMeta } from "../types/protocol";
import { describeError } from "../util/errors";

export function RenameDatasetModal({
  dataset,
  open,
  onClose,
}: {
  dataset: DatasetMeta | null;
  open: boolean;
  onClose: () => void;
}) {
  const { message } = AntApp.useApp();
  const { t } = useT();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) setValue(dataset?.name ?? "");
  }, [open, dataset]);

  const submit = async () => {
    if (!dataset || busy) return;
    const name = value.trim();
    if (!name || name === dataset.name) {
      onClose();
      return;
    }
    setBusy(true);
    try {
      const meta = await ipc.request<DatasetMeta>("dataset.rename", {
        id: dataset.id,
        name,
      });
      const st = useWorkspace.getState();
      st.setDatasets(st.datasets.map((d) => (d.id === meta.id ? { ...d, ...meta } : d)));
      message.success(t("Dataset renamed"));
      onClose();
    } catch (e) {
      const err = e as { code: string; message: string };
      message.error(describeError(err, "DATASET"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={t("Rename dataset")}
      open={open}
      onCancel={onClose}
      onOk={() => void submit()}
      okText={t("Rename")}
      confirmLoading={busy}
      okButtonProps={{ disabled: !value.trim() }}
      destroyOnHidden
    >
      <Input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onPressEnter={() => void submit()}
        maxLength={200}
        placeholder={t("Dataset name")}
        autoFocus
      />
      <Typography.Text type="secondary" style={{ fontSize: 12, display: "block", marginTop: 8 }}>
        {t("Only the display name changes; the source files are not touched.")}
      </Typography.Text>
    </Modal>
  );
}

/** Opens a confirm dialog and removes the dataset via `dataset.remove`. */
export function useDatasetDelete() {
  const { message, modal } = AntApp.useApp();
  const { t } = useT();
  return useCallback(
    (d: DatasetMeta) => {
      modal.confirm({
        title: t("Delete dataset?"),
        content: (
          <Typography.Text>
            {t("“{name}” will be removed from the workspace together with its cached statistics and descriptor runs/results. The source files at", { name: d.name })}{" "}
            <Typography.Text code>{d.source_path}</Typography.Text>{" "}
            {t("are not deleted.")}
          </Typography.Text>
        ),
        okText: t("Delete"),
        okType: "danger",
        autoFocusButton: "cancel",
        onOk: async () => {
          try {
            await ipc.request("dataset.remove", { id: d.id });
            message.success(t("Dataset deleted"));
            await refetchDatasets();
          } catch (e) {
            const err = e as { code: string; message: string };
            message.error(describeError(err, "DATASET"));
            throw e; // keep the dialog open when removal fails
          }
        },
      });
    },
    [modal, message, t],
  );
}

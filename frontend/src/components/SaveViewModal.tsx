// Shared "save dataset view" modal with two explicit operations:
// - save: store the selected frames as-is (a bookmark of the selection);
// - subtract (purify): store a base scope (full dataset or an existing view)
//   minus the selected frames — the stepwise purification chain (subtract
//   duplicates, then non-physical, then whatever a later analysis flags).
// Source data is never copied; only frame indices are stored.
import { useEffect, useMemo, useState } from "react";
import { App as AntApp, Input, Modal, Radio, Select, Typography } from "antd";
import { ipc } from "../ipc/client";
import { useT } from "../i18n";
import type { DatasetView } from "../types/protocol";

export default function SaveViewModal({ open, onClose, datasetId, frames, totalFrames, source, defaultName = "", onSaved }: {
  open: boolean;
  onClose: () => void;
  datasetId: string;
  /** Dataset frames the current step selected — stored directly, or subtracted from the base scope. */
  frames: number[];
  /** Frame count of the full dataset, enabling "Full dataset" as a subtract base. */
  totalFrames?: number;
  /** Provenance merged into the view's filter spec (e.g. {source, check}). */
  source: Record<string, unknown>;
  defaultName?: string;
  onSaved?: (view: DatasetView) => void;
}) {
  const { message } = AntApp.useApp();
  const { t } = useT();
  const [views, setViews] = useState<DatasetView[]>([]);
  const [mode, setMode] = useState<"save" | "subtract">("save");
  const [baseViewId, setBaseViewId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(defaultName);
    setMode("save");
    setBaseViewId(null);
    setViews([]);
    ipc.request<DatasetView[]>("dataset.view.list", {})
      .then((all) => setViews(all.filter((view) => view.dataset_id === datasetId && !view.stale)))
      .catch(() => setViews([]));
  }, [open, datasetId, defaultName]);

  const baseView = views.find((view) => view.id === baseViewId) ?? null;
  const resultFrames = useMemo(() => {
    if (mode === "save") return frames;
    const drop = new Set(frames);
    const base = baseView ? baseView.frame_indices : Array.from({ length: totalFrames ?? 0 }, (_, index) => index);
    return base.filter((index) => !drop.has(index));
  }, [mode, baseView, frames, totalFrames]);

  const save = async () => {
    if (!name.trim() || resultFrames.length === 0) return;
    setBusy(true);
    try {
      const view = await ipc.request<DatasetView>("dataset.view.create", {
        dataset_id: datasetId,
        name: name.trim(),
        role: "filtered",
        indices: resultFrames,
        filter: mode === "subtract"
          ? { ...source, op: "subtract", base_view_id: baseView?.id ?? null }
          : { ...source, op: "select" },
      });
      window.dispatchEvent(new Event("dataset-views-changed"));
      message.success(t("Dataset view saved"));
      onSaved?.(view);
      onClose();
    } catch (e) {
      const err = e as { code?: string; message?: string };
      message.error(`${err.code ?? "DATASET_VIEW"}: ${err.message ?? t("Could not save dataset view")}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={t("Save dataset view")}
      open={open}
      okText={t("Save")}
      confirmLoading={busy}
      okButtonProps={{ disabled: !name.trim() || resultFrames.length === 0 }}
      onOk={() => void save()}
      onCancel={onClose}
      destroyOnClose
    >
      <Input
        autoFocus
        value={name}
        placeholder={t("View name")}
        onChange={(event) => setName(event.target.value)}
        onPressEnter={() => void save()}
      />
      <Radio.Group
        value={mode}
        onChange={(event) => setMode(event.target.value)}
        style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 8 }}
      >
        <Radio value="save">{t("Save selection as view")}</Radio>
        <Radio value="subtract">{t("Subtract selection from base scope")}</Radio>
      </Radio.Group>
      {mode === "subtract" && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
          <Typography.Text>{t("Base scope")}</Typography.Text>
          <Select
            style={{ minWidth: 220 }}
            value={baseViewId ?? "__full__"}
            options={[
              ...(totalFrames ? [{ value: "__full__", label: `${t("Full dataset")} · ${totalFrames.toLocaleString()}` }] : []),
              ...views.map((view) => ({ value: view.id, label: `${view.name} · ${view.number_of_frames.toLocaleString()}` })),
            ]}
            onChange={(value) => setBaseViewId(value === "__full__" ? null : value)}
          />
        </div>
      )}
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
        {mode === "save"
          ? t("The view stores these {n} frame indices without copying source data.", { n: frames.length })
          : resultFrames.length === 0
            ? t("The result is empty — the selection covers the whole base scope.")
            : t("Subtracting the selected {m} frames from {base} leaves {n} frames to store.", { m: frames.length.toLocaleString(), base: baseView?.name ?? t("Full dataset"), n: resultFrames.length.toLocaleString() })}
      </Typography.Paragraph>
    </Modal>
  );
}

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { App as AntApp, Button, Dropdown, Empty, Input, InputNumber, Modal, Space, Tooltip, Typography } from "antd";
import {
  Add16Regular,
  ChevronLeft16Regular,
  ChevronRight16Regular,
  Document16Regular,
  FolderOpen16Regular,
  BranchFork16Regular,
  MoreHorizontal16Regular,
  Search16Regular,
} from "@fluentui/react-icons";
import { open as openDialog, save as saveDialog } from "@tauri-apps/plugin-dialog";
import { ipc } from "../../ipc/client";
import { RenameDatasetModal, useDatasetDelete } from "../datasetActions";
import { refetchDatasets, useWorkspace } from "../../stores/workspace";
import { formatLabel, formatSize } from "../../util/format";
import { trackJob, watchJob } from "../../stores/jobs";
import { useT } from "../../i18n";
import type { DatasetMeta, DatasetView } from "../../types/protocol";
import { describeError } from "../../util/errors";

type SplitTarget = { dataset: DatasetMeta; view?: DatasetView };

export default function Sidebar() {
  const { message } = AntApp.useApp();
  const confirmDelete = useDatasetDelete();
  const { t } = useT();
  const [renameTarget, setRenameTarget] = useState<DatasetMeta | null>(null);
  const datasets = useWorkspace((s) => s.datasets);
  const setActiveDataset = useWorkspace((s) => s.setActiveDataset);
  const [collapsed, setCollapsed] = useState(false);
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [views, setViews] = useState<DatasetView[]>([]);
  const [splitTarget, setSplitTarget] = useState<SplitTarget | null>(null);
  const [splitPrefix, setSplitPrefix] = useState("");
  const [trainPercent, setTrainPercent] = useState(80);
  const [validationPercent, setValidationPercent] = useState(10);
  const [splitSeed, setSplitSeed] = useState(42);
  const [renameViewTarget, setRenameViewTarget] = useState<DatasetView | null>(null);
  const [renameViewName, setRenameViewName] = useState("");

  const refreshViews = useCallback(async () => {
    try {
      setViews(await ipc.request<DatasetView[]>("dataset.view.list", {}));
    } catch (error) {
      const err = error as { message?: string };
      message.error(err.message ?? t("Could not load dataset views"));
    }
  }, [message, t]);

  useEffect(() => { void refreshViews(); }, [refreshViews, datasets]);
  useEffect(() => {
    const refresh = () => void refreshViews();
    window.addEventListener("dataset-views-changed", refresh);
    return () => window.removeEventListener("dataset-views-changed", refresh);
  }, [refreshViews]);

  const filtered = useMemo(
    () =>
      datasets.filter(
        (d) => !query || d.name.toLowerCase().includes(query.toLowerCase()) || views.some((view) => view.dataset_id === d.id && view.name.toLowerCase().includes(query.toLowerCase())),
      ),
    [datasets, query, views],
  );

  const openSplit = useCallback((dataset: DatasetMeta, view?: DatasetView) => {
    setSplitTarget({ dataset, view });
    setSplitPrefix(view?.name ?? dataset.name);
    setTrainPercent(80);
    setValidationPercent(10);
    setSplitSeed(42);
  }, []);

  const split = useCallback(async () => {
    if (!splitTarget) return;
    const { dataset, view } = splitTarget;
    setBusy(true);
    try {
      await ipc.request("dataset.view.split", {
        dataset_id: dataset.id,
        ...(view ? { view_id: view.id } : {}),
        name_prefix: splitPrefix.trim(),
        seed: splitSeed,
        train_ratio: trainPercent / 100,
        validation_ratio: validationPercent / 100,
      });
      await refreshViews();
      window.dispatchEvent(new Event("dataset-views-changed"));
      setSplitTarget(null);
      message.success(t("Train, validation, and test views created"));
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(describeError(err, "DATASET_VIEW", t("Could not create split")));
    } finally {
      setBusy(false);
    }
  }, [message, refreshViews, splitPrefix, splitSeed, splitTarget, t, trainPercent, validationPercent]);

  const renameView = useCallback(async () => {
    if (!renameViewTarget || !renameViewName.trim()) return;
    setBusy(true);
    try {
      await ipc.request("dataset.view.rename", { id: renameViewTarget.id, name: renameViewName.trim() });
      setRenameViewTarget(null);
      await refreshViews();
      window.dispatchEvent(new Event("dataset-views-changed"));
      message.success(t("Dataset view renamed"));
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(describeError(err, "DATASET_VIEW", t("Could not rename dataset view")));
    } finally {
      setBusy(false);
    }
  }, [message, refreshViews, renameViewName, renameViewTarget, t]);

  const materialize = useCallback(async (dataset: DatasetMeta, view: DatasetView) => {
    const destination = await saveDialog({
      title: t("Materialize dataset view"),
      defaultPath: dataset.format === "extxyz" ? `${view.name}.extxyz` : view.name,
      ...(dataset.format === "extxyz" ? { filters: [{ name: "extxyz", extensions: ["xyz", "extxyz"] }] } : {}),
    });
    if (!destination) return;
    try {
      const submitted = await ipc.request<{ job_id: string }>("dataset.view.materialize", { view_id: view.id, dest_path: destination });
      trackJob(submitted.job_id, "dataset.view.materialize", dataset.id);
      const written = await watchJob(submitted.job_id) as { status: string; result?: { path?: string; name?: string; lineage?: Record<string, unknown> }; error?: { message?: string } | null };
      if (written.status !== "COMPLETED" || !written.result?.path) throw new Error(written.error?.message ?? written.status);
      const registered = await ipc.request<{ job_id: string }>("dataset.register", { path: written.result.path, name: written.result.name ?? view.name, lineage: written.result.lineage });
      trackJob(registered.job_id, "dataset.register");
      const completed = await watchJob(registered.job_id);
      if (completed.status !== "COMPLETED") throw new Error(completed.error?.message ?? completed.status);
      await refetchDatasets();
      await refreshViews();
      message.success(t("Dataset view materialized and registered"));
    } catch (error) {
      const err = error as { code?: string; message?: string };
      message.error(describeError(err, "DATASET_VIEW", t("Could not materialize dataset view")));
    }
  }, [message, refreshViews, t]);

  const removeView = useCallback((view: DatasetView) => {
    Modal.confirm({
      title: t("Delete dataset view?"),
      content: t("The source dataset and materialized datasets are not deleted."),
      okText: t("Delete"),
      okButtonProps: { danger: true },
      cancelText: t("Cancel"),
      onOk: async () => {
        await ipc.request("dataset.view.remove", { id: view.id });
        await refreshViews();
        window.dispatchEvent(new Event("dataset-views-changed"));
      },
    });
  }, [refreshViews, t]);

  const totalBytes = useMemo(
    () => datasets.reduce((s, d) => s + (d.file_size ?? 0), 0),
    [datasets],
  );

  const register = async () => {
    if (!path.trim()) return;
    setBusy(true);
    try {
      const r = await ipc.request<{ job_id: string | null; cache: unknown }>(
        "dataset.register",
        { path: path.trim(), name: name.trim() || undefined },
      );
      if (r.job_id) {
        setAdding(false);
        setPath("");
        setName("");
        message.info(t("Registering dataset…"));
        trackJob(r.job_id, "dataset.register");
        void watchJob(r.job_id)
          .then(async (done) => {
            if (done.status !== "COMPLETED") {
              message.error(t("Register failed: {message}", { message: done.error?.message ?? done.status }));
              return;
            }
            try {
              await refreshAfterRegister();
              message.success(t("Dataset added"));
            } catch {
              message.warning(t("Dataset registered, but the list could not be refreshed"));
            }
          })
          .catch((error) => {
            const err = error as { code?: string; message?: string };
            message.error(t("Register failed: {message}", { message: describeError(err, "DATASET") }));
          });
      }
    } catch (e) {
      const err = e as { code: string; message: string };
      message.error(describeError(err, "DATASET"));
    } finally {
      setBusy(false);
    }
  };

  const refreshAfterRegister = async () => {
    const list = await ipc.request<DatasetMeta[]>("dataset.list");
    useWorkspace.getState().setDatasets(list);
    const first = list[0];
    if (!useWorkspace.getState().activeDatasetId && first) setActiveDataset(first.id);
  };

  const browse = async (directory: boolean) => {
    try {
      const selected = await openDialog(
        directory
          ? { directory: true, multiple: false, title: t("Select DeepMD dataset folder") }
          : {
              multiple: false,
              title: t("Select extxyz dataset file"),
              filters: [{ name: "extxyz", extensions: ["xyz", "extxyz"] }],
            },
      );
      const p = Array.isArray(selected) ? selected[0] : selected;
      if (p) setPath(p);
    } catch {
      message.error(t("Could not open the file dialog"));
    }
  };

  if (collapsed) {
    return (
      <div
        style={{
          width: 44,
          flex: "0 0 44px",
          borderRight: "1px solid #E1E4E8",
          background: "#FAFAFA",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          padding: "12px 0",
          gap: 8,
        }}
      >
        <Tooltip title={t("Show datasets")} placement="right">
          <Button
            type="text"
            size="small"
            icon={<ChevronRight16Regular />}
            onClick={() => setCollapsed(false)}
          />
        </Tooltip>
      </div>
    );
  }

  return (
    <div
      style={{
        width: 240,
        flex: "0 0 240px",
        borderRight: "1px solid #E1E4E8",
        background: "#FAFAFA",
        display: "flex",
        flexDirection: "column",
        padding: "12px 12px",
        gap: 8,
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Typography.Text strong style={{ fontSize: 12, color: "#616161", letterSpacing: 1 }}>
          {t("DATASETS")}
        </Typography.Text>
        <Tooltip title={t("Collapse sidebar")}>
          <Button
            type="text"
            size="small"
            icon={<ChevronLeft16Regular />}
            onClick={() => setCollapsed(true)}
          />
        </Tooltip>
      </div>
      <Input
        size="small"
        prefix={<Search16Regular style={{ color: "#8A8A8A" }} />}
        placeholder={t("Search datasets...")}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <Button
        icon={<Add16Regular />}
        onClick={() => setAdding(true)}
        style={{ justifyContent: "flex-start" }}
      >
        {t("Add Dataset")}
      </Button>
      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        {filtered.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<span style={{ fontSize: 12 }}>{t("No datasets")}</span>}
            style={{ marginTop: 24 }}
          />
        ) : (
          filtered.map((d) => <Fragment key={d.id}>
            <DatasetItem d={d} parentName={datasets.find((item) => item.id === d.lineage?.parent_dataset_id)?.name} onRename={setRenameTarget} onDelete={confirmDelete} onSplit={() => openSplit(d)} />
            {views.filter((view) => view.dataset_id === d.id).map((view) => <DatasetViewItem key={view.id} view={view} onRename={() => { setRenameViewTarget(view); setRenameViewName(view.name); }} onSplit={() => openSplit(d, view)} onMaterialize={() => void materialize(d, view)} onDelete={() => removeView(view)} />)}
          </Fragment>)
        )}
      </div>
      <div style={{ borderTop: "1px solid #EAECF0", paddingTop: 8 }}>
        <Typography.Text strong style={{ fontSize: 11, color: "#616161", letterSpacing: 0.5 }}>
          {t("Dataset Storage")}
        </Typography.Text>
        <div style={{ fontSize: 12, color: "#242424", fontVariantNumeric: "tabular-nums", marginTop: 2 }}>
          {datasets.length > 0 && totalBytes > 0
            ? datasets.length > 1
              ? t("{size} · {n} datasets", { size: formatSize(totalBytes), n: datasets.length })
              : t("{size} · {n} dataset", { size: formatSize(totalBytes), n: datasets.length })
            : "—"}
        </div>
      </div>
      <Modal
        title={t("Add Dataset")}
        open={adding}
        onCancel={() => setAdding(false)}
        onOk={register}
        okText={t("Register")}
        confirmLoading={busy}
        okButtonProps={{ disabled: !path.trim() }}
      >
        <Space direction="vertical" style={{ width: "100%" }} size={12}>
          <Space.Compact style={{ width: "100%" }}>
            <Input
              placeholder={t("D:\\datasets\\GaAs  (DeepMD directory or .xyz file)")}
              value={path}
              readOnly
              onChange={(e) => setPath(e.target.value)}
            />
            <Button
              icon={<FolderOpen16Regular />}
              onClick={() => void browse(true)}
              title={t("Browse for a DeepMD dataset folder")}
            />
            <Button
              icon={<Document16Regular />}
              onClick={() => void browse(false)}
              title={t("Browse for an .xyz / .extxyz file")}
            />
          </Space.Compact>
          <Input
            placeholder={t("Display name (optional)")}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t("Format is auto-detected (DeepMD raw directory / extxyz file). The source data is registered read-only, never copied.")}
          </Typography.Text>
        </Space>
      </Modal>
      <RenameDatasetModal
        dataset={renameTarget}
        open={renameTarget !== null}
        onClose={() => setRenameTarget(null)}
      />
      <Modal
        title={t("Create train/validation/test split")}
        open={splitTarget !== null}
        okText={t("Create split")}
        confirmLoading={busy}
        okButtonProps={{ disabled: !splitPrefix.trim() || trainPercent <= 0 || validationPercent <= 0 || trainPercent + validationPercent >= 100 }}
        onOk={() => void split()}
        onCancel={() => setSplitTarget(null)}
      >
        <Space direction="vertical" size={10} style={{ width: "100%" }}>
          <Input value={splitPrefix} placeholder={t("Split name prefix")} onChange={(event) => setSplitPrefix(event.target.value)} />
          <Space wrap>
            <Typography.Text>{t("Train %")}</Typography.Text>
            <InputNumber min={1} max={98} value={trainPercent} onChange={(value) => setTrainPercent(value ?? 80)} />
            <Typography.Text>{t("Validation %")}</Typography.Text>
            <InputNumber min={1} max={98} value={validationPercent} onChange={(value) => setValidationPercent(value ?? 10)} />
            <Typography.Text>{t("Test %: {n}", { n: Math.max(0, 100 - trainPercent - validationPercent) })}</Typography.Text>
          </Space>
          <Space>
            <Typography.Text>{t("Random seed")}</Typography.Text>
            <InputNumber value={splitSeed} onChange={(value) => setSplitSeed(value ?? 42)} />
          </Space>
          <Typography.Text type="secondary">{t("The split is deterministic, disjoint, and covers the selected scope exactly once.")}</Typography.Text>
        </Space>
      </Modal>
      <Modal
        title={t("Rename dataset view")}
        open={renameViewTarget !== null}
        okText={t("Rename")}
        confirmLoading={busy}
        okButtonProps={{ disabled: !renameViewName.trim() }}
        onOk={() => void renameView()}
        onCancel={() => setRenameViewTarget(null)}
      >
        <Input autoFocus value={renameViewName} onChange={(event) => setRenameViewName(event.target.value)} onPressEnter={() => void renameView()} />
      </Modal>
    </div>
  );
}

function DatasetItem({
  d,
  parentName,
  onRename,
  onDelete,
  onSplit,
}: {
  d: DatasetMeta;
  parentName?: string;
  onRename: (d: DatasetMeta) => void;
  onDelete: (d: DatasetMeta) => void;
  onSplit: () => void;
}) {
  const setActiveDataset = useWorkspace((s) => s.setActiveDataset);
  const activeDatasetId = useWorkspace((s) => s.activeDatasetId);
  const { t } = useT();
  const active = d.id === activeDatasetId;
  return (
    <div
      className="dataset-item"
      onClick={() => setActiveDataset(d.id)}
      style={{
        padding: "8px 10px",
        marginBottom: 4,
        borderRadius: 6,
        cursor: "pointer",
        background: active ? "#EBF3FC" : "transparent",
        borderLeft: active ? "3px solid #0F6CBD" : "3px solid transparent",
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 2 }}>
        <div
          style={{
            flex: 1,
            minWidth: 0,
            fontSize: 13,
            fontWeight: active ? 600 : 400,
            color: active ? "#0F6CBD" : "#242424",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {d.name}
          {!d.cache_valid && (
            <Typography.Text type="warning" style={{ fontSize: 11, marginLeft: 6 }}>
              {t("changed")}
            </Typography.Text>
          )}
        </div>
        <Dropdown
          menu={{
            items: [
              { key: "rename", label: t("Rename") },
              { key: "split", label: t("Create train/validation/test split") },
              { key: "delete", label: t("Delete"), danger: true },
            ],
            onClick: ({ key, domEvent }) => {
              domEvent.stopPropagation();
              if (key === "rename") onRename(d);
              if (key === "split") onSplit();
              if (key === "delete") onDelete(d);
            },
          }}
          trigger={["click"]}
        >
          <Button
            className="dataset-item-actions"
            type="text"
            size="small"
            icon={<MoreHorizontal16Regular />}
            onClick={(e) => e.stopPropagation()}
            style={{ height: 20, width: 20, minWidth: 0, marginRight: -4, flex: "0 0 auto" }}
          />
        </Dropdown>
      </div>
      <div style={{ fontSize: 11, color: "#616161" }}>
        {formatLabel(d.format)} · {t("{n} structures", { n: d.number_of_frames.toLocaleString("en-US", { useGrouping: false }) })}
      </div>
      <div style={{ fontSize: 11, color: "#8A8A8A" }}>
        {d.elements.join(" · ")} · PBC {d.periodicity.flags.join("") || "—"}
      </div>
      {d.lineage && <div style={{ fontSize: 10, color: "#616161", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {t("Derived from {name}", { name: parentName ?? d.lineage.parent_dataset_id ?? t("removed source") })}
      </div>}
    </div>
  );
}

function DatasetViewItem({ view, onRename, onSplit, onMaterialize, onDelete }: { view: DatasetView; onRename: () => void; onSplit: () => void; onMaterialize: () => void; onDelete: () => void }) {
  const { t } = useT();
  return <div className="dataset-view-item">
    <BranchFork16Regular aria-hidden />
    <div className="dataset-view-copy">
      <div className="dataset-view-name">{view.name}</div>
      <div className="dataset-view-meta">{t("{n} structures", { n: view.number_of_frames.toLocaleString("en-US", { useGrouping: false }) })}{view.role ? ` · ${view.role}` : ""}{view.stale ? ` · ${t("stale")}` : ""}</div>
    </div>
    <Dropdown menu={{ items: [
      { key: "rename", label: t("Rename") },
      { key: "split", label: t("Create train/validation/test split"), disabled: view.stale || view.number_of_frames < 3 },
      { key: "materialize", label: t("Materialize as dataset"), disabled: view.stale },
      { type: "divider" },
      { key: "delete", label: t("Delete view"), danger: true },
    ], onClick: ({ key }) => { if (key === "rename") onRename(); if (key === "split") onSplit(); if (key === "materialize") onMaterialize(); if (key === "delete") onDelete(); } }} trigger={["click"]}>
      <Button className="dataset-item-actions" type="text" size="small" aria-label={t("Dataset view actions")} icon={<MoreHorizontal16Regular />} />
    </Dropdown>
  </div>;
}

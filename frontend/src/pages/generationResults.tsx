// Results phase: descriptor-space map (gray = original, blue = evaluated,
// orange = accepted, with a generation slider), local-environment discovery,
// and the actions that turn accepted structures into a new dataset.
import { useEffect, useMemo, useState } from "react";
import { App as AntApp, Button, Card, Col, Empty, Modal, Row, Select, Slider, Space, Spin, Statistic, Tag, Typography } from "antd";
import { Database24Regular } from "@fluentui/react-icons";
import { save as saveDialog } from "@tauri-apps/plugin-dialog";
import type { PlotMouseEvent } from "plotly.js";
import { ipc } from "../ipc/client";
import ScientificPlot from "../viz/ScientificPlot";
import { GENERATION_GEOMETRY_REJECTION_LABELS, GENERATION_OBJECTIVE_LABELS, GENERATION_OPTIMIZER_LABELS, GENERATION_STOP_REASON_LABELS } from "../features/generation/labels";
import type { GenerationPca, GenerationPreview, GenerationRow } from "../features/generation/types";
import type { DatasetView, FramePayload } from "../types/protocol";
import StructurePreview from "../components/StructurePreview";
import { refetchDatasets, useWorkspace } from "../stores/workspace";
import { jobStatusLabel, trackJob, watchJob } from "../stores/jobs";
import { useT } from "../i18n";

function metric(label: string, value: string | number) {
  return <Statistic title={label} value={value} valueStyle={{ fontSize: 22 }} />;
}

type GenerationPoint = { kind: "original" | "evaluated"; index: number };

function SummaryCard({ row }: { row: GenerationRow }) {
  const { t, tr } = useT();
  const preview: GenerationPreview | null = row.preview ?? null;
  const rounds = preview?.rounds ?? [];
  const latest = rounds[rounds.length - 1];
  const geometryReasonTotals = rounds.reduce<Record<string, number>>((acc, round) => {
    for (const [reason, count] of Object.entries(round.rejected_geometry_by_reason ?? {})) {
      acc[reason] = (acc[reason] ?? 0) + count;
    }
    return acc;
  }, {});
  return (
    <Card size="small" title={t("Run summary")}>
      <div style={{ display: "flex", gap: 32, flexWrap: "wrap" }}>
        {metric(t("Status"), jobStatusLabel(tr, row.status))}
        {metric(t("Stopped by"), preview?.stopped_by ? t(GENERATION_STOP_REASON_LABELS[preview.stopped_by] ?? preview.stopped_by) : "—")}
        {metric(t("Target descriptor"), row.descriptor_name ?? "—")}
        {metric(t("Descriptor evaluations"), preview?.evaluations ?? row.evaluations)}
        {metric(t("Accepted structures"), preview?.accepted ?? row.accepted_count)}
        {metric(t("Rounds"), rounds.length)}
        {metric(t("Coverage radius"), latest ? latest.coverage_radius.toFixed(3) : "—")}
      </div>
      {Object.keys(geometryReasonTotals).length > 0 && (
        <div style={{ marginTop: 12, color: "#616161", fontSize: 12 }}>
          {t("Geometry rejection reasons")}: {Object.entries(geometryReasonTotals).map(([reason, count]) =>
            `${t(GENERATION_GEOMETRY_REJECTION_LABELS[reason] ?? reason)} ${count}`,
          ).join(" · ")}
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <Tag color={row.status === "COMPLETED" ? "green" : row.status === "CANCELLED" ? "orange" : "red"}>
          {t(GENERATION_OPTIMIZER_LABELS[row.optimizer as keyof typeof GENERATION_OPTIMIZER_LABELS] ?? row.optimizer)} · {t(GENERATION_OBJECTIVE_LABELS[row.objective as keyof typeof GENERATION_OBJECTIVE_LABELS] ?? row.objective)}
        </Tag>
        <span style={{ fontSize: 12, color: "#9AA0A6" }}>{row.id}</span>
      </div>
    </Card>
  );
}

function DescriptorSpaceMap({
  row,
  pca,
  error,
  onSelectPoint,
}: {
  row: GenerationRow;
  pca: GenerationPca | null;
  error: boolean;
  onSelectPoint: (point: GenerationPoint) => void;
}) {
  const { t } = useT();
  const [maxGeneration, setMaxGeneration] = useState<number>(0);

  useEffect(() => {
    setMaxGeneration(Math.max(0, ...(pca?.evaluated_generation ?? [])));
  }, [row.id, pca]);

  const { visibleEvaluated, visibleAccepted } = useMemo(() => {
    if (!pca) return { visibleEvaluated: null as { point: [number, number]; index: number }[] | null, visibleAccepted: null as { point: [number, number]; index: number }[] | null };
    const evaluated: { point: [number, number]; index: number }[] = [];
    const accepted: { point: [number, number]; index: number }[] = [];
    pca.evaluated.forEach((point, i) => {
      if ((pca.evaluated_generation[i] ?? 0) > maxGeneration) return;
      if (pca.evaluated_accepted[i]) accepted.push({ point, index: i });
      else evaluated.push({ point, index: i });
    });
    return { visibleEvaluated: evaluated, visibleAccepted: accepted };
  }, [pca, maxGeneration]);

  const ranges = useMemo(() => {
    if (!pca) return null;
    let xMin = Infinity;
    let xMax = -Infinity;
    let yMin = Infinity;
    let yMax = -Infinity;
    const include = ([x, y]: [number, number]) => {
      if (Number.isFinite(x) && Number.isFinite(y)) {
        xMin = Math.min(xMin, x);
        xMax = Math.max(xMax, x);
        yMin = Math.min(yMin, y);
        yMax = Math.max(yMax, y);
      }
    };
    pca.original.forEach(include);
    pca.evaluated.forEach(include);
    if (!Number.isFinite(xMin)) return null;
    const pad = (min: number, max: number) => {
      const margin = Math.max((max - min) * 0.04, Math.abs(max) * 0.01, 0.01);
      return [min - margin, max + margin] as [number, number];
    };
    return { x: pad(xMin, xMax), y: pad(yMin, yMax) };
  }, [pca]);
  const originalTrace = useMemo(
    () => ({
      x: pca?.original.map((p) => p[0]) ?? [],
      y: pca?.original.map((p) => p[1]) ?? [],
      type: "scatter" as const,
      mode: "markers" as const,
      marker: { color: "#C8C8C8", size: 4, opacity: 0.55 },
      name: t("Original dataset"),
      showlegend: true,
    }),
    [pca, t],
  );
  const plotData = useMemo(
    () => [
      originalTrace,
      {
        x: visibleEvaluated?.length ? visibleEvaluated.map(({ point }) => point[0]) : [null],
        y: visibleEvaluated?.length ? visibleEvaluated.map(({ point }) => point[1]) : [null],
        type: "scatter" as const,
        mode: "markers" as const,
        marker: { color: "#2899F5", size: 5, opacity: 0.6 },
        name: t("Evaluated candidates"),
        showlegend: true,
      },
      {
        x: visibleAccepted?.length ? visibleAccepted.map(({ point }) => point[0]) : [null],
        y: visibleAccepted?.length ? visibleAccepted.map(({ point }) => point[1]) : [null],
        type: "scatter" as const,
        mode: "markers" as const,
        marker: { color: "#E8A33D", size: 7, opacity: 0.9 },
        name: t("Accepted structures"),
        showlegend: true,
      },
    ],
    [originalTrace, t, visibleAccepted, visibleEvaluated],
  );

  if (error) {
    return (
      <Card size="small" title={t("Descriptor space")}>
        <div style={{ fontSize: 13, color: "#616161" }}>{t("Descriptor-space map is unavailable for this run.")}</div>
      </Card>
    );
  }
  if (!pca) {
    return (
      <Card size="small" title={t("Descriptor space")}>
        <div style={{ fontSize: 13, color: "#9AA0A6" }}>{t("Loading descriptor-space map…")}</div>
      </Card>
    );
  }
  const maxSlider = Math.max(1, ...pca.evaluated_generation);

  return (
    <Card size="small" title={t("Descriptor space")}>
      <ScientificPlot
        style={{ width: "100%", height: 420 }}
        data={plotData}
        onClick={(event: Readonly<PlotMouseEvent>) => {
          const point = event.points?.[0];
          if (!point || typeof point.pointIndex !== "number") return;
          if (point.curveNumber === 0) {
            const index = pca.original_frames[point.pointIndex];
            if (Number.isInteger(index) && index >= 0) onSelectPoint({ kind: "original", index });
          } else if (point.curveNumber === 1) {
            const selected = visibleEvaluated?.[point.pointIndex];
            if (selected) onSelectPoint({ kind: "evaluated", index: selected.index });
          } else if (point.curveNumber === 2) {
            const selected = visibleAccepted?.[point.pointIndex];
            if (selected) onSelectPoint({ kind: "evaluated", index: selected.index });
          }
        }}
        layout={{
          uirevision: row.id,
          datarevision: maxGeneration,
          transition: { duration: 0 },
          margin: { t: 8, r: 16, b: 44, l: 56 },
          xaxis: { title: { text: pca.x_label }, range: ranges?.x, autorange: false },
          yaxis: { title: { text: pca.y_label }, range: ranges?.y, autorange: false },
          legend: { orientation: "h", y: -0.18 },
        }}
        config={{ displayModeBar: false, responsive: true }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <span style={{ fontSize: 13, color: "#616161", whiteSpace: "nowrap" }}>
          {t("Generation {generation}", { generation: maxGeneration })}
        </span>
        <Slider
          style={{ flex: 1 }}
          min={0}
          max={maxSlider}
          value={maxGeneration}
          onChange={(v) => setMaxGeneration(v)}
          tooltip={{ formatter: (v) => `${t("Generation")} ${v}` }}
        />
        <span style={{ fontSize: 12, color: "#9AA0A6" }}>{t("Drag to replay the expansion")}</span>
      </div>
    </Card>
  );
}

function LocalEnvironmentCard({ row, pca }: { row: GenerationRow; pca: GenerationPca | null }) {
  const { t } = useT();
  const preview: GenerationPreview | null = row.preview ?? null;
  const fallbackAccepted = preview?.accepted ?? row.accepted_count;
  const discovery = pca?.discovery ?? null;
  const generated = discovery?.generated_environments ?? fallbackAccepted;
  const novel = discovery?.novel_environments ?? 0;
  const fraction = generated > 0 ? (novel / generated) * 100 : 0;
  return (
    <Card size="small" title={t("LOCAL ENVIRONMENT DISCOVERY")}>
      <div style={{ display: "flex", gap: 32, flexWrap: "wrap" }}>
        {metric(t("Original environments"), discovery?.original_environments ?? "—")}
        {metric(t("Generated environments"), generated)}
        {metric(t("Novel environments"), novel)}
        {metric(t("Novel fraction"), `${fraction.toFixed(1)} %`)}
      </div>
      <div style={{ marginTop: 8, fontSize: 12, color: "#9AA0A6" }}>
        {t("Novel environments are local descriptor rows farther than the novelty threshold from every archived environment.")}
      </div>
    </Card>
  );
}

export default function GenerationResultsPanel({ row, onMaterialized }: { row: GenerationRow; onMaterialized: () => void }) {
  const { t } = useT();
  const { message } = AntApp.useApp();
  const datasets = useWorkspace((s) => s.datasets);
  const [busy, setBusy] = useState(false);
  const [materializedPath, setMaterializedPath] = useState<string | null>(null);
  const [pca, setPca] = useState<GenerationPca | null>(null);
  const [pcaError, setPcaError] = useState(false);
  const [selectedPoint, setSelectedPoint] = useState<GenerationPoint | null>(null);
  const [structureFrame, setStructureFrame] = useState<FramePayload | null>(null);
  const [structureBusy, setStructureBusy] = useState(false);
  const [structureError, setStructureError] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [targetDatasetId, setTargetDatasetId] = useState<string | null>(null);
  const writableDatasets = useMemo(() => datasets.filter((dataset) => dataset.format === "extxyz"), [datasets]);

  useEffect(() => {
    if (!addOpen) return;
    setTargetDatasetId((current) =>
      writableDatasets.some((dataset) => dataset.id === current)
        ? current
        : writableDatasets.find((dataset) => dataset.id === row.dataset_id)?.id ?? writableDatasets[0]?.id ?? null,
    );
  }, [addOpen, row.dataset_id, writableDatasets]);

  useEffect(() => {
    const canReadArtifact =
      (row.status === "COMPLETED" || row.status === "CANCELLED") &&
      row.artifact_complete === true &&
      row.accepted_count > 0;
    setPca(null);
    setPcaError(!canReadArtifact);
    setSelectedPoint(null);
    setStructureFrame(null);
    if (!canReadArtifact) return;
    let cancelled = false;
    ipc
      .request<GenerationPca>("generation.pca", { id: row.id })
      .then((result) => !cancelled && setPca(result))
      .catch(() => !cancelled && setPcaError(true));
    return () => {
      cancelled = true;
    };
  }, [row.id, row.status, row.artifact_complete, row.accepted_count]);

  useEffect(() => {
    setStructureFrame(null);
    setStructureError(false);
    if (!selectedPoint) {
      setStructureBusy(false);
      return;
    }
    let cancelled = false;
    setStructureBusy(true);
    const request = selectedPoint.kind === "original"
      ? ipc.request<FramePayload>("dataset.frame", { id: row.dataset_id, index: selectedPoint.index })
      : ipc.request<FramePayload>("generation.structure", { id: row.id, index: selectedPoint.index });
    request
      .then((frame) => !cancelled && setStructureFrame(frame))
      .catch(() => !cancelled && setStructureError(true))
      .finally(() => !cancelled && setStructureBusy(false));
    return () => {
      cancelled = true;
    };
  }, [row.id, row.dataset_id, selectedPoint]);

  const exportAccepted = async () => {
    setBusy(true);
    try {
      const target = await saveDialog({
        title: t("Export accepted structures"),
        defaultPath: row.id + "_accepted.extxyz",
        filters: [{ name: "extxyz", extensions: ["xyz", "extxyz"] }],
      });
      if (!target) return;
      const submitted = await ipc.request<{ job_id: string }>("generation.export", {
        id: row.id,
        what: "accepted",
        path: target,
      });
      trackJob(submitted.job_id, "generation.export", row.dataset_id);
      const written = await watchJob(submitted.job_id);
      const payload = written.result as unknown as { path?: string } | null;
      if (written.status !== "COMPLETED" || !payload?.path) {
        throw new Error(written.error?.message ?? written.status);
      }
      message.success(t("Export written to {path}", { path: payload.path }));
    } catch (error) {
      console.error(error);
      message.error(t("Export failed: {message}", { message: error instanceof Error ? error.message : String(error) }));
    } finally {
      setBusy(false);
    }
  };

  const materialize = async () => {
    setBusy(true);
    try {
      const suggested = row.id + "_accepted.extxyz";
      const target = await saveDialog({
        title: t("Destination path for the expanded dataset (.extxyz)"),
        defaultPath: suggested,
        filters: [{ name: "extxyz", extensions: ["xyz", "extxyz"] }],
      });
      if (!target) return;

      interface MaterializeResult {
        path: string;
        name: string;
        lineage: Record<string, unknown>;
      }
      const submitted = await ipc.request<{ job_id: string }>("generation.materialize", {
        id: row.id,
        path: target,
        name: row.id + "_expanded",
      });
      trackJob(submitted.job_id, "generation.materialize", row.dataset_id);
      const written = await watchJob(submitted.job_id);
      const payload = written.result as unknown as MaterializeResult | null;
      if (written.status !== "COMPLETED" || !payload?.path) {
        throw new Error(written.error?.message ?? written.status);
      }
      setMaterializedPath(payload.path);
      const reg = await ipc.request<{ job_id: string }>("dataset.register", {
        path: payload.path,
        name: payload.name,
        lineage: payload.lineage,
      });
      trackJob(reg.job_id, "dataset.register");
      const registered = await watchJob(reg.job_id);
      if (registered.status !== "COMPLETED") {
        throw new Error(registered.error?.message ?? registered.status);
      }
      let refreshed = true;
      try {
        await refetchDatasets();
      } catch {
        refreshed = false;
      }
      window.dispatchEvent(new Event("dataset-views-changed"));
      onMaterialized();
      if (refreshed) message.success(t("Expanded dataset registered with lineage"));
      else message.warning(t("Expanded dataset was registered, but the workspace list could not be refreshed"));
    } catch (error) {
      console.error(error);
      message.error(t("Materialize failed: {message}", { message: error instanceof Error ? error.message : String(error) }));
    } finally {
      setBusy(false);
    }
  };

  const addToExisting = async () => {
    if (!targetDatasetId) return;
    setBusy(true);
    let appendFinished = false;
    try {
      const submitted = await ipc.request<{ job_id: string }>("generation.add_to_dataset", {
        id: row.id,
        dataset_id: targetDatasetId,
      });
      trackJob(submitted.job_id, "generation.add_to_dataset", targetDatasetId);
      const appended = await watchJob(submitted.job_id);
      const payload = appended.result as unknown as {
        appended_frame_start?: number;
        appended_structures?: number;
        scan_job_id?: string | null;
      } | null;
      if (appended.status !== "COMPLETED" || !payload) {
        throw new Error(appended.error?.message ?? appended.status);
      }
      appendFinished = true;

      let rescanned = false;
      let scanJobId = payload.scan_job_id ?? null;
      if (!scanJobId) {
        try {
          scanJobId = (await ipc.request<{ job_id: string }>("dataset.rescan", { id: targetDatasetId })).job_id;
          trackJob(scanJobId, "dataset.statistics", targetDatasetId);
        } catch {
          scanJobId = null;
        }
      }
      if (scanJobId) {
        const scan = await watchJob(scanJobId);
        rescanned = scan.status === "COMPLETED";
        if (!rescanned) throw new Error(scan.error?.message ?? scan.status);
      }
      if (!rescanned) throw new Error(t("Dataset rescan did not start"));

      const frameStart = payload.appended_frame_start;
      const count = payload.appended_structures ?? row.accepted_count;
      if (!Number.isInteger(frameStart) || frameStart! < 0 || !Number.isInteger(count) || count <= 0) {
        throw new Error(t("The appended frame range is unavailable"));
      }
      const views = await ipc.request<DatasetView[]>("dataset.view.list", { dataset_id: targetDatasetId });
      const existingNames = new Set(views.map((view) => view.name));
      const baseName = t("Generated structures from {id}", { id: row.id });
      let viewName = baseName;
      for (let suffix = 2; existingNames.has(viewName); suffix += 1) viewName = `${baseName} (${suffix})`;
      const view = await ipc.request<DatasetView>("dataset.view.create", {
        dataset_id: targetDatasetId,
        name: viewName,
        role: "selection",
        filter: { type: "generation", generation_run_id: row.id },
        indices: Array.from({ length: count }, (_, index) => frameStart! + index),
      });
      try {
        await refetchDatasets();
      } catch {
        // The view has been persisted; the workspace list can refresh on the next navigation.
      }
      window.dispatchEvent(new Event("dataset-views-changed"));
      setAddOpen(false);
      onMaterialized();
      message.success(t("Added {count} structures and created view {name}", { count, name: view.name }));
    } catch (error) {
      console.error(error);
      const detail = error instanceof Error ? error.message : String(error);
      if (appendFinished) {
        try {
          await refetchDatasets();
        } catch {
          // The appended file remains valid even if refreshing the list fails.
        }
        window.dispatchEvent(new Event("dataset-views-changed"));
        setAddOpen(false);
        onMaterialized();
        message.warning(t("Structures were added, but the dataset refresh or new view failed: {message}", { message: detail }));
      } else {
        message.error(t("Add to dataset failed: {message}", { message: detail }));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <SummaryCard row={row} />
      <Row gutter={[12, 12]} align="stretch">
        <Col xs={24} xl={16}>
          <DescriptorSpaceMap row={row} pca={pca} error={pcaError} onSelectPoint={setSelectedPoint} />
        </Col>
        <Col xs={24} xl={8}>
          <Card size="small" title={t("Structure preview")} style={{ height: "100%" }}>
            {selectedPoint && (
              <Typography.Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
                {selectedPoint.kind === "original"
                  ? t("Original frame {index}", { index: selectedPoint.index })
                  : t("Generated candidate {index}", { index: selectedPoint.index })}
              </Typography.Text>
            )}
            {structureFrame ? (
              <StructurePreview frame={structureFrame} height={360} />
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  structureBusy ? <Spin size="small" /> : structureError
                    ? t("Could not load the selected structure.")
                    : t("Click a point in the descriptor-space map to preview its structure.")
                }
              />
            )}
          </Card>
        </Col>
      </Row>
      <LocalEnvironmentCard row={row} pca={pca} />
      <Card
        size="small"
        title={t("Accepted structures")}
        extra={
          (row.status === "COMPLETED" || row.status === "CANCELLED") && row.artifact_complete === true && row.accepted_count > 0 && (
            <Space wrap>
              <Button icon={<Database24Regular />} loading={busy} onClick={() => void materialize()}>
                {t("Materialize as new dataset")}
              </Button>
              <Button loading={busy} onClick={() => setAddOpen(true)}>
                {t("Add to existing dataset")}
              </Button>
              <Button loading={busy} onClick={() => void exportAccepted()}>
                {t("Export accepted structures")}
              </Button>
            </Space>
          )
        }
      >
        {materializedPath && (
          <div style={{ fontSize: 13, color: "#616161", marginBottom: 8 }}>{materializedPath}</div>
        )}
        <div style={{ fontSize: 13, color: "#616161" }}>
          {t("{count} structures accepted; every accepted.extxyz frame carries candidate provenance (generation, parent, operator, fitness).", {
            count: row.accepted_count,
          })}
        </div>
      </Card>
      <Modal
        title={t("Add generated structures to an existing dataset")}
        open={addOpen}
        onCancel={() => !busy && setAddOpen(false)}
        onOk={() => void addToExisting()}
        okText={t("Add structures")}
        cancelText={t("Cancel")}
        confirmLoading={busy}
        okButtonProps={{ disabled: !targetDatasetId }}
        closable={!busy}
        maskClosable={!busy}
        destroyOnHidden
      >
        <Select
          value={targetDatasetId}
          onChange={setTargetDatasetId}
          options={writableDatasets.map((dataset) => ({
            value: dataset.id,
            label: dataset.name + " (" + dataset.number_of_frames + ")",
          }))}
          placeholder={t("Select an extxyz dataset")}
          notFoundContent={t("No extxyz datasets available")}
          style={{ width: "100%" }}
        />
        <Typography.Text type="secondary" style={{ display: "block", marginTop: 12 }}>
          {t("Only extxyz datasets are supported. Generated frames will be appended to the selected source file; statistics will be rescanned, dependent results marked stale, and a view of just the new structures created.")}
        </Typography.Text>
      </Modal>
    </div>
  );
}

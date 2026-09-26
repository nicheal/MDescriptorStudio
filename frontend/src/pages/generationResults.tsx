// Results phase: descriptor-space map (gray = original, blue = evaluated,
// orange = accepted, with a generation slider), local-environment discovery,
// and the actions that turn accepted structures into a new dataset.
import { useEffect, useMemo, useState } from "react";
import { Alert, App as AntApp, Button, Card, Col, Collapse, Empty, Modal, Row, Select, Slider, Space, Spin, Statistic, Tag, Typography } from "antd";
import { Database24Regular } from "@fluentui/react-icons";
import { save as saveDialog } from "@tauri-apps/plugin-dialog";
import type { PlotMouseEvent } from "plotly.js";
import { ipc } from "../ipc/client";
import ScientificPlot from "../viz/ScientificPlot";
import { GENERATION_GEOMETRY_REJECTION_LABELS, GENERATION_OBJECTIVE_LABELS, GENERATION_OPTIMIZER_LABELS, GENERATION_STOP_REASON_LABELS } from "../features/generation/labels";
import type { GenerationPca, GenerationPreview, GenerationRow, PendingGenerationRegistration } from "../features/generation/types";
import type { DatasetView, FramePayload } from "../types/protocol";
import StructurePreview from "../components/StructurePreview";
import { refetchDatasets, useWorkspace } from "../stores/workspace";
import { jobStatusLabel, trackJob, watchJob } from "../stores/jobs";
import { discoveryStats } from "../features/generation/summary";
import { useGenerationStore } from "../features/generation/generationStore";
import GenerationConvergenceCharts from "../features/generation/GenerationConvergenceCharts";
import { useT } from "../i18n";
import { describeError } from "../util/errors";

function metric(label: string, value: string | number) {
  return <Statistic title={label} value={value} valueStyle={{ fontSize: 22 }} />;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function numberText(value: unknown, digits = 3): string | null {
  return typeof value === "number" && Number.isFinite(value) ? String(Number(value.toFixed(digits))) : null;
}

function errorDetail(error: unknown, fallback: string) {
  if (error && typeof error === "object" && "message" in error && typeof error.message === "string") {
    return "code" in error
      ? describeError(error as { code?: string; message: string; error_id?: string }, "GENERATION", fallback)
      : error.message;
  }
  return error instanceof Error ? error.message : fallback;
}

type GenerationPoint = { kind: "original" | "evaluated"; index: number };

function SummaryCard({ row, onBackToConfig }: { row: GenerationRow; onBackToConfig: () => void }) {
  const { t, tr } = useT();
  const datasets = useWorkspace((state) => state.datasets);
  const preview: GenerationPreview | null = row.preview ?? null;
  const rounds = preview?.rounds ?? [];
  const latest = rounds[rounds.length - 1];
  let params: Record<string, unknown> = {};
  try {
    const parsed: unknown = JSON.parse(row.params_json || "{}");
    if (parsed && typeof parsed === "object") params = parsed as Record<string, unknown>;
  } catch {
    // Keep the result summary usable when a legacy or damaged row has invalid JSON.
  }
  const budget = record(params.budget);
  const optimizerParams = record(params.optimizer_params);
  const objective = record(params.objective);
  const constraints = record(params.constraints);
  const operatorLabels: Record<string, string> = {
    atomic_displacement: "Atomic displacement",
    isotropic_strain: "Isotropic strain",
    anisotropic_strain: "Anisotropic strain",
    cell_shear: "Cell shear",
    vacancy: "Vacancy",
    interstitial_atom: "Interstitial atom",
    substitution: "Substitution",
    antisite_swap: "Antisite swap",
  };
  const operators = Array.isArray(params.operators) ? params.operators.map((value) => {
    const operator = record(value);
    return { name: String(operator.name ?? ""), params: record(operator.params) };
  }) : [];
  const operatorSummary = operators.map(({ name, params: values }) => {
    const label = t(operatorLabels[name] ?? name);
    if (name === "atomic_displacement") {
      const sigma = numberText(values.max_sigma);
      const cutoff = numberText(values.hard_cutoff);
      return `${label}${sigma ? ` · ${t("Maximum displacement")} σ ≤ ${sigma} Å` : ""}${cutoff ? ` · ${t("Hard displacement cutoff")} ${cutoff} Å` : ""}`;
    }
    if (name === "isotropic_strain" || name === "anisotropic_strain") {
      const strain = numberText(values.max_strain == null ? null : Number(values.max_strain) * 100, 1);
      return `${label}${strain ? ` · ±${strain} %` : ""}`;
    }
    if (name === "cell_shear") {
      const shear = numberText(values.max_shear == null ? null : Number(values.max_shear) * 100, 1);
      return `${label}${shear ? ` · ${t("Maximum shear")} ${shear} %` : ""}`;
    }
    const element = values.element;
    return element ? `${label} · ${String(element)}` : label;
  });
  const anchors = Array.isArray(params.anchor_frames) ? params.anchor_frames as number[] : [];
  const strategyValues = [
    `${t("Seed structures per run")}: ${String(optimizerParams.n_seeds ?? "—")}`,
    `${t("Candidate batch")}: ${String(optimizerParams.children_per_seed ?? "—")}`,
    `${t("Accepted / round")}: ${String(optimizerParams.batch_accept ?? "—")}`,
    ...(typeof optimizerParams.parent_fraction === "number" ? [`${t("Parent fraction")}: ${numberText(optimizerParams.parent_fraction, 2)}`] : []),
    ...(typeof optimizerParams.immigrant_fraction === "number" ? [`${t("Immigrant fraction")}: ${numberText(optimizerParams.immigrant_fraction, 2)}`] : []),
    ...(typeof optimizerParams.pso_weight_pbest === "number" ? [`${t("Pull: personal best")}: ${numberText(optimizerParams.pso_weight_pbest, 2)}`] : []),
    ...(typeof optimizerParams.pso_weight_gbest === "number" ? [`${t("Pull: global best")}: ${numberText(optimizerParams.pso_weight_gbest, 2)}`] : []),
    ...(typeof optimizerParams.pso_weight_mut === "number" ? [`${t("Pull: own position")}: ${numberText(optimizerParams.pso_weight_mut, 2)}`] : []),
    ...(optimizerParams.reuse_accepted_seeds === true ? [t("Reuse accepted structures as seeds")] : []),
  ].join(" · ");
  const objectiveValues = [
    t(GENERATION_OBJECTIVE_LABELS[objective.type as keyof typeof GENERATION_OBJECTIVE_LABELS] ?? String(objective.type ?? row.objective)),
    ...(typeof objective.scaling === "string" ? [`${t("Feature scaling")}: ${t(objective.scaling === "standardized" ? "Standardized" : objective.scaling === "raw" ? "Raw" : "Robust")}`] : []),
    ...(typeof objective.aggregation === "string" ? [`${t("Aggregation")}: ${t(objective.aggregation === "top_fraction_mean" ? "Top fraction mean" : objective.aggregation === "mean" ? "Mean" : objective.aggregation === "quantile" ? "Quantile" : "Maximum (diagnostic)")}`] : []),
    ...(typeof objective.top_fraction === "number" ? [`${t("Top fraction")}: ${numberText(objective.top_fraction, 2)}`] : []),
    ...(typeof objective.quantile === "number" ? [`${t("Quantile")}: ${numberText(objective.quantile, 2)}`] : []),
    ...(typeof objective.novelty_threshold === "number" ? [`${t("Novel threshold")}: ${numberText(objective.novelty_threshold)}`] : []),
    ...(typeof objective.local_weight === "number" ? [`${t("Local environment weight")}: ${numberText(objective.local_weight, 2)} · ${t("Structure weight")}: ${numberText(objective.structure_weight, 2)}`] : []),
  ].join(" · ");
  const minDistanceMode = String(constraints.min_distance_mode ?? "");
  const pairDistances = record(constraints.min_distance_pairs);
  const constraintValues = [
    `${t("Minimum distance")}: ${t(minDistanceMode === "covalent" ? "Covalent radius × factor" : minDistanceMode === "absolute" ? "Absolute (Å)" : "None")}`,
    ...(typeof constraints.min_distance_factor === "number" ? [`${t("Covalent radius factor")}: ${numberText(constraints.min_distance_factor, 2)}×`] : []),
    ...(typeof constraints.min_distance === "number" ? [`${t("Minimum distance")}: ${numberText(constraints.min_distance)} Å`] : []),
    ...(Object.keys(pairDistances).length ? [`${t("Element-pair distance overrides")}: ${Object.entries(pairDistances).map(([pair, distance]) => `${pair}=${String(distance)} Å`).join(", ")}`] : []),
    ...(typeof constraints.max_volume_change === "number" ? [`${t("Maximum volume change")}: ±${numberText(Number(constraints.max_volume_change) * 100, 1)} %`] : []),
    ...(typeof constraints.min_volume_per_atom === "number" ? [`${t("Minimum volume per atom")}: ${numberText(constraints.min_volume_per_atom)} Å³/atom`] : []),
    ...(typeof constraints.max_volume_per_atom === "number" ? [`${t("Maximum volume per atom")}: ${numberText(constraints.max_volume_per_atom)} Å³/atom`] : []),
    `${t("Lock composition")}: ${constraints.composition_locked === false ? t("No") : t("Yes")}`,
    `${t("Lock atom count")}: ${constraints.atom_count_locked === false ? t("No") : t("Yes")}`,
  ].join(" · ");
  const budgetValues = [
    `${String(budget.max_evaluations ?? "—")} ${t("evaluations")}`,
    `${String(budget.max_accepted ?? "—")} ${t("accepted")}`,
    `${String(budget.max_generations ?? "—")} ${t("generations")}`,
    `${t("No-improvement rounds")}: ${String(budget.no_improvement_rounds ?? t("Disabled"))}`,
    ...(typeof budget.target_novelty === "number" ? [`${t("Target novelty")}: ≥ ${numberText(budget.target_novelty)}`] : []),
  ].join(" · ");
  const datasetName = datasets.find((dataset) => dataset.id === row.dataset_id)?.name ?? row.dataset_id;
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
        {metric(t("Source dataset"), datasetName)}
        {metric(t("Target descriptor"), row.descriptor_name ?? "—")}
        {metric(t("Descriptor evaluations"), preview?.evaluations ?? row.evaluations)}
        {metric(t("Accepted structures"), preview?.accepted ?? row.accepted_count)}
        {metric(t("Rounds"), rounds.length)}
        {metric(t("Coverage radius"), latest?.coverage_radius != null ? latest.coverage_radius.toFixed(3) : "—")}
      </div>
      {row.status === "FAILED" && (
        <Alert type="error" showIcon style={{ marginTop: 12 }} message={t("Expansion failed")}
          description={row.error_message ?? t("The backend did not retain a failure message for this older run.")}
          action={<Button size="small" onClick={onBackToConfig}>{t("Back to configuration")}</Button>} />
      )}
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
      <Collapse ghost style={{ marginTop: 8 }} items={[
        { key: "params", label: t("Run parameters"), children: (
          <div style={{ display: "grid", gridTemplateColumns: "minmax(120px, max-content) minmax(0, 1fr)", gap: "6px 14px", fontSize: 12, color: "#616161" }}>
            <b>{t("Seed")}</b><span>{params.seed_mode === "random" ? t("Random") : String(params.seed ?? "—")}</span>
            <b>{t("Seed scope")}</b><span>{typeof params.seed_view_id === "string" ? `${t("Selected view")} · ${params.seed_view_id}` : t("Full dataset")}</span>
            {anchors.length > 0 && <><b>{t("Target region")}</b><span>{t("Anchor frames")}: {anchors.join(", ")} · {t("Region radius")}: {String(params.region_radius ?? "—")} {t("robust-scaled descriptor units")}</span></>}
            <b>{t("Method")}</b><span>{t(GENERATION_OPTIMIZER_LABELS[row.optimizer as keyof typeof GENERATION_OPTIMIZER_LABELS] ?? row.optimizer)} · {strategyValues}</span>
            <b>{t("Enabled operators")}</b><span>{operatorSummary.join(" · ") || "—"}</span>
            <b>{t("Objective configuration")}</b><span>{objectiveValues}</span>
            <b>{t("Geometry constraints")}</b><span>{constraintValues}</span>
            <b>{t("Budget")}</b><span>{budgetValues}</span>
          </div>
        )},
        { key: "convergence", label: t("Convergence history"), children: <GenerationConvergenceCharts rounds={rounds} /> },
      ]} />
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
      <div style={{ fontSize: 12, color: "#9AA0A6", marginTop: 4 }}>{t("PCA map is a two-dimensional projection; distances and overlap can change under projection.")}</div>
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

function LocalEnvironmentCard({ pca, error }: { pca: GenerationPca | null; error: boolean }) {
  const { t } = useT();
  const discovery = pca?.discovery ?? null;
  const stats = discoveryStats(discovery);
  return (
      <Card size="small" title={t("LOCAL ENVIRONMENT DISCOVERY")}>
        {!stats && <Typography.Text type="secondary">{t(error || pca ? "Discovery statistics are unavailable for this run" : "Loading discovery statistics")}</Typography.Text>}
        {stats && <>
      <div style={{ display: "flex", gap: 32, flexWrap: "wrap" }}>
        {metric(t("Original environments"), discovery!.original_environments)}
        {metric(t("Generated environments"), stats.generated)}
        {metric(t("Novel environments"), stats.novel)}
        {metric(t("Novel fraction"), `${stats.fraction.toFixed(1)} %`)}
      </div>
        </>}
      <div style={{ marginTop: 8, fontSize: 12, color: "#9AA0A6" }}>
        {t("Novel environments are local descriptor rows farther than the novelty threshold from every archived environment. This is descriptor-space novelty and geometry screening; it does not establish physical stability or label quality.")}
      </div>
      </Card>
  );
}

export default function GenerationResultsPanel({ row, onMaterialized, onBackToConfig }: { row: GenerationRow; onMaterialized: () => void; onBackToConfig: () => void }) {
  const { t } = useT();
  const { message } = AntApp.useApp();
  const datasets = useWorkspace((s) => s.datasets);
  const [busy, setBusy] = useState(false);
  const pendingRegistration = useGenerationStore((state) => state.pendingRegistrations[row.id] ?? null);
  const setPendingRegistration = useGenerationStore((state) => state.setPendingRegistration);
  const [registrationError, setRegistrationError] = useState<string | null>(null);
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
      message.error(t("Export failed: {message}", { message: errorDetail(error, t("Export failed")) }));
    } finally {
      setBusy(false);
    }
  };

  const registerSavedDataset = async (file: PendingGenerationRegistration) => {
    setBusy(true);
    setRegistrationError(null);
    try {
      const reg = await ipc.request<{ job_id: string }>("dataset.register", {
        path: file.path,
        name: file.name,
        lineage: file.lineage,
      });
      trackJob(reg.job_id, "dataset.register");
      const registered = await watchJob(reg.job_id);
      if (registered.status !== "COMPLETED") {
        throw new Error(registered.error?.message ?? registered.status);
      }
      setPendingRegistration(row.id, null);
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
      const detail = errorDetail(error, t("Dataset written but registration did not complete"));
      setRegistrationError(detail);
      message.error(t("Dataset written but registration failed: {message}", { message: detail }));
    } finally {
      setBusy(false);
    }
  };

  const materialize = async () => {
    setBusy(true);
    let saved: PendingGenerationRegistration | null = null;
    try {
      const target = await saveDialog({
        title: t("Destination path for the expanded dataset (.extxyz)"),
        defaultPath: row.id + "_accepted.extxyz",
        filters: [{ name: "extxyz", extensions: ["xyz", "extxyz"] }],
      });
      if (!target) return;
      const submitted = await ipc.request<{ job_id: string }>("generation.materialize", {
        id: row.id,
        path: target,
        name: row.id + "_expanded",
      });
      trackJob(submitted.job_id, "generation.materialize", row.dataset_id);
      const written = await watchJob(submitted.job_id);
      const payload = written.result as unknown as { path?: string; name?: string; lineage?: Record<string, unknown> } | null;
      if (written.status !== "COMPLETED" || !payload?.path || !payload.name || !payload.lineage) {
        throw new Error(written.error?.message ?? written.status);
      }
      saved = { path: payload.path, name: payload.name, lineage: payload.lineage };
      setPendingRegistration(row.id, saved);
      setRegistrationError(null);
    } catch (error) {
      console.error(error);
      message.error(t("Materialize failed: {message}", { message: errorDetail(error, t("Materialize failed")) }));
    } finally {
      setBusy(false);
    }
    if (saved) void registerSavedDataset(saved);
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
      <SummaryCard row={row} onBackToConfig={onBackToConfig} />
      {(row.status === "COMPLETED" || row.status === "CANCELLED") && row.artifact_complete === true && row.accepted_count > 0 && (
        <Card size="small" title={t("Accepted structures")}
          extra={<Space wrap>
            <Button type="primary" icon={<Database24Regular />} loading={busy} disabled={pendingRegistration != null} onClick={() => void materialize()}>{t("Save as new dataset")}</Button>
            <Button loading={busy} disabled={pendingRegistration != null} onClick={() => setAddOpen(true)}>{t("Add to existing dataset")}</Button>
            <Button loading={busy} onClick={() => void exportAccepted()}>{t("Export accepted structures")}</Button>
          </Space>}>
          <div style={{ fontSize: 13, color: "#616161" }}>
            {t("The new dataset contains only the accepted generated structures; the original source dataset is not copied into it.", { count: row.accepted_count })}
          </div>
          <div style={{ fontSize: 12, color: "#9AA0A6", marginTop: 4 }}>
            {t("{count} accepted structures; each frame carries candidate provenance such as generation, parent, operator, and fitness.", { count: row.accepted_count })}
          </div>
          {pendingRegistration && <Alert type={registrationError ? "error" : "info"} showIcon style={{ marginTop: 10 }}
            message={registrationError ? t("Dataset written but registration did not complete") : t("Dataset written — registration is ready")}
            description={<span>{pendingRegistration.path}{registrationError ? ` · ${registrationError}` : ""}</span>}
            action={<Button size="small" loading={busy} onClick={() => void registerSavedDataset(pendingRegistration)}>{t("Retry registration")}</Button>} />}
        </Card>
      )}
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
                  : (() => {
                    const generation = pca?.evaluated_generation[selectedPoint.index];
                    const novelty = pca?.evaluated_novelty[selectedPoint.index];
                    const accepted = pca?.evaluated_accepted[selectedPoint.index];
                    return t("Candidate {index} · generation {generation} · accepted {accepted} · novelty {novelty}", {
                      index: selectedPoint.index,
                      generation: generation ?? "—",
                      accepted: accepted == null ? "—" : accepted ? t("Yes") : t("No"),
                      novelty: novelty == null ? "—" : novelty.toFixed(3),
                    });
                  })()}
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
      <LocalEnvironmentCard pca={pca} error={pcaError} />
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
          {t("Target dataset")}: {writableDatasets.find((dataset) => dataset.id === targetDatasetId)?.name ?? t("None")} · {t("Accepted structures to append")}: {row.accepted_count} · {t("Expected total structures")}: {(writableDatasets.find((dataset) => dataset.id === targetDatasetId)?.number_of_frames ?? 0) + row.accepted_count}
          <br />
          {t("Only extxyz datasets are supported. The accepted structures will be appended to the selected file; statistics will be rescanned, dependent results marked stale, and a view of just the new structures created.")}
        </Typography.Text>
      </Modal>
    </div>
  );
}

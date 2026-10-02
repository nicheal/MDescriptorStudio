// Running phase: live metrics, rejection counters, and the two convergence
// curves that judge whether the expansion is still paying off.
import { Alert, Button, Card, Collapse, Space, Spin, Statistic, Tag } from "antd";
import type { GenerationPreview, GenerationRow } from "../features/generation/types";
import GenerationConvergenceCharts from "../features/generation/GenerationConvergenceCharts";
import { GENERATION_GEOMETRY_REJECTION_LABELS } from "../features/generation/labels";
import { useT } from "../i18n";
import { jobStatusLabel } from "../stores/jobs";
import { isActiveGenerationStatus, otherCandidateCount } from "../features/generation/summary";

function metric(label: string, value: string | number) {
  return <Statistic title={label} value={value} valueStyle={{ fontSize: 22 }} />;
}

export default function GenerationRunPanel({
  row,
  onCancel,
  cancelPending = false,
  onResume,
  resumePending = false,
  lastUpdatedAt,
  connectionError,
  onRetry,
  sourceDatasetName,
}: {
  row: GenerationRow;
  onCancel: () => void;
  cancelPending?: boolean;
  onResume?: () => void;
  resumePending?: boolean;
  lastUpdatedAt?: number | null;
  connectionError?: string | null;
  onRetry?: () => void;
  sourceDatasetName?: string | null;
}) {
  const { t, tr, locale } = useT();
  const preview: GenerationPreview | null = row.preview ?? null;
  const rounds = preview?.rounds ?? [];
  const totals = rounds.reduce(
    (acc, r) => ({
      proposed: acc.proposed + r.proposed,
      rejectedGeometry: acc.rejectedGeometry + r.rejected_geometry,
      rejectedDuplicate: acc.rejectedDuplicate + r.rejected_duplicate,
      rejectedScreening: acc.rejectedScreening + (r.rejected_screening ?? 0),
      screeningPassed: acc.screeningPassed + (r.screening_passed ?? 0),
      screeningUnscreenable: acc.screeningUnscreenable + (r.screening_unscreenable ?? 0),
      screeningTrainReady: acc.screeningTrainReady + (r.screening_train_ready ?? 0),
    }),
    { proposed: 0, rejectedGeometry: 0, rejectedDuplicate: 0, rejectedScreening: 0, screeningPassed: 0, screeningUnscreenable: 0, screeningTrainReady: 0 },
  );
  const geometryReasonTotals = rounds.reduce<Record<string, number>>((acc, round) => {
    for (const [reason, count] of Object.entries(round.rejected_geometry_by_reason ?? {})) {
      acc[reason] = (acc[reason] ?? 0) + count;
    }
    return acc;
  }, {});
  const rejectedOther = otherCandidateCount(totals.proposed, totals.rejectedGeometry, totals.rejectedDuplicate, preview?.accepted ?? 0, totals.rejectedScreening);
  const screeningActive = totals.rejectedScreening > 0 || totals.screeningPassed > 0 || totals.screeningUnscreenable > 0;
  const latest = rounds[rounds.length - 1];
  const evaluations = preview?.evaluations ?? row.evaluations;
  let params: { budget?: Record<string, unknown>; objective?: Record<string, unknown>; screening?: Record<string, unknown> } = {};
  try {
    const parsed: unknown = JSON.parse(row.params_json || "{}");
    if (parsed && typeof parsed === "object") {
      const record = parsed as Record<string, unknown>;
      const budget = record.budget && typeof record.budget === "object" ? record.budget as Record<string, unknown> : undefined;
      const objective = record.objective && typeof record.objective === "object" ? record.objective as Record<string, unknown> : undefined;
      const constraints = record.constraints && typeof record.constraints === "object" ? record.constraints as Record<string, unknown> : undefined;
      const screening = constraints?.energy_screening && typeof constraints.energy_screening === "object"
        ? constraints.energy_screening as Record<string, unknown>
        : undefined;
      params = { budget, objective, screening };
    }
  } catch {
    // A damaged saved configuration should not blank the live monitor.
  }
  const maxEvaluations = Number(params.budget?.max_evaluations ?? 0) || undefined;
  const maxAccepted = Number(params.budget?.max_accepted ?? 0) || undefined;
  const maxGenerations = Number(params.budget?.max_generations ?? 0) || undefined;
  const noImprovementRounds = Number(params.budget?.no_improvement_rounds ?? 0) || undefined;
  const targetNovelty = Number(params.budget?.target_novelty ?? 0) || undefined;
  const firstRoundHint = params.objective?.scaling === "raw"
    ? t("Raw mode skips scaling statistics, but candidate descriptors and exact novelty scoring still run against the full reference archive.")
    : t("Large atom-level reference descriptors may take time to scale before the first round. Round metrics appear after candidate evaluation; structures are available after the run completes.");
  const generations = rounds.length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Card
        size="small"
        title={<Space>{t("Expansion run")}<Tag color={row.status === "QUEUED" ? "default" : row.status === "COMPLETED" ? "green" : row.status === "FAILED" ? "red" : row.status === "CANCELLED" ? "orange" : row.status === "INTERRUPTED" ? "purple" : "blue"}>{jobStatusLabel(tr, row.status)}</Tag></Space>}
        extra={
          <Space>
            {row.status === "INTERRUPTED" && !!row.resumable && onResume ? (
              <Button type="primary" loading={resumePending} disabled={resumePending} onClick={onResume}>
                {t("Resume")}
              </Button>
            ) : null}
            {isActiveGenerationStatus(row.status) ? <Button danger loading={cancelPending} disabled={cancelPending} onClick={onCancel}>
              {cancelPending ? t("Cancel requested") : t("Cancel")}
            </Button> : null}
          </Space>
        }
      >
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap", marginBottom: 12, fontSize: 12, color: "#616161" }}>
          <span>{t("Source dataset")}: <b>{sourceDatasetName ?? row.dataset_id}</b></span>
          <span>{t("Elapsed")}: <b>{(() => {
            const started = Date.parse(row.started_at ?? row.created_at);
            const elapsed = Number.isFinite(started) ? Math.max(0, Math.floor((Date.now() - started) / 1000)) : 0;
            return `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
          })()}</b></span>
          {lastUpdatedAt && <span>{t("Last updated")}: <b>{new Date(lastUpdatedAt).toLocaleTimeString(locale)}</b></span>}
        </div>
        {connectionError && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={t("Status connection interrupted")}
          description={connectionError} action={onRetry && <Button size="small" onClick={onRetry}>{t("Retry")}</Button>} />}
        {cancelPending && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={t("Saving accepted structures before stopping")}
          />
        )}
        {row.status === "QUEUED" && <Alert type="info" showIcon style={{ marginBottom: 12 }} message={t("Run queued; waiting for a worker")} />}
        {row.status === "INTERRUPTED" && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={t("The run was interrupted (backend restart or shutdown)")}
            description={
              resumePending
                ? undefined
                : t("Resume continues from the last completed round; everything up to that boundary is kept.")
            }
          />
        )}
        {row.status === "RUNNING" && !preview && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={
              <span>
                <Spin size="small" style={{ marginInlineEnd: 8 }} />
                {t("Preparing the first generation round")}
              </span>
            }
            description={firstRoundHint}
          />
        )}
        <div style={{ display: "flex", gap: 32, flexWrap: "wrap" }}>
          {metric(t("Generation"), `${generations}`)}
          {metric(
            t("Evaluations"),
            maxEvaluations ? `${evaluations} / ${maxEvaluations}` : `${evaluations}`,
          )}
          {metric(t("Accepted"), `${preview?.accepted ?? row.accepted_count}`)}
          {latest && metric(t("Accepted this round"), latest.accepted)}
          {metric(t("Best novelty"), latest?.best_novelty != null ? latest.best_novelty.toFixed(3) : "—")}
          {metric(t("Mean novelty"), latest?.mean_novelty != null ? latest.mean_novelty.toFixed(3) : "—")}
          {metric(t("Coverage radius"), latest?.coverage_radius != null ? latest.coverage_radius.toFixed(3) : "—")}
          {screeningActive && metric(t("Train-ready"), `${totals.screeningTrainReady}`)}
        </div>
        <Collapse ghost style={{ marginTop: 8 }} items={[{ key: "rejections", label: t("Candidate selection details"), children: (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, color: "#616161", fontSize: 12 }}>
            <span>{t("Rejected — geometry")}: <b>{totals.rejectedGeometry}</b></span>
            <span>{t("Rejected — duplicate")}: <b>{totals.rejectedDuplicate}</b></span>
            {screeningActive && (
              <span>
                {t("Rejected — energy/force screening")}: <b>{totals.rejectedScreening}</b>
                {" · "}{t("Screening passed")}: <b>{totals.screeningPassed}</b>
                {" · "}{t("Unscreenable")}: <b>{totals.screeningUnscreenable}</b>
                {" · "}{t("Train-ready")}: <b>{totals.screeningTrainReady}</b>
              </span>
            )}
            <span>{t("Other candidates not selected")}: <b>{rejectedOther}</b></span>
            {Object.keys(geometryReasonTotals).length > 0 && <span>
              {t("Geometry rejection reasons")}: {Object.entries(geometryReasonTotals).map(([reason, count]) =>
                `${t(GENERATION_GEOMETRY_REJECTION_LABELS[reason] ?? reason)} ${count}`,
              ).join(" · ")}
            </span>}
            {screeningActive && params.screening && (
              <span>
                {t("Screening bounds")}:{" "}
                {params.screening.max_energy_per_atom != null
                  ? `${t("energy")} ≤ ${String(params.screening.max_energy_per_atom)} eV/atom`
                  : t("energy: measure only")}
                {" · "}
                {params.screening.max_force != null
                  ? `${t("force")} ≤ ${String(params.screening.max_force)} eV/Å`
                  : t("force: measure only")}
              </span>
            )}
            {screeningActive && preview?.screening_model_identity && (
              <span>
                {t("Screening model")}: <b>{String(preview.screening_model_identity.model ?? "—")}</b>
                {preview.screening_model_identity.resolved_source === "package"
                  ? ` · ${t("bundled default model")}`
                  : ` · ${t("explicit checkpoint")}`}
                {preview.screening_model_identity.sha256
                  ? ` · SHA256 ${String(preview.screening_model_identity.sha256).slice(0, 16)}…`
                  : ""}
              </span>
            )}
          </div>
        )}]} />
        <div style={{ marginTop: 8, fontSize: 12, color: "#616161" }}>
          {t("Run stop summary")} · {t("Max descriptor evaluations")}: {maxEvaluations ?? "—"} · {t("Max accepted structures")}: {maxAccepted ?? "—"} · {t("Max generations")}: {maxGenerations ?? "—"} · {t("No-improvement rounds")}: {noImprovementRounds ?? t("Disabled")}{targetNovelty != null ? ` · ${t("Target novelty")}: ${targetNovelty}` : ""}
        </div>
      </Card>

      <GenerationConvergenceCharts rounds={rounds} />
    </div>
  );
}

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
  lastUpdatedAt,
  connectionError,
  onRetry,
  sourceDatasetName,
}: {
  row: GenerationRow;
  onCancel: () => void;
  cancelPending?: boolean;
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
    }),
    { proposed: 0, rejectedGeometry: 0, rejectedDuplicate: 0 },
  );
  const geometryReasonTotals = rounds.reduce<Record<string, number>>((acc, round) => {
    for (const [reason, count] of Object.entries(round.rejected_geometry_by_reason ?? {})) {
      acc[reason] = (acc[reason] ?? 0) + count;
    }
    return acc;
  }, {});
  const rejectedOther = otherCandidateCount(totals.proposed, totals.rejectedGeometry, totals.rejectedDuplicate, preview?.accepted ?? 0);
  const latest = rounds[rounds.length - 1];
  const evaluations = preview?.evaluations ?? row.evaluations;
  let params: { budget?: Record<string, unknown>; objective?: Record<string, unknown> } = {};
  try {
    const parsed: unknown = JSON.parse(row.params_json || "{}");
    if (parsed && typeof parsed === "object") {
      const record = parsed as Record<string, unknown>;
      const budget = record.budget && typeof record.budget === "object" ? record.budget as Record<string, unknown> : undefined;
      const objective = record.objective && typeof record.objective === "object" ? record.objective as Record<string, unknown> : undefined;
      params = { budget, objective };
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
        title={<Space>{t("Expansion run")}<Tag color={row.status === "QUEUED" ? "default" : row.status === "COMPLETED" ? "green" : row.status === "FAILED" ? "red" : row.status === "CANCELLED" ? "orange" : "blue"}>{jobStatusLabel(tr, row.status)}</Tag></Space>}
        extra={
          isActiveGenerationStatus(row.status) ? <Button danger loading={cancelPending} disabled={cancelPending} onClick={onCancel}>
            {cancelPending ? t("Cancel requested") : t("Cancel")}
          </Button> : null
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
        </div>
        <Collapse ghost style={{ marginTop: 8 }} items={[{ key: "rejections", label: t("Candidate selection details"), children: (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, color: "#616161", fontSize: 12 }}>
            <span>{t("Rejected — geometry")}: <b>{totals.rejectedGeometry}</b></span>
            <span>{t("Rejected — duplicate")}: <b>{totals.rejectedDuplicate}</b></span>
            <span>{t("Other candidates not selected")}: <b>{rejectedOther}</b></span>
            {Object.keys(geometryReasonTotals).length > 0 && <span>
              {t("Geometry rejection reasons")}: {Object.entries(geometryReasonTotals).map(([reason, count]) =>
                `${t(GENERATION_GEOMETRY_REJECTION_LABELS[reason] ?? reason)} ${count}`,
              ).join(" · ")}
            </span>}
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

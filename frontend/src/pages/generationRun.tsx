// Running phase: live metrics, rejection counters, and the two convergence
// curves that judge whether the expansion is still paying off.
import { Alert, Button, Card, Spin, Statistic } from "antd";
import ScientificPlot from "../viz/ScientificPlot";
import type { GenerationPreview, GenerationRow } from "../features/generation/types";
import { useT } from "../i18n";

function metric(label: string, value: string | number) {
  return <Statistic title={label} value={value} valueStyle={{ fontSize: 22 }} />;
}

export default function GenerationRunPanel({ row, onCancel, cancelPending = false }: { row: GenerationRow; onCancel: () => void; cancelPending?: boolean }) {
  const { t } = useT();
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
  const rejectedLowNovelty = Math.max(0, totals.proposed - totals.rejectedGeometry - totals.rejectedDuplicate - (preview?.accepted ?? 0));
  const latest = rounds[rounds.length - 1];
  const evaluations = preview?.evaluations ?? row.evaluations;
  const params = JSON.parse(row.params_json || "{}");
  const maxEvaluations = Number(params.budget?.max_evaluations ?? 0) || undefined;
  const firstRoundHint = params.objective?.scaling === "raw"
    ? t("Raw mode skips scaling statistics, but candidate descriptors and exact novelty scoring still run against the full reference archive.")
    : t("Large atom-level reference descriptors may take time to scale before the first round. Round metrics appear after candidate evaluation; structures are available after the run completes.");
  const generations = rounds.length;
  const x = rounds.map((r) => r.generation);
  const novelTrace = rounds.map((r) => r.novel_environments);
  const radiusTrace = rounds.map((r) => r.coverage_radius);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Card
        size="small"
        title={t("Expansion in progress")}
        extra={
          <Button danger loading={cancelPending} disabled={cancelPending} onClick={onCancel}>
            {cancelPending ? t("Cancel requested") : t("Cancel")}
          </Button>
        }
      >
        {cancelPending && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={t("Saving accepted structures before stopping")}
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
          {metric(t("Best novelty"), latest?.best_novelty != null ? latest.best_novelty.toFixed(3) : "—")}
          {metric(t("Mean novelty"), latest?.mean_novelty != null ? latest.mean_novelty.toFixed(3) : "—")}
          {metric(t("Coverage radius"), latest ? latest.coverage_radius.toFixed(3) : "—")}
        </div>
        <div style={{ display: "flex", gap: 24, marginTop: 12, color: "#616161", fontSize: 13 }}>
          <span>
            {t("Rejected — geometry")}: <b>{totals.rejectedGeometry}</b>
          </span>
          <span>
            {t("Rejected — duplicate")}: <b>{totals.rejectedDuplicate}</b>
          </span>
          <span>
            {t("Rejected — low novelty")}: <b>{rejectedLowNovelty}</b>
          </span>
        </div>
      </Card>

      <Card size="small" title={t("Novel environments per round")}>
        <ScientificPlot
          style={{ width: "100%", height: 260 }}
          data={[
            {
              x,
              y: novelTrace,
              type: "scatter",
              mode: "lines+markers",
              line: { color: "#0F6CBD" },
              name: t("Novel environments"),
            },
          ]}
          layout={{
            margin: { t: 8, r: 16, b: 40, l: 48 },
            xaxis: { title: { text: t("Generation") } },
            yaxis: { title: { text: t("Novel environments") } },
          }}
          config={{ displayModeBar: false, responsive: true }}
        />
      </Card>

      <Card size="small" title={t("Coverage radius per round")}>
        <ScientificPlot
          style={{ width: "100%", height: 260 }}
          data={[
            {
              x,
              y: radiusTrace,
              type: "scatter",
              mode: "lines+markers",
              line: { color: "#E8A33D" },
              name: t("Coverage radius"),
            },
          ]}
          layout={{
            margin: { t: 8, r: 16, b: 40, l: 48 },
            xaxis: { title: { text: t("Generation") } },
            yaxis: { title: { text: t("Coverage radius") } },
          }}
          config={{ displayModeBar: false, responsive: true }}
        />
      </Card>
    </div>
  );
}

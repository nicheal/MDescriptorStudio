import { Card, Col, Row, Typography } from "antd";
import ScientificPlot from "../../viz/ScientificPlot";
import type { GenerationRound } from "./types";
import { useT } from "../../i18n";

export default function GenerationConvergenceCharts({ rounds }: { rounds: GenerationRound[] }) {
  const { t } = useT();
  if (rounds.length === 0) return <Typography.Text type="secondary">{t("No convergence rounds were recorded")}</Typography.Text>;
  const generations = rounds.map((round) => round.generation);
  const uniqueTrace = rounds.map((round) => round.unique_novel_environments);
  const archivedTrace = rounds.map((round) => round.archived_unique_novel_environments);
  // Post-screening kept-set series (2026-10-02 audit D): only meaningful when
  // the backend reports it AND screening actually diverged from the discovered
  // count somewhere — otherwise the line would just overlap the first one.
  const archivedDiffers = archivedTrace.some(
    (value, index) => value != null && value !== uniqueTrace[index],
  );
  return (
    <Row gutter={[12, 12]}>
      <Col xs={24} xl={12}>
        <Card size="small" title={t("Novel environments per round")}>
          <ScientificPlot
            style={{ width: "100%", height: 260 }}
            data={[
              {
                x: generations,
                y: rounds.map((round) => round.novel_environments),
                type: "scatter",
                mode: "lines+markers",
                line: { color: "#0F6CBD" },
                name: t("Novel environments"),
              },
              // Older runs omit the de-duplicated count; retain their recorded
              // raw series and add the second line only when the backend has it.
              ...(uniqueTrace.some((value) => value != null)
                ? [{
                    x: generations,
                    y: uniqueTrace,
                    type: "scatter" as const,
                    mode: "lines+markers" as const,
                    line: { color: "#8FBCE6" },
                    name: t("Unique novel environments"),
                  }]
                : []),
              ...(archivedDiffers
                ? [{
                    x: generations,
                    y: archivedTrace,
                    type: "scatter" as const,
                    mode: "lines+markers" as const,
                    line: { color: "#C42B1C", dash: "dot" as const },
                    name: t("Archived (post-screening)"),
                  }]
                : []),
            ]}
            layout={{
              margin: { t: 8, r: 16, b: 40, l: 48 },
              xaxis: { title: { text: t("Generation") } },
              yaxis: { title: { text: t("Novel environments") } },
            }}
            config={{ displayModeBar: false, responsive: true }}
          />
        </Card>
      </Col>
      <Col xs={24} xl={12}>
        <Card size="small" title={t("Coverage radius per round")}>
          <ScientificPlot
            style={{ width: "100%", height: 260 }}
            data={[{
              x: generations,
              y: rounds.map((round) => round.coverage_radius),
              type: "scatter",
              mode: "lines+markers",
              line: { color: "#E8A33D" },
              name: t("Coverage radius"),
            }]}
            layout={{
              margin: { t: 8, r: 16, b: 40, l: 48 },
              xaxis: { title: { text: t("Generation") } },
              yaxis: { title: { text: t("Coverage radius") } },
            }}
            config={{ displayModeBar: false, responsive: true }}
          />
        </Card>
      </Col>
    </Row>
  );
}

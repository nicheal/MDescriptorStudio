import { Button, Space, Tag, Typography } from "antd";
import { ArrowRight16Regular, Delete16Regular } from "@fluentui/react-icons";
import { jobStatusLabel } from "../stores/jobs";
import { useT } from "../i18n";
import StructurePreview from "../components/StructurePreview";
import type { AnalysisPreview, AnalysisRow, FramePayload } from "../types/protocol";
import { stalenessNote } from "./analysisPreview";
import { Row, SectionHeading, type Point } from "./analysisShared";

type AnalysisInspectorProps = {
  selectedPoint: Point | null;
  selectedFrame: FramePayload | null;
  selectedFrameBusy: boolean;
  preview: AnalysisPreview | null;
  localCutoff: number;
  visibleAnalyses: AnalysisRow[];
  loadingAnalysisId: string | null;
  onOpenPoint: () => void;
  onSelectAtom: (atom: number) => void;
  onLoadAnalysis: (row: AnalysisRow) => void | Promise<void>;
  onDeleteAnalysis: (row: AnalysisRow) => void | Promise<void>;
};

export default function AnalysisInspector({
  selectedPoint,
  selectedFrame,
  selectedFrameBusy,
  preview,
  localCutoff,
  visibleAnalyses,
  loadingAnalysisId,
  onOpenPoint,
  onSelectAtom,
  onLoadAnalysis,
  onDeleteAnalysis,
}: AnalysisInspectorProps) {
  const { t, tr, locale } = useT();
  return (
    <aside className="analysis-inspector">
      <section className="analysis-card">
        <SectionHeading title={t("INSPECTOR")} meta={selectedPoint ? t("Frame {index}", { index: selectedPoint.frame }) : undefined} />
        {selectedPoint ? <>
          <Row k={t("Sample")} v={selectedPoint.sample_id ?? String(selectedPoint.i)} />
          <Row k={t("Frame index")} v={String(selectedPoint.frame)} />
          {selectedPoint.row != null && <Row k={t("Atom index in frame")} v={String(selectedPoint.row)} />}
          <Button size="small" icon={<ArrowRight16Regular />} onClick={onOpenPoint}>{t("Open in Explore")}</Button>
        </> : <Typography.Text type="secondary">{t("Click a point, or use box/lasso selection, to inspect a structure.")}</Typography.Text>}
      </section>

      <section className="analysis-card">
        <SectionHeading title={t("STRUCTURE PREVIEW")} meta={selectedFrame ? t("Frame {index}", { index: selectedFrame.index }) : undefined} />
        {selectedFrame ? <StructurePreview
          frame={selectedFrame}
          selectedAtom={selectedPoint?.row}
          localCutoff={preview?.kind === "local_diversity" && selectedPoint?.row != null ? localCutoff : undefined}
          onOpen={onOpenPoint}
          onSelectAtom={onSelectAtom}
        /> : <div className="analysis-empty-small">{selectedFrameBusy ? t("Loading structure…") : t("Select a sample to preview it.")}</div>}
      </section>

      <section className="analysis-card">
        <SectionHeading title={t("ANALYSIS HISTORY")} meta={`${visibleAnalyses.length}`} />
        {visibleAnalyses.length ? <div className="analysis-history-list">{visibleAnalyses.slice(0, 10).map((row) => {
          const note = stalenessNote(row.status, row.stale_reason);
          return <div className="analysis-history-row" key={row.id}>
            <div>
              <Typography.Text strong>{row.analysis_type}</Typography.Text>
              <Typography.Text type="secondary" style={{ display: "block", fontSize: 11 }}>{new Date(row.created_at).toLocaleString(locale)}</Typography.Text>
            </div>
            <Space size={4}>
              <Tag color={row.status === "COMPLETED" ? "green" : row.status === "STALE" ? "orange" : undefined} title={note ?? undefined} style={note ? { cursor: "help" } : undefined}>{jobStatusLabel(tr, row.status)}</Tag>
              <Button size="small" type="text" icon={<ArrowRight16Regular />} aria-label={t("Load {name} analysis", { name: row.analysis_type })} title={t("Load cached analysis")} loading={loadingAnalysisId === row.id} disabled={row.status !== "COMPLETED" || (loadingAnalysisId !== null && loadingAnalysisId !== row.id)} onClick={() => void onLoadAnalysis(row)} />
              <Button size="small" type="text" icon={<Delete16Regular />} aria-label={t("Delete {name} analysis", { name: row.analysis_type })} disabled={row.status === "RUNNING" || row.status === "QUEUED"} onClick={() => void onDeleteAnalysis(row)} />
            </Space>
          </div>;
        })}</div> : <Typography.Text type="secondary">{t("No analysis artifacts for this descriptor run yet.")}</Typography.Text>}
      </section>
    </aside>
  );
}

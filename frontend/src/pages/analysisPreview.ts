import type { AnalysisPreview } from "../types/protocol";

export interface AnalysisPoint {
  i: number;
  frame: number;
  row?: number;
  sample_id?: string;
  x: number;
  y: number;
  label?: number;
  score?: number;
  distance?: number;
  energy?: number | null;
  force_max?: number | null;
  volume?: number | null;
}

export function normalizePoints(preview: AnalysisPreview): AnalysisPoint[] {
  const raw = Array.isArray(preview.points) ? preview.points : [];
  return raw.flatMap((value) => {
    const x = Number(value.x ?? value.pc1);
    const y = Number(value.y ?? value.pc2);
    const frame = Number(value.frame ?? 0);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return [];
    return [{ i: Number(value.i ?? 0), frame, row: value.row == null ? undefined : Number(value.row), sample_id: value.sample_id == null ? undefined : String(value.sample_id), x, y, label: value.label == null ? undefined : Number(value.label), score: value.score == null ? undefined : Number(value.score), distance: value.distance == null ? undefined : Number(value.distance) }];
  });
}

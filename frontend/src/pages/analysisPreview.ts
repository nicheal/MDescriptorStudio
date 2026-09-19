import type { AnalysisPreview, RunRow } from "../types/protocol";

/**
 * The Descriptor Results page is a descriptor-run history, not a failure log.  Failed
 * calculations remain available through Jobs, while this page keeps the
 * rows that can be inspected or are still transitioning to a result.
 */
export function displayableDescriptorRuns(runs: readonly RunRow[]): RunRow[] {
  return runs.filter((run) => run.status !== "FAILED");
}

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
  element?: number;
  cluster?: number;
  energy_per_atom?: number | null;
  force_max?: number | null;
  volume?: number | null;
  coordination?: number | null;
  novelty?: number | null;
  uncertainty?: number | null;
  diversity?: number | null;
}

export function normalizePoints(preview: AnalysisPreview): AnalysisPoint[] {
  const raw = Array.isArray(preview.points) ? preview.points : [];
  return raw.flatMap((value) => {
    const x = Number(value.x ?? value.pc1);
    const y = Number(value.y ?? value.pc2);
    const frame = Number(value.frame ?? 0);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return [];
    return [{ i: Number(value.i ?? 0), frame, row: value.row == null ? undefined : Number(value.row), sample_id: value.sample_id == null ? undefined : String(value.sample_id), x, y, label: value.label == null ? undefined : Number(value.label), score: value.score == null ? undefined : Number(value.score), distance: value.distance == null ? undefined : Number(value.distance), element: value.element == null ? undefined : Number(value.element), cluster: value.cluster == null ? undefined : Number(value.cluster), energy_per_atom: value.energy_per_atom == null ? null : Number(value.energy_per_atom), force_max: value.force_max == null ? null : Number(value.force_max), volume: value.volume == null ? null : Number(value.volume), coordination: value.coordination == null ? undefined : Number(value.coordination), novelty: value.novelty == null ? undefined : Number(value.novelty), uncertainty: value.uncertainty == null ? undefined : Number(value.uncertainty), diversity: value.diversity == null ? undefined : Number(value.diversity) }];
  });
}

/**
 * Whether the result carries any per-frame property to colour points by.
 *
 * Only PCA asks the backend for those properties, so on every other projection
 * the "Color by" control had nothing to act on — dropping its fields on the way
 * in made the same thing happen to a result that *did* have them.
 */
export function hasColorByData(points: readonly AnalysisPoint[]): boolean {
  return points.some((point) => [point.energy_per_atom, point.force_max, point.volume].some((value) => value != null && Number.isFinite(value)));
}

export function selectedDisplayIndices(points: Pick<AnalysisPoint, "i">[], selectedIndices: readonly number[]): number[] {
  const selected = new Set(selectedIndices);
  return points.flatMap((point, displayIndex) => selected.has(point.i) ? [displayIndex] : []);
}

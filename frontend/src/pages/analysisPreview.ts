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

/**
 * Which artifact arrays arrived narrower than the chart asked for.
 *
 * The backend keeps one `analysis.chunk` reply inside the protocol's frame cap by
 * shortening the column window, and it cuts rows at the page limit; both answer
 * `truncated: true`, because a chart cannot tell either apart from an artifact
 * that genuinely is that shape. Where the row count is known it goes into the
 * label, so a panel that drew the first 20 000 of 179 700 pairs says so rather
 * than implying a complete population.
 */
export function narrowedArrays(
  replies: readonly { array: string; truncated: boolean; rows?: number; total?: number }[],
): string[] {
  return replies
    .filter((reply) => reply.truncated)
    .map((reply) =>
      reply.rows != null && reply.total != null && reply.total > reply.rows
        ? `${reply.array} (${reply.rows.toLocaleString()} / ${reply.total.toLocaleString()})`
        : reply.array,
    )
    .sort();
}

/** What a point carries even when the analysis attached nothing to it. */
const POINT_IDENTITY_KEYS = new Set(["i", "frame", "sample_id", "row", "x", "y"]);

/**
 * The list the result table tabulates.
 *
 * A result with coordinates used to send that list twice — `points` for the
 * scatter and a `rows` copy carrying the arrays' plural names for the table —
 * so a capped preview was twice the size it needed to be and "the table shows
 * the samples the scatter drew" was an invariant rather than the shape. There is
 * one list now, which leaves a choice to make: a `points` list is worth a table
 * only when it carries a per-sample field beyond identity and coordinates. A
 * plain sampling result attaches nothing per point, and there the samples it
 * selected are the table.
 */
export function previewTableRows(preview: AnalysisPreview | null): Record<string, unknown>[] {
  if (!preview) return [];
  if (Array.isArray(preview.rows)) return preview.rows;
  const points = Array.isArray(preview.points) ? preview.points : [];
  // Every point is built from the same per-sample arrays, so one describes them all.
  if (points.length && Object.keys(points[0]).some((key) => !POINT_IDENTITY_KEYS.has(key))) return points;
  if (Array.isArray(preview.selected)) return preview.selected;
  if (Array.isArray(preview.pairs)) return preview.pairs as Record<string, unknown>[];
  if (Array.isArray(preview.runs)) return preview.runs as Record<string, unknown>[];
  if (Array.isArray(preview.top_indices)) {
    const values = Array.isArray(preview.top_values) ? preview.top_values : [];
    return preview.top_indices.map((feature, position) => ({ rank: position + 1, feature, variance: values[position] }));
  }
  return [];
}

/**
 * The four per-sample fields a table row contributes to the selected point, read
 * under whichever of the two names they arrived in: rows taken from `points`
 * carry the singular names the plot uses, while the row lists of analyses
 * without coordinates pass the source array names straight through.
 */
export function previewRowFields(row: Record<string, unknown>): { label?: number; score?: number; distance?: number; cluster?: number } {
  const either = (singular: string, plural: string) => {
    const value = row[singular] ?? row[plural];
    return value == null ? undefined : Number(value);
  };
  return { label: either("label", "labels"), score: either("score", "scores"), distance: either("distance", "distances"), cluster: either("cluster", "cluster_labels") };
}

/**
 * The keys a table becomes columns from, capped at seven.
 *
 * The two plotted coordinates go last on purpose: the chart already reads them,
 * and a cluster result's table would otherwise spend its seven columns on x and y
 * before reaching the assignment it is there to show.
 */
export function previewTableColumns(row: Record<string, unknown>): string[] {
  return Object.keys(row)
    .sort((a, b) => Number(a === "x" || a === "y") - Number(b === "x" || b === "y"))
    .slice(0, 7);
}


/** The extent of the values actually being drawn.
 *
 * The metric strip beside a matrix has to describe that matrix. Reading
 * `distance_min`/`distance_max` next to a similarity grid printed a cosine
 * "Maximum" of 1.86 - a number the plotted values never reach - because the two
 * came from different quantities (deep review pass 5, 5-C4).
 */
export function matrixExtent(matrix: number[][]): { min: number | null; max: number | null } {
  let lowest: number | null = null;
  let highest: number | null = null;
  for (const row of matrix) {
    for (const value of row) {
      if (!Number.isFinite(value)) continue;
      if (lowest === null || value < lowest) lowest = value;
      if (highest === null || value > highest) highest = value;
    }
  }
  return { min: lowest, max: highest };
}

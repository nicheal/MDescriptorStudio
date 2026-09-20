// Trajectory math for the descriptor-trajectory module, kept free of React and
// Plotly so it can be unit-tested directly.
//
// The threshold helpers deliberately mirror the backend implementation
// (`_trajectory_threshold` in backend/mdescriptor_studio_backend/analysis/engine.py):
// the analysis artifact stores the canonical detection, while the view
// re-derives it live so the sensitivity control stays interactive.

export type EventMethod = "mad" | "zscore" | "percentile";

export interface StepStats {
  median: number;
  mad: number;
  mean: number;
  std: number;
}

export const EVENT_METHODS: EventMethod[] = ["mad", "zscore", "percentile"];
export const METHOD_DEFAULT_SENSITIVITY: Record<EventMethod, number> = { mad: 3, zscore: 3, percentile: 1 };
/** MAD scaled to a normal-consistent sigma, as used by the backend. */
export const MAD_SIGMA = 1.4826;

/**
 * Event threshold over descriptor-space step distances.
 * `mad` is robust against one dominant jump, `zscore` is not, and
 * `percentile` reads the sensitivity as "top k percent of frames".
 *
 * The `mad` branch needs a non-zero spread. Every frame recorded twice, a
 * quantized descriptor or rejected Monte Carlo steps drive the MAD to zero, and
 * `median + k * 0` is just the median - which flags about half the trajectory as
 * events. The backend falls back to `mean + k * std` and warns when it does
 * (see _trajectory_threshold); mirroring only the formula without that guard
 * made the panel's transitions, event rate and threshold describe an analysis
 * that was never run.
 */
export function trajectoryThreshold(steps: number[], stats: StepStats, method: EventMethod, sensitivity: number): number {
  if (method === "percentile") {
    if (!steps.length || sensitivity >= 50) return Number.POSITIVE_INFINITY;
    const sorted = steps.slice().sort((left, right) => left - right);
    const position = (sorted.length - 1) * (1 - sensitivity / 100);
    const lower = Math.floor(position);
    const upper = Math.ceil(position);
    return lower === upper ? sorted[lower] : sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
  }
  const robustSigma = MAD_SIGMA * stats.mad;
  if (method === "mad" && robustSigma > 0) return stats.median + sensitivity * robustSigma;
  return stats.mean + sensitivity * stats.std;
}

export function stepStats(steps: number[]): StepStats {
  if (!steps.length) return { median: 0, mad: 0, mean: 0, std: 0 };
  const sorted = steps.slice().sort((left, right) => left - right);
  const median = medianOfSorted(sorted);
  const deviations = steps.map((value) => Math.abs(value - median)).sort((left, right) => left - right);
  const mean = steps.reduce((sum, value) => sum + value, 0) / steps.length;
  const variance = steps.reduce((sum, value) => sum + (value - mean) ** 2, 0) / steps.length;
  return { median, mad: medianOfSorted(deviations), mean, std: Math.sqrt(variance) };
}

function medianOfSorted(sorted: number[]): number {
  const middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

/** Ordinal percentile of every step, matching numpy's stable argsort ordering. */
export function stepPercentiles(steps: number[]): number[] {
  const order = steps.map((_, index) => index).sort((left, right) => steps[left] - steps[right] || left - right);
  const percentiles = new Array<number>(steps.length);
  order.forEach((position, rank) => { percentiles[position] = (rank + 1) / steps.length; });
  return percentiles;
}

/** Order-preserving stride that returns at most `limit + 1` indices. */
export function decimate(count: number, stride: number, limit = 4000): number[] {
  const step = Math.max(1, Math.ceil(stride), Math.ceil(count / limit));
  const indices: number[] = [];
  for (let index = 0; index < count; index += step) indices.push(index);
  if (count && indices[indices.length - 1] !== count - 1) indices.push(count - 1);
  return indices;
}

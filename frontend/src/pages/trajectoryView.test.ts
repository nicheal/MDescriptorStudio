// The trajectory view re-derives the event threshold live so the sensitivity
// control stays interactive. These tests pin that derivation to the formula the
// backend uses (`_trajectory_threshold` in analysis/engine.py).
import { describe, expect, it } from "vitest";
import { decimate, MAD_SIGMA, stepPercentiles, stepStats, trajectoryThreshold } from "./trajectoryMath";

const STEPS = [1, 1, 1, 1, 1, 1, 1, 1, 1, 21];

describe("trajectoryThreshold", () => {
  const stats = stepStats(STEPS);

  it("uses median + k * 1.4826 * MAD when the steps have a MAD", () => {
    // STEPS is nine 1s and one 21, so its MAD is exactly 0 - the degenerate case
    // below. Spread the steps out to exercise the robust formula itself.
    const spread = [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 30];
    const robust = stepStats(spread);
    expect(robust.mad).toBeGreaterThan(0);
    expect(trajectoryThreshold(spread, robust, "mad", 3)).toBeCloseTo(robust.median + 3 * MAD_SIGMA * robust.mad, 12);
    expect(spread.filter((value) => value > trajectoryThreshold(spread, robust, "mad", 3))).toEqual([30]);
  });

  it("falls back to mean + k * sigma when the MAD is zero, as the backend does", () => {
    // Half the steps identical drives the MAD to 0, and `median + k * 0` is just
    // the median: about half the trajectory then reads as events. The backend
    // refuses that and uses the standard deviation instead, warning that it did.
    // This fixture has exactly that shape, and the old mirror of the formula
    // without the guard reported a transition the analysis never detected.
    expect(stats.mad).toBe(0);
    expect(trajectoryThreshold(STEPS, stats, "mad", 3)).toBeCloseTo(stats.mean + 3 * stats.std, 12);
    expect(trajectoryThreshold(STEPS, stats, "mad", 3)).not.toBeCloseTo(stats.median, 12);
    // z-score always takes that branch, so the two agree here.
    expect(trajectoryThreshold(STEPS, stats, "mad", 3))
      .toBeCloseTo(trajectoryThreshold(STEPS, stats, "zscore", 3), 12);
  });

  it("uses mean + k * sigma for the z-score variant", () => {
    expect(trajectoryThreshold(STEPS, stats, "zscore", 2.5)).toBeCloseTo(stats.mean + 2.5 * stats.std, 12);
  });

  it("uses the top percentage for the percentile variant", () => {
    // 10 steps: the top 10% lands between the smallest step (1) and the jump
    // (21), and the top 49% still equals the median step.
    expect(trajectoryThreshold(STEPS, stats, "percentile", 10)).toBeCloseTo(3, 12);
    expect(trajectoryThreshold(STEPS, stats, "percentile", 49)).toBeCloseTo(1, 12);
    // Above 50% the "top percent" would stop being a rare-event threshold.
    expect(trajectoryThreshold(STEPS, stats, "percentile", 50)).toBe(Number.POSITIVE_INFINITY);
    expect(trajectoryThreshold(STEPS, stats, "percentile", 90)).toBe(Number.POSITIVE_INFINITY);
  });

  it("returns no threshold for an empty step series", () => {
    expect(trajectoryThreshold([], stepStats([]), "percentile", 1)).toBe(Number.POSITIVE_INFINITY);
  });
});

describe("stepStats", () => {
  it("reports median, MAD, mean and standard deviation of the steps", () => {
    expect(stepStats([1, 2, 3, 4])).toEqual({ median: 2.5, mad: 1, mean: 2.5, std: Math.sqrt(1.25) });
  });

  it("degrades to zeros without steps", () => {
    expect(stepStats([])).toEqual({ median: 0, mad: 0, mean: 0, std: 0 });
  });
});

describe("stepPercentiles", () => {
  it("ranks the largest step at 100% and keeps ties in trajectory order", () => {
    expect(stepPercentiles([5, 1, 5, 3])).toEqual([0.75, 0.25, 1, 0.5]);
  });
});

describe("decimate", () => {
  it("keeps the first and last index while respecting the stride", () => {
    expect(decimate(10, 4)).toEqual([0, 4, 8, 9]);
    expect(decimate(3, 1)).toEqual([0, 1, 2]);
    expect(decimate(0, 5)).toEqual([]);
  });

  it("never returns more points than the rendering limit", () => {
    const indices = decimate(10_000, 1, 4_000);
    expect(indices.length).toBeLessThanOrEqual(4_001);
    expect(indices[0]).toBe(0);
    expect(indices[indices.length - 1]).toBe(9_999);
  });
});

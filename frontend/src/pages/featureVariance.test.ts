import { describe, expect, it, vi } from "vitest";

// Importing the module tree pulls in plotly's scattergl, whose svg-path-sdf
// dependency touches a 2D canvas while modules are still loading — before any
// test body could spy on it. jsdom has no canvas, so stub it at hoist time;
// the SDF atlas path is browser-only and irrelevant to these tests.
vi.hoisted(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});

import { buildKde } from "./featureVariance";

describe("buildKde", () => {
  const values = [0.1, 0.2, 0.2, 0.3, 0.9, 1.4, 1.5, 2.8];
  const edges = [0, 0.5, 1, 1.5, 2, 2.5, 3];

  it("is scaled to the population it is told about, not to the sample it holds", () => {
    // The detail panel draws its bars from every finite value while the curve is
    // fitted on a bounded sample that keeps every outlier. Passing the bar total
    // made a 61%-outlier sample read as the whole distribution's shape (pass 5,
    // 5-C1); the third argument is now the sample's own count.
    const small = buildKde(values, edges, 100);
    const large = buildKde(values, edges, 1000);
    expect(small.x.length).toBe(large.x.length);
    const ratio = large.y.map((y, index) => y / small.y[index]);
    ratio.forEach((value) => expect(value).toBeCloseTo(10, 6));
  });

  it("draws nothing when there is no spread to smooth", () => {
    expect(buildKde([1, 1, 1], edges, 3).y).toEqual([]);
    expect(buildKde([1], edges, 1).y).toEqual([]);
  });
});

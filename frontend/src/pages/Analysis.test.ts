import { describe, expect, it } from "vitest";
import { normalizePoints } from "./analysisPreview";

describe("Analysis preview mapping", () => {
  it("normalizes PCA and generic projection points while dropping invalid coordinates", () => {
    const points = normalizePoints({
      analysis_id: "ana-test",
      points: [
        { i: 3, frame: 12, pc1: 1.5, pc2: -2, sample_id: "frame:12" },
        { i: 4, frame: 13, x: 0.25, y: 0.75, row: 8, label: 2, score: 0.9 },
        { i: 5, frame: 14, x: "not-a-number", y: 1 },
      ],
    });

    expect(points).toHaveLength(2);
    expect(points[0]).toMatchObject({ i: 3, frame: 12, x: 1.5, y: -2, sample_id: "frame:12" });
    expect(points[1]).toMatchObject({ i: 4, frame: 13, row: 8, label: 2, score: 0.9, x: 0.25, y: 0.75 });
  });

  it("uses stable defaults for missing sample identity", () => {
    expect(normalizePoints({ analysis_id: "ana-test", points: [{ x: 1, y: 2 }] })).toEqual([
      { i: 0, frame: 0, row: undefined, sample_id: undefined, x: 1, y: 2, label: undefined, score: undefined, distance: undefined },
    ]);
  });
});

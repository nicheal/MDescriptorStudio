import { describe, expect, it } from "vitest";
import { displayableDescriptorRuns, normalizePoints, selectedDisplayIndices } from "./analysisPreview";
import type { RunRow } from "../types/protocol";

describe("Analysis preview mapping", () => {
  it("normalizes PCA and generic projection points while dropping invalid coordinates", () => {
    const points = normalizePoints({
      analysis_id: "ana-test",
      points: [
        { i: 3, frame: 12, pc1: 1.5, pc2: -2, sample_id: "frame:12" },
        { i: 4, frame: 13, x: 0.25, y: 0.75, row: 8, label: 2, score: 0.9, element: 33, cluster: 4 },
        { i: 5, frame: 14, x: "not-a-number", y: 1 },
      ],
    });

    expect(points).toHaveLength(2);
    expect(points[0]).toMatchObject({ i: 3, frame: 12, x: 1.5, y: -2, sample_id: "frame:12" });
    expect(points[1]).toMatchObject({ i: 4, frame: 13, row: 8, label: 2, score: 0.9, element: 33, cluster: 4, x: 0.25, y: 0.75 });
  });

  it("uses stable defaults for missing sample identity", () => {
    expect(normalizePoints({ analysis_id: "ana-test", points: [{ x: 1, y: 2 }] })).toEqual([
      { i: 0, frame: 0, row: undefined, sample_id: undefined, x: 1, y: 2, label: undefined, score: undefined, distance: undefined, element: undefined, cluster: undefined },
    ]);
  });

  it("maps logical sample selections to displayed positions after preview sampling", () => {
    const points = normalizePoints({
      analysis_id: "ana-test",
      points: [{ i: 10, frame: 10, x: 1, y: 1 }, { i: 42, frame: 42, x: 2, y: 2 }],
    });
    expect(selectedDisplayIndices(points, [42])).toEqual([1]);
  });

  it("keeps failed descriptor calculations out of descriptor results", () => {
    const rows = [
      { id: "completed", status: "COMPLETED" },
      { id: "running", status: "RUNNING" },
      { id: "failed", status: "FAILED" },
    ] as unknown as RunRow[];

    expect(displayableDescriptorRuns(rows).map((row) => row.id)).toEqual(["completed", "running"]);
  });
});

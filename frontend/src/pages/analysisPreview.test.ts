import { describe, expect, it } from "vitest";
import { previewRowFields, previewTableColumns, previewTableRows } from "./analysisPreview";
import type { AnalysisPreview } from "../types/protocol";

const preview = (value: Record<string, unknown>): AnalysisPreview => ({ analysis_id: "ana_1", ...value });

describe("previewTableRows", () => {
  it("takes the row list an analysis without coordinates still returns", () => {
    const rows = [{ i: 0, frame: 3, distances: 1.5 }];
    expect(previewTableRows(preview({ rows, selected: [{ i: 9, frame: 9 }] }))).toEqual(rows);
  });

  it("tabulates points once they carry a per-sample field", () => {
    const points = [{ i: 0, frame: 3, sample_id: "frame:3", x: 1.5, y: -2, label: 2, cluster: 1 }];
    expect(previewTableRows(preview({ points, selected: [{ i: 0, frame: 3 }] }))).toEqual(points);
  });

  it("falls back to the selection when the points hold nothing but identity", () => {
    // A plain sampling result attaches no array per sample: its table is the
    // samples it picked, not 20k points whose only columns are frame numbers.
    const points = [{ i: 0, frame: 3, sample_id: "frame:3", row: 1, x: 1.5, y: -2 }];
    const selected = [{ i: 0, frame: 3 }];
    expect(previewTableRows(preview({ points, selected }))).toEqual(selected);
  });

  it("ranks feature-importance output the way the table expects", () => {
    expect(previewTableRows(preview({ top_indices: [7, 2], top_values: [0.4, 0.1] }))).toEqual([
      { rank: 1, feature: 7, variance: 0.4 },
      { rank: 2, feature: 2, variance: 0.1 },
    ]);
  });

  it("returns nothing for a preview with no list at all", () => {
    expect(previewTableRows(null)).toEqual([]);
    expect(previewTableRows(preview({ kind: "pairwise", matrix: [[0]] }))).toEqual([]);
  });
});

describe("previewTableColumns", () => {
  it("keeps the assignment columns by ordering the plotted coordinates last", () => {
    const point = { i: 0, frame: 3, sample_id: "frame:3", row: 1, element: 31, x: 1.5, y: -2, label: 2, cluster: 1 };
    expect(previewTableColumns(point)).toEqual(["i", "frame", "sample_id", "row", "element", "label", "cluster"]);
  });

  it("leaves a coordinate-only row alone", () => {
    expect(previewTableColumns({ i: 0, x: 1, y: 2 })).toEqual(["i", "x", "y"]);
  });
});

describe("previewRowFields", () => {
  it("reads the singular names points carry", () => {
    expect(previewRowFields({ label: 1, score: 0.5, distance: 2, cluster: 3 })).toEqual({
      label: 1, score: 0.5, distance: 2, cluster: 3,
    });
  });

  it("reads the array names a row list passes straight through", () => {
    expect(previewRowFields({ labels: 1, scores: 0.5, distances: 2, cluster_labels: 3 })).toEqual({
      label: 1, score: 0.5, distance: 2, cluster: 3,
    });
  });

  it("leaves absent fields undefined rather than zero", () => {
    expect(previewRowFields({ i: 4, frame: 4 })).toEqual({ label: undefined, score: undefined, distance: undefined, cluster: undefined });
  });
});

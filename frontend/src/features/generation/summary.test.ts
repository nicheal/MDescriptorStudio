import { describe, expect, it } from "vitest";
import { discoveryStats, isActiveGenerationStatus, otherCandidateCount } from "./summary";

describe("generation summaries", () => {
  it("recognizes only queued and running rows as active", () => {
    expect(isActiveGenerationStatus("QUEUED")).toBe(true);
    expect(isActiveGenerationStatus("RUNNING")).toBe(true);
    expect(isActiveGenerationStatus("FAILED")).toBe(false);
  });

  it("calls unaccounted candidates not selected instead of low-novelty rejection", () => {
    expect(otherCandidateCount(14, 2, 3, 4)).toBe(5);
    expect(otherCandidateCount(3, 2, 2, 1)).toBe(0);
  });

  it("keeps missing discovery data unavailable instead of inventing zero counts", () => {
    expect(discoveryStats(null)).toBeNull();
    expect(discoveryStats({
      original_structures: 10,
      accepted_structures: 4,
      original_environments: 100,
      generated_environments: 20,
      novel_environments: 5,
    })).toEqual({ generated: 20, novel: 5, fraction: 25 });
  });
});

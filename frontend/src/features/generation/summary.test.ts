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

  it("keeps screened-out candidates out of the generic other bucket", () => {
    // 2026-10-02 audit D: an energy/force screening rejection is its own
    // label, not "other candidate not selected".
    expect(otherCandidateCount(14, 2, 3, 4, 2)).toBe(3);
    expect(otherCandidateCount(3, 2, 2, 1, 1)).toBe(0);
  });

  it("keeps missing discovery data unavailable instead of inventing zero counts", () => {
    expect(discoveryStats(null)).toBeNull();
    // gen-5 secondary metrics (strict_unique_v2 / archived rate) stay
    // undefined when an older run omits them; rejected_screening defaults
    // to 0 — the truthful count for runs without screening — which keeps
    // the archived display row hidden.
    expect(discoveryStats({
      original_structures: 10,
      accepted_structures: 4,
      original_environments: 100,
      generated_environments: 20,
      novel_environments: 5,
    })).toEqual({
      generated: 20,
      novel: 5,
      fraction: 25,
      strictUniqueV2: undefined,
      archivedNovel: undefined,
      rejectedScreening: 0,
    });
  });
});

import type { GenerationPcaDiscovery } from "./types";

export function isActiveGenerationStatus(status: string): boolean {
  return status === "QUEUED" || status === "RUNNING";
}

export function otherCandidateCount(
  proposed: number,
  rejectedGeometry: number,
  rejectedDuplicate: number,
  accepted: number,
  rejectedScreening = 0,
): number {
  // Screened-out candidates carry their own label (2026-10-02 audit D) —
  // they must not be absorbed into the generic "other" bucket.
  return Math.max(0, proposed - rejectedGeometry - rejectedDuplicate - accepted - rejectedScreening);
}

export function discoveryStats(discovery: GenerationPcaDiscovery | null | undefined) {
  if (!discovery) return null;
  return {
    generated: discovery.generated_environments,
    novel: discovery.novel_environments,
    uniqueNovel: discovery.unique_novel_environments,
    fraction: discovery.generated_environments > 0
      ? (discovery.novel_environments / discovery.generated_environments) * 100
      : 0,
  };
}

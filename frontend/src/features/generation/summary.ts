import type { GenerationPcaDiscovery } from "./types";

export function isActiveGenerationStatus(status: string): boolean {
  return status === "QUEUED" || status === "RUNNING";
}

export function otherCandidateCount(proposed: number, rejectedGeometry: number, rejectedDuplicate: number, accepted: number): number {
  return Math.max(0, proposed - rejectedGeometry - rejectedDuplicate - accepted);
}

export function discoveryStats(discovery: GenerationPcaDiscovery | null | undefined) {
  if (!discovery) return null;
  return {
    generated: discovery.generated_environments,
    novel: discovery.novel_environments,
    fraction: discovery.generated_environments > 0
      ? (discovery.novel_environments / discovery.generated_environments) * 100
      : 0,
  };
}

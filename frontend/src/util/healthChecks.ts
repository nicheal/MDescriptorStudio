// Shared data-health vocabulary: the canonical check keys the backend scan
// emits and their UI titles. Consumers translate titles through t() so the
// keys stay the single source of truth across rail, drawer, and inspector.
export const CHECK_KEYS = [
  "missing_values",
  "invalid_cell",
  "duplicate_structures",
  "extreme_force",
  "nonphysical_structures",
  "net_force",
] as const;

type HealthCheckKey = (typeof CHECK_KEYS)[number];

const CHECK_TITLES: Record<HealthCheckKey, string> = {
  missing_values: "Missing values",
  invalid_cell: "Invalid cell",
  duplicate_structures: "Duplicate structures",
  extreme_force: "Extreme force",
  nonphysical_structures: "Non-physical structures",
  net_force: "Net force",
};

export function healthCheckTitle(key: string, t: (k: string) => string): string {
  return t(CHECK_TITLES[key as HealthCheckKey] ?? key);
}

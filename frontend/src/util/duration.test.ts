import { describe, expect, it } from "vitest";
import { formatComputeDuration } from "./duration";

describe("formatComputeDuration", () => {
  it("formats elapsed descriptor compute time as a compact clock", () => {
    expect(formatComputeDuration("2026-01-01T00:00:00Z", "2026-01-01T00:02:05Z")).toBe("02:05");
    expect(formatComputeDuration("2026-01-01T00:00:00Z", "2026-01-01T01:02:05Z")).toBe("1:02:05");
  });

  it("shows an unavailable value when a run has no completed interval", () => {
    expect(formatComputeDuration(null, null)).toBe("—");
    expect(formatComputeDuration("not-a-date", "2026-01-01T00:00:01Z")).toBe("—");
    expect(formatComputeDuration("2026-01-01T00:00:02Z", "2026-01-01T00:00:01Z")).toBe("—");
  });
});

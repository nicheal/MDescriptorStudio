import { describe, expect, it } from "vitest";
import { formatNumber } from "./format";

describe("formatNumber", () => {
  it("shows integer indices and counts without decimal padding or grouping", () => {
    for (const value of [0, 1, -1, 999, 1000, 1234567, Number.MAX_SAFE_INTEGER]) {
      expect(formatNumber(value)).toBe(String(value));
      expect(formatNumber(value, 6)).toBe(String(value));
    }
  });

  it("retains fractional metrics and uses a decimal point without grouping", () => {
    expect(formatNumber(0.123456)).toBe("0.12346");
    expect(formatNumber(0.123456, 6)).toBe("0.123456");
    expect(formatNumber(12345.678)).toBe("12345.68");
    expect(formatNumber(-12345.678)).toBe("-12345.68");
    expect(Number(formatNumber(0.00000012345))).toBeCloseTo(0.00000012345, 12);
  });
});

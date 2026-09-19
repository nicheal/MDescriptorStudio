import { describe, expect, it } from "vitest";
import { createAnalysisCache, type CachedAnalysis } from "./analysisCache";

const value = (size: number): CachedAnalysis => ({
  preview: null,
  points: [],
  selectedIndices: [],
  arrays: { values: ["x".repeat(size)] },
  narrowed: [],
});

describe("analysis cache", () => {
  it("evicts least-recently-used entries by count", () => {
    const cache = createAnalysisCache({ maxEntries: 2, maxBytes: 10_000 });
    cache.set("a", value(10));
    cache.set("b", value(10));
    expect(cache.get("a")).toBeDefined();
    cache.set("c", value(10));
    expect(cache.get("b")).toBeUndefined();
    expect(cache.get("a")).toBeDefined();
  });

  it("evicts entries that exceed the byte budget", () => {
    const cache = createAnalysisCache({ maxEntries: 5, maxBytes: 100 });
    cache.set("large", value(200));
    expect(cache.size).toBe(0);
    expect(cache.bytes).toBe(0);
  });

  it("replaces a selection without re-walking the payload", () => {
    const cache = createAnalysisCache({ maxEntries: 2, maxBytes: 100_000 });
    cache.set("a", value(10));
    const before = cache.bytes;
    cache.setSelectedIndices("a", [1, 2, 3]);
    expect(cache.get("a")?.selectedIndices).toEqual([1, 2, 3]);
    // three numbers accounted for exactly, the walked payload left alone
    expect(cache.bytes).toBe(before + 24);
    expect(() => cache.setSelectedIndices("absent", [1])).not.toThrow();
  });
});

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("outlier algorithm regression", () => {
  it("keeps the selected algorithm dependency in the run callback", () => {
    const source = readFileSync(resolve(process.cwd(), "src/pages/Analysis.tsx"), "utf8");
    const callback = source.match(/const runTabAnalysis = useCallback\(async \(\) => \{([\s\S]*?)\n\s*\}, \[([\s\S]*?)\]\);/);
    expect(callback).not.toBeNull();
    expect(callback?.[1]).toContain("algorithm: outlierAlgorithm");
    expect(callback?.[2].split(",").map((dependency) => dependency.trim())).toContain("outlierAlgorithm");
  });
});

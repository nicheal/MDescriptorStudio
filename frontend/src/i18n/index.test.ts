// Completeness guard for the bilingual dictionary: every literal key passed to
// t("…") in the source must exist in zh.ts, so switching to Chinese never
// leaks untranslated UI text. Dynamic keys (t(variable)) that cannot be
// scanned are listed explicitly below.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { zhDict } from "./zh";

const SRC_DIR = join(process.cwd(), "src");

function collectFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      out.push(...collectFiles(full));
    } else if (/\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name)) {
      out.push(full);
    }
  }
  return out;
}

function extractLiteralKeys(source: string): string[] {
  const keys: string[] = [];
  const pattern = /\bt\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(source)) !== null) {
    const raw = match[1] ?? match[2] ?? "";
    try {
      keys.push(JSON.parse(`"${raw}"`));
    } catch {
      keys.push(raw);
    }
  }
  return keys;
}

// Keys reached through t(variable) rather than a literal at the call site.
const DYNAMIC_KEYS = [
  "Overview",
  "Explore",
  "Descriptors",
  "Results",
  "Analysis",
  "Similarity",
];

describe("zh dictionary completeness", () => {
  it("covers every literal t() key used in the source", () => {
    const used = new Set<string>(DYNAMIC_KEYS);
    for (const file of collectFiles(SRC_DIR)) {
      for (const key of extractLiteralKeys(readFileSync(file, "utf8"))) {
        used.add(key);
      }
    }
    const missing = [...used].filter((key) => !(key in zhDict));
    expect(missing, `missing zh translations for: ${missing.join(" | ")}`).toEqual([]);
  });

  it("resolves keys per language with {var} interpolation", async () => {
    const { translateKey } = await import("./index");
    expect(translateKey("en", "{n} structures", { n: 5 })).toBe("5 structures");
    expect(translateKey("zh", "{n} structures", { n: 5 })).toBe("5 个结构");
    expect(translateKey("en", "Refresh")).toBe("Refresh");
    expect(translateKey("zh", "Refresh")).toBe("刷新");
  });
});

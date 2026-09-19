import type { AnalysisPreview } from "../types/protocol";
import type { AnalysisPoint } from "./analysisPreview";
import type { AnalysisArrays } from "./analysisVisualizations";

export type CachedAnalysis = {
  preview: AnalysisPreview | null;
  points: AnalysisPoint[];
  selectedIndices: number[];
  arrays: AnalysisArrays;
};

export const MAX_ANALYSIS_CACHE_ENTRIES = 5;
export const MAX_ANALYSIS_CACHE_BYTES = 256 * 1024 * 1024;

type CacheEntry = { value: CachedAnalysis; bytes: number };

/** Disposable LRU for chart data; the backend artifact remains the source of truth. */
export function createAnalysisCache(options: { maxEntries?: number; maxBytes?: number } = {}) {
  const maxEntries = options.maxEntries ?? MAX_ANALYSIS_CACHE_ENTRIES;
  const maxBytes = options.maxBytes ?? MAX_ANALYSIS_CACHE_BYTES;
  const entries = new Map<string, CacheEntry>();
  let bytes = 0;

  const trim = () => {
    while (entries.size && (entries.size > maxEntries || bytes > maxBytes)) {
      const oldest = entries.keys().next().value as string | undefined;
      if (oldest == null) break;
      const removed = entries.get(oldest);
      entries.delete(oldest);
      bytes -= removed?.bytes ?? 0;
    }
  };

  return {
    get(key: string): CachedAnalysis | undefined {
      const entry = entries.get(key);
      if (!entry) return undefined;
      entries.delete(key);
      entries.set(key, entry);
      return entry.value;
    },
    set(key: string, value: CachedAnalysis): void {
      const previous = entries.get(key);
      if (previous) bytes -= previous.bytes;
      const entry = { value, bytes: estimateBytes(value) };
      entries.delete(key);
      entries.set(key, entry);
      bytes += entry.bytes;
      trim();
    },
    /** Replace only the selection, without re-walking the payload.
     *
     * A cached projection can carry 20k points across 18 fields; re-estimating
     * that on every click cost tens of milliseconds to record a change in a
     * list that is orders of magnitude smaller. Its exact size is tracked
     * separately so the byte total stays honest.
     */
    setSelectedIndices(key: string, indices: number[]): void {
      const entry = entries.get(key);
      if (!entry) return;
      bytes += (indices.length - entry.value.selectedIndices.length) * 8;
      entry.value = { ...entry.value, selectedIndices: indices };
    },
    delete(key: string): void {
      const previous = entries.get(key);
      if (!previous) return;
      entries.delete(key);
      bytes -= previous.bytes;
    },
    clear(): void {
      entries.clear();
      bytes = 0;
    },
    get size(): number {
      return entries.size;
    },
    get bytes(): number {
      return bytes;
    },
  };
}

function estimateBytes(value: unknown, seen = new Set<object>()): number {
  if (value == null || typeof value === "boolean") return 8;
  if (typeof value === "number") return 8;
  if (typeof value === "string") return value.length * 2;
  if (typeof value !== "object") return 0;
  if (seen.has(value)) return 0;
  seen.add(value);
  if (Array.isArray(value)) return 24 + value.reduce((sum, item) => sum + estimateBytes(item, seen), 0);
  return 32 + Object.entries(value).reduce((sum, [key, item]) => sum + key.length * 2 + estimateBytes(item, seen), 0);
}

export const analysisCache = createAnalysisCache();

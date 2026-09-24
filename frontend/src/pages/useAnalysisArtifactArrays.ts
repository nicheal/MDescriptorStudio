import { useEffect, useState } from "react";
import { ipc } from "../ipc/client";
import { artifactArraysForPreview } from "../features/analysis";
import type { AnalysisChunk, AnalysisPreview } from "../types/protocol";
import { analysisCache } from "./analysisCache";
import { narrowedArrays, normalizePoints } from "./analysisPreview";
import { selectedIndicesFromPreview, type NumericArrays } from "./analysisShared";

type ArtifactArraysState = {
  analysisId: string | null;
  preview: AnalysisPreview | null;
  retry: number;
};

/** Load only the arrays required by the active visualization and keep failures retryable. */
export function useAnalysisArtifactArrays({ analysisId, preview, retry }: ArtifactArraysState) {
  const [arrays, setArrays] = useState<NumericArrays>({});
  const [narrowed, setNarrowed] = useState<string[]>([]);
  const [settled, setSettled] = useState<ArtifactArraysState | null>(null);
  const [error, setError] = useState(false);
  // Gate the very first render too: an effect-only loading flag mounts the
  // WebGL charts, immediately purges them, then creates them again after I/O.
  const needsArrays = artifactArraysForPreview(String(preview?.kind ?? ""), String(preview?.algorithm ?? "")).length > 0;
  const busy = Boolean(analysisId && needsArrays && (
    settled?.analysisId !== analysisId || settled?.preview !== preview || settled?.retry !== retry
  ));

  useEffect(() => {
    const kind = String(preview?.kind ?? "");
    const arrayNames = artifactArraysForPreview(kind, String(preview?.algorithm ?? ""));
    setError(false);
    if (!analysisId || !arrayNames.length) {
      setArrays({});
      setNarrowed([]);
      setSettled({ analysisId, preview, retry });
      return;
    }

    const cached = analysisCache.get(analysisId);
    const cachedArrays = cached?.arrays ?? {};
    const missingArrays = arrayNames.filter((name) => !Object.prototype.hasOwnProperty.call(cachedArrays, name));
    if (!missingArrays.length) {
      setArrays(cachedArrays);
      setNarrowed(cached?.narrowed ?? []);
      setSettled({ analysisId, preview, retry });
      return;
    }

    let disposed = false;
    void Promise.all(missingArrays.map(async (name) => {
      try {
        const loadAll = (kind === "effective_dimension" && name === "explained_variance") || kind === "trajectory";
        const values: unknown[] = [];
        let offset = 0;
        let truncated = false;
        let rowsTotal = Number.NaN;
        while (true) {
          const chunk = await ipc.request<AnalysisChunk>("analysis.chunk", {
            analysis_id: analysisId,
            array: name,
            offset,
            limit: 20_000,
            column_end: 2_000,
          });
          values.push(...chunk.data);
          truncated = truncated || chunk.truncated;
          rowsTotal = Number(chunk.shape?.[0]);
          if (!loadAll) break;
          const nextOffset = Number(chunk.next_offset);
          if (!chunk.data.length || !Number.isFinite(nextOffset) || nextOffset <= offset || (Number.isFinite(rowsTotal) && nextOffset >= rowsTotal)) break;
          offset = nextOffset;
        }
        return { array: name, values, truncated, rows: values.length, total: rowsTotal } as const;
      } catch {
        return null;
      }
    })).then((entries) => {
      if (disposed) return;
      const loaded = entries.filter(
        (entry): entry is { array: string; values: unknown[]; truncated: boolean; rows: number; total: number } => entry !== null,
      );
      setError(loaded.length !== entries.length);
      const nextArrays = { ...cachedArrays, ...Object.fromEntries(loaded.map((entry) => [entry.array, entry.values] as const)) };
      const current = analysisCache.get(analysisId);
      const nextNarrowed = narrowedArrays([
        ...loaded,
        ...Array.from(new Set([...(current?.narrowed ?? []), ...(cached?.narrowed ?? [])])).map((array) => ({ array, truncated: true })),
      ]);
      analysisCache.set(analysisId, {
        preview: current?.preview ?? cached?.preview ?? preview,
        points: current?.points ?? cached?.points ?? normalizePoints(preview ?? { analysis_id: analysisId }),
        selectedIndices: current?.selectedIndices ?? cached?.selectedIndices ?? selectedIndicesFromPreview(preview ?? { analysis_id: analysisId }),
        arrays: nextArrays,
        narrowed: nextNarrowed,
      });
      setArrays(nextArrays);
      setNarrowed(nextNarrowed);
    }).finally(() => {
      if (!disposed) setSettled({ analysisId, preview, retry });
    });

    return () => { disposed = true; };
  }, [analysisId, preview, retry]);

  return { arrays, setArrays, narrowed, setNarrowed, busy, error };
}

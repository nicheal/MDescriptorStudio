import { createAsyncRequestGuard } from "../util/asyncRequestGuard";
import type { FramePayload } from "../types/protocol";

export type RequestFrame = (
  method: "dataset.frame",
  params: { id: string; index: number; bond_cutoff: number },
) => Promise<FramePayload>;

export interface ExploreFrameLoad {
  datasetId: string;
  total: number;
  index: number;
  bondCutoff: number;
  requestCutoff: number;
  onLoading: (loading: boolean) => void;
  onFrame: (frame: FramePayload, index: number) => void;
  onError?: (error: unknown) => void;
}

export interface ExploreFrameLoader {
  load(request: ExploreFrameLoad): Promise<void>;
  invalidate(): void;
}

export function createExploreFrameLoader(
  requestFrame: RequestFrame,
  getActiveDatasetId: () => string | null,
): ExploreFrameLoader {
  const guard = createAsyncRequestGuard();
  let activeLoad: { requestId: number; onLoading: (loading: boolean) => void } | null = null;

  return {
    async load(request) {
      const requestId = guard.next();
      const index = Math.max(0, Math.min(request.index, request.total - 1));
      activeLoad = { requestId, onLoading: request.onLoading };
      request.onLoading(true);
      try {
        const frame = await requestFrame("dataset.frame", {
          id: request.datasetId,
          index,
          bond_cutoff: request.requestCutoff,
        });
        if (!guard.isCurrent(requestId) || getActiveDatasetId() !== request.datasetId) return;
        request.onFrame({ ...frame, bond_cutoff: request.bondCutoff }, index);
      } catch (error) {
        if (guard.isCurrent(requestId)) request.onError?.(error);
      } finally {
        if (guard.isCurrent(requestId)) {
          request.onLoading(false);
          if (activeLoad?.requestId === requestId) activeLoad = null;
        }
      }
    },
    invalidate() {
      guard.invalidate();
      const previousLoad = activeLoad;
      activeLoad = null;
      previousLoad?.onLoading(false);
    },
  };
}

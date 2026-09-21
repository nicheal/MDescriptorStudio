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

/**
 * Where an external frame pointer sends the viewer while a fetch is in flight.
 *
 * A running `dataset.frame` request cannot be interrupted, so a jump arriving
 * mid-flight used to be dropped outright: the response then wrote the shared
 * pointer back to the frame it had been fetching, and the screen stayed put
 * while the panel that asked for the jump said it had made it (deep review pass
 * 4, E-5). Remembering the request and replaying it once nothing is in flight is
 * the whole rule, and it has to survive the pointer being overwritten by that
 * finishing response - which is why `pending` is an input, not local state.
 */
export function resolveExternalFrame(input: {
  pointer: number;
  displayed: number | null;
  loading: boolean;
  pending: number | null;
}): { pending: number | null; fetch: number | null } {
  const pending = input.pointer !== input.displayed ? input.pointer : input.pending;
  if (pending == null || pending === input.displayed) return { pending: null, fetch: null };
  if (input.loading) return { pending, fetch: null };
  return { pending: null, fetch: pending };
}

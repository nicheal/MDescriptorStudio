/**
 * Small generation guard for async UI requests. Starting a newer request or
 * invalidating the owner makes every older completion a no-op.
 */
export interface AsyncRequestGuard {
  next(): number;
  isCurrent(requestId: number): boolean;
  invalidate(): void;
}

export function createAsyncRequestGuard(): AsyncRequestGuard {
  let generation = 0;
  return {
    next: () => ++generation,
    isCurrent: (requestId) => requestId === generation,
    invalidate: () => {
      generation += 1;
    },
  };
}

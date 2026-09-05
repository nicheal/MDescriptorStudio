// Force-vector helpers shared by the Explore viewer's force arrows.
import type { FramePayload } from "../types/protocol";

export interface ForceArrowGeometry {
  /** |F| in eV/Å, recomputed from the components so length and direction agree. */
  magnitude: number;
  /** Arrow length in Å. */
  length: number;
  /** Unit vector along +F (acceleration direction). */
  dir: { x: number; y: number; z: number };
}

/**
 * Arrow length is normalized per frame: the strongest force in the frame
 * renders at `targetLength` Å, then the user multiplier applies. The result
 * is clamped to `[minLength, maxLength]` so weak forces still produce a
 * visible arrow and an aggressive multiplier cannot stretch it past the
 * view. Returns null when the atom has no usable force components or the
 * force is exactly zero.
 */
export function forceArrowGeometry(
  fx: number | null,
  fy: number | null,
  fz: number | null,
  frameMaxForce: number,
  multiplier: number,
  targetLength = 3.0,
  maxLength = 12,
  minLength = 1.2,
): ForceArrowGeometry | null {
  if (fx == null || fy == null || fz == null) return null;
  if (![fx, fy, fz, frameMaxForce, multiplier].every(Number.isFinite)) return null;
  if (frameMaxForce <= 1e-9 || multiplier <= 0) return null;
  const magnitude = Math.sqrt(fx * fx + fy * fy + fz * fz);
  if (magnitude <= 1e-9) return null;
  const length = Math.min(
    Math.max((magnitude / frameMaxForce) * targetLength * multiplier, minLength),
    maxLength,
  );
  return { magnitude, length, dir: { x: fx / magnitude, y: fy / magnitude, z: fz / magnitude } };
}

/** Largest |F| (eV/Å) across the frame's real atoms; 0 when no forces exist. */
export function frameMaxForce(frame: FramePayload): number {
  let max = 0;
  for (const row of frame.atom_rows) {
    if (row.fx == null || row.fy == null || row.fz == null) continue;
    const magnitude = Math.sqrt(row.fx * row.fx + row.fy * row.fy + row.fz * row.fz);
    if (Number.isFinite(magnitude) && magnitude > max) max = magnitude;
  }
  return max;
}

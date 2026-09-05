// Frame-level structure metrics for the Explore Structure Inspector: cell
// parameters, mass density, shortest interatomic distance, net force. All
// operate on the payload as delivered by `dataset.frame` (xyz may contain
// periodic ghost images beyond natoms).
import { ATOMIC_MASS } from "./elements";

export interface CellParameters {
  /** Lattice vector lengths in Å. */
  a: number;
  b: number;
  c: number;
  /** Inter-vector angles in degrees: α = b∧c, β = a∧c, γ = a∧b. */
  alpha: number;
  beta: number;
  gamma: number;
}

/** amu → g/cm³ conversion factor: 1 amu = 1.6605…e-24 g and 1 Å³ = 1e-24 cm³. */
const AMU_PER_A3_TO_G_CM3 = 1.66053906660;

/**
 * Lattice lengths and inter-vector angles from a row-major cell
 * [a1x, a1y, a1z, a2x, …, a3z] (the layout used by the unit-cell wireframe).
 * Returns null for a malformed or degenerate cell.
 */
export function cellParameters(cell: number[]): CellParameters | null {
  if (!cell || cell.length !== 9) return null;
  const v1 = [cell[0], cell[1], cell[2]];
  const v2 = [cell[3], cell[4], cell[5]];
  const v3 = [cell[6], cell[7], cell[8]];
  const len = (v: number[]) => Math.hypot(v[0], v[1], v[2]);
  const a = len(v1);
  const b = len(v2);
  const c = len(v3);
  if (![a, b, c].every((l) => Number.isFinite(l) && l > 1e-9)) return null;
  const angleDeg = (u: number[], v: number[]) => {
    const cos = (u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / (len(u) * len(v));
    return (Math.acos(Math.min(1, Math.max(-1, cos))) * 180) / Math.PI;
  };
  return {
    a,
    b,
    c,
    alpha: angleDeg(v2, v3),
    beta: angleDeg(v1, v3),
    gamma: angleDeg(v1, v2),
  };
}

// Hash-cell size (Å) for the shortest-distance scan: any pair closer than
// this lands in the 27-cell neighborhood. Sparse frames with nothing this
// close are small by construction and fall back to an exact O(N²) scan.
const MIN_DIST_CELL = 3.0;

/** Closest atom pair behind minimumInteratomicDistance. */
export interface MinDistancePair {
  /** Center-to-center distance in Å. */
  distance: number;
  /** Indices into the frame's xyz block (ghost images sit beyond natoms). */
  i: number;
  j: number;
}

/**
 * Closest pair of atoms across the frame's xyz block, including ghost images,
 * so the result reflects the periodic structure across cell boundaries and the
 * indices resolve inside the viewer's parsed atoms. Coincident points
 * (≤ 1e-6 Å, the same duplicates the bond parser skips) are ignored; null when
 * fewer than two usable atoms remain.
 */
export function minimumDistancePair(xyz: string): MinDistancePair | null {
  const lines = xyz.trim().split(/\r?\n/);
  const atomCount = Number(lines[0]);
  if (!Number.isInteger(atomCount) || atomCount <= 0) return null;

  const atoms: [number, number, number][] = [];
  for (let i = 0; i < atomCount; i += 1) {
    const tokens = lines[i + 2]?.trim().split(/\s+/) ?? [];
    const x = Number(tokens[1]);
    const y = Number(tokens[2]);
    const z = Number(tokens[3]);
    if (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z)) atoms.push([x, y, z]);
  }
  if (atoms.length < 2) return null;

  const cellSize = MIN_DIST_CELL;
  const cells = new Map<string, number[]>();
  const key = (x: number, y: number, z: number) =>
    `${Math.floor(x / cellSize)},${Math.floor(y / cellSize)},${Math.floor(z / cellSize)}`;
  atoms.forEach((p, index) => {
    const k = key(p[0], p[1], p[2]);
    const bucket = cells.get(k);
    if (bucket) bucket.push(index);
    else cells.set(k, [index]);
  });

  const minSquaredWithinCell = 3.0 * 3.0;
  let minSquared = Infinity;
  let bestI = -1;
  let bestJ = -1;
  for (let i = 0; i < atoms.length; i += 1) {
    const [x, y, z] = atoms[i];
    const cx = Math.floor(x / cellSize);
    const cy = Math.floor(y / cellSize);
    const cz = Math.floor(z / cellSize);
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dy = -1; dy <= 1; dy += 1) {
        for (let dz = -1; dz <= 1; dz += 1) {
          for (const j of cells.get(`${cx + dx},${cy + dy},${cz + dz}`) ?? []) {
            if (j <= i) continue;
            const q = atoms[j];
            const d2 = (x - q[0]) ** 2 + (y - q[1]) ** 2 + (z - q[2]) ** 2;
            if (d2 <= 1e-12 || d2 >= minSquared) continue;
            if (d2 < minSquaredWithinCell) {
              minSquared = d2;
              bestI = i;
              bestJ = j;
            }
          }
        }
      }
    }
  }
  if (Number.isFinite(minSquared)) return { distance: Math.sqrt(minSquared), i: bestI, j: bestJ };

  // Nothing within the hash radius: exact scan (sparse frames are small).
  for (let i = 0; i < atoms.length; i += 1) {
    for (let j = i + 1; j < atoms.length; j += 1) {
      const p = atoms[i];
      const q = atoms[j];
      const d2 = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2;
      if (d2 > 1e-12 && d2 < minSquared) {
        minSquared = d2;
        bestI = i;
        bestJ = j;
      }
    }
  }
  return Number.isFinite(minSquared) ? { distance: Math.sqrt(minSquared), i: bestI, j: bestJ } : null;
}

/**
 * Shortest center-to-center distance (Å) across the frame's xyz block,
 * including ghost images, so the value reflects the periodic structure
 * across cell boundaries. Coincident points (≤ 1e-6 Å, the same duplicates
 * the bond parser skips) are ignored; null when fewer than two usable atoms
 * remain.
 */
export function minimumInteratomicDistance(xyz: string): number | null {
  const pair = minimumDistancePair(xyz);
  return pair ? pair.distance : null;
}

/**
 * |ΣF| (eV/Å) over the frame's real atoms. Requires every atom to carry a
 * complete force vector — a partial sum would silently understate the net.
 */
export function netForceMagnitude(
  rows: { fx: number | null; fy: number | null; fz: number | null }[],
): number | null {
  if (!rows.length) return null;
  let sx = 0;
  let sy = 0;
  let sz = 0;
  for (const row of rows) {
    if (row.fx == null || row.fy == null || row.fz == null) return null;
    sx += row.fx;
    sy += row.fy;
    sz += row.fz;
  }
  return Math.sqrt(sx * sx + sy * sy + sz * sz);
}

/**
 * Mass density (g/cm³) from standard atomic weights and the cell volume.
 * Returns null when the volume is unusable or any element is outside the
 * mass table — a partial mass would produce a plausible-looking wrong value.
 */
export function massDensity(elements: string[], volumeA3: number | null): number | null {
  if (!elements.length || volumeA3 == null || !Number.isFinite(volumeA3) || volumeA3 <= 1e-9) {
    return null;
  }
  let totalAmu = 0;
  for (const el of elements) {
    const mass = ATOMIC_MASS[el];
    if (mass == null) return null;
    totalAmu += mass;
  }
  return (totalAmu * AMU_PER_A3_TO_G_CM3) / volumeA3;
}

/** Display-ready view of a frame's virial tensor W (eV) for the Structure
 * Inspector: the raw row-major 3×3 plus symmetry-derived compact forms. */
export interface VirialSummary {
  /** the 3×3 tensor, [row][col], exactly as stored in the source frame */
  rows: [number, number, number][];
  /** W equals its transpose within the payload's rounding, so a Voigt-ordered
   * row cannot misrepresent the tensor */
  symmetric: boolean;
  /** [xx, yy, zz, yz, xz, xy] when symmetric; null otherwise */
  voigt: [number, number, number, number, number, number] | null;
}

/**
 * Parse `dataset.frame`'s flattened row-major virial. Sources disagree on the
 * stress sign convention, so nothing here is normalized beyond symmetry
 * detection — the components stay exactly as stored. Null for absent or
 * malformed payloads.
 */
export function virialSummary(virial: number[] | null | undefined): VirialSummary | null {
  if (!virial || virial.length !== 9 || virial.some((v) => !Number.isFinite(v))) return null;
  const [xx, xy, xz, yx, yy, yz, zx, zy, zz] = virial;
  const symmetric =
    Math.abs(xy - yx) <= 1e-6 && Math.abs(xz - zx) <= 1e-6 && Math.abs(yz - zy) <= 1e-6;
  return {
    rows: [[xx, xy, xz], [yx, yy, yz], [zx, zy, zz]],
    symmetric,
    voigt: symmetric ? [xx, yy, zz, yz, xz, xy] : null,
  };
}

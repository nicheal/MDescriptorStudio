// Shared 3Dmol viewer model builder: converts a frame payload into the atom
// list 3Dmol renders, with optional periodic images and a spatial-hash bond
// pass so large frames avoid an O(N²) pair scan.
import type { FramePayload } from "../types/protocol";

export type ViewerAtom = {
  elem: string;
  x: number;
  y: number;
  z: number;
  bonds: number[];
  bondOrder: number[];
  // Index in the viewer model. Real atoms use their Atom Table index; a
  // periodic image may additionally carry its real-atom parent index.
  i: number;
  parent?: number;
};

// Atom record 3Dmol hands back from a click callback; only the custom indices
// attached in parseViewerAtoms matter here.
export type ClickedAtom = Pick<ViewerAtom, "i" | "parent">;

export type ViewerModel = {
  addAtoms: (atoms: ViewerAtom[]) => void;
};

export function parseViewerAtoms(frame: FramePayload, cutoff: number, includePeriodicImages = false): ViewerAtom[] {
  // atom_rows is always the real-atom block. Periodic images are consumed only
  // by local-shell rendering, so the ordinary viewer remains inside the cell.
  const lines = frame.xyz.trim().split(/\r?\n/);
  const xyzAtomCount = Number.parseInt(lines[0]?.trim() ?? "", 10);
  const parsedAtomCount = Number.isFinite(xyzAtomCount)
    ? Math.max(0, Math.min(xyzAtomCount, lines.length - 2))
    : 0;
  const declaredGhostCount = Number.isFinite(frame.ghost_count)
    ? Math.max(0, Math.floor(frame.ghost_count))
    : 0;
  const geometryRealCount = Math.min(
    Math.max(0, frame.geometry_atom_count ?? frame.natoms),
    Math.max(0, frame.natoms),
    Math.max(0, parsedAtomCount - declaredGhostCount),
  );
  const rowsByIndex = new Map(frame.atom_rows.map((row) => [row.i, row]));
  const atoms: ViewerAtom[] = [];
  for (let index = 0; index < geometryRealCount; index += 1) {
    const row = rowsByIndex.get(index);
    if (row) {
      atoms.push({ elem: row.el, x: row.x, y: row.y, z: row.z, bonds: [], bondOrder: [], i: index });
      continue;
    }
    const parts = lines[index + 2]?.trim().split(/\s+/) ?? [];
    const x = Number(parts[1]);
    const y = Number(parts[2]);
    const z = Number(parts[3]);
    if (parts.length >= 4 && [x, y, z].every(Number.isFinite)) {
      atoms.push({ elem: parts[0], x, y, z, bonds: [], bondOrder: [], i: index });
    }
  }
  if (!atoms.length) return [];

  if (includePeriodicImages && frame.xyz) {
    const displayedAtomCount = Math.min(parsedAtomCount, geometryRealCount + declaredGhostCount);
    for (let xyzIndex = geometryRealCount; xyzIndex < displayedAtomCount; xyzIndex += 1) {
      const parts = lines[xyzIndex + 2]?.trim().split(/\s+/) ?? [];
      if (parts.length < 4) continue;
      const x = Number(parts[1]);
      const y = Number(parts[2]);
      const z = Number(parts[3]);
      if (![x, y, z].every(Number.isFinite)) continue;
      const parent = frame.ghost_parents?.[xyzIndex - geometryRealCount];
      const parentIndex = typeof parent === "number" && Number.isInteger(parent) && parent >= 0 && parent < geometryRealCount ? parent : undefined;
      atoms.push({
        elem: parts[0],
        x,
        y,
        z,
        bonds: [],
        bondOrder: [],
        i: atoms.length,
        ...(parentIndex != null ? { parent: parentIndex } : {}),
      });
    }
  }

  // Use a spatial hash so large frames do not require an O(N²) pair scan.
  const cells = new Map<string, number[]>();
  const cellKey = (x: number, y: number, z: number) =>
    `${Math.floor(x / cutoff)},${Math.floor(y / cutoff)},${Math.floor(z / cutoff)}`;
  atoms.forEach((atom, index) => {
    const key = cellKey(atom.x, atom.y, atom.z);
    const bucket = cells.get(key);
    if (bucket) bucket.push(index);
    else cells.set(key, [index]);
  });

  const cutoffSquared = cutoff * cutoff;
  for (let i = 0; i < atoms.length; i += 1) {
    const atom = atoms[i];
    const cx = Math.floor(atom.x / cutoff);
    const cy = Math.floor(atom.y / cutoff);
    const cz = Math.floor(atom.z / cutoff);
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dy = -1; dy <= 1; dy += 1) {
        for (let dz = -1; dz <= 1; dz += 1) {
          const candidates = cells.get(`${cx + dx},${cy + dy},${cz + dz}`) ?? [];
          for (const j of candidates) {
            if (j <= i) continue;
            const other = atoms[j];
            const distanceSquared =
              (atom.x - other.x) ** 2 + (atom.y - other.y) ** 2 + (atom.z - other.z) ** 2;
            if (distanceSquared <= 1e-6 || distanceSquared > cutoffSquared) continue;
            atom.bonds.push(j);
            atom.bondOrder.push(1);
            other.bonds.push(i);
            other.bondOrder.push(1);
          }
        }
      }
    }
  }
  return atoms;
}

/** Atoms within `cutoff` of one atom, nearest first, excluding the atom itself
 * and any image standing exactly on it. Shared by the preview card and the
 * Explore page so a local shell means the same thing in both. */
export function neighborsWithinCutoff(
  atoms: ViewerAtom[],
  selectedAtom: number,
  cutoff: number,
): { index: number; distance: number; parent?: number }[] {
  const center = atoms[selectedAtom];
  if (!center) return [];
  return atoms
    .map((atom, index) => ({
      index,
      parent: atom.parent,
      distance: Math.sqrt((atom.x - center.x) ** 2 + (atom.y - center.y) ** 2 + (atom.z - center.z) ** 2),
    }))
    .filter(({ index, distance }) => index !== selectedAtom && distance > 1e-6 && distance <= cutoff)
    .sort((left, right) => left.distance - right.distance);
}

/** The 1-based page of a paginated atom table that shows `atom`, or null when
 * the row is not in `rows`. Kept out of the component so the page a selection
 * lands on is testable: the table's own order, not the atom number, decides it. */
export function atomPageFor(rows: { i: number }[], atom: number, pageSize: number): number | null {
  const position = rows.findIndex((row) => row.i === atom);
  return position < 0 ? null : Math.floor(position / pageSize) + 1;
}

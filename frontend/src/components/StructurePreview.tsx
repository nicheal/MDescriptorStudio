import { useEffect, useRef, useState } from "react";
import { elementColor } from "../util/elements";
import { useT } from "../i18n";
import type { FramePayload } from "../types/protocol";

type ViewerAtom = {
  elem: string;
  x: number;
  y: number;
  z: number;
  bonds: number[];
  bondOrder: number[];
  // Index in the parsed payload (real atoms 0..natoms-1, periodic images
  // after), kept on the atom so a viewer click can identify it.
  i: number;
  // For periodic images: the real atom this image mirrors.
  parent?: number;
};

// Atom record 3Dmol hands back from a click callback; only the custom
// identity fields attached in parseViewerAtoms matter here.
type ClickedAtom = Pick<ViewerAtom, "i" | "parent">;

type ViewerModel = {
  addAtoms: (atoms: ViewerAtom[]) => void;
};

type Viewer = {
  clear: () => void;
  addModel: () => ViewerModel;
  addLine: (spec: object) => void;
  addSphere?: (spec: object) => void;
  setStyle: (sel: object, style: object) => void;
  addStyle: (sel: object, style: object) => void;
  setClickable: (sel: object, clickable: boolean, callback: (atom: ClickedAtom) => void) => void;
  zoomTo: () => void;
  render: () => void;
};

function parseViewerAtoms(frame: FramePayload): ViewerAtom[] {
  const cutoff = Math.max(0.1, frame.bond_cutoff || 2.4);
  const atoms: ViewerAtom[] = frame.atom_rows.map((row) => ({
    elem: row.el,
    x: row.x,
    y: row.y,
    z: row.z,
    bonds: [],
    bondOrder: [],
    i: row.i,
  }));
  // ``atom_rows`` intentionally contains only real atoms because its indices
  // are used for navigation. The XYZ payload additionally contains periodic
  // image atoms; include those images in the viewer/local-shell graph without
  // changing the original atom index space.
  const xyzLines = frame.xyz.trim().split(/\r?\n/);
  const xyzCount = Number(xyzLines[0]);
  if (Number.isInteger(xyzCount) && xyzCount > atoms.length) {
    for (let index = atoms.length; index < xyzCount; index += 1) {
      const [elem, xText, yText, zText] = xyzLines[index + 2]?.trim().split(/\s+/) ?? [];
      const x = Number(xText);
      const y = Number(yText);
      const z = Number(zText);
      if (!elem || !Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue;
      atoms.push({ elem, x, y, z, bonds: [], bondOrder: [], i: atoms.length });
    }
  }
  // Periodic images sit at indices >= natoms in payload order; record which
  // real atom each mirrors so a click on an image selects the real atom.
  frame.ghost_parents?.forEach((parent, k) => {
    const atom = atoms[frame.natoms + k];
    if (atom) atom.parent = parent;
  });
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

function addUnitCell(viewer: Viewer, cell: number[] | null) {
  if (!cell || cell.length !== 9) return;
  const point = (i: number, j: number, k: number) => ({
    x: i * cell[0] + j * cell[3] + k * cell[6],
    y: i * cell[1] + j * cell[4] + k * cell[7],
    z: i * cell[2] + j * cell[5] + k * cell[8],
  });
  const edges: [number, number, number, number, number, number][] = [
    [0, 0, 0, 1, 0, 0], [0, 0, 0, 0, 1, 0], [0, 0, 0, 0, 0, 1],
    [1, 1, 1, 0, 1, 1], [1, 1, 1, 1, 0, 1], [1, 1, 1, 1, 1, 0],
    [1, 0, 0, 1, 1, 0], [1, 0, 0, 1, 0, 1],
    [0, 1, 0, 1, 1, 0], [0, 1, 0, 0, 1, 1],
    [0, 0, 1, 1, 0, 1], [0, 0, 1, 0, 1, 1],
  ];
  for (const [i1, j1, k1, i2, j2, k2] of edges) {
    viewer.addLine({
      start: point(i1, j1, k1),
      end: point(i2, j2, k2),
      color: "#0F6CBD",
      opacity: 0.72,
      linewidth: 1.5,
    });
  }
}

function localNeighbors(atoms: ViewerAtom[], selectedAtom: number, cutoff: number): { index: number; distance: number }[] {
  const center = atoms[selectedAtom];
  if (!center) return [];
  return atoms
    .map((atom, index) => ({
      index,
      distance: Math.sqrt((atom.x - center.x) ** 2 + (atom.y - center.y) ** 2 + (atom.z - center.z) ** 2),
    }))
    .filter(({ index, distance }) => index !== selectedAtom && distance > 1e-6 && distance <= cutoff)
    .sort((left, right) => left.distance - right.distance);
}

interface StructurePreviewProps {
  frame: FramePayload;
  onOpen: () => void;
  selectedAtom?: number;
  localCutoff?: number;
  /** Click-to-select on the viewer; receives the clicked real-atom index. */
  onSelectAtom?: (atom: number) => void;
}

export default function StructurePreview({ frame, onOpen, selectedAtom, localCutoff, onSelectAtom }: StructurePreviewProps) {
  const { t } = useT();
  const viewerDiv = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<Viewer | null>(null);
  const [viewerReady, setViewerReady] = useState(false);
  const [viewerError, setViewerError] = useState<string | null>(null);
  // The 3Dmol click callback is registered inside the render effect; route it
  // through a ref so every click dispatches to the latest handler.
  const selectHandlerRef = useRef(onSelectAtom);
  selectHandlerRef.current = onSelectAtom;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const mod = await import("3dmol");
        const $3Dmol = ((mod as { default?: unknown }).default ?? mod) as {
          createViewer: (element: HTMLElement, options: object) => Viewer;
        };
        if (cancelled || !viewerDiv.current) return;
        viewerRef.current = $3Dmol.createViewer(viewerDiv.current, { backgroundColor: "white" });
        setViewerReady(true);
      } catch (error) {
        console.error("structure preview init failed", error);
        setViewerError(String(error));
      }
    })();

    return () => {
      cancelled = true;
      setViewerReady(false);
      viewerRef.current?.clear();
      viewerRef.current = null;
      if (viewerDiv.current) viewerDiv.current.innerHTML = "";
    };
  }, []);

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewerReady || !viewer) return;
    viewer.clear();
    const atoms = parseViewerAtoms(frame);
    const model = viewer.addModel();
    model.addAtoms(atoms);
    for (const element of new Set(atoms.map((atom) => atom.elem))) {
      const color = elementColor(element);
      viewer.setStyle(
        { elem: element },
        { sphere: { scale: 0.24, color }, stick: { radius: 0.1, color } },
      );
    }
    if (selectedAtom != null && selectedAtom >= 0 && selectedAtom < atoms.length) {
      viewer.addStyle({ index: selectedAtom }, { sphere: { scale: 0.44, color: "#D13438" }, stick: { radius: 0.15, color: "#D13438" } });
      if (localCutoff != null && Number.isFinite(localCutoff)) {
        const neighbors = localNeighbors(atoms, selectedAtom, Math.max(0.1, Math.min(10, localCutoff)));
        const center = atoms[selectedAtom];
        for (const neighbor of neighbors) {
          viewer.addStyle({ index: neighbor.index }, { sphere: { scale: 0.34, color: "#F7630C" }, stick: { radius: 0.13, color: "#F7630C" } });
          viewer.addLine({ start: center, end: atoms[neighbor.index], color: "#F7630C", opacity: 0.7, linewidth: 2 });
        }
        viewer.addSphere?.({ center, radius: Math.max(0.1, Math.min(10, localCutoff)), color: "#F7630C", opacity: 0.12, wireframe: true });
      }
    }
    addUnitCell(viewer, frame.cell);
    // Click-to-select: atoms report their payload index; periodic images map
    // back to their parent real atom. Registered before render() so 3Dmol
    // builds the picking intersection shapes.
    viewer.setClickable({}, true, (atom) => {
      const target = atom.parent ?? atom.i;
      if (target != null && target >= 0 && target < frame.natoms) selectHandlerRef.current?.(target);
    });
    viewer.zoomTo();
    viewer.render();
  }, [frame, localCutoff, selectedAtom, viewerReady]);

  return (
    <div className="results-structure-preview-shell">
      <div
        ref={viewerDiv}
        className="results-structure-viewer"
        aria-label={t("Structure preview for frame {index}", { index: frame.index })}
      />
      {viewerError && <div className="results-structure-viewer-error">viewer error: {viewerError}</div>}
      {selectedAtom != null && localCutoff != null && <div className="results-structure-local-badge">{t("Local shell ≤ {cutoff} Å", { cutoff: localCutoff.toFixed(2) })}</div>}
      <button type="button" className="results-structure-open" onClick={onOpen}>
        {t("Open frame {index} in Explore", { index: frame.index })}
      </button>
    </div>
  );
}

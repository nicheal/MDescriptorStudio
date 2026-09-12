// Explore page (M2): 3Dmol viewer dominant (~70%), Structure Inspector (30%),
// frame navigation, Atom Table. Design doc §18/§89, ADR-10 perf targets.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Empty, InputNumber, Space, Table, Tooltip, Typography } from "antd";
import {
  ArrowLeft16Regular,
  ArrowRight16Regular,
  ArrowShuffle16Regular,
  ArrowFit16Regular,
  Warning16Filled,
} from "@fluentui/react-icons";
import { ipc } from "../ipc/client";
import { activeDataset, useWorkspace } from "../stores/workspace";
import { useT } from "../i18n";
import { elementColor } from "../util/elements";
import { forceArrowGeometry, frameMaxForce } from "../util/forces";
import { cellParameters, massDensity, minimumDistancePair, netForceMagnitude, virialSummary } from "../util/structure";
import { CHECK_KEYS, healthCheckTitle } from "../util/healthChecks";
import type { DatasetHealth, FramePayload, HealthFindings } from "../types/protocol";

const DEFAULT_BOND_CUTOFF = 2.4;
const MIN_BOND_CUTOFF = 0.1;
const MAX_BOND_CUTOFF = 10;
// data-health severity color for the inspector: rows behind a flagged check
// (and the banner listing them) render in this red
const HEALTH_RED = "#D13438";
// localized labels for the per-frame missing-properties inspector row
const MISSING_PROP_LABELS: Record<string, string> = { energy: "Energy", forces: "Forces", virial: "Virial" };
// Force arrows are normalized per frame: the strongest force in the frame
// renders at this length (Å) before the user multiplier applies. The length
// is measured from the atom center, and ARROW_START_OFFSET keeps the tail
// outside the highlight sphere so the shaft stays visible.
const FORCE_ARROW_TARGET_LENGTH = 3.0;
const FORCE_ARROW_START_OFFSET = 0.75;
const FORCE_ARROW_COLOR = "#B4009E";

type ViewerAtom = {
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
type ClickedAtom = Pick<ViewerAtom, "i" | "parent">;

type ViewerModel = {
  addAtoms: (atoms: ViewerAtom[]) => void;
};

function parseViewerAtoms(frame: FramePayload, cutoff: number, includePeriodicImages = false): ViewerAtom[] {
  // atom_rows is always the real-atom block. Periodic images are consumed only
  // by local-shell rendering, so the ordinary viewer remains inside the cell.
  const realAtomCount = Math.max(0, frame.natoms);
  const atoms: ViewerAtom[] = frame.atom_rows
    .slice(0, realAtomCount)
    .map((row, index) => ({ elem: row.el, x: row.x, y: row.y, z: row.z, bonds: [], bondOrder: [], i: index }));
  if (!atoms.length) return [];

  if (includePeriodicImages && frame.xyz) {
    const lines = frame.xyz.trim().split(/\r?\n/);
    const xyzAtomCount = Number.parseInt(lines[0]?.trim() ?? "", 10);
    const parsedAtomCount = Number.isFinite(xyzAtomCount)
      ? Math.max(0, Math.min(xyzAtomCount, lines.length - 2))
      : 0;
    const declaredGhostCount = Number.isFinite(frame.ghost_count)
      ? Math.max(0, Math.floor(frame.ghost_count))
      : Math.max(0, parsedAtomCount - realAtomCount);
    const displayedAtomCount = Math.min(parsedAtomCount, realAtomCount + declaredGhostCount);
    for (let xyzIndex = realAtomCount; xyzIndex < displayedAtomCount; xyzIndex += 1) {
      const parts = lines[xyzIndex + 2]?.trim().split(/\s+/) ?? [];
      if (parts.length < 4) continue;
      const x = Number(parts[1]);
      const y = Number(parts[2]);
      const z = Number(parts[3]);
      if (![x, y, z].every(Number.isFinite)) continue;
      const parent = frame.ghost_parents?.[xyzIndex - realAtomCount];
      const parentIndex = typeof parent === "number" && Number.isInteger(parent) && parent >= 0 && parent < atoms.length ? parent : undefined;
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
  const cellCoords = (value: number) => Math.floor(value / cutoff);
  atoms.forEach((atom, index) => {
    const key = cellKey(atom.x, atom.y, atom.z);
    const bucket = cells.get(key);
    if (bucket) bucket.push(index);
    else cells.set(key, [index]);
  });

  const cutoffSquared = cutoff * cutoff;
  for (let i = 0; i < atoms.length; i += 1) {
    const atom = atoms[i];
    const cx = cellCoords(atom.x);
    const cy = cellCoords(atom.y);
    const cz = cellCoords(atom.z);
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

function clampBondCutoff(value: number): number {
  return Math.max(MIN_BOND_CUTOFF, Math.min(MAX_BOND_CUTOFF, value));
}

function neighborsWithinCutoff(
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

export default function Explore() {
  const st = useWorkspace();
  const d = activeDataset(st);
  const { t } = useT();
  const [frame, setFrame] = useState<FramePayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [jumpTo, setJumpTo] = useState<number | null>(null);
  const [bondCutoff, setBondCutoff] = useState(DEFAULT_BOND_CUTOFF);
  const [localCutoff, setLocalCutoff] = useState(3.0);
  // Local shell is opt-in: a plain selection highlights only the atom itself
  // (plus its force arrow); neighbors stay uncolored unless the shell is shown.
  const [showLocalEnvironment, setShowLocalEnvironment] = useState(false);
  const [showForceArrow, setShowForceArrow] = useState(true);
  const [forceArrowScale, setForceArrowScale] = useState(1);
  // "Shortest interatomic distance" row click: highlight the closest atom pair
  // instead of the single-atom selection (the two are mutually exclusive).
  const [showDistancePair, setShowDistancePair] = useState(false);
  const viewerDiv = useRef<HTMLDivElement>(null);
  const atomTableRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<
    {
      clear: () => void;
      addModel: () => ViewerModel;
      addLine: (spec: object) => void;
      addArrow: (spec: object) => void;
      addSphere?: (spec: object) => void;
      setStyle: (sel: object, style: object) => void;
      addStyle: (sel: object, style: object) => void;
      setClickable: (sel: object, clickable: boolean, callback: (atom: ClickedAtom) => void) => void;
      getView: () => number[];
      setView: (view: number[]) => void;
      zoomTo: () => void;
      render: () => void;
    } | null
  >(null);
  const [viewerReady, setViewerReady] = useState(false);
  const [viewerError, setViewerError] = useState<string | null>(null);
  const loadStart = useRef<number>(0);
  const renderedFrameRef = useRef<number | null>(null);
  // The backend's xyz padding extent follows bond_cutoff. Keep track of the
  // last requested extent so enabling or enlarging a local shell can fetch
  // enough periodic images without refetching on every render.
  const fetchedGhostCutoffRef = useRef(0);

  const total = d?.number_of_frames ?? 0;
  const selectedSample = st.selectedSample;
  const selectedAtom = selectedSample
    && selectedSample.datasetId === d?.id
    && selectedSample.frame === frame?.index
    ? selectedSample.atom
    : undefined;
  const selectedLocalNeighbors = frame && selectedAtom != null
    ? neighborsWithinCutoff(
        parseViewerAtoms(frame, Math.max(bondCutoff, localCutoff), showLocalEnvironment),
        selectedAtom,
        localCutoff,
      )
    : [];
  const selectedLocalNeighborSet = new Set(
    showLocalEnvironment
      ? selectedLocalNeighbors
          .flatMap(({ index, parent }) => [index, parent ?? index])
          .filter((index) => index >= 0 && index < (frame?.natoms ?? 0))
      : [],
  );
  // Force components live on atom_rows (real atoms only); selection indices
  // always reference real atoms, so a direct index lookup is safe.
  const selectedForceRow = frame && selectedAtom != null && selectedAtom < frame.atom_rows.length
    ? frame.atom_rows[selectedAtom]
    : undefined;
  const maxForce = frame ? frameMaxForce(frame) : 0;
  // The atom carrying the frame's largest |F| — the target behind a red
  // "Max |F|" row; clicking the row selects it exactly like a table click.
  const maxForceAtom = useMemo(() => {
    if (!frame) return null;
    let best: number | null = null;
    let bestMagnitude = -1;
    for (const row of frame.atom_rows) {
      if (row.fx == null || row.fy == null || row.fz == null) continue;
      const magnitude = Math.sqrt(row.fx ** 2 + row.fy ** 2 + row.fz ** 2);
      if (Number.isFinite(magnitude) && magnitude > bestMagnitude) {
        bestMagnitude = magnitude;
        best = row.i;
      }
    }
    return best;
  }, [frame]);
  const selectedArrow = selectedForceRow
    ? forceArrowGeometry(selectedForceRow.fx, selectedForceRow.fy, selectedForceRow.fz, maxForce, forceArrowScale, FORCE_ARROW_TARGET_LENGTH)
    : null;
  // Frame-level metrics for the inspector; recomputed only when a new frame
  // arrives, not on selection clicks.
  const cellParams = useMemo(
    () => (frame?.cell && frame.cell.length === 9 ? cellParameters(frame.cell) : null),
    [frame],
  );
  const minDistancePair = useMemo(() => (frame ? minimumDistancePair(frame.xyz, frame.natoms) : null), [frame]);
  const minAtomDistance = minDistancePair?.distance ?? null;
  const netForce = useMemo(() => (frame ? netForceMagnitude(frame.atom_rows) : null), [frame]);
  const density = useMemo(
    () => (frame ? massDensity(frame.atom_rows.map((row) => row.el), frame.volume) : null),
    [frame],
  );
  // Virial tensor of the frame as stored in the source (eV); sign conventions
  // differ between extxyz/deepmd ecosystems, so nothing is normalized here.
  const virial = useMemo(() => (frame ? virialSummary(frame.virial) : null), [frame]);

  // Health-check findings for the red inspector highlights; refetched on
  // dataset switch and after any exclude/restore/rescan (statsTick).
  const [health, setHealth] = useState<DatasetHealth | null>(null);
  const [healthFindings, setHealthFindings] = useState<HealthFindings | null>(null);
  useEffect(() => {
    if (!d) return;
    let disposed = false;
    const dsId = d.id;
    (async () => {
      try {
        let r = await ipc.request<{ recalculating: boolean; job_id: string | null; stats: { health?: DatasetHealth; health_findings?: HealthFindings } | null }>(
          "dataset.statistics",
          { id: dsId },
        );
        if (!r.stats && r.job_id) {
          await new Promise<void>((resolve) => {
            const off = ipc.on("job.finished", (data) => {
              const j = data as { job_id: string };
              if (j.job_id !== r.job_id) return;
              off();
              resolve();
            });
          });
          r = await ipc.request("dataset.statistics", { id: dsId });
        }
        if (!disposed && r.stats?.health) setHealth(r.stats.health);
        if (!disposed && r.stats?.health_findings) setHealthFindings(r.stats.health_findings);
      } catch (e) {
        console.error("dataset.statistics failed", e);
      }
    })();
    return () => {
      disposed = true;
    };
  }, [d?.id, st.statsTick]); // eslint-disable-line react-hooks/exhaustive-deps

  const idx = frame?.index ?? st.activeFrameIndex;
  // checks that flag the frame currently shown in the viewer
  const flaggedChecks = useMemo(() => {
    if (!healthFindings) return [] as string[];
    return CHECK_KEYS.filter((k) => healthFindings[k]?.includes(idx));
  }, [healthFindings, idx]);
  const flaggedSet = useMemo(() => new Set(flaggedChecks), [flaggedChecks]);
  // which declared properties the currently shown frame lacks (the "what is
  // missing" behind a missing_values flag); empty unless the frame is flagged
  const missingProps = useMemo(() => {
    if (!frame || !flaggedSet.has("missing_values")) return [] as string[];
    const declared = health?.missing_by_property
      ? Object.keys(health.missing_by_property)
      : ["energy", "forces", "virial"];
    const absent: string[] = [];
    if (frame.energy == null) absent.push("energy");
    if (frame.force_max == null) absent.push("forces");
    if (frame.virial_present === false) absent.push("virial");
    return absent.filter((p) => declared.includes(p));
  }, [frame, flaggedSet, health]);

  // Shared selection logic for the atom table and viewer click-to-select:
  // clicking the selected atom again clears the selection. Browse selections
  // work without an active descriptor run; runId is attached only when one
  // exists so Analysis links still resolve.
  const selectAtom = (atomIndex: number) => {
    if (!d) return;
    if (atomIndex === selectedAtom) {
      st.setSelectedSample(null);
      return;
    }
    setShowDistancePair(false);
    st.setSelectedSample({
      datasetId: d.id,
      ...(st.activeDescriptorRunId ? { runId: st.activeDescriptorRunId } : {}),
      mode: "atom",
      frame: idx,
      atom: atomIndex,
    });
  };
  // The 3Dmol click callback is registered inside the render effect; route it
  // through a ref so every click dispatches to the latest selection logic.
  const atomClickRef = useRef(selectAtom);
  atomClickRef.current = selectAtom;

  // Keep the table's selected row visible when selection originates in the
  // viewer (and use the same behavior for table/inspector selections).
  useEffect(() => {
    if (selectedAtom == null) return;
    const row = atomTableRef.current?.querySelector<HTMLTableRowElement>("tbody tr.explore-atom-row-selected");
    if (!row || typeof row.scrollIntoView !== "function") return;
    const prefersReducedMotion = typeof window !== "undefined"
      && typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    row.scrollIntoView({ behavior: prefersReducedMotion ? "auto" : "smooth", block: "center", inline: "nearest" });
  }, [frame?.index, selectedAtom]);

  // Red "Max |F|" row click: select the offending atom exactly like a table
  // click (highlight + force arrow, arrow forced on so it is always visible);
  // clicking it again clears the selection.
  const selectMaxForceAtom = () => {
    if (!d || maxForceAtom == null) return;
    setShowForceArrow(true);
    selectAtom(maxForceAtom);
  };

  // Red "Shortest interatomic distance" row click: highlight the two closest
  // displayed atoms; clicking again hides.
  const toggleDistancePair = () => {
    if (!minDistancePair) return;
    if (showDistancePair) {
      setShowDistancePair(false);
      return;
    }
    st.setSelectedSample(null);
    setShowDistancePair(true);
  };

  const fetchFrame = useCallback(
    async (index: number, requestedBondCutoff = bondCutoff) => {
      if (!d) return;
      const idx = Math.max(0, Math.min(index, total - 1));
      const cutoff = clampBondCutoff(requestedBondCutoff);
      const requestCutoff = showLocalEnvironment && selectedAtom != null
        ? clampBondCutoff(Math.max(cutoff, localCutoff))
        : cutoff;
      setLoading(true);
      loadStart.current = performance.now();
      try {
        const f = await ipc.request<FramePayload>("dataset.frame", {
          id: d.id,
          index: idx,
          // Keep the public display threshold separate from the larger
          // periodic-padding extent needed by an active local shell.
          bond_cutoff: requestCutoff,
        });
        fetchedGhostCutoffRef.current = requestCutoff;
        setFrame({ ...f, bond_cutoff: cutoff });
        st.setActiveFrame(idx);
        setJumpTo(null);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [d, total, bondCutoff, localCutoff, selectedAtom, showLocalEnvironment],
  );

  // reset when dataset changes
  useEffect(() => {
    renderedFrameRef.current = null;
    fetchedGhostCutoffRef.current = 0;
    setFrame(null);
    setShowDistancePair(false);
    if (d && total > 0) void fetchFrame(st.activeFrameIndex || 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d?.id]);

  // External frame navigation (e.g. the findings drawer's preview): follow the
  // shared active-frame pointer while this page is open. Internal navigation
  // already lands on the pointer, and frame===null defers to the reset above.
  useEffect(() => {
    if (!d || loading || !frame) return;
    if (st.activeFrameIndex === frame.index) return;
    void fetchFrame(st.activeFrameIndex);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.activeFrameIndex, loading]);

  // A local shell may need periodic images farther out than the bond display
  // cutoff. Fetch that larger xyz padding only while the shell is active.
  useEffect(() => {
    if (!d || !frame || !showLocalEnvironment || selectedAtom == null) return;
    const neededCutoff = clampBondCutoff(Math.max(bondCutoff, localCutoff));
    if (fetchedGhostCutoffRef.current + 1e-9 >= neededCutoff) return;
    void fetchFrame(frame.index, bondCutoff);
  }, [bondCutoff, d, fetchFrame, frame, localCutoff, selectedAtom, showLocalEnvironment]);

  // 3Dmol lifecycle
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mod = await import("3dmol");
        const $3Dmol = ((mod as { default?: unknown }).default ?? mod) as {
          createViewer: (el: HTMLElement, opts: object) => never;
        };
        if (cancelled || !viewerDiv.current) return;
        viewerRef.current = $3Dmol.createViewer(viewerDiv.current, {
          backgroundColor: "white",
          // Orthographic projection so crystal structures keep parallel cell
          // edges (no perspective foreshortening) while rotating.
          orthographic: true,
        }) as never;
        setViewerReady(true);
      } catch (e) {
        console.error("3dmol init failed", e);
        setViewerError(String(e));
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
    const v = viewerRef.current;
    if (!viewerReady || !v || !frame) return;
    const previousView = renderedFrameRef.current === frame.index ? v.getView() : null;
    v.clear();
    const displayCutoff = showLocalEnvironment && selectedAtom != null
      ? Math.max(clampBondCutoff(frame.bond_cutoff), localCutoff)
      : clampBondCutoff(frame.bond_cutoff);
    const shellActive = showLocalEnvironment && selectedAtom != null;
    const atoms = parseViewerAtoms(frame, displayCutoff, shellActive);
    const selected =
      selectedAtom != null && selectedAtom >= 0 && selectedAtom < atoms.length ? selectedAtom : null;
    const shellNeighbors = shellActive && selected != null
      ? neighborsWithinCutoff(atoms, selected, localCutoff)
      : [];
    // Shell-only rendering: with the local shell shown, real atoms beyond the
    // cutoff are left out of the model entirely. Required periodic images are
    // treated as ordinary shell neighbors, while their parent index keeps
    // clicks mapped back to the real Atom Table row.
    const shellNewIndex = shellActive && selected != null
      ? new Map<number, number>(
          [...shellNeighbors.map((n) => n.index), selected]
            .sort((left, right) => left - right)
            .map((orig, newIdx): [number, number] => [orig, newIdx]),
        )
      : null;
    const renderAtoms = shellNewIndex
      ? [...shellNewIndex.keys()].map((orig) => {
          const atom = atoms[orig];
          const bonds: number[] = [];
          const bondOrder: number[] = [];
          atom.bonds.forEach((bonded, k) => {
            const mapped = shellNewIndex.get(bonded);
            if (mapped != null) {
              bonds.push(mapped);
              bondOrder.push(atom.bondOrder[k]);
            }
          });
          return { ...atom, bonds, bondOrder };
        })
      : atoms;
    const model = v.addModel();
    model.addAtoms(renderAtoms);
    for (const el of new Set(renderAtoms.map((atom) => atom.elem))) {
      const color = elementColor(el);
      v.setStyle({ elem: el }, { sphere: { scale: 0.28, color }, stick: { radius: 0.12, color } });
    }
    if (selected != null) {
      const selectedIndex = shellNewIndex ? shellNewIndex.get(selected)! : selected;
      v.addStyle({ index: selectedIndex }, { sphere: { scale: 0.5, color: "#D13438" }, stick: { radius: 0.17, color: "#D13438" } });
      if (shellActive) {
        // Neighbors keep their element colors; only the selected atom is
        // highlighted. The lines and cutoff sphere still mark the shell.
        const center = atoms[selected];
        for (const neighbor of shellNeighbors) {
          v.addLine({ start: center, end: atoms[neighbor.index], color: "#F7630C", opacity: 0.7, linewidth: 2 });
        }
        v.addSphere?.({ center, radius: localCutoff, color: "#F7630C", opacity: 0.12, wireframe: true });
      }
      if (showForceArrow) {
        const row = selected < frame.atom_rows.length ? frame.atom_rows[selected] : undefined;
        const arrow = row
          ? forceArrowGeometry(row.fx, row.fy, row.fz, frameMaxForce(frame), forceArrowScale, FORCE_ARROW_TARGET_LENGTH)
          : null;
        const atom = atoms[selected];
        if (arrow && atom) {
          v.addArrow({
            start: {
              x: atom.x + arrow.dir.x * FORCE_ARROW_START_OFFSET,
              y: atom.y + arrow.dir.y * FORCE_ARROW_START_OFFSET,
              z: atom.z + arrow.dir.z * FORCE_ARROW_START_OFFSET,
            },
            end: {
              x: atom.x + arrow.dir.x * arrow.length,
              y: atom.y + arrow.dir.y * arrow.length,
              z: atom.z + arrow.dir.z * arrow.length,
            },
            color: FORCE_ARROW_COLOR,
            radius: 0.12,
          });
        }
      }
    }
    // Shortest-pair highlight: red spheres on both real-atom partners plus a
    // dashed line between them.
    if (showDistancePair && minDistancePair) {
      for (const index of [minDistancePair.i, minDistancePair.j]) {
        if (index >= 0 && index < atoms.length) {
          v.addStyle({ index }, { sphere: { scale: 0.5, color: HEALTH_RED }, stick: { radius: 0.17, color: HEALTH_RED } });
        }
      }
      const a = atoms[minDistancePair.i];
      const b = atoms[minDistancePair.j];
      if (a && b) {
        v.addLine({ start: a, end: b, color: HEALTH_RED, opacity: 0.9, linewidth: 3, dashed: true });
      }
    }
    // unit cell wireframe (12 edges) for periodic frames; cell is row-major a1,a2,a3
    if (frame.cell && frame.cell.length === 9) {
      const A = frame.cell;
      const p = (i: number, j: number, k: number) => ({
        x: i * A[0] + j * A[3] + k * A[6],
        y: i * A[1] + j * A[4] + k * A[7],
        z: i * A[2] + j * A[5] + k * A[8],
      });
      const edges: [number, number, number, number, number, number][] = [
        [0, 0, 0, 1, 0, 0], [0, 0, 0, 0, 1, 0], [0, 0, 0, 0, 0, 1],
        [1, 1, 1, 0, 1, 1], [1, 1, 1, 1, 0, 1], [1, 1, 1, 1, 1, 0],
        [1, 0, 0, 1, 1, 0], [1, 0, 0, 1, 0, 1],
        [0, 1, 0, 1, 1, 0], [0, 1, 0, 0, 1, 1],
        [0, 0, 1, 1, 0, 1], [0, 0, 1, 0, 1, 1],
      ];
      for (const [i1, j1, k1, i2, j2, k2] of edges) {
        v.addLine({
          start: p(i1, j1, k1),
          end: p(i2, j2, k2),
          color: "#0F6CBD",
          opacity: 0.9,
          linewidth: 2,
        });
      }
    }
    // Click-to-select: real atoms report their Atom Table index; a displayed
    // periodic image reports its real-atom parent. Registered before render()
    // so 3Dmol builds the picking intersection shapes.
    v.setClickable({}, true, (atom) => {
      const target = atom.parent ?? atom.i;
      if (target != null && target >= 0 && target < frame.natoms) atomClickRef.current(target);
    });
    if (previousView && !(showLocalEnvironment && selected != null)) v.setView(previousView);
    else v.zoomTo(); // shell-only view fits the isolated shell instead of the whole cell
    v.render();
    renderedFrameRef.current = frame.index;
    const ms = performance.now() - loadStart.current;
    console.info(`frame ${frame.index} fetched+rendered in ${ms.toFixed(0)}ms`);
  }, [viewerReady, frame, localCutoff, selectedAtom, showLocalEnvironment, showForceArrow, forceArrowScale, showDistancePair, minDistancePair]);

  if (!d) return <Empty description={t("Register a dataset first")} style={{ marginTop: 120 }} />;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: 8 }}>
      {/* frame navigation bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexWrap: "wrap",
          background: "#FFFFFF",
          border: "1px solid #EAECF0",
          borderRadius: 6,
          padding: "6px 12px",
        }}
      >
        <Typography.Text strong>
          {t("Frame {index} / {total}", { index: idx, total: total - 1 })}
        </Typography.Text>
        <Space size={4}>
          <Button size="small" icon={<ArrowLeft16Regular />} onClick={() => void fetchFrame(idx - 1)} disabled={loading || idx <= 0} />
          <Button size="small" icon={<ArrowRight16Regular />} onClick={() => void fetchFrame(idx + 1)} disabled={loading || idx >= total - 1} />
          <Button
            size="small"
            icon={<ArrowShuffle16Regular />}
            onClick={() => void fetchFrame(Math.floor(Math.random() * total))}
            disabled={loading}
          >
            {t("Random")}
          </Button>
          <InputNumber
            size="small"
            min={0}
            max={total - 1}
            value={jumpTo ?? idx}
            onChange={(v) => setJumpTo(v)}
            onStep={(v) => void fetchFrame(v)}
            onPressEnter={() => {
              if (jumpTo !== null) void fetchFrame(jumpTo);
            }}
            style={{ width: 90 }}
          />
          <Button
            size="small"
            icon={<ArrowFit16Regular />}
            onClick={() => {
              viewerRef.current?.zoomTo();
              viewerRef.current?.render();
            }}
          />
        </Space>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
          <Tooltip title={t("Show bonds for atom pairs no farther apart than this distance.")}>
            <Typography.Text type="secondary" style={{ fontSize: 12, cursor: "help" }}>
              {t("Bond length ≤")}
            </Typography.Text>
          </Tooltip>
          <InputNumber
            size="small"
            min={MIN_BOND_CUTOFF}
            max={MAX_BOND_CUTOFF}
            step={0.1}
            precision={2}
            value={bondCutoff}
            disabled={loading}
            aria-label={t("Bond length display threshold")}
            addonAfter="Å"
            onChange={(value) => {
              if (value == null || !Number.isFinite(value)) return;
              const next = clampBondCutoff(value);
              if (next === bondCutoff) return;
              setBondCutoff(next);
              void fetchFrame(idx, next);
            }}
            style={{ width: 122 }}
          />
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Button
            size="small"
            type={showLocalEnvironment ? "primary" : "default"}
            disabled={selectedAtom == null}
            onClick={() => setShowLocalEnvironment((visible) => !visible)}
          >
            {showLocalEnvironment ? t("Hide local shell") : t("Show local shell")}
          </Button>
          {showLocalEnvironment && selectedAtom != null && <InputNumber
            size="small"
            min={0.1}
            max={MAX_BOND_CUTOFF}
            step={0.1}
            precision={2}
            value={localCutoff}
            aria-label={t("Local environment cutoff")}
            addonAfter="Å"
            onChange={(value) => {
              if (value != null && Number.isFinite(value)) setLocalCutoff(clampBondCutoff(value));
            }}
            style={{ width: 122 }}
          />}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Tooltip title={t("Draws an arrow from the selected atom along its force vector.")}>
            <Button
              size="small"
              type={showForceArrow ? "primary" : "default"}
              disabled={!selectedForceRow}
              onClick={() => setShowForceArrow((visible) => !visible)}
            >
              {showForceArrow ? t("Hide force arrow") : t("Show force arrow")}
            </Button>
          </Tooltip>
          {showForceArrow && selectedForceRow && <InputNumber
            size="small"
            min={0.25}
            max={10}
            step={0.25}
            precision={2}
            value={forceArrowScale}
            aria-label={t("Force arrow scale")}
            addonAfter="×"
            onChange={(value) => {
              if (value != null && Number.isFinite(value)) setForceArrowScale(Math.max(0.25, Math.min(10, value)));
            }}
            style={{ width: 96 }}
          />}
        </div>
        <div style={{ width: 56, flexShrink: 0, textAlign: "right" }} aria-live="polite">
          <Typography.Text type="secondary" style={{ visibility: loading ? "visible" : "hidden" }}>
            {t("loading…")}
          </Typography.Text>
        </div>
      </div>

      <div style={{ display: "flex", gap: 12, flex: 1, minHeight: 0 }}>
        {/* 3D viewer ~70% */}
        <div
          style={{
            flex: 7,
            background: "#FFFFFF",
            border: "1px solid #EAECF0",
            borderRadius: 6,
            position: "relative",
            minHeight: 420,
          }}
        >
          <div ref={viewerDiv} style={{ width: "100%", height: "100%", minHeight: 420 }} />
          {viewerError && (
            <div
              style={{
                position: "absolute",
                top: 8,
                left: 8,
                color: "#C42B1C",
                fontSize: 12,
                fontFamily: "monospace",
              }}
            >
              {t("viewer error: {message}", { message: viewerError })}
            </div>
          )}
        </div>
        {/* Structure Inspector ~30% */}
        <div
          style={{
            flex: 3,
            background: "#FFFFFF",
            borderLeft: "1px solid #E1E4E8",
            padding: 12,
            overflowY: "auto",
          }}
        >
          <Typography.Text strong style={{ fontSize: 12, color: "#616161", letterSpacing: 1 }}>
            {t("STRUCTURE")}
          </Typography.Text>
          {flaggedChecks.length > 0 && (
            <div
              style={{
                marginTop: 8,
                border: `1px solid ${HEALTH_RED}55`,
                background: "#FDF3F2",
                borderRadius: 6,
                padding: "7px 10px",
                fontSize: 12,
                color: HEALTH_RED,
                display: "flex",
                gap: 7,
                alignItems: "flex-start",
              }}
            >
              <Warning16Filled style={{ flex: "0 0 auto", marginTop: 2 }} />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{t("Flagged by data health")}</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "2px 10px", marginTop: 2 }}>
                  {flaggedChecks.map((check) => (
                    <a
                      key={check}
                      onClick={() => useWorkspace.getState().openFindings(check)}
                      style={{ color: HEALTH_RED, cursor: "pointer", textDecoration: "underline" }}
                    >
                      {healthCheckTitle(check, t)}
                    </a>
                  ))}
                </div>
              </div>
            </div>
          )}
          <InspectorRows
            rows={[
              [t("Frame"), String(idx)],
              [t("Formula"), frame?.formula ?? "—"],
              [t("Atoms"), String(frame?.natoms ?? "—")],
              [t("E / atom"), frame?.energy_per_atom != null ? `${frame.energy_per_atom.toFixed(4)} eV` : "—"],
              [
                t("Max |F|"),
                frame?.force_max != null ? `${frame.force_max.toFixed(4)} eV/Å` : "—",
                flaggedSet.has("extreme_force") ? HEALTH_RED : undefined,
                flaggedSet.has("extreme_force") && maxForceAtom != null ? selectMaxForceAtom : undefined,
                flaggedSet.has("extreme_force") && maxForceAtom != null
                  ? t("Click to highlight the atom with the largest force and show its force arrow.")
                  : undefined,
              ],
              [t("ΣF (net force)"), netForce != null ? `${netForce.toFixed(4)} eV/Å` : "—", flaggedSet.has("net_force") ? HEALTH_RED : undefined],
              [
                t("Virial (eV)"),
                virial ? (virial.voigt ?? virial.rows.flat()).map((v) => v.toFixed(4)).join("  ") : "—",
                undefined,
                undefined,
                t("Virial tensor as stored in the source frame (eV). Symmetric tensors show Voigt order xx yy zz yz xz xy; asymmetric tensors show the full row-major matrix."),
              ],
              [t("Cell volume"), frame?.volume != null ? `${frame.volume.toFixed(2)} Å³` : "—"],
              [t("Cell parameters"), cellParams
                ? `${cellParams.a.toFixed(3)} ${cellParams.b.toFixed(3)} ${cellParams.c.toFixed(3)} Å · ${cellParams.alpha.toFixed(1)}° ${cellParams.beta.toFixed(1)}° ${cellParams.gamma.toFixed(1)}°`
                : "—"],
              [t("Density"), density != null ? `${density.toFixed(3)} g/cm³` : "—"],
              [
                t("Shortest interatomic distance"),
                minAtomDistance != null ? `${minAtomDistance.toFixed(3)} Å` : "—",
                flaggedSet.has("nonphysical_structures") ? HEALTH_RED : undefined,
                flaggedSet.has("nonphysical_structures") && minDistancePair ? toggleDistancePair : undefined,
                flaggedSet.has("nonphysical_structures") && minDistancePair
                  ? t("Click to highlight the two atoms that form the shortest interatomic distance.")
                  : undefined,
              ],
              ["PBC", frame?.pbc ?? "—", flaggedSet.has("invalid_cell") ? HEALTH_RED : undefined],
              [t("Selected atom"), selectedAtom != null ? `#${selectedAtom}` : "—"],
              [t("Selected |F|"), selectedForceRow?.f != null ? `${selectedForceRow.f.toFixed(4)} eV/Å` : "—"],
              [t("Local coordination"), selectedAtom != null ? String(selectedLocalNeighbors.length) : "—"],
              [t("Neighbor shell"), selectedAtom != null ? `${localCutoff.toFixed(2)} Å` : "—"],
              ...(missingProps.length > 0
                ? [[t("Missing"), missingProps.map((p) => t(MISSING_PROP_LABELS[p] ?? p)).join(" · "), HEALTH_RED] as [string, string, string]]
                : []),
            ]}
          />
          {showDistancePair && minDistancePair && minAtomDistance != null && (
            <Typography.Paragraph type="secondary" style={{ fontSize: 11, marginTop: 10, marginBottom: 0 }}>
              {t("Shortest pair: {pair}", {
                pair:
                  [minDistancePair.i, minDistancePair.j]
                    .map((k) => `#${k}`)
                    .join(" – ") + ` (${minAtomDistance.toFixed(3)} Å)`,
              })}
            </Typography.Paragraph>
          )}
          {selectedAtom != null && showLocalEnvironment && <Typography.Paragraph type="secondary" style={{ fontSize: 11, marginTop: 10, marginBottom: 0 }}>
            {t("Neighbors: {list}", {
              list: selectedLocalNeighbors.length
                ? selectedLocalNeighbors
                    .map(({ index, parent, distance }) => `#${parent ?? index}${parent != null ? "·PBC" : ""} (${distance.toFixed(2)} Å)`)
                    .join(", ")
                : t("none within cutoff"),
            })}
          </Typography.Paragraph>}
          {showForceArrow && selectedArrow && <Typography.Paragraph type="secondary" style={{ fontSize: 11, marginTop: 10, marginBottom: 0 }}>
            {t("Force arrow: {magnitude} eV/Å ≈ {length} Å (frame max {max} eV/Å → {target} Å)", {
              magnitude: selectedArrow.magnitude.toFixed(3),
              length: selectedArrow.length.toFixed(2),
              max: maxForce.toFixed(3),
              target: (FORCE_ARROW_TARGET_LENGTH * forceArrowScale).toFixed(2),
            })}
          </Typography.Paragraph>}
        </div>
      </div>

      {/* Atom table */}
      <div ref={atomTableRef} style={{ background: "#FFFFFF", border: "1px solid #EAECF0", borderRadius: 6, maxHeight: 260, overflow: "auto" }}>
        <Table
          size="small"
          tableLayout="fixed"
          pagination={false}
          dataSource={frame?.atom_rows ?? []}
          rowKey="i"
          rowClassName={(row) => row.i === selectedAtom ? "explore-atom-row-selected" : selectedLocalNeighborSet.has(row.i) ? "explore-atom-row-neighbor" : ""}
          onRow={(row) => ({
            onClick: () => selectAtom(row.i),
          })}
          columns={[
            { title: "#", dataIndex: "i", key: "i", width: 60 },
            { title: t("Element"), dataIndex: "el", key: "el", width: 80 },
            { title: "x (Å)", dataIndex: "x", key: "x", align: "right" },
            { title: "y (Å)", dataIndex: "y", key: "y", align: "right" },
            { title: "z (Å)", dataIndex: "z", key: "z", align: "right" },
            { title: "Fx", dataIndex: "fx", key: "fx", align: "right", render: fmt },
            { title: "Fy", dataIndex: "fy", key: "fy", align: "right", render: fmt },
            { title: "Fz", dataIndex: "fz", key: "fz", align: "right", render: fmt },
            { title: "|F|", dataIndex: "f", key: "f", align: "right", render: fmt },
          ]}
        />
      </div>
    </div>
  );
}

function fmt(v: number | null): string {
  return v == null ? "—" : v.toFixed(4);
}

// label, value, severity color, click handler + hover tooltip for the
// health-flagged rows the user can act on
type InspectorRow = [label: string, value: string, color?: string, onClick?: () => void, title?: string];

function InspectorRows({ rows }: { rows: InspectorRow[] }) {
  return (
    <div style={{ marginTop: 8 }}>
      {rows.map(([k, v, color, onClick, title]) => (
        <div
          key={k}
          title={title}
          onClick={onClick}
          className={onClick ? "explore-inspector-row-clickable" : undefined}
          style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", fontSize: 13 }}
        >
          <span
            style={{
              color: color ?? "#616161",
              fontWeight: color ? 600 : 400,
              textDecoration: onClick ? "underline" : undefined,
              textUnderlineOffset: 2,
            }}
          >
            {k}
          </span>
          <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: color ? 600 : 500, color: color ?? undefined }}>{v}</span>
        </div>
      ))}
    </div>
  );
}

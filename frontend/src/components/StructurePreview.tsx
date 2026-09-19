import { useEffect, useRef, useState } from "react";
import { elementColor } from "../util/elements";
import { neighborsWithinCutoff, parseViewerAtoms } from "../util/viewerAtoms";
import type { ClickedAtom, ViewerModel } from "../util/viewerAtoms";
import { useT } from "../i18n";
import { STRUCTURE_VIEWER_BACKGROUND, addUnitCell, load3Dmol } from "../viz/StructureViewer";
import type { FramePayload } from "../types/protocol";

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
    const element = viewerDiv.current;
    let cancelled = false;
    void (async () => {
      try {
        const $3Dmol = await load3Dmol();
        if (cancelled || !element) return;
        viewerRef.current = $3Dmol.createViewer(element, { backgroundColor: STRUCTURE_VIEWER_BACKGROUND }) as Viewer;
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
      if (element) element.innerHTML = "";
    };
  }, []);

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewerReady || !viewer) return;
    viewer.clear();
    const localShellActive = selectedAtom != null && localCutoff != null && Number.isFinite(localCutoff);
    // frame.bond_cutoff falls back to the same 2.4 Å default the backend uses
    const atoms = parseViewerAtoms(frame, Math.max(0.1, frame.bond_cutoff || 2.4), localShellActive);
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
        const neighbors = neighborsWithinCutoff(atoms, selectedAtom, Math.max(0.1, Math.min(10, localCutoff)));
        const center = atoms[selectedAtom];
        for (const neighbor of neighbors) {
          viewer.addStyle({ index: neighbor.index }, { sphere: { scale: 0.34, color: "#F7630C" }, stick: { radius: 0.13, color: "#F7630C" } });
          viewer.addLine({ start: center, end: atoms[neighbor.index], color: "#F7630C", opacity: 0.7, linewidth: 2 });
        }
        viewer.addSphere?.({ center, radius: Math.max(0.1, Math.min(10, localCutoff)), color: "#F7630C", opacity: 0.12, wireframe: true });
      }
    }
    addUnitCell(viewer, frame.cell);
    // Click-to-select: real atoms report their Atom Table index; a displayed
    // periodic image reports its real-atom parent. Registered before render()
    // so 3Dmol builds the picking intersection shapes.
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

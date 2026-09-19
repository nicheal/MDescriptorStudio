/** Structure viewers are 3Dmol by convention.
 *
 * The module shape, the viewer subset the pages are allowed to use, and the one
 * unsound cast all live here, so a page cannot hand its own copy of the surface
 * to `as never` and stop checking it.
 */
import type { ClickedAtom, ViewerModel } from "../util/viewerAtoms";

type StructureViewerModule = {
  createViewer: (element: HTMLElement, options: object) => unknown;
};

const STRUCTURE_VIEWER_BACKGROUND = "white";

async function load3Dmol(): Promise<StructureViewerModule> {
  const mod = await import("3dmol");
  return ((mod as { default?: unknown }).default ?? mod) as StructureViewerModule;
}

/** The 3Dmol surface this app actually uses.
 *
 * 3Dmol ships declarations, but ``createViewer`` there returns a class whose
 * ``addModel().addAtoms()`` takes the library's own atom spec, while both
 * callers feed the narrower ``ViewerAtom`` shape ``util/viewerAtoms`` builds.
 * Declaring the subset we touch is what keeps a rename upstream from compiling
 * quietly and failing at render time.
 */
export type StructureViewer = {
  addArrow: (spec: object) => void;
  addLine: (spec: object) => void;
  addModel: () => ViewerModel;
  addSphere: (spec: object) => void;
  addStyle: (sel: object, style: object) => void;
  clear: () => void;
  getView: () => number[];
  render: () => void;
  setClickable: (sel: object, clickable: boolean, callback: (atom: ClickedAtom) => void) => void;
  setStyle: (sel: object, style: object) => void;
  setView: (view: number[]) => void;
  zoomTo: () => void;
};

export async function createStructureViewer(
  element: HTMLElement,
  options: Record<string, unknown> = {},
): Promise<StructureViewer> {
  const $3Dmol = await load3Dmol();
  return $3Dmol.createViewer(element, { backgroundColor: STRUCTURE_VIEWER_BACKGROUND, ...options }) as StructureViewer;
}

/** Row-major 3x3 lattice (a1, a2, a3) as the wireframe the viewer draws.
 * Both the preview card and the Explore page need it, and a diverging copy
 * means one of them silently stops matching the other's cell convention. */
const UNIT_CELL_EDGES: [number, number, number, number, number, number][] = [
  [0, 0, 0, 1, 0, 0], [0, 0, 0, 0, 1, 0], [0, 0, 0, 0, 0, 1],
  [1, 1, 1, 0, 1, 1], [1, 1, 1, 1, 0, 1], [1, 1, 1, 1, 1, 0],
  [1, 0, 0, 1, 1, 0], [1, 0, 0, 1, 0, 1],
  [0, 1, 0, 1, 1, 0], [0, 1, 0, 0, 1, 1],
  [0, 0, 1, 1, 0, 1], [0, 0, 1, 0, 1, 1],
];

/** Free what a viewer leaves behind when it is unmounted.
 *
 * 3Dmol exposes no dispose: `clear()` drops the models but keeps the WebGL
 * context, and because the library registers a bound `mouseup` handler on
 * document.body that it never removes, the viewer graph stays reachable for
 * the life of the page. Browsers cap live contexts (around 16) and discard the
 * oldest past that limit, which surfaces as earlier viewers silently going
 * blank — so the context is released explicitly through its own canvas before
 * the element is emptied.
 */
export function disposeStructureViewer(element: HTMLElement | null | undefined, viewer: unknown): void {
  try {
    (viewer as { clear?: () => void } | null | undefined)?.clear?.();
  } catch (error) {
    console.error("structure viewer clear failed", error);
  }
  const canvas = element?.querySelector("canvas");
  if (canvas) {
    for (const type of ["webgl2", "webgl"] as const) {
      const context = canvas.getContext(type) as WebGLRenderingContext | null;
      const loser = context?.getExtension("WEBGL_lose_context");
      if (loser) {
        loser.loseContext();
        break;
      }
    }
  }
  if (element) element.innerHTML = "";
}

export function addUnitCell(
  viewer: { addLine: (spec: object) => void },
  cell: number[] | null | undefined,
  style: { opacity?: number; linewidth?: number } = {},
): void {
  if (!cell || cell.length !== 9) return;
  const point = (i: number, j: number, k: number) => ({
    x: i * cell[0] + j * cell[3] + k * cell[6],
    y: i * cell[1] + j * cell[4] + k * cell[7],
    z: i * cell[2] + j * cell[5] + k * cell[8],
  });
  for (const [i1, j1, k1, i2, j2, k2] of UNIT_CELL_EDGES) {
    viewer.addLine({
      start: point(i1, j1, k1),
      end: point(i2, j2, k2),
      color: "#0F6CBD",
      opacity: 0.72,
      linewidth: 1.5,
      ...style,
    });
  }
}

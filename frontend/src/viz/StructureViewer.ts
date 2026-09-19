/** Structure viewers are 3Dmol by convention.
 *
 * The dynamic import is centralized so the pages do not each own a copy of
 * the module shape cast and viewer options.
 */
export type StructureViewerModule = {
  createViewer: (element: HTMLElement, options: object) => unknown;
};

export const STRUCTURE_VIEWER_BACKGROUND = "white";

export async function load3Dmol(): Promise<StructureViewerModule> {
  const mod = await import("3dmol");
  return ((mod as { default?: unknown }).default ?? mod) as StructureViewerModule;
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

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

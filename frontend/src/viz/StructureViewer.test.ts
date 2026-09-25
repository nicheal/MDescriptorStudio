import { beforeEach, describe, expect, it, vi } from "vitest";
import { createStructureViewer, disposeStructureViewer } from "./StructureViewer";

const { createViewerMock, hosts } = vi.hoisted(() => ({
  createViewerMock: vi.fn(),
  hosts: [] as HTMLElement[],
}));

// 3Dmol is reached through a CommonJS interop, so both shapes have to exist:
// `load3Dmol` prefers the default export and falls back to the namespace.
vi.mock("3dmol", () => ({ default: { createViewer: createViewerMock }, createViewer: createViewerMock }));

/** What 3Dmol leaves behind in the container it is handed: one canvas. */
function fake3Dmol() {
  hosts.length = 0;
  createViewerMock.mockImplementation((element: HTMLElement) => {
    hosts.push(element);
    element.appendChild(document.createElement("canvas"));
    return { clear: vi.fn() };
  });
}

describe("structure viewer lifecycle", () => {
  beforeEach(() => {
    // jsdom has no canvas: dispose's WebGL context release is browser-only
    // and reaches the jsdom "not implemented" notice without this stub
    // (same pattern as analysisRendering.test.ts).
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    createViewerMock.mockReset();
    fake3Dmol();
  });

  it("gives each viewer a host of its own inside the page's element", async () => {
    const element = document.createElement("div");
    const viewer = await createStructureViewer(element);

    expect(hosts).toHaveLength(1);
    expect(hosts[0]).not.toBe(element);
    expect(element.contains(hosts[0])).toBe(true);
    expect(element.querySelectorAll("canvas")).toHaveLength(1);

    disposeStructureViewer(viewer);
    expect(element.children).toHaveLength(0);
  });

  it("releases only its own canvas when a cancelled mount overlaps the next one", async () => {
    // Explore and the preview card unmount while 3Dmol is still loading and then
    // mount again: the cancelled viewer is released after the surviving one has
    // attached its canvas. Releasing through the element the two shared removed
    // both, which is the permanently blank structure pane.
    const element = document.createElement("div");
    const cancelled = await createStructureViewer(element);
    const surviving = await createStructureViewer(element);
    expect(element.querySelectorAll("canvas")).toHaveLength(2);

    disposeStructureViewer(cancelled);

    expect(hosts[0].parentElement).toBeNull();
    expect(hosts[1].parentElement).toBe(element);
    expect(hosts[1].querySelector("canvas")).not.toBeNull();

    disposeStructureViewer(surviving);
    expect(element.children).toHaveLength(0);
  });

  it("accepts a mount that never produced a viewer", () => {
    expect(() => disposeStructureViewer(null)).not.toThrow();
    expect(() => disposeStructureViewer(undefined)).not.toThrow();
  });
});

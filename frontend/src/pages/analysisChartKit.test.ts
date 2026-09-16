// In WebGL-less WebView2 environments Plotly renders a "WebGL is not supported"
// banner instead of scattergl charts. These tests pin the SVG downgrade in
// plotData(), which must keep non-GL traces untouched and pass through when
// WebGL works. plotData caches the detection, so each case imports a fresh module.
import { describe, expect, it, vi } from "vitest";
import type { Data } from "plotly.js";

vi.mock("../plotlyBundle", () => ({ default: () => null }));
vi.mock("antd", () => ({ Empty: () => null, Typography: { Text: () => null } }));

async function importFresh() {
  vi.resetModules();
  return await import("./analysisChartKit");
}

const GL_TRACE = { type: "scattergl", mode: "markers", x: [1], y: [2] } as Data;
const SVG_TRACE = { type: "bar", x: [1], y: [2] } as Data;

describe("plotData", () => {
  it("downgrades scattergl to SVG scatter when WebGL is unavailable (jsdom has no canvas)", async () => {
    const getContextSpy = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    try {
      const { plotData, webglAvailable } = await importFresh();
      expect(webglAvailable()).toBe(false);
      const result = plotData([GL_TRACE, SVG_TRACE]);
      expect(result[0].type).toBe("scatter");
      expect((result[0] as { mode?: unknown }).mode).toBe("markers");
      expect(result[1]).toBe(SVG_TRACE);
    } finally {
      getContextSpy.mockRestore();
    }
  });

  it("passes traces through untouched when WebGL is available", async () => {
    const createSpy = vi.spyOn(document, "createElement").mockReturnValue({ getContext: () => ({}) } as unknown as HTMLCanvasElement);
    try {
      const { plotData, webglAvailable } = await importFresh();
      expect(webglAvailable()).toBe(true);
      expect(plotData([GL_TRACE])).toEqual([GL_TRACE]);
    } finally {
      createSpy.mockRestore();
    }
  });
});

import { expect, test } from "@playwright/test";

test("sampling scatter releases mouse capture and allows app navigation", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis", exact: true }).click();
  await page.getByRole("tab", { name: "Dataset Sampling", exact: true }).click();
  await page.getByRole("tab", { name: "Representative Sampling", exact: true }).click();
  await page.evaluate(async () => {
    const modulePath = "/src/ipc/client.ts";
    const { ipc } = await import(modulePath);
    const request = ipc.request.bind(ipc);
    ipc.request = async (method: string, params: object) => {
      if (method === "analysis.chunk") return { data: Array.from({ length: 1000 }, (_, i) => 1 / (i + 1)), shape: [1000], truncated: false };
      const result = await request(method, params);
      if (method === "analysis.preview" && result.kind === "sampling") {
        return { ...result, kind: "acquisition", algorithm: "novelty_fps", selected_count: 1000,
          points: Array.from({ length: 3813 }, (_, i) => ({ i, frame: i, x: Math.sin(i) * (1 + i / 3813), y: Math.cos(i) * (1 + i / 3813), distance: i / 3813 })),
          selected: Array.from({ length: 1000 }, (_, i) => ({ i, frame: i })) };
      }

      return result;
    };
  });
  await page.getByRole("button", { name: /Run Representative Sampling/i }).click();
  const plot = page.getByRole("group", { name: "Selected representative samples in descriptor space", exact: true }).locator(".js-plotly-plot");
  await expect(plot).toBeVisible();
  type Graph = HTMLElement & { data: { x: number[]; y: number[] }[]; _fullLayout: { xaxis: { _offset: number; l2p: (x: number) => number }; yaxis: { _offset: number; l2p: (y: number) => number } } };
  await expect.poll(() => plot.evaluate((el) => Boolean((el as Graph)._fullLayout?.xaxis))).toBe(true);
  const score = page.getByRole("group", { name: "Score at the moment of each pick", exact: true });
  await expect(score.locator(".xtick")).not.toHaveCount(0);
  expect(await score.locator(".xtick").count()).toBeLessThanOrEqual(20);
  await plot.scrollIntoViewIfNeeded();
  const point = await plot.evaluate((el) => {
    const graph = el as Graph;
    const rect = el.getBoundingClientRect();
    const { xaxis, yaxis } = graph._fullLayout;
    return { x: rect.left + xaxis._offset + xaxis.l2p(graph.data[0].x[0]), y: rect.top + yaxis._offset + yaxis.l2p(graph.data[0].y[0]) };
  });
  await page.mouse.click(point.x, point.y);
  await expect(page.locator(".dragcover")).toHaveCount(0, { timeout: 3000 });
  await expect(page.getByText("1 frames selected", { exact: true })).toBeVisible({ timeout: 3000 });
  await page.getByRole("button", { name: "Results", exact: true }).click({ timeout: 3000 });
  await expect(page.getByText("DESCRIPTOR RESULTS", { exact: true })).toBeVisible();
});




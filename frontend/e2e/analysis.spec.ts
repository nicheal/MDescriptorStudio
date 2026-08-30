import { expect, test } from "@playwright/test";

test("browser preview keeps descriptor results in a separate Results page", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Results", exact: true }).click();
  await expect(page.getByText("DESCRIPTOR RESULTS", { exact: true })).toBeVisible();
  await expect(page.getByRole("table")).toBeVisible();

  await page.getByRole("button", { name: "Analysis", exact: true }).click();
  await expect(page.getByText("DESCRIPTOR RESULTS", { exact: true })).not.toBeVisible();
});

test("browser preview exposes the Analysis workflow and run selector", async ({ page }) => {
  await page.goto("/preview.html");
  await expect(page.getByRole("button", { name: "Analysis" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Analysis" }).click();
  await expect(page.getByRole("combobox", { name: "Analysis descriptor run" })).toBeVisible();
  await expect(page.getByText(/analysis history/i)).toBeVisible();
});

test("browser preview reloads cached PCA and changes coordinates with preprocessing", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  await page.getByRole("button", { name: "Load pca analysis" }).click();
  const plot = page.locator(".analysis-plot-frame .js-plotly-plot");
  await expect(plot).toBeVisible({ timeout: 30_000 });

  const before = await plot.evaluate((node) => JSON.stringify((node as unknown as { data?: { x?: number[] }[] }).data?.[0]?.x?.slice(0, 6)));
  const preprocess = page.locator(".analysis-controls .ant-select").nth(2);
  await preprocess.click();
  await page.getByText("Standardized", { exact: true }).last().click();
  await expect.poll(async () => {
    const current = page.locator(".analysis-plot-frame .js-plotly-plot");
    if (await current.count() === 0) return null;
    return current.evaluate((node) => JSON.stringify((node as unknown as { data?: { x?: number[] }[] }).data?.[0]?.x?.slice(0, 6)));
  }, { timeout: 30_000 }).not.toBe(before);
});

test("browser preview renders an Overview chart after a module run", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  await expect(page.getByRole("button", { name: "Run feature variance" })).toBeVisible();
  await page.getByRole("button", { name: "Run feature variance" }).click();
  await expect(page.locator(".analysis-overview-chart-frame")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("FEATURE VARIANCE", { exact: true })).toBeVisible();
});

test("browser preview places the sensitivity run pair after Module", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();

  const controls = page.locator(".analysis-controls");
  await controls.locator(":scope > .ant-space").first().locator(".ant-select").click();
  await page.getByText("Parameter sensitivity", { exact: true }).last().click();

  const spaces = controls.locator(":scope > .ant-space");
  await expect(spaces.nth(0)).toContainText("Module");
  await expect(spaces.nth(1)).toContainText("Reference");
  await expect(spaces.nth(1)).toContainText("Query");
});

test("browser preview renders the dedicated pairwise similarity heatmap", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  await page.getByRole("tab", { name: "Similarity" }).click();
  const controls = page.locator(".analysis-controls");
  await controls.getByText("Query neighbors", { exact: true }).click();
  await page.getByText("Pairwise matrix", { exact: true }).last().click();
  await page.getByRole("button", { name: "Run Similarity" }).click();
  await expect(page.getByText("PAIRWISE SIMILARITY MATRIX", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Similarity heatmap")).toBeVisible();
});

test("atom-level local selection stays highlighted when opened in Explore", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  await page.getByRole("tab", { name: "Local" }).click();
  await page.getByRole("button", { name: "Run Local" }).click();
  await expect(page.getByText("LOCAL ENVIRONMENT DIVERSITY", { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.locator(".analysis-main .ant-table-tbody").nth(1).locator("tr").first().click();
  await expect(page.locator(".analysis-inspector").getByText("Row", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Open in Explore" }).click();
  await expect(page.locator(".explore-atom-row-selected")).toHaveCount(1, { timeout: 30_000 });
});

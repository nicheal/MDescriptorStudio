import { expect, test } from "@playwright/test";

test("browser preview keeps descriptor results in a separate Results page", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Results", exact: true }).click();
  await expect(page.getByText("DESCRIPTOR RESULTS", { exact: true })).toBeVisible();
  await expect(page.getByRole("table")).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Device", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "GPU", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "CPU", exact: true })).toHaveCount(2);
  await expect(page.getByRole("columnheader", { name: "Compute time", exact: true })).toBeVisible();

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

test("browser preview opens the selected analysis method guide", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 720 });
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  const controls = page.locator(".analysis-controls");
  const parameterSpace = controls.locator(":scope > .ant-space").first();
  const runButton = page.getByRole("button", { name: "Run feature variance" });
  const guideButton = page.getByRole("button", { name: "Open method guide" });
  const parameterBox = await parameterSpace.boundingBox();
  const runBox = await runButton.boundingBox();
  const guideBox = await guideButton.boundingBox();
  expect(parameterBox).not.toBeNull();
  expect(runBox).not.toBeNull();
  expect(guideBox).not.toBeNull();
  expect(Math.abs((runBox?.y ?? 0) - (parameterBox?.y ?? 0))).toBeLessThan(8);
  expect(Math.abs((guideBox?.y ?? 0) - (parameterBox?.y ?? 0))).toBeLessThan(8);
  await guideButton.click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("Theory", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Applications", { exact: true })).toBeVisible();
  await expect(dialog).toContainText("Feature variance");
});

test("browser preview aligns descriptor names left and array shapes right in the run selector", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 720 });
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();

  const runSelect = page.locator(".analysis-toolbar .ant-select").first();
  const selectedLabel = runSelect.locator(".ant-select-selection-item .analysis-run-label");
  await expect(selectedLabel).toBeVisible();
  await expect(selectedLabel.locator(".analysis-run-name")).toHaveText("DPA-2");
  await expect(selectedLabel.locator(".analysis-run-shape")).toHaveText("[12480, 256]");

  const selectedBox = await selectedLabel.boundingBox();
  const nameBox = await selectedLabel.locator(".analysis-run-name").boundingBox();
  const shapeBox = await selectedLabel.locator(".analysis-run-shape").boundingBox();
  expect(selectedBox).not.toBeNull();
  expect(nameBox).not.toBeNull();
  expect(shapeBox).not.toBeNull();
  expect((nameBox?.x ?? 0)).toBeLessThan(shapeBox?.x ?? 0);
  expect(Math.abs((shapeBox?.x ?? 0) + (shapeBox?.width ?? 0) - ((selectedBox?.x ?? 0) + (selectedBox?.width ?? 0)))).toBeLessThan(3);

  await runSelect.click();
  const optionLabel = page.locator(".ant-select-item-option .analysis-run-label").first();
  await expect(optionLabel).toBeVisible();
  await expect(optionLabel.locator(".analysis-run-name")).toHaveText("DPA-2");
  await expect(optionLabel.locator(".analysis-run-shape")).toHaveText("[12480, 256]");
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

test("browser preview exposes feature variance filters and distribution detail", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  await page.getByRole("button", { name: "Run feature variance" }).click();
  await expect(page.locator(".feature-variance-layout")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("combobox", { name: "Variance metric" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Feature display filter" })).toBeVisible();
  await expect(page.getByText("35", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Constant 1" }).click();
  await expect(page.getByRole("button", { name: "Constant 1" })).toHaveAttribute("aria-pressed", "true");
  const featurePicker = page.getByRole("combobox", { name: "Select feature for detail" });
  await featurePicker.click();
  await page.locator(".ant-select-dropdown .ant-select-item-option").filter({ hasText: "Feature 0 ·" }).last().click();
  await expect(page.locator(".feature-variance-detail").getByText("Feature detail", { exact: true })).toBeVisible();
  await expect(page.getByText("Constant feature; KDE omitted.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Feature value histogram and KDE")).toBeVisible();
  await expect(page.getByLabel("Feature value box plot")).toBeVisible();
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

test("browser preview renders structural perturbation response curves", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  const controls = page.locator(".analysis-controls");
  await controls.locator(":scope > .ant-space").first().locator(".ant-select").click();
  await page.getByText("Structural perturbation", { exact: true }).last().click();
  await page.getByRole("button", { name: "Run perturbation sensitivity" }).click();
  await expect(page.getByText("STRUCTURAL PERTURBATION SENSITIVITY", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Per-structure perturbation response heatmap")).toBeVisible({ timeout: 30_000 });
});

test("browser preview exposes the Mantel permutation visualization", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  await page.getByRole("tab", { name: "Compare" }).click();
  const controls = page.locator(".analysis-controls");
  const selects = controls.locator(".ant-select");
  await selects.nth(1).click();
  await page.getByText("ACE · run-ace", { exact: true }).last().click();
  await selects.nth(2).click();
  await page.getByText("Mantel permutation test", { exact: true }).last().click();
  await page.getByRole("button", { name: "Run Compare" }).click();
  await expect(page.getByText("MANTEL PERMUTATION TEST", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Mantel permutation null distribution")).toBeVisible({ timeout: 30_000 });
});

test("language switch in Settings applies immediately and persists across reload", async ({ page }) => {
  await page.goto("/preview.html");
  await expect(page.getByRole("button", { name: "Analysis" })).toBeVisible({ timeout: 30_000 });

  // open Settings and switch to Chinese (antd radio buttons hide their inputs)
  await page.getByRole("button", { name: "Settings" }).click();
  await page.getByText("简体中文", { exact: true }).click();
  await expect(page.getByRole("button", { name: "总览" })).toBeVisible();
  await expect(page.getByText("语言", { exact: true })).toBeVisible();

  // the choice persists (localStorage) — reload and verify Chinese is still active
  await page.reload();
  await expect(page.getByRole("button", { name: "总览" })).toBeVisible({ timeout: 30_000 });

  // switch back to English
  await page.getByRole("button", { name: "设置" }).click();
  await page.getByText("English", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Overview" })).toBeVisible();
});

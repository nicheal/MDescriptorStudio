import { expect, test } from "@playwright/test";

// Regression tests for the Analysis restore logic: switching the Overview
// module (or returning to the Overview tab) must never auto-navigate to
// another tab. A result slot recorded under the wrong (tab, parameters)
// context used to make the page jump to Projection-tSNE.

async function openAnalysis(page: import("@playwright/test").Page) {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  await expect(page.getByRole("combobox", { name: "Analysis descriptor run" })).toBeVisible();
}

async function runTsne(page: import("@playwright/test").Page) {
  await page.getByRole("tab", { name: "Projection" }).click();
  await page.locator(".analysis-controls .ant-select").first().click();
  await page.locator(`.ant-select-item-option[title="t-SNE"]`).click();
  await page.getByRole("button", { name: "Run TSNE" }).click();
}

async function selectOverviewModule(page: import("@playwright/test").Page, label: string) {
  await page.locator(".analysis-controls .ant-select").first().click();
  // Options with a computed result carry the cache-dot node label, so match
  // by text inside the open dropdown instead of the title attribute.
  await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").getByText(label, { exact: true }).click();
  await expect(page.getByRole("button", { name: `Run ${label.toLowerCase()}` })).toBeVisible();
}

test("running t-SNE while leaving Projection does not poison the Overview module switch", async ({ page }) => {
  await openAnalysis(page);

  // Start a t-SNE run and switch to Overview while the job is still running.
  await runTsne(page);
  await page.getByRole("tab", { name: "Overview" }).click();

  // Wait for the job to finish (the toolbar running indicator clears; only
  // the module that started the job shows a busy Run button).
  await expect(page.getByText("TSNE running")).toBeVisible();
  await expect(page.getByText("TSNE running")).toBeHidden({ timeout: 15_000 });

  // Switch Overview module away and back: the page must stay on Overview.
  await selectOverviewModule(page, "Feature correlation");
  await selectOverviewModule(page, "Feature variance");

  await expect(page.locator(".ant-tabs-tab-active")).toHaveText(/Overview/, { timeout: 10_000 });
});

test("loading a t-SNE analysis from history must not yank the page back from Overview", async ({ page }) => {
  await openAnalysis(page);

  // Compute a t-SNE result so it lands in the analysis history.
  await runTsne(page);
  await expect(page.getByRole("button", { name: "Run TSNE" })).not.toHaveClass(/ant-btn-loading/, { timeout: 15_000 });

  // From the Overview tab (module: feature variance), load the tsne history
  // row. Loading intentionally navigates to Projection; returning to Overview
  // and switching modules must stay on Overview instead of being yanked back
  // to Projection-tSNE.
  await page.getByRole("tab", { name: "Overview" }).click();
  await page.getByRole("button", { name: "Load tsne analysis" }).click();
  await expect(page.locator(".ant-tabs-tab-active")).toHaveText(/Projection/, { timeout: 10_000 });

  await page.getByRole("tab", { name: "Overview" }).click();
  await selectOverviewModule(page, "Feature correlation");
  await selectOverviewModule(page, "Feature variance");

  await expect(page.locator(".ant-tabs-tab-active")).toHaveText(/Overview/, { timeout: 10_000 });
});

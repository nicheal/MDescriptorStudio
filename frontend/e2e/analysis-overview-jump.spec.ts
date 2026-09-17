import { expect, test } from "@playwright/test";

// Regression tests for Analysis restore logic: switching shared overview
// modules must never cross module boundaries or jump to another group.

async function openAnalysis(page: import("@playwright/test").Page) {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  await expect(page.getByRole("combobox", { name: "Analysis descriptor run" })).toBeVisible();
}

async function selectModule(page: import("@playwright/test").Page, group: string, module: string) {
  await page.getByRole("tab", { name: group, exact: true }).click();
  await page.getByRole("tab", { name: module, exact: true }).click();
}

async function runTsne(page: import("@playwright/test").Page) {
  await selectModule(page, "Structure & Environments", "Descriptor Space");
  await page.locator(".analysis-controls .ant-select").first().click();
  await page.locator(`.ant-select-item-option[title="t-SNE"]`).click();
  await page.getByRole("button", { name: "Run TSNE" }).click();
}

async function selectOverviewModule(page: import("@playwright/test").Page, label: string) {
  await selectModule(page, "Representation Quality", label);
  await expect(page.getByRole("button", { name: new RegExp(`Run ${label}`, "i") })).toBeVisible();
}

test("running t-SNE while leaving Projection does not poison the Overview module switch", async ({ page }) => {
  await openAnalysis(page);

  // Start a t-SNE run and switch to a shared overview module while the job is still running.
  await runTsne(page);
  await selectOverviewModule(page, "Feature Correlation");

  // Wait for the job to finish (the toolbar running indicator clears; only
  // the module that started the job shows a busy Run button).
  await expect(page.getByText("TSNE running")).toBeVisible();
  await expect(page.getByText("TSNE running")).toBeHidden({ timeout: 15_000 });

  // Switch shared overview module away and back: the page must stay in its group.
  await selectOverviewModule(page, "Feature Variance");
  await selectOverviewModule(page, "Feature Correlation");

  await expect(page.getByRole("tab", { name: "Representation Quality", exact: true })).toHaveAttribute("aria-selected", "true", { timeout: 10_000 });
});

test("loading a t-SNE analysis from history must not yank the page back from Overview", async ({ page }) => {
  await openAnalysis(page);

  // Compute a t-SNE result so it lands in the analysis history.
  await runTsne(page);
  await expect(page.getByRole("button", { name: "Run TSNE" })).not.toHaveClass(/ant-btn-loading/, { timeout: 15_000 });

  // From a shared overview module, load the tsne history row. Loading
  // intentionally navigates to Descriptor Space; returning to the overview
  // group must stay there instead of being yanked back to t-SNE.
  await selectOverviewModule(page, "Feature Variance");
  await page.getByRole("button", { name: "Load tsne analysis" }).click();
  await expect(page.getByRole("tab", { name: "Descriptor Space", exact: true })).toHaveAttribute("aria-selected", "true", { timeout: 10_000 });

  await selectOverviewModule(page, "Feature Correlation");
  await selectOverviewModule(page, "Feature Variance");

  await expect(page.getByRole("tab", { name: "Representation Quality", exact: true })).toHaveAttribute("aria-selected", "true", { timeout: 10_000 });
});

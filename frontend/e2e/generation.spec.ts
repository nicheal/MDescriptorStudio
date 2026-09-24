import { expect, test } from "@playwright/test";

test("generation page walks config → running → results against the mock backend", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Generation", exact: true }).click();
  await expect(page.getByText("DATASET EXPANSION", { exact: true })).toBeVisible();

  // Six-section workflow form is present (section cards number their titles).
  const heads = page.locator(".ant-card-head-title");
  for (const section of ["SOURCE", "SEARCH OBJECTIVE", "SEARCH SPACE", "PHYSICAL CONSTRAINTS", "OPTIMIZER", "COMPUTE BUDGET"]) {
    await expect(heads.filter({ hasText: section })).toBeVisible();
  }

  // The mock pre-selects the active dataset and a completed descriptor run.
  await expect(page.getByRole("button", { name: /Run Expansion/i })).toBeEnabled();

  await page.getByRole("button", { name: /Run Expansion/i }).click();
  // The mock run settles in ~800ms, fast enough that the running phase can
  // flash by — the durable assertion is the results view.
  await expect(page.getByText("Run summary", { exact: true })).toBeVisible({ timeout: 10000 });
  await expect(heads.filter({ hasText: "LOCAL ENVIRONMENT DISCOVERY" })).toBeVisible();
  await expect(heads.filter({ hasText: "Descriptor space" })).toBeVisible();
  await expect(heads.filter({ hasText: "Accepted structures" })).toBeVisible();

  // The run lands in the expansion history once back on the configuration view.
  await page.getByRole("button", { name: /Back to configuration/i }).click();
  await expect(page.getByText("Expansion history", { exact: true })).toBeVisible();
});

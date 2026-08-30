import { expect, test } from "@playwright/test";

test("browser preview exposes the Analysis workflow and run selector", async ({ page }) => {
  await page.goto("/preview.html");
  await expect(page.getByRole("button", { name: "Analysis" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Analysis" }).click();
  await expect(page.getByRole("combobox", { name: "Analysis descriptor run" })).toBeVisible();
  await expect(page.getByText(/analysis history/i)).toBeVisible();
});

test("browser preview renders an Overview chart after a module run", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  await expect(page.getByRole("button", { name: "Run feature variance" })).toBeVisible();
  await page.getByRole("button", { name: "Run feature variance" }).click();
  await expect(page.locator(".analysis-overview-chart-frame")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("FEATURE VARIANCE", { exact: true })).toBeVisible();
});

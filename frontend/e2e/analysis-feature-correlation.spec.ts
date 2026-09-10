import { expect, test } from "@playwright/test";

test("feature correlation runs with Spearman and enables clustered ordering", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();

  await page.locator(".analysis-controls .ant-select").first().click();
  await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").getByText("Feature correlation", { exact: true }).click();

  await page.getByRole("combobox", { name: "Correlation method" }).locator("xpath=../../..").click();
  await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").getByText("Spearman", { exact: true }).click();
  await page.getByRole("button", { name: "Run feature correlation" }).click();

  await expect(page.getByText("FEATURE CORRELATION", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("columnheader", { name: "Spearman ρ" })).toBeVisible();
  await expect(page.locator(".feature-correlation-table table")).toHaveCSS("table-layout", "fixed");

  await page.getByRole("combobox", { name: "Feature order" }).locator("xpath=../../..").click();
  const clusteredOption = page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option", { hasText: "Clustered order" });
  await expect(clusteredOption).not.toHaveClass(/ant-select-item-option-disabled/);
  await clusteredOption.click();
  await expect(page.locator(".analysis-purpose-chart").getByText("Feature · Clustered order", { exact: true })).toBeVisible();
});

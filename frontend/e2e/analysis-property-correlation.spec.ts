import { expect, test } from "@playwright/test";

test("property analysis is organized around encoding, localization, and reliability", async ({ page }) => {
  await page.setViewportSize({ width: 2880, height: 1080 });
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();

  await page.locator(".analysis-controls .ant-select").first().click();
  await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").getByText("Property correlation", { exact: true }).click();
  await expect(page.getByRole("spinbutton", { name: "CV folds" })).toHaveValue("5");
  await expect(page.getByRole("combobox", { name: "Distance metric" })).toBeVisible();

  const rowCenters = await Promise.all([
    page.locator(".analysis-overview-module-select").boundingBox(),
    page.getByRole("spinbutton", { name: "CV folds" }).boundingBox(),
    page.getByRole("spinbutton", { name: "OOD-like threshold" }).boundingBox(),
    page.getByRole("button", { name: "Run property correlation" }).boundingBox(),
    page.getByRole("button", { name: "Open method guide" }).boundingBox(),
  ]).then((boxes) => boxes.map((box) => box ? box.y + box.height / 2 : Number.NaN));
  expect(rowCenters.every(Number.isFinite)).toBe(true);
  expect(Math.max(...rowCenters) - Math.min(...rowCenters)).toBeLessThan(2);
  const controlWidths = await page.locator(".analysis-controls-property").evaluate((element) => ({
    client: element.clientWidth,
    scroll: element.scrollWidth,
  }));
  expect(controlWidths.scroll).toBeLessThanOrEqual(controlWidths.client + 1);

  await page.getByRole("button", { name: "Run property correlation" }).click();

  await expect(page.getByText("PROPERTY INFORMATION", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Property Encoding", { exact: true })).toBeVisible();
  await expect(page.getByText("Information Localization", { exact: true })).toBeVisible();
  await expect(page.getByText("Representation Reliability", { exact: true })).toBeVisible();
  await expect(page.getByText("OOF Prediction vs. Ground Truth", { exact: true })).toBeVisible();
  await expect(page.getByText("Descriptor standardized within each fold", { exact: true })).toBeVisible();
  await expect(page.locator(".analysis-json-preview")).toHaveCount(0);

  await page.getByRole("combobox", { name: "Association method" }).locator("xpath=../../..").click();
  await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").getByText("Mutual Information", { exact: true }).click();
  await expect(page.getByLabel("Feature–Property Association").getByText("Mutual Information", { exact: true })).toBeVisible();

  await page.getByRole("combobox", { name: "Prediction view" }).locator("xpath=../../..").click();
  await page.locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)").getByText("Density", { exact: true }).click();
  const plots = page.locator(".property-analysis .js-plotly-plot");
  await expect(plots).toHaveCount(4);

});

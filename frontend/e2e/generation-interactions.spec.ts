import { expect, test, type Page } from "@playwright/test";

type PreviewMock = {
  failNext: (method: string, message: string, count?: number) => void;
  delayNext: (method: string, milliseconds: number) => void;
  setGenerationSubmitStatus: (status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED") => void;
  omitGenerationDiscovery: (omit: boolean) => void;
  count: (method: string) => number;
};

async function enterGeneration(page: Page) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/preview.html");
  await page.waitForFunction(() => Boolean((window as unknown as { __mdsMock?: PreviewMock }).__mdsMock));
  await page.getByRole("button", { name: "Generation", exact: true }).click();
  await expect(page.getByText("DATASET EXPANSION", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Run Expansion/i })).toBeEnabled({ timeout: 10000 });
}

async function injectFailure(page: Page, method: string, message: string, count = 1) {
  await page.evaluate(({ method, message, count }) => {
    (window as unknown as { __mdsMock: PreviewMock }).__mdsMock.failNext(method, message, count);
  }, { method, message, count });
}

async function setGenerationStatus(page: Page, status: "RUNNING" | "COMPLETED" | "FAILED") {
  await page.evaluate((status) => {
    (window as unknown as { __mdsMock: PreviewMock }).__mdsMock.setGenerationSubmitStatus(status);
  }, status);
}

test("history and seed scope errors keep data and scope safe with retry", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/preview.html");
  await page.waitForFunction(() => Boolean((window as unknown as { __mdsMock?: PreviewMock }).__mdsMock));
  // The preview route is mounted under React StrictMode, which intentionally exercises mount effects twice in development.
  await injectFailure(page, "generation.list", "mock history transport failed", 2);
  await page.getByRole("button", { name: "Generation", exact: true }).click();
  await expect(page.getByText("Could not load expansion history", { exact: true })).toBeVisible();
  await expect(page.getByText("No expansion runs yet — submit one from configuration.", { exact: true })).not.toBeVisible();
  await page.locator(".ant-alert").filter({ hasText: "Could not load expansion history" }).locator(".ant-alert-action button").click();
  await expect(page.getByText("Could not load expansion history", { exact: true })).not.toBeVisible();

  const scope = page.getByLabel("Seed scope").first();
  await scope.click({ force: true });
  await page.getByText("Training split · 9984", { exact: true }).click();
  await injectFailure(page, "dataset.view.list", "mock view refresh failed", 2);
  await page.evaluate(() => window.dispatchEvent(new Event("dataset-views-changed")));
  await expect(page.getByText("Could not verify seed views", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /Run Expansion/i })).toBeDisabled();
  await expect(page.locator(".ant-select").filter({ has: page.locator("#source-seedViewId") }).locator(".ant-select-selection-item")).toContainText("Training split · 9984");
  await page.locator(".ant-alert").filter({ hasText: "Could not verify seed views" }).locator(".ant-alert-action button").click();
  await expect(page.getByText("Could not verify seed views", { exact: true })).not.toBeVisible();
  await expect(page.locator(".ant-select").filter({ has: page.locator("#source-seedViewId") }).locator(".ant-select-selection-item")).toContainText("Training split · 9984");
});

test("catalog and descriptor failures offer local retry", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/preview.html");
  await page.waitForFunction(() => Boolean((window as unknown as { __mdsMock?: PreviewMock }).__mdsMock));
  await injectFailure(page, "generation.catalog", "mock catalog unavailable", 2);
  await injectFailure(page, "result.list", "mock descriptor list unavailable", 2);
  await page.getByRole("button", { name: "Generation", exact: true }).click();
  await expect(page.getByText("Could not load the expansion catalog", { exact: true })).toBeVisible();
  await expect(page.getByText("Could not load descriptor runs", { exact: true })).toBeVisible();
  await page.locator(".ant-alert").filter({ hasText: "Could not load the expansion catalog" }).locator(".ant-alert-action button").click();
  await page.locator(".ant-alert").filter({ hasText: "Could not load descriptor runs" }).getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByText("Could not load the expansion catalog", { exact: true })).not.toBeVisible();
  await expect(page.getByText("Could not load descriptor runs", { exact: true })).not.toBeVisible();
  await expect(page.getByRole("button", { name: /Run Expansion/i })).toBeEnabled();
});

test("field errors persist across edits, anchor text is preserved, and keyboard actions focus the field", async ({ page }) => {
  await enterGeneration(page);
  const anchors = page.getByLabel("Anchor dataset frames");
  await anchors.fill("12abc");
  await page.getByText("SEARCH OBJECTIVE", { exact: true }).click();
  await expect(anchors).toHaveValue("12abc");
  await expect(page.locator("#generation-anchor-error")).toBeVisible();
  await anchors.fill("");
  await expect(page.locator("#generation-anchor-error")).not.toBeVisible();

  await page.getByRole("button", { name: "Advanced element-pair distance overrides" }).click();
  await page.getByLabel("Element-pair distance overrides").fill("C-C=bad");
  for (const label of ["Atomic displacement", "Isotropic strain", "Anisotropic strain", "Cell shear", "Vacancy", "Interstitial atom", "Substitution", "Antisite swap"]) {
    const control = page.getByRole("switch", { name: label, exact: true });
    if (await control.getAttribute("aria-checked") === "true") await control.click();
  }
  await page.getByRole("button", { name: /Run Expansion/i }).click();
  const summary = page.locator("#generation-error-summary");
  await expect(summary).toBeVisible();
  await expect(summary).toContainText("Enable at least one structure operator");
  await expect(summary).toContainText("Use element pairs like C-C=1.5, C-H=1.0 with positive distances up to 20 Å");

  await page.getByLabel("Element-pair distance overrides").fill("C-C=1.5");
  await expect(summary).toContainText("Enable at least one structure operator");
  await expect(summary).not.toContainText("Use element pairs like C-C=1.5, C-H=1.0 with positive distances up to 20 Å");
  const operatorError = summary.getByRole("button", { name: "Enable at least one structure operator" });
  await operatorError.focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe("searchSpace-operators");
});

test("a poll error can be retried and cancellation still exposes accepted-result actions", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/preview.html");
  await page.waitForFunction(() => Boolean((window as unknown as { __mdsMock?: PreviewMock }).__mdsMock));
  await setGenerationStatus(page, "RUNNING");
  await injectFailure(page, "generation.get", "mock status connection failed");
  await page.evaluate(() => (window as unknown as { __mdsMock: PreviewMock }).__mdsMock.omitGenerationDiscovery(true));
  await page.getByRole("button", { name: "Generation", exact: true }).click();
  await expect(page.getByRole("button", { name: /Run Expansion/i })).toBeEnabled();
  await page.getByRole("button", { name: /Run Expansion/i }).click();
  const pollError = page.locator(".ant-alert").filter({ hasText: "mock status connection failed" });
  await expect(pollError).toBeVisible({ timeout: 5000 });
  await pollError.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Back to configuration", exact: true }).click();
  await expect(page.getByRole("button", { name: "Return to current run", exact: true })).toBeVisible();
  await injectFailure(page, "generation.get", "mock background status refresh failed");
  const backgroundError = page.locator(".ant-alert").filter({ hasText: "mock background status refresh failed" });
  await expect(backgroundError).toBeVisible({ timeout: 5000 });
  await page.getByRole("button", { name: "Return to current run", exact: true }).click();
  await expect(page.locator(".ant-alert").filter({ hasText: "mock background status refresh failed" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("button", { name: "Save as new dataset", exact: true })).toBeVisible({ timeout: 10000 });
  await expect(page.getByText("Cancelled", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Discovery statistics are unavailable for this run", { exact: true })).toBeVisible();
});

test("failed generation results show the backend error and return action", async ({ page }) => {
  await enterGeneration(page);
  await setGenerationStatus(page, "FAILED");
  await page.getByRole("button", { name: /Run Expansion/i }).click();
  await expect(page.getByText("Mock expansion failed during initialization", { exact: true })).toBeVisible({ timeout: 10000 });
  await page.getByRole("button", { name: "Back to configuration", exact: true }).first().click();
  await expect(page.getByRole("button", { name: /Run Expansion/i })).toBeVisible();
});

test("registration retry reuses the saved artifact instead of materializing twice", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/preview.html");
  await page.waitForFunction(() => Boolean((window as unknown as { __mdsMock?: PreviewMock }).__mdsMock));
  await injectFailure(page, "dataset.register", "mock registration unavailable");
  await page.getByRole("button", { name: "Generation", exact: true }).click();
  await expect(page.getByRole("button", { name: /Run Expansion/i })).toBeEnabled();
  await page.getByRole("button", { name: /Run Expansion/i }).click();
  await page.getByRole("button", { name: "Save as new dataset", exact: true }).waitFor({ state: "visible", timeout: 10000 });
  await page.getByRole("button", { name: "Save as new dataset", exact: true }).click();
  await expect(page.getByText("Dataset written but registration did not complete", { exact: true })).toBeVisible({ timeout: 10000 });
  await expect(page.locator(".ant-alert-description")).toContainText("C:\\preview\\analysis_subset.csv");
  await page.getByRole("button", { name: "Retry registration", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __mdsMock: PreviewMock }).__mdsMock.count("dataset.register"))).toBe(2);
  await expect(page.getByText("Expanded dataset registered with lineage", { exact: true })).toBeVisible({ timeout: 10000 });
  await expect(page.getByText("Dataset written but registration did not complete", { exact: true })).not.toBeVisible();
  await expect(page.getByText("Dataset written but registration did not complete", { exact: true })).not.toBeVisible({ timeout: 10000 });
  const counts = await page.evaluate(() => {
    const mock = (window as unknown as { __mdsMock: PreviewMock }).__mdsMock;
    return [mock.count("generation.materialize"), mock.count("dataset.register")];
  });
  expect(counts).toEqual([1, 2]);
});

test("opening a run cannot steal navigation after returning to configuration", async ({ page }) => {
  await enterGeneration(page);
  await page.getByRole("button", { name: /Run Expansion/i }).click();
  await expect(page.getByText("Run summary", { exact: true })).toBeVisible({ timeout: 10000 });
  await page.getByRole("button", { name: "Back to configuration", exact: true }).click();
  await expect(page.getByRole("button", { name: "Return to current run", exact: true })).toBeVisible();
  await page.evaluate(() => (window as unknown as { __mdsMock: PreviewMock }).__mdsMock.delayNext("generation.get", 900));
  await page.getByRole("button", { name: /Open .* expansion/ }).first().click();
  await expect(page.getByRole("button", { name: "Back to configuration", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Back to configuration", exact: true }).click();
  await expect(page.getByRole("button", { name: /Run Expansion/i })).toBeVisible();
  await page.waitForTimeout(1100);
  await expect(page.getByRole("button", { name: /Run Expansion/i })).toBeVisible();
  await expect(page.getByRole("button", { name: "Return to current run", exact: true })).toBeVisible();
});

test("dataset changes isolate history and clear dataset-bound seed scope", async ({ page }) => {
  await enterGeneration(page);
  const scope = page.getByLabel("Seed scope").first();
  await scope.click({ force: true });
  await page.getByText("Training split · 9984", { exact: true }).click();

  // Seed a completed mock row for GaAs so the history response has something
  // observable to leak if the dataset request guard is wrong.
  await page.evaluate(() => {
    const mock = (window as unknown as { __mdsMock: PreviewMock }).__mdsMock;
    mock.call("generation.submit", { dataset_id: "ds-gaas", descriptor_run_id: "run-dpa2" });
    window.dispatchEvent(new Event("generation-history-changed"));
  });
  await expect(page.getByRole("button", { name: /Open .* expansion/ })).toBeVisible();

  // Hold a GaAs history response while the workspace switches to Si. Its old
  // response must not replace Si's newer, dataset-isolated history.
  await page.evaluate(() => {
    const mock = (window as unknown as { __mdsMock: PreviewMock }).__mdsMock;
    mock.delayNext("generation.list", 700);
    window.dispatchEvent(new Event("generation-history-changed"));
  });
  await page.getByText("Si Training Set", { exact: true }).click();
  await expect(page.getByText("No expansion runs yet — submit one from configuration.", { exact: true })).toBeVisible();
  await page.waitForTimeout(850);
  await expect(page.getByRole("button", { name: /Open .* expansion/ })).toHaveCount(0);
  await expect(page.locator(".ant-select").filter({ has: page.locator("#source-seedViewId") }).locator(".ant-select-selection-item")).toContainText("Full dataset");
  await expect(page.getByRole("button", { name: /Run Expansion/i })).toBeEnabled();
});
test("generation form fits wide and narrow preview sizes without obscuring focused budget fields", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Generation", exact: true }).click();
  await expect(page.getByRole("button", { name: /Run Expansion/i })).toBeEnabled({ timeout: 10000 });
  await page.screenshot({ path: testInfo.outputPath("generation-1280x800.png"), fullPage: false });
  const wide = await page.evaluate(() => {
    const field = document.querySelector<HTMLElement>("#budget-maxGenerations input");
    const fields = document.getElementById("generation-config-fields");
    const summary = document.getElementById("generation-config-summary");
    field?.scrollIntoView({ block: "center" });
    const fieldBox = field?.getBoundingClientRect();
    const fieldsBox = fields?.getBoundingClientRect();
    const summaryBox = summary?.getBoundingClientRect();
    const panelBox = fields?.parentElement?.getBoundingClientRect();
    return {
      width: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
      overlaps: Boolean(fieldBox && summaryBox && fieldBox.left < summaryBox.right && fieldBox.right > summaryBox.left && fieldBox.top < summaryBox.bottom && fieldBox.bottom > summaryBox.top),
      summaryFollowsFields: Boolean(fieldsBox && summaryBox && summaryBox.top >= fieldsBox.bottom + 7),
      summaryInsidePanel: Boolean(panelBox && summaryBox && summaryBox.bottom <= panelBox.bottom + 1),
    };
  });
  expect(wide.width).toBeLessThanOrEqual(wide.viewport);
  expect(wide.overlaps).toBe(false);
  expect(wide.summaryFollowsFields).toBe(true);
  expect(wide.summaryInsidePanel).toBe(true);

  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.screenshot({ path: testInfo.outputPath("generation-wide-content.png"), fullPage: false });
  const columns = await page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll<HTMLElement>("#generation-config-fields > .ant-card"));
    const first = cards[0]?.getBoundingClientRect();
    const second = cards[1]?.getBoundingClientRect();
    return { sameRow: Boolean(first && second && Math.abs(first.top - second.top) < 2), sideBySide: Boolean(first && second && second.left > first.right) };
  });
  expect(columns.sameRow).toBe(true);
  expect(columns.sideBySide).toBe(true);
  await page.setViewportSize({ width: 1024, height: 800 });
  await page.screenshot({ path: testInfo.outputPath("generation-narrow-content.png"), fullPage: false });
  const narrow = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  expect(narrow.width).toBeLessThanOrEqual(narrow.viewport);
});
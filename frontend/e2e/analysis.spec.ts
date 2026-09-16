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

test("cross-dataset analysis selects compatible runs and a saved dataset view", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis", exact: true }).click();
  await page.getByRole("tab", { name: "Coverage", exact: true }).click();

  await expect(page.getByText("Cross-dataset analysis", { exact: true })).toBeVisible();
  const rows = page.locator(".analysis-cross-input-row");
  await expect(rows.nth(0)).toContainText("GaAs Training Set");
  await expect(rows.nth(0)).toContainText("[12480, 256]");
  await expect(rows.nth(1)).toContainText("Si Training Set");
  await expect(rows.nth(1)).toContainText("[6320, 256]");
  await expect(page.getByText("Compatible feature space", { exact: true })).toBeVisible();

  await rows.nth(0).locator(".ant-select").nth(1).click();
  await page.getByText("Training split · 9,984", { exact: true }).last().click();
  await expect(rows.nth(0)).toContainText("Training split · 9,984");
  await page.getByRole("button", { name: "Run Coverage", exact: true }).click();
  await expect(page.getByText("DATASET COVERAGE", { exact: true })).toBeVisible({ timeout: 30_000 });
});

test("dataset split entry exposes deterministic ratios and seed", async ({ page }) => {
  await page.goto("/preview.html");
  await page.locator(".dataset-item .dataset-item-actions").first().click();
  await page.getByText("Create train/validation/test split", { exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Train %");
  await expect(dialog).toContainText("Validation %");
  await expect(dialog).toContainText("Test %: 10");
  await expect(dialog).toContainText("Random seed");
  await expect(dialog.getByRole("button", { name: "Create split", exact: true })).toBeEnabled();
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

  const plotConfig = await plot.evaluate((node) => {
    const graph = node as HTMLDivElement & {
      layout?: { xaxis?: { title?: { text?: string } }; yaxis?: { title?: { text?: string } } };
      _context?: { showSendToCloud?: boolean };
    };
    return {
      xaxisTitle: graph.layout?.xaxis?.title?.text,
      yaxisTitle: graph.layout?.yaxis?.title?.text,
      showSendToCloud: graph._context?.showSendToCloud,
      modebarTitles: Array.from(node.querySelectorAll(".modebar-btn"), (button) => button.getAttribute("data-title") ?? ""),
    };
  });
  expect(plotConfig.xaxisTitle).toBe("PC1");
  expect(plotConfig.yaxisTitle).toBe("PC2");
  expect(plotConfig.showSendToCloud).toBe(false);
  expect(plotConfig.modebarTitles.some((title) => /share|cloud|chart studio/i.test(title))).toBe(false);

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

test("browser preview preserves trajectory overlay axes", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis", exact: true }).click();
  await page.locator(".analysis-overview-module-select").click();
  await page.getByText("Trajectory", { exact: true }).last().click();
  await page.getByRole("button", { name: "Run trajectory", exact: true }).click();
  await expect(page.getByText("DESCRIPTOR TRAJECTORY", { exact: true })).toBeVisible({ timeout: 30_000 });
  const timeline = page.locator(".trajectory-chart-grid .js-plotly-plot").first();
  await expect(timeline).toBeVisible({ timeout: 30_000 });
  await page.getByRole("checkbox", { name: "Distance to reference", exact: true }).check();

  await expect.poll(() => timeline.evaluate((node) => {
    const graph = node as HTMLDivElement & {
      _fullLayout?: { yaxis2?: { tickmode?: string } };
    };
    return graph._fullLayout?.yaxis2?.tickmode;
  }), { timeout: 30_000 }).toBe("auto");
  const state = await timeline.evaluate((node) => {
    const graph = node as HTMLDivElement & {
      data?: Array<{ type?: string; name?: string; yaxis?: string }>;
      layout?: {
        xaxis?: { title?: { text?: string } };
        yaxis?: { title?: { text?: string } };
        yaxis2?: { title?: { text?: string }; tickmode?: string };
      };
    };
    return {
      xaxisTitle: graph.layout?.xaxis?.title?.text,
      yaxisTitle: graph.layout?.yaxis?.title?.text,
      yaxis2Title: graph.layout?.yaxis2?.title?.text,
      tickmode: graph.layout?.yaxis2?.tickmode,
      referenceTrace: graph.data?.some((trace) => trace.type === "scatter" && trace.name === "Distance to reference" && trace.yaxis === "y2"),
    };
  });
  expect(state).toEqual({
    xaxisTitle: "frame",
    yaxisTitle: "Step distance",
    yaxis2Title: "Distance from reference",
    tickmode: "auto",
    referenceTrace: true,
  });
});

test("browser preview renders an Overview chart after a module run", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  await expect(page.getByRole("button", { name: "Run feature variance" })).toBeVisible();
  await page.getByRole("button", { name: "Run feature variance" }).click();
  await expect(page.locator(".analysis-overview-chart-frame")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("FEATURE VARIANCE", { exact: true })).toBeVisible();
});

test("browser preview explains effective dimension metrics and spectrum ranges", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  const controls = page.locator(".analysis-controls");
  await controls.locator(":scope > .ant-space").first().locator(".ant-select").click();
  await page.getByText("Effective dimension", { exact: true }).last().click();
  await expect(page.getByRole("button", { name: "Run effective dimension" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "PCA preprocessing" })).toBeVisible();
  await page.getByRole("button", { name: "Run effective dimension" }).click();

  await expect(page.getByText("EFFECTIVE DIMENSION", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("PR effective dimension", { exact: true })).toBeVisible();
  await expect(page.getByText("90% effective dimension", { exact: true })).toBeVisible();
  await expect(page.getByLabel("PCA method details")).toContainText("Scaling");
  await expect(page.locator(".analysis-json-preview")).toHaveCount(0);

  const plot = page.locator(".analysis-overview-chart-frame .js-plotly-plot");
  await expect(plot).toBeVisible();
  const chartState = await plot.evaluate((node) => {
    const graph = node as HTMLDivElement & {
      data?: Array<{ name?: string }>;
      layout?: { shapes?: unknown[]; annotations?: Array<{ text?: string }> };
    };
    return {
      names: graph.data?.map((trace) => trace.name),
      shapes: graph.layout?.shapes?.length ?? 0,
      annotations: graph.layout?.annotations?.map((annotation) => annotation.text) ?? [],
    };
  });
  expect(chartState.names).toEqual(expect.arrayContaining(["Single-component explained variance ratio", "Cumulative explained variance ratio"]));
  expect(chartState.shapes).toBe(3);
  expect(chartState.annotations).toEqual(expect.arrayContaining(["90% · PC4", "95% · PC6", "99% · PC11"]));

  await expect(page.getByText("20 of 61 components shown", { exact: true })).toBeVisible();
  const rangeSelect = page.locator(".analysis-spectrum-toolbar .ant-select");
  await rangeSelect.click();
  await page.getByText("First 50", { exact: true }).last().click();
  await expect(page.getByText("50 of 61 components shown", { exact: true })).toBeVisible();
  await rangeSelect.click();
  await page.getByText("All components", { exact: true }).last().click();
  await expect(page.getByText("61 of 61 components shown", { exact: true })).toBeVisible();
});

test("browser preview exposes feature variance filters and distribution detail", async ({ page }) => {
  await page.goto("/preview.html");
  await page.getByRole("button", { name: "Analysis" }).click();
  await page.getByRole("button", { name: "Run feature variance" }).click();
  await expect(page.locator(".feature-variance-layout")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("combobox", { name: "Variance metric" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Feature display filter" })).toBeVisible();
  await expect(page.locator(".feature-variance-k-control input")).toBeDisabled();
  await expect(page.getByText("35", { exact: true })).toBeVisible();
  await expect(page.locator("section.analysis-card:not(.analysis-visual-card) .ant-table")).toHaveCount(0);
  const featureVariancePlot = page.locator(".feature-variance-overview-chart .js-plotly-plot");
  const featureVarianceAxis = await featureVariancePlot.evaluate((node) => {
    const graph = node as HTMLDivElement & {
      data?: Array<{ y?: unknown[]; customdata?: unknown[][] }>;
      layout?: { yaxis?: { tickvals?: unknown[]; ticktext?: unknown[] } };
    };
    const customdata = graph.data?.[0]?.customdata ?? [];
    return {
      y: graph.data?.[0]?.y ?? [],
      tickvals: graph.layout?.yaxis?.tickvals ?? [],
      ticktext: graph.layout?.yaxis?.ticktext ?? [],
      labels: customdata.map((row) => `Feature ${row[0]}`),
    };
  });
  expect(featureVarianceAxis.y).toHaveLength(35);
  expect(featureVarianceAxis.tickvals).toEqual(featureVarianceAxis.y);
  expect(featureVarianceAxis.ticktext).toHaveLength(featureVarianceAxis.y.length);
  expect(featureVarianceAxis.ticktext).toEqual(featureVarianceAxis.labels);

  await page.getByRole("button", { name: "Constant 1" }).click();
  await expect(page.getByRole("button", { name: "Constant 1" })).toHaveAttribute("aria-pressed", "true");
  const featurePicker = page.getByRole("combobox", { name: "Select feature for detail" });
  await featurePicker.click();
  await page.locator(".ant-select-dropdown .ant-select-item-option").filter({ hasText: "Feature 0 ·" }).last().click();
  await expect(page.locator(".feature-variance-detail").getByText("Feature detail", { exact: true })).toBeVisible();
  await expect(page.getByText("Constant feature; KDE omitted.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Feature value histogram and KDE")).toBeVisible();
  await expect(page.getByLabel("Feature value box plot")).toBeVisible();
  await page.getByRole("tab", { name: "Feature statistics" }).click();
  await expect(page.locator(".feature-variance-stat-grid")).toBeVisible();
  await page.getByRole("tab", { name: "Variance distribution" }).click();
  await page.locator(".feature-variance-toolbar .ant-select").nth(2).click();
  await page.getByText("All features", { exact: true }).last().click();
  await page.locator(".feature-variance-toolbar .ant-select").nth(3).click();
  await page.getByText("Linear", { exact: true }).last().click();
  await page.locator(".feature-variance-overview-chart .js-plotly-plot").evaluate((node) => {
    const graph = node as HTMLDivElement & { emit?: (eventName: string, payload: unknown) => void };
    if (typeof graph.emit !== "function") throw new Error("Plotly event emitter is unavailable");
    graph.emit("plotly_click", { points: [{ pointIndex: 1 }] });
  });
  await expect(page.getByRole("tab", { name: "Feature statistics" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".feature-variance-stat-grid")).toBeVisible();
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

import { expect, test } from "@playwright/test";

async function openAnalysis(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "Analysis", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Analysis descriptor run" })).toBeVisible();
}

async function selectAnalysisModule(page: import("@playwright/test").Page, group: string, module: string) {
  await page.getByRole("tab", { name: group, exact: true }).click();
  await page.getByRole("tab", { name: module, exact: true }).click();
}

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
  await openAnalysis(page);
  await expect(page.getByRole("tab", { name: "Structure & Environments", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Descriptor Space", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText(/analysis history/i)).toBeVisible();
});

test("cross-dataset analysis selects compatible runs and a saved dataset view", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Coverage & Novelty", "Data Coverage");

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
  await page.getByRole("button", { name: /Run Data Coverage/i }).click();
  await expect(page.getByText("DATASET COVERAGE", { exact: true })).toBeVisible({ timeout: 30_000 });
  // The result states which scale its distances were measured on, because the
  // request never said: the cross-dataset algorithms resolve and record it.
  await expect(page.getByText("Feature scale", { exact: true })).toBeVisible();
  await expect(page.locator(".analysis-metric").filter({ hasText: "Feature scale" })).toContainText("standardized");
});

test("browser preview lets the drift module choose its granularity", async ({ page }) => {
  // Drift compares whichever of the two matrices its runs share, so granularity
  // is one of its own inputs and its cache identity moves with it. Until the
  // panel had a control, changing the mode elsewhere silently moved the
  // reference points with nothing on screen to explain or undo it.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Coverage & Novelty", "Dataset Drift");
  await expect(page.getByText("Cross-dataset analysis", { exact: true })).toBeVisible();

  await expect(page.getByText("Granularity", { exact: true })).toBeVisible();
  await page.locator(".analysis-controls .ant-select").last().click();
  await page.getByText("Atom / local", { exact: true }).last().click();
  await expect(page.locator(".ant-select-selection-item").filter({ hasText: "Atom / local" })).toBeVisible();
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
  await openAnalysis(page);
  await selectAnalysisModule(page, "Representation Quality", "Feature Variance");
  const controls = page.locator(".analysis-controls");
  const parameterSpace = controls.locator(":scope > .ant-space").first();
  const runButton = page.getByRole("button", { name: /Run Feature Variance/i });
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
  await openAnalysis(page);
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
  await openAnalysis(page);
  await selectAnalysisModule(page, "Evolution & Response", "Descriptor Trajectory");
  await page.getByRole("button", { name: /Run Descriptor Trajectory/i }).click();
  await expect(page.getByText("DESCRIPTOR TRAJECTORY", { exact: true })).toBeVisible({ timeout: 30_000 });
  // The step distances only mean something with the scale they were measured on,
  // which the result now states and the panel prints beside them.
  await expect(page.locator(".analysis-metric").filter({ hasText: "Feature scale" })).toContainText("standardized");
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
  await openAnalysis(page);
  await selectAnalysisModule(page, "Representation Quality", "Feature Variance");
  await expect(page.getByRole("button", { name: /Run Feature Variance/i })).toBeVisible();
  await page.getByRole("button", { name: /Run Feature Variance/i }).click();
  await expect(page.locator(".analysis-overview-chart-frame")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("FEATURE VARIANCE", { exact: true })).toBeVisible();
});

test("browser preview explains effective dimension metrics and spectrum ranges", async ({ page }) => {
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Representation Quality", "Effective Dimension");
  await expect(page.getByRole("button", { name: /Run Effective Dimension/i })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "PCA preprocessing" })).toBeVisible();
  await page.getByRole("button", { name: /Run Effective Dimension/i }).click();

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
  await openAnalysis(page);
  await selectAnalysisModule(page, "Representation Quality", "Feature Variance");
  await page.getByRole("button", { name: /Run Feature Variance/i }).click();
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
  await openAnalysis(page);
  await selectAnalysisModule(page, "Representation Quality", "Parameter Sensitivity");

  await expect(page.getByRole("tab", { name: "Parameter Sensitivity", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("Reference", { exact: true })).toBeVisible();
  await expect(page.getByText("Query", { exact: true })).toBeVisible();
});

test("browser preview renders the dedicated pairwise similarity heatmap", async ({ page }) => {
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Structure & Environments", "Similarity");
  const controls = page.locator(".analysis-controls");
  await controls.getByText("Query neighbors", { exact: true }).click();
  await page.getByText("Pairwise matrix", { exact: true }).last().click();
  await page.getByRole("button", { name: "Run Similarity" }).click();
  await expect(page.getByText("PAIRWISE SIMILARITY MATRIX", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Similarity heatmap")).toBeVisible();
});

test("atom-level local selection stays highlighted when opened in Explore", async ({ page }) => {
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Structure & Environments", "Local Environment");
  await page.getByRole("button", { name: /Run Local Environment/i }).click();
  await expect(page.getByText("LOCAL ENVIRONMENT DIVERSITY", { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.locator(".analysis-main .ant-table-tbody").nth(1).locator("tr").first().click();
  await expect(page.locator(".analysis-inspector").getByText("Row", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Open in Explore" }).click();
  await expect(page.locator(".explore-atom-row-selected")).toHaveCount(1, { timeout: 30_000 });
});

test("browser preview renders structural perturbation response curves", async ({ page }) => {
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Evolution & Response", "Structural Perturbation Response");
  await page.getByRole("button", { name: /Run Structural Perturbation Response/i }).click();
  await expect(page.getByText("STRUCTURAL PERTURBATION SENSITIVITY", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Per-structure perturbation response heatmap")).toBeVisible({ timeout: 30_000 });
});

test("browser preview exposes the Mantel permutation visualization", async ({ page }) => {
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Representation Quality", "Descriptor Comparison");
  const controls = page.locator(".analysis-controls");
  const selects = controls.locator(".ant-select");
  await selects.nth(1).click();
  await page.getByText("ACE · run-ace", { exact: true }).last().click();
  await selects.nth(2).click();
  await page.getByText("Mantel permutation test", { exact: true }).last().click();
  await page.getByRole("button", { name: /Run Descriptor Comparison/i }).click();
  await expect(page.getByText("MANTEL PERMUTATION TEST", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Mantel permutation null distribution")).toBeVisible({ timeout: 30_000 });
});

test("Analysis navigation exposes six groups and all 18 modules in order", async ({ page }) => {
  await page.setViewportSize({ width: 2880, height: 900 });
  await page.goto("/preview.html");
  await openAnalysis(page);

  const groups = [
    { label: "Structure & Environments", modules: ["Descriptor Space", "Similarity", "Structural Clusters", "Local Environment"] },
    { label: "Property Information", modules: ["Property Information Analysis"] },
    { label: "Evolution & Response", modules: ["Descriptor Trajectory", "Structural Perturbation Response"] },
    { label: "Coverage & Novelty", modules: ["Data Coverage", "Train / Test Overlap", "Dataset Drift", "Outlier Environments"] },
    { label: "Representation Quality", modules: ["Feature Variance", "Feature Correlation", "Effective Dimension", "Kernel Analysis", "Parameter Sensitivity", "Descriptor Comparison"] },
    { label: "Dataset Sampling", modules: ["Representative Sampling"] },
  ];

  const groupTabs = page.locator(".analysis-group-tabs .ant-tabs-tab");
  await expect(groupTabs).toHaveCount(groups.length);
  await expect(groupTabs).toHaveText(groups.map((group) => group.label));

  let moduleCount = 0;
  for (const group of groups) {
    await page.getByRole("tab", { name: group.label, exact: true }).click();
    const moduleTabs = page.locator(".analysis-module-tabs .ant-tabs-tab");
    await expect(moduleTabs).toHaveCount(group.modules.length);
    await expect(moduleTabs).toHaveText(group.modules);
    moduleCount += group.modules.length;
  }
  expect(moduleCount).toBe(18);
});

test("narrow Analysis navigation keeps overflow groups keyboard accessible", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/preview.html");
  await openAnalysis(page);

  const overflow = page.locator(".analysis-group-tabs .ant-tabs-nav-more");
  await expect(overflow).toBeVisible();
  await overflow.focus();
  await overflow.press("Enter");
  const menu = page.locator(".ant-tabs-dropdown");
  await expect(menu).toBeVisible();
  await expect(menu.getByText("Dataset Sampling", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
});

test("returning to a group restores its most recently selected module", async ({ page }) => {
  await page.goto("/preview.html");
  await openAnalysis(page);

  await selectAnalysisModule(page, "Structure & Environments", "Similarity");
  await selectAnalysisModule(page, "Representation Quality", "Descriptor Comparison");
  await page.getByRole("tab", { name: "Structure & Environments", exact: true }).click();

  await expect(page.getByRole("tab", { name: "Similarity", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Descriptor Space", exact: true })).not.toHaveAttribute("aria-selected", "true");
});

test("shared overview modules keep their own group and result identity", async ({ page }) => {
  await page.goto("/preview.html");
  await openAnalysis(page);

  await selectAnalysisModule(page, "Representation Quality", "Feature Correlation");
  await selectAnalysisModule(page, "Evolution & Response", "Descriptor Trajectory");
  await selectAnalysisModule(page, "Representation Quality", "Feature Correlation");

  await expect(page.getByRole("tab", { name: "Representation Quality", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Feature Correlation", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: /Run Feature Correlation/i })).toBeVisible();
});

test("coverage and overlap history restore their separate modules", async ({ page }) => {
  await page.goto("/preview.html");
  await openAnalysis(page);

  await selectAnalysisModule(page, "Coverage & Novelty", "Data Coverage");
  await page.getByRole("button", { name: /Run Data Coverage/i }).click();
  await expect(page.getByText("DATASET COVERAGE", { exact: true })).toBeVisible({ timeout: 30_000 });

  await selectAnalysisModule(page, "Coverage & Novelty", "Train / Test Overlap");
  await page.getByRole("button", { name: /Run Train \/ Test Overlap/i }).click();
  await expect(page.getByText("TRAIN / TEST OVERLAP", { exact: true })).toBeVisible({ timeout: 30_000 });

  await page.getByRole("button", { name: "Load coverage analysis" }).click();
  await expect(page.getByRole("tab", { name: "Data Coverage", exact: true })).toHaveAttribute("aria-selected", "true", { timeout: 10_000 });
  await expect(page.getByRole("button", { name: /Run Data Coverage/i })).toBeVisible();

  await page.getByRole("button", { name: "Load overlap analysis" }).click();
  await expect(page.getByRole("tab", { name: "Train / Test Overlap", exact: true })).toHaveAttribute("aria-selected", "true", { timeout: 10_000 });
  await expect(page.getByRole("button", { name: /Run Train \/ Test Overlap/i })).toBeVisible();
});

test("history restores a non-default feature variance threshold", async ({ page }) => {
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Representation Quality", "Feature Variance");

  const thresholdInputs = page.locator(".analysis-controls .ant-input-number-input");
  await thresholdInputs.nth(0).fill("0.02");
  await thresholdInputs.nth(1).fill("0.08");
  await page.getByRole("button", { name: /Run Feature Variance/i }).click();
  await expect(page.locator(".feature-variance-layout")).toBeVisible({ timeout: 30_000 });

  await thresholdInputs.nth(1).fill("0.01");
  await page.getByRole("button", { name: "Load feature_variance analysis" }).click();
  await expect.poll(async () => thresholdInputs.nth(1).inputValue(), { timeout: 10_000 }).toBe("0.080000");
});

test("history restores the canonical cluster algorithm", async ({ page }) => {
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Structure & Environments", "Structural Clusters");

  const algorithm = page.locator(".analysis-controls .ant-select").first();
  await algorithm.click();
  await page.getByText("DBSCAN", { exact: true }).last().click();
  await page.getByRole("button", { name: /Run Structural Clusters/i }).click();
  await expect(page.getByText("CLUSTER STRUCTURE", { exact: true })).toBeVisible({ timeout: 30_000 });

  // The result table reads the same list the scatter draws, so the cluster
  // assignment has to survive the seven columns the table shows: the two
  // coordinates the chart already reads are ordered out of the window.
  const resultTable = page.locator(".analysis-main .ant-table").last();
  await expect(resultTable.getByRole("columnheader", { name: "cluster", exact: true })).toBeVisible();
  await expect(resultTable.getByRole("columnheader", { name: "label", exact: true })).toBeVisible();
  await expect(resultTable.getByRole("columnheader", { name: "x", exact: true })).toHaveCount(0);

  await algorithm.click();
  await page.getByText("KMEANS", { exact: true }).last().click();
  await page.getByRole("button", { name: "Load clusters analysis" }).last().click();
  await expect.poll(async () => algorithm.locator(".ant-select-selection-item").innerText(), { timeout: 10_000 }).toBe("DBSCAN");
});

test("background PCA completion records its source module without stealing navigation", async ({ page }) => {
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Structure & Environments", "Descriptor Space");
  await page.getByRole("button", { name: "Run PCA" }).click();
  await selectAnalysisModule(page, "Representation Quality", "Feature Correlation");

  await expect(page.getByText("PCA running")).toBeVisible();
  await expect(page.getByText("PCA running")).toBeHidden({ timeout: 15_000 });
  await expect(page.getByRole("tab", { name: "Representation Quality", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Feature Correlation", exact: true })).toHaveAttribute("aria-selected", "true");

  await selectAnalysisModule(page, "Structure & Environments", "Descriptor Space");
  await expect(page.locator(".analysis-plot-frame .js-plotly-plot")).toBeVisible({ timeout: 30_000 });
});

test("slow history loads are discarded when a same-module parameter changes", async ({ page }) => {
  await page.addInitScript(() => {
    (window as unknown as { __PREVIEW_ANALYSIS_PREVIEW_DELAY__?: number }).__PREVIEW_ANALYSIS_PREVIEW_DELAY__ = 1200;
  });
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Structure & Environments", "Structural Clusters");

  const algorithm = page.locator(".analysis-controls .ant-select").first();
  await expect(page.getByRole("button", { name: "Load clusters analysis" })).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "Load clusters analysis" }).click();
  await expect(algorithm.locator(".ant-select-selection-item")).toHaveText("AGGLOMERATIVE");

  await algorithm.click();
  await page.getByText("KMEANS", { exact: true }).last().click();
  await expect(algorithm.locator(".ant-select-selection-item")).toHaveText("KMEANS");
  await expect(page.getByText("CLUSTER STRUCTURE", { exact: true })).toBeHidden({ timeout: 5_000 });
});

test("sampling keeps save-view and export actions available for selected frames", async ({ page }) => {
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Dataset Sampling", "Representative Sampling");
  await page.getByRole("button", { name: /Run Representative Sampling/i }).click();
  await expect(page.getByText("REPRESENTATIVE SAMPLING", { exact: true })).toBeVisible({ timeout: 30_000 });

  const resultRows = page.locator(".analysis-main .ant-table-tbody tr");
  await expect(resultRows.first()).toBeVisible({ timeout: 10_000 });
  await resultRows.first().click();
  await expect(page.getByText(/1 frames selected/)).toBeVisible();
  await page.getByRole("button", { name: "Save selection as view", exact: true }).click();
  const saveDialog = page.getByRole("dialog");
  await expect(saveDialog).toBeVisible();
  await saveDialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Dataset view saved", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Choose…", exact: true }).click();
  await expect(page.getByLabel("Export destination")).toHaveValue(/analysis_subset\.csv/);
  await page.getByRole("button", { name: "Export", exact: true }).click();
  await expect(page.getByText(/Export written to/)).toBeVisible({ timeout: 15_000 });
});

test("browser preview restores the analysis module it was left on", async ({ page }) => {
  // The writing half of analysis-view persistence had e2e coverage; the reading
  // half did not, because the mock answered every settings.get with a literal.
  // So a restore that never happened - or one that threw - was invisible to every
  // browser run (deep review pass 4, C-6).
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Representation Quality", "Feature Variance");
  await expect(page.getByRole("tab", { name: "Feature Variance", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect
    .poll(() => page.evaluate(() => window.localStorage.getItem("mockBackend.settings")))
    .toContain("workspace.analysisUi");

  await page.reload();
  await openAnalysis(page);
  await expect(page.getByRole("tab", { name: "Feature Variance", exact: true })).toHaveAttribute("aria-selected", "true");
});

test("history restores both runs a drift result was computed from", async ({ page }) => {
  // A stored cross-dataset result is restorable only if its row names both input
  // runs. The mock's rows named one whatever the submission was given, so the
  // restore refused with "the source descriptor runs are no longer available" and
  // every browser run that depended on that shape saw a degenerate row instead
  // (deep review pass 4, C-7).
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/preview.html");
  await openAnalysis(page);
  await selectAnalysisModule(page, "Coverage & Novelty", "Dataset Drift");
  await page.getByRole("button", { name: /Run Dataset Drift/i }).click();
  await expect(page.getByText("DATASET DRIFT", { exact: true })).toBeVisible({ timeout: 30_000 });

  // Move the pair out of place first, so "the row restored its inputs" is an
  // observation and not the leftover of the run above.
  await page.getByRole("button", { name: "Swap" }).click();
  await expect(page.locator(".analysis-cross-input-row").nth(1)).not.toContainText("Si Training Set");

  await page.getByRole("button", { name: "Load drift analysis" }).click();
  await expect(page.getByText("The source descriptor runs are no longer available")).toHaveCount(0);
  await expect(page.locator(".analysis-cross-input-row").nth(1)).toContainText("Si Training Set");
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

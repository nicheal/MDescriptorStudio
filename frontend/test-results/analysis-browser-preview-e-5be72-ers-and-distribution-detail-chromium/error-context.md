# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: analysis.spec.ts >> browser preview exposes feature variance filters and distribution detail
- Location: e2e\analysis.spec.ts:104:1

# Error details

```
Error: expect(locator).toHaveAttribute(expected) failed

Locator:  getByRole('tab', { name: 'Feature statistics' })
Expected: "true"
Received: "false"
Timeout:  5000ms

Call log:
  - Expect "toHaveAttribute" with timeout 5000ms
  - waiting for getByRole('tab', { name: 'Feature statistics' })
    14 × locator resolved to <div role="tab" tabindex="-1" aria-selected="false" class="ant-tabs-tab-btn" id="rc-tabs-3-tab-stats" aria-controls="rc-tabs-3-panel-stats">Feature statistics</div>
       - unexpected value "false"

```

```yaml
- tab "Feature statistics"
```

# Test source

```ts
  40  |   expect(Math.abs((guideBox?.y ?? 0) - (parameterBox?.y ?? 0))).toBeLessThan(8);
  41  |   await guideButton.click();
  42  | 
  43  |   const dialog = page.getByRole("dialog");
  44  |   await expect(dialog).toBeVisible();
  45  |   await expect(dialog.getByText("Theory", { exact: true })).toBeVisible();
  46  |   await expect(dialog.getByText("Applications", { exact: true })).toBeVisible();
  47  |   await expect(dialog).toContainText("Feature variance");
  48  | });
  49  | 
  50  | test("browser preview aligns descriptor names left and array shapes right in the run selector", async ({ page }) => {
  51  |   await page.setViewportSize({ width: 1920, height: 720 });
  52  |   await page.goto("/preview.html");
  53  |   await page.getByRole("button", { name: "Analysis" }).click();
  54  | 
  55  |   const runSelect = page.locator(".analysis-toolbar .ant-select").first();
  56  |   const selectedLabel = runSelect.locator(".ant-select-selection-item .analysis-run-label");
  57  |   await expect(selectedLabel).toBeVisible();
  58  |   await expect(selectedLabel.locator(".analysis-run-name")).toHaveText("DPA-2");
  59  |   await expect(selectedLabel.locator(".analysis-run-shape")).toHaveText("[12480, 256]");
  60  | 
  61  |   const selectedBox = await selectedLabel.boundingBox();
  62  |   const nameBox = await selectedLabel.locator(".analysis-run-name").boundingBox();
  63  |   const shapeBox = await selectedLabel.locator(".analysis-run-shape").boundingBox();
  64  |   expect(selectedBox).not.toBeNull();
  65  |   expect(nameBox).not.toBeNull();
  66  |   expect(shapeBox).not.toBeNull();
  67  |   expect((nameBox?.x ?? 0)).toBeLessThan(shapeBox?.x ?? 0);
  68  |   expect(Math.abs((shapeBox?.x ?? 0) + (shapeBox?.width ?? 0) - ((selectedBox?.x ?? 0) + (selectedBox?.width ?? 0)))).toBeLessThan(3);
  69  | 
  70  |   await runSelect.click();
  71  |   const optionLabel = page.locator(".ant-select-item-option .analysis-run-label").first();
  72  |   await expect(optionLabel).toBeVisible();
  73  |   await expect(optionLabel.locator(".analysis-run-name")).toHaveText("DPA-2");
  74  |   await expect(optionLabel.locator(".analysis-run-shape")).toHaveText("[12480, 256]");
  75  | });
  76  | 
  77  | test("browser preview reloads cached PCA and changes coordinates with preprocessing", async ({ page }) => {
  78  |   await page.goto("/preview.html");
  79  |   await page.getByRole("button", { name: "Analysis" }).click();
  80  |   await page.getByRole("button", { name: "Load pca analysis" }).click();
  81  |   const plot = page.locator(".analysis-plot-frame .js-plotly-plot");
  82  |   await expect(plot).toBeVisible({ timeout: 30_000 });
  83  | 
  84  |   const before = await plot.evaluate((node) => JSON.stringify((node as unknown as { data?: { x?: number[] }[] }).data?.[0]?.x?.slice(0, 6)));
  85  |   const preprocess = page.locator(".analysis-controls .ant-select").nth(2);
  86  |   await preprocess.click();
  87  |   await page.getByText("Standardized", { exact: true }).last().click();
  88  |   await expect.poll(async () => {
  89  |     const current = page.locator(".analysis-plot-frame .js-plotly-plot");
  90  |     if (await current.count() === 0) return null;
  91  |     return current.evaluate((node) => JSON.stringify((node as unknown as { data?: { x?: number[] }[] }).data?.[0]?.x?.slice(0, 6)));
  92  |   }, { timeout: 30_000 }).not.toBe(before);
  93  | });
  94  | 
  95  | test("browser preview renders an Overview chart after a module run", async ({ page }) => {
  96  |   await page.goto("/preview.html");
  97  |   await page.getByRole("button", { name: "Analysis" }).click();
  98  |   await expect(page.getByRole("button", { name: "Run feature variance" })).toBeVisible();
  99  |   await page.getByRole("button", { name: "Run feature variance" }).click();
  100 |   await expect(page.locator(".analysis-overview-chart-frame")).toBeVisible({ timeout: 30_000 });
  101 |   await expect(page.getByText("FEATURE VARIANCE", { exact: true })).toBeVisible();
  102 | });
  103 | 
  104 | test("browser preview exposes feature variance filters and distribution detail", async ({ page }) => {
  105 |   await page.goto("/preview.html");
  106 |   await page.getByRole("button", { name: "Analysis" }).click();
  107 |   await page.getByRole("button", { name: "Run feature variance" }).click();
  108 |   await expect(page.locator(".feature-variance-layout")).toBeVisible({ timeout: 30_000 });
  109 |   await expect(page.getByRole("combobox", { name: "Variance metric" })).toBeVisible();
  110 |   await expect(page.getByRole("combobox", { name: "Feature display filter" })).toBeVisible();
  111 |   await expect(page.locator(".feature-variance-k-control input")).toBeDisabled();
  112 |   await expect(page.getByText("35", { exact: true })).toBeVisible();
  113 |   await expect(page.locator("section.analysis-card:not(.analysis-visual-card) .ant-table")).toHaveCount(0);
  114 | 
  115 |   await page.getByRole("button", { name: "Constant 1" }).click();
  116 |   await expect(page.getByRole("button", { name: "Constant 1" })).toHaveAttribute("aria-pressed", "true");
  117 |   const featurePicker = page.getByRole("combobox", { name: "Select feature for detail" });
  118 |   await featurePicker.click();
  119 |   await page.locator(".ant-select-dropdown .ant-select-item-option").filter({ hasText: "Feature 0 ·" }).last().click();
  120 |   await expect(page.locator(".feature-variance-detail").getByText("Feature detail", { exact: true })).toBeVisible();
  121 |   await expect(page.getByText("Constant feature; KDE omitted.", { exact: true })).toBeVisible();
  122 |   await expect(page.getByLabel("Feature value histogram and KDE")).toBeVisible();
  123 |   await expect(page.getByLabel("Feature value box plot")).toBeVisible();
  124 |   await page.getByRole("tab", { name: "Feature statistics" }).click();
  125 |   await expect(page.locator(".feature-variance-stat-grid")).toBeVisible();
  126 |   await page.getByRole("tab", { name: "Variance distribution" }).click();
  127 |   await page.locator(".feature-variance-toolbar .ant-select").nth(2).click();
  128 |   await page.getByText("All features", { exact: true }).last().click();
  129 |   await page.locator(".feature-variance-toolbar .ant-select").nth(3).click();
  130 |   await page.getByText("Linear", { exact: true }).last().click();
  131 |   const varianceBars = page.locator(".feature-variance-overview-chart .trace.bars path");
  132 |   const varianceBarIndex = await varianceBars.evaluateAll((nodes) => nodes.findIndex((node) => {
  133 |     const rect = node.getBoundingClientRect();
  134 |     return rect.width > 2 && rect.height > 2 && rect.top > 80;
  135 |   }));
  136 |   expect(varianceBarIndex).toBeGreaterThanOrEqual(0);
  137 |   const varianceBarBox = await varianceBars.nth(varianceBarIndex).boundingBox();
  138 |   expect(varianceBarBox).not.toBeNull();
  139 |   if (varianceBarBox) await page.mouse.click(varianceBarBox.x + varianceBarBox.width / 2, varianceBarBox.y + varianceBarBox.height / 2);
> 140 |   await expect(page.getByRole("tab", { name: "Feature statistics" })).toHaveAttribute("aria-selected", "true");
      |                                                                       ^ Error: expect(locator).toHaveAttribute(expected) failed
  141 |   await expect(page.locator(".feature-variance-stat-grid")).toBeVisible();
  142 | });
  143 | 
  144 | test("browser preview places the sensitivity run pair after Module", async ({ page }) => {
  145 |   await page.goto("/preview.html");
  146 |   await page.getByRole("button", { name: "Analysis" }).click();
  147 | 
  148 |   const controls = page.locator(".analysis-controls");
  149 |   await controls.locator(":scope > .ant-space").first().locator(".ant-select").click();
  150 |   await page.getByText("Parameter sensitivity", { exact: true }).last().click();
  151 | 
  152 |   const spaces = controls.locator(":scope > .ant-space");
  153 |   await expect(spaces.nth(0)).toContainText("Module");
  154 |   await expect(spaces.nth(1)).toContainText("Reference");
  155 |   await expect(spaces.nth(1)).toContainText("Query");
  156 | });
  157 | 
  158 | test("browser preview renders the dedicated pairwise similarity heatmap", async ({ page }) => {
  159 |   await page.goto("/preview.html");
  160 |   await page.getByRole("button", { name: "Analysis" }).click();
  161 |   await page.getByRole("tab", { name: "Similarity" }).click();
  162 |   const controls = page.locator(".analysis-controls");
  163 |   await controls.getByText("Query neighbors", { exact: true }).click();
  164 |   await page.getByText("Pairwise matrix", { exact: true }).last().click();
  165 |   await page.getByRole("button", { name: "Run Similarity" }).click();
  166 |   await expect(page.getByText("PAIRWISE SIMILARITY MATRIX", { exact: true })).toBeVisible({ timeout: 30_000 });
  167 |   await expect(page.getByLabel("Similarity heatmap")).toBeVisible();
  168 | });
  169 | 
  170 | test("atom-level local selection stays highlighted when opened in Explore", async ({ page }) => {
  171 |   await page.goto("/preview.html");
  172 |   await page.getByRole("button", { name: "Analysis" }).click();
  173 |   await page.getByRole("tab", { name: "Local" }).click();
  174 |   await page.getByRole("button", { name: "Run Local" }).click();
  175 |   await expect(page.getByText("LOCAL ENVIRONMENT DIVERSITY", { exact: true })).toBeVisible({ timeout: 30_000 });
  176 |   await page.locator(".analysis-main .ant-table-tbody").nth(1).locator("tr").first().click();
  177 |   await expect(page.locator(".analysis-inspector").getByText("Row", { exact: true })).toBeVisible();
  178 |   await page.getByRole("button", { name: "Open in Explore" }).click();
  179 |   await expect(page.locator(".explore-atom-row-selected")).toHaveCount(1, { timeout: 30_000 });
  180 | });
  181 | 
  182 | test("browser preview renders structural perturbation response curves", async ({ page }) => {
  183 |   await page.goto("/preview.html");
  184 |   await page.getByRole("button", { name: "Analysis" }).click();
  185 |   const controls = page.locator(".analysis-controls");
  186 |   await controls.locator(":scope > .ant-space").first().locator(".ant-select").click();
  187 |   await page.getByText("Structural perturbation", { exact: true }).last().click();
  188 |   await page.getByRole("button", { name: "Run perturbation sensitivity" }).click();
  189 |   await expect(page.getByText("STRUCTURAL PERTURBATION SENSITIVITY", { exact: true })).toBeVisible({ timeout: 30_000 });
  190 |   await expect(page.getByLabel("Per-structure perturbation response heatmap")).toBeVisible({ timeout: 30_000 });
  191 | });
  192 | 
  193 | test("browser preview exposes the Mantel permutation visualization", async ({ page }) => {
  194 |   await page.goto("/preview.html");
  195 |   await page.getByRole("button", { name: "Analysis" }).click();
  196 |   await page.getByRole("tab", { name: "Compare" }).click();
  197 |   const controls = page.locator(".analysis-controls");
  198 |   const selects = controls.locator(".ant-select");
  199 |   await selects.nth(1).click();
  200 |   await page.getByText("ACE · run-ace", { exact: true }).last().click();
  201 |   await selects.nth(2).click();
  202 |   await page.getByText("Mantel permutation test", { exact: true }).last().click();
  203 |   await page.getByRole("button", { name: "Run Compare" }).click();
  204 |   await expect(page.getByText("MANTEL PERMUTATION TEST", { exact: true })).toBeVisible({ timeout: 30_000 });
  205 |   await expect(page.getByLabel("Mantel permutation null distribution")).toBeVisible({ timeout: 30_000 });
  206 | });
  207 | 
  208 | test("language switch in Settings applies immediately and persists across reload", async ({ page }) => {
  209 |   await page.goto("/preview.html");
  210 |   await expect(page.getByRole("button", { name: "Analysis" })).toBeVisible({ timeout: 30_000 });
  211 | 
  212 |   // open Settings and switch to Chinese (antd radio buttons hide their inputs)
  213 |   await page.getByRole("button", { name: "Settings" }).click();
  214 |   await page.getByText("简体中文", { exact: true }).click();
  215 |   await expect(page.getByRole("button", { name: "总览" })).toBeVisible();
  216 |   await expect(page.getByText("语言", { exact: true })).toBeVisible();
  217 | 
  218 |   // the choice persists (localStorage) — reload and verify Chinese is still active
  219 |   await page.reload();
  220 |   await expect(page.getByRole("button", { name: "总览" })).toBeVisible({ timeout: 30_000 });
  221 | 
  222 |   // switch back to English
  223 |   await page.getByRole("button", { name: "设置" }).click();
  224 |   await page.getByText("English", { exact: true }).click();
  225 |   await expect(page.getByRole("button", { name: "Overview" })).toBeVisible();
  226 | });
  227 | 
```
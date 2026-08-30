# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: analysis.spec.ts >> browser preview renders an Overview chart after a module run
- Location: e2e\analysis.spec.ts:11:1

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: expect(locator).toBeVisible() failed

Locator: locator('.analysis-overview-chart-frame')
Expected: visible
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 30000ms
  - waiting for locator('.analysis-overview-chart-frame')
  - Protocol error (Runtime.callFunctionOn): Internal server error, session closed.

```

```yaml
- banner:
  - text: MDescriptor Studio
  - button "Jobs"
  - button "Settings"
  - button "Minimize"
  - button "Maximize"
  - button "Close"
- strong: DATASETS
- button
- img "search"
- textbox "Search datasets..."
- button "Add Dataset"
- text: GaAs Training Set
- button "more":
  - img "more"
- text: Deepmd · 12,480 structures Ga · As · PBC XYZ Si Training Set
- button "more":
  - img "more"
- text: Extxyz · 6,320 structures Si · PBC XYZ MoS2 AIMD
- button "more":
  - img "more"
- text: Extxyz · 2,000 structures Mo · S · PBC XYZ Al2O3 Datasetchanged
- button "more":
  - img "more"
- text: Deepmd · 8,500 structures Al · O · PBC XYZ
- strong: Dataset Storage
- text: 324.2 GB · 4 datasets GaAs Training Set Deepmd 12,480 structures Ga As PBC XYZ Energy
- img "check-circle"
- text: Force
- img "check-circle"
- text: Virial
- img "check-circle"
- code: D:\Datasets\GaAs_Training
- button "more":
  - img "more"
- button "Overview"
- button "Explore"
- button "Descriptors"
- button "Analysis"
- strong: Run
- combobox "Analysis descriptor run"
- text: DPA-2 · [12480, 256] COMPLETED
- button "Refresh"
- strong: RUN
- text: 2 results
- table:
  - rowgroup:
    - row "Descriptor Scope Shape Status Created 操作":
      - columnheader "Descriptor"
      - columnheader "Scope"
      - columnheader "Shape"
      - columnheader "Status"
      - columnheader "Created"
      - columnheader "操作"
  - rowgroup:
    - row "DPA-2 dataset [12480, 256] COMPLETED 8/30/2026, 8:34:26 PM Delete DPA-2 result":
      - cell "DPA-2"
      - cell "dataset"
      - cell "[12480, 256]":
        - code: "[12480, 256]"
      - cell "COMPLETED"
      - cell "8/30/2026, 8:34:26 PM"
      - cell "Delete DPA-2 result":
        - button "Delete DPA-2 result"
    - row "SOAP dataset [6320, 432] COMPLETED 8/30/2026, 9:32:26 PM Delete SOAP result":
      - cell "SOAP"
      - cell "dataset"
      - cell "[6320, 432]":
        - code: "[6320, 432]"
      - cell "COMPLETED"
      - cell "8/30/2026, 9:32:26 PM"
      - cell "Delete SOAP result":
        - button "Delete SOAP result"
- tablist:
  - tab "Overview" [selected]
  - tab "Projection"
  - tab "Similarity"
  - tab "Clusters"
  - tab "Outliers"
  - tab "Sampling"
  - tab "Coverage"
  - tab "Compare"
- tabpanel "Overview"
- main:
  - text: Module
  - combobox
  - text: Feature variance All results stay on the backend as bounded artifacts.
  - button "Run feature variance"
  - img "No data"
  - text: Run an analysis module to see its bounded result preview.
- complementary:
  - strong: INSPECTOR
  - text: Click a point, or use box/lasso selection, to inspect a structure.
  - strong: STRUCTURE PREVIEW
  - text: Select a sample to preview it.
  - strong: ANALYSIS HISTORY
  - text: "1"
  - strong: pca
  - text: 8/30/2026, 8:36:26 PM COMPLETED
  - button
- text: Ready MDescriptor 0.3.2 CPU 16 threads
```

# Test source

```ts
  1  | import { expect, test } from "@playwright/test";
  2  | 
  3  | test("browser preview exposes the Analysis workflow and run selector", async ({ page }) => {
  4  |   await page.goto("/preview.html");
  5  |   await expect(page.getByRole("button", { name: "Analysis" })).toBeVisible({ timeout: 30_000 });
  6  |   await page.getByRole("button", { name: "Analysis" }).click();
  7  |   await expect(page.getByRole("combobox", { name: "Analysis descriptor run" })).toBeVisible();
  8  |   await expect(page.getByText(/analysis history/i)).toBeVisible();
  9  | });
  10 | 
  11 | test("browser preview renders an Overview chart after a module run", async ({ page }) => {
  12 |   await page.goto("/preview.html");
  13 |   await page.getByRole("button", { name: "Analysis" }).click();
  14 |   await expect(page.getByRole("button", { name: "Run feature variance" })).toBeVisible();
  15 |   await page.getByRole("button", { name: "Run feature variance" }).click();
> 16 |   await expect(page.locator(".analysis-overview-chart-frame")).toBeVisible({ timeout: 30_000 });
     |                                                                ^ Error: expect(locator).toBeVisible() failed
  17 |   await expect(page.getByText("FEATURE VARIANCE", { exact: true })).toBeVisible();
  18 | });
  19 | 
```
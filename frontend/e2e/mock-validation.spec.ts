// The mock has to refuse what the sidecar refuses.
//
// preview.tsx used to answer an unknown dataset, job, view, check or analysis id
// with data - an empty row list, a null stats, or a job fabricated as RUNNING
// that therefore never ended - so every error branch in the renderer (the toast,
// the retry, the "not found" placeholder) was unreachable from the one suite
// that drives it, and a spec that misspelled an id watched a spinner instead of
// failing. These assertions go through the same reply builder the frame dispatch
// uses, so they describe what a page would actually receive.
import { expect, test } from "@playwright/test";

type Reply = {
  result?: unknown;
  error?: { code?: string; message?: string; error_id?: string };
};

const respond = (page: import("@playwright/test").Page, method: string, params: Record<string, unknown>) =>
  page.evaluate(
    ([name, sent]) =>
      (window as unknown as { __mdsMock?: { respond: (m: string, p: Record<string, unknown>) => Reply } }).__mdsMock?.respond(name, sent),
    [method, params] as [string, Record<string, unknown>],
  );

test.beforeEach(async ({ page }) => {
  await page.goto("/preview.html");
  await page.waitForFunction(() => Boolean(window.__mdsMock));
});

test("the preview mock refuses a bad request the way the sidecar does", async ({ page }) => {
  const cases: [string, Record<string, unknown>, string][] = [
    ["dataset.statistics", { id: "ds-nope" }, "DATASET_NOT_FOUND"],
    ["dataset.findings", { id: "ds-gaas", check: "not_a_check" }, "INVALID_PARAMS"],
    ["dataset.findings", { id: "ds-nope", check: "missing_values" }, "DATASET_NOT_FOUND"],
    ["dataset.frame", { id: "ds-nope", index: 0 }, "DATASET_NOT_FOUND"],
    ["dataset.view.create", { dataset_id: "ds-nope", name: "V", indices: [0] }, "DATASET_NOT_FOUND"],
    ["dataset.view.rename", { id: "view-nope", name: "x" }, "DATASET_NOT_FOUND"],
    ["dataset.view.remove", { id: "view-nope" }, "DATASET_NOT_FOUND"],
    ["job.get", { id: "job-never-submitted" }, "JOB_NOT_FOUND"],
    ["descriptor.describe", { name: "no-such-descriptor" }, "INVALID_PARAMS"],
    ["settings.get", { key: "workspace.notASetting" }, "INVALID_PARAMS"],
    ["settings.set", { key: "ui.notASetting", value: "1" }, "INVALID_PARAMS"],
    ["analysis.preview", { analysis_id: "ana-nope" }, "ANALYSIS_NOT_FOUND"],
    ["analysis.chunk", { analysis_id: "ana-nope", array: "coords" }, "ANALYSIS_NOT_FOUND"],
  ];

  for (const [method, params, code] of cases) {
    const reply = await respond(page, method, params);
    expect(reply, `${method} answered nothing`).toBeTruthy();
    expect(reply?.error?.code, `${method} ${JSON.stringify(params)} should refuse as ${code}, got ${JSON.stringify(reply)}`).toBe(code);
    expect(reply?.result, `${method} must not answer with data as well`).toBeUndefined();
    // The code is what the renderer branches on; error_id is what the user can
    // quote, and the sidecar sends it on every error frame.
    expect(reply?.error?.error_id).toBeTruthy();
  }
});

test("an unknown analysis array is refused, and a real one is not", async ({ page }) => {
  // The mock publishes an analysis' arrays while it builds that analysis'
  // preview, like a result page does - so drive the same order here. (Keying
  // them per analysis id, so one analysis cannot be served another's arrays,
  // is still open.)
  const call = (method: string, params: Record<string, unknown>) =>
    page.evaluate(([name, sent]) => (window as unknown as { __mdsMock?: { call: (m: string, p: Record<string, unknown>) => unknown } }).__mdsMock?.call(name, sent), [method, params] as [string, Record<string, unknown>]);

  await call("analysis.pairwise", { run_id: "run-dpa2" });
  await call("analysis.preview", { analysis_id: "ana-mock-pairwise", limit: 20 });
  const missing = await respond(page, "analysis.chunk", { analysis_id: "ana-mock-pairwise", array: "not_published" });
  expect(missing?.error?.code).toBe("ANALYSIS_INPUT_INVALID");
  const known = await respond(page, "analysis.chunk", { analysis_id: "ana-mock-pairwise", array: "similarity_matrix" });
  expect(known?.error, JSON.stringify(known?.error)).toBeUndefined();
  expect(Array.isArray(known?.result)).toBeFalsy();
  expect((known?.result as { data?: unknown[] }).data).toBeDefined();
});

test("a job the mock finished answers as finished rather than running forever", async ({ page }) => {
  const before = await respond(page, "job.get", { id: "job-pca-live" });
  expect(before?.error?.code).toBe("JOB_NOT_FOUND");

  await page.evaluate(() => (window as unknown as { __mdsMock?: { call: (m: string, p: Record<string, unknown>) => unknown } }).__mdsMock?.call("analysis.pca", { run_id: "run-dpa2" }));
  const inFlight = await respond(page, "job.get", { id: "job-pca-live" });
  expect((inFlight?.result as { status?: string }).status).toBe("RUNNING");

  // The mock emits job.finished 800 ms after the submit; polling job.get is how
  // a watcher that missed that event settles, so the row has to reflect it.
  await page.waitForTimeout(1200);
  const settled = await respond(page, "job.get", { id: "job-pca-live" });
  expect((settled?.result as { status?: string }).status).toBe("COMPLETED");
  expect((settled?.result as { result?: Record<string, unknown> }).result?.analysis_id).toBe("ana-mock-pca");
});

test("an analysis submission is checked before the mock answers it", async ({ page }) => {
  // The handlers return canned results whatever they are sent, so every one of
  // these is a mistake the renderer could make and the suite would never see:
  // a dropped run id, a renamed field, a mode only some algorithms accept, a
  // QUEUED run, two descriptor spaces compared as if they were one.
  const cases: [string, Record<string, unknown>, string][] = [
    ["analysis.pca", {}, "INVALID_PARAMS"],
    ["analysis.pca", { run_id: "run-nope" }, "INVALID_PARAMS"],
    ["analysis.pca", { run_id: "run-soap" }, "RESULT_INCOMPATIBLE"],
    ["analysis.pca", { run_id: "run-dpa2", mode: "structures" }, "ANALYSIS_INPUT_INVALID"],
    ["analysis.pca", { run_id: "run-dpa2", preprocess: "normalize" }, "ANALYSIS_INPUT_INVALID"],
    ["analysis.pca", { run_id: "run-dpa2", n_samples: 0 }, "ANALYSIS_INPUT_INVALID"],
    ["analysis.pca", { run_id: "run-dpa2", view_id: "view-nope" }, "DATASET_NOT_FOUND"],
    ["analysis.pca", { run_id: "run-dpa2-si", view_id: "view-gaas-train" }, "ANALYSIS_INPUT_INVALID"],
    ["analysis.coverage", { reference_run_id: "run-dpa2" }, "INVALID_PARAMS"],
    ["analysis.coverage", { reference_run_id: "run-dpa2", query_run_id: "run-ace" }, "ANALYSIS_INPUT_INVALID"],
    ["analysis.coverage", { reference_run_id: "run-dpa2", query_run_id: "run-dpa2-si", view_id: "view-gaas-train" }, "ANALYSIS_INPUT_INVALID"],
    ["analysis.sensitivity", { run_ids: ["run-dpa2", "run-ace"] }, "ANALYSIS_INPUT_INVALID"],
    ["analysis.export", { run_id: "run-dpa2", output_path: "" }, "ANALYSIS_INPUT_INVALID"],
    ["analysis.export", { run_id: "run-dpa2", output_path: "C:\\preview\\a.csv", format: "npy" }, "ANALYSIS_INPUT_INVALID"],
    ["analysis.export", { run_id: "run-dpa2", output_path: "C:\\preview\\a.csv", indices: [-1] }, "ANALYSIS_INPUT_INVALID"],
  ];
  for (const [method, params, code] of cases) {
    const reply = await respond(page, method, params);
    expect(reply?.error?.code, `${method} ${JSON.stringify(params)} should refuse as ${code}, got ${JSON.stringify(reply)}`).toBe(code);
  }

  // Positive controls: what the app actually sends must still be answered.
  const valid: [string, Record<string, unknown>][] = [
    ["analysis.pca", { run_id: "run-dpa2", mode: "structure", preprocess: "center", seed: 42 }],
    ["analysis.coverage", { reference_run_id: "run-dpa2", query_run_id: "run-dpa2-si", mode: "structure", reference_view_id: "view-gaas-train" }],
    ["analysis.export", { run_id: "run-dpa2", indices: [0, 2], mode: "structure", format: "csv", output_path: "C:\\preview\\subset.csv" }],
  ];
  for (const [method, params] of valid) {
    const reply = await respond(page, method, params);
    expect(reply?.error, `${method} refused a valid submission: ${JSON.stringify(reply?.error)}`).toBeUndefined();
    expect(reply?.result).toBeDefined();
  }
});

test("a valid request still answers with a result, not an error", async ({ page }) => {
  for (const [method, params] of [
    ["dataset.statistics", { id: "ds-gaas" }],
    ["dataset.frame", { id: "ds-gaas", index: 3 }],
    ["dataset.findings", { id: "ds-gaas", check: "missing_values" }],
    ["job.get", { id: "job-soap" }],
    ["descriptor.describe", { name: "soap" }],
    ["settings.get", { key: "ui.language" }],
  ] as [string, Record<string, unknown>][]) {
    const reply = await respond(page, method, params);
    expect(reply?.error, `${method} refused a valid request: ${JSON.stringify(reply?.error)}`).toBeUndefined();
    expect(reply?.result).toBeDefined();
  }
});

test("acquisition publishes the pick trace the panel reads", async ({ page }) => {
  // ARTIFACT_ARRAYS["acquisition"] names pick_scores, so a result page loading
  // an acquisition asks the backend for exactly that array. The mock publishes
  // an analysis' arrays while it builds that analysis' preview, like the panel
  // does, so drive the same order.
  const call = (method: string, params: Record<string, unknown>) =>
    page.evaluate(
      ([name, sent]) =>
        (window as unknown as { __mdsMock?: { call: (m: string, p: Record<string, unknown>) => unknown } }).__mdsMock?.call(name, sent),
      [method, params] as [string, Record<string, unknown>],
    );

  await call("analysis.acquisition", { reference_run_id: "run-dpa2", query_run_id: "run-dpa2-si", n_samples: 8 });
  await call("analysis.preview", { analysis_id: "ana-mock-acquisition", limit: 20 });

  const chunk = await respond(page, "analysis.chunk", { analysis_id: "ana-mock-acquisition", array: "pick_scores" });
  expect(chunk?.error, JSON.stringify(chunk?.error)).toBeUndefined();
  const data = (chunk?.result as { data?: unknown[] }).data;
  expect(Array.isArray(data)).toBeTruthy();
  expect(data?.length).toBeGreaterThan(0);
});

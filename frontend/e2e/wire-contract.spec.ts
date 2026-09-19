// The mock backend has to answer like the real one.
//
// Every other spec here drives the UI against preview.tsx, so a response that
// changes shape on the sidecar is invisible to them all - the UI would be coded
// against keys nothing sends. tests/data/backend-response-keys.json is captured
// from the real backend by tests/test_backend_response_contract.py; this spec
// checks the mock's answers against the same record.
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

type Contract = Record<string, { params: Record<string, unknown>; kind: string; keys?: string[] }>;

const contract = JSON.parse(
  readFileSync(new URL("../../tests/data/backend-response-keys.json", import.meta.url), "utf8"),
) as Contract;

declare global {
  interface Window {
    __mdsMock?: { call: (method: string, params?: Record<string, unknown>) => unknown };
  }
}

test("the preview mock answers with the real backend's envelope", async ({ page }) => {
  await page.goto("/preview.html");
  await page.waitForFunction(() => Boolean(window.__mdsMock));

  // The golden records "@dataset" rather than a generated id so both sides can
  // pick their own; the mock seeds exactly one dataset.
  const datasetId = await page.evaluate(() => {
    const list = window.__mdsMock?.call("dataset.list") as { id: string }[] | undefined;
    return list?.[0]?.id;
  });
  expect(datasetId).toBeTruthy();

  const drifted: string[] = [];
  for (const [method, expected] of Object.entries(contract)) {
    const params = { ...expected.params };
    if (params.id === "@dataset") params.id = datasetId;
    const observed = await page.evaluate(
      ([name, sent]) => {
        const value = window.__mdsMock?.call(name, sent);
        if (Array.isArray(value)) return { kind: "array", keys: [] };
        if (value && typeof value === "object") return { kind: "object", keys: Object.keys(value).sort() };
        return { kind: typeof value, keys: [] };
      },
      [method, params] as [string, Record<string, unknown>],
    );
    if (observed.kind !== expected.kind || (expected.keys ?? []).join() !== observed.keys.join()) {
      drifted.push(
        `${method}: mock ${observed.kind} [${observed.keys.join(", ")}] != backend ${expected.kind} [${(expected.keys ?? []).join(", ")}]`,
      );
    }
  }
  expect(drifted, `preview.tsx drifted from the real response shapes:\n${drifted.join("\n")}`).toEqual([]);
});

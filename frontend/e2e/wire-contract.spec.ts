// The mock backend has to answer like the real one.
//
// Every other spec here drives the UI against preview.tsx, so a response that
// changes shape on the sidecar is invisible to them all - the UI would be coded
// against keys nothing sends. tests/data/backend-response-keys.json is captured
// from the real backend by tests/test_backend_response_contract.py; this spec
// checks the mock's answers against the same record.
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

type Contract = Record<string, { params: Record<string, unknown>; kind: string; keys?: string[] | null }>;

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
        if (Array.isArray(value)) {
          // Row keys, not just "it is an array": the UI reads the fields inside
          // these rows, and an array answer used to satisfy this gate vacuously.
          const first = value.find((row) => row && typeof row === "object" && !Array.isArray(row)) as Record<string, unknown> | undefined;
          return { kind: "array", keys: first ? Object.keys(first).sort() : null };
        }
        if (value && typeof value === "object") return { kind: "object", keys: Object.keys(value).sort() };
        return { kind: typeof value, keys: [] };
      },
      [method, params] as [string, Record<string, unknown>],
    );
    // null keys means an empty reply on one side: nothing was observed, so
    // nothing may be claimed - the kind is still compared.
    const comparable = expected.keys !== null && observed.keys !== null;
    const show = (value: { kind: string; keys?: string[] | null }) => `${value.kind} [${value.keys?.join(", ") ?? "no rows returned"}]`;
    if (observed.kind !== expected.kind || (comparable && (expected.keys ?? []).join() !== observed.keys.join())) {
      drifted.push(`${method}: mock ${show(observed)} != backend ${show(expected)}`);
    }
  }
  expect(drifted, `preview.tsx drifted from the real response shapes:\n${drifted.join("\n")}`).toEqual([]);
});
test("the preview mock computes and removes like the sidecar does", async ({ page }) => {
  // Both RPCs used to be missing, so `NO_HANDLER` - a code the sidecar never
  // sends - was what every descriptor-compute and dataset-delete attempt got in
  // a browser run: no descriptor run could be created, and no refusal branch of
  // either flow was reachable (deep review pass 4, Q5).
  await page.goto("/preview.html");
  await page.waitForFunction(() => Boolean(window.__mdsMock));
  const datasetId = await page.evaluate(() => (window.__mdsMock?.call("dataset.list") as { id: string }[])[0].id);

  const cached = await page.evaluate(
    ([id]) => window.__mdsMock?.call("descriptor.submit", { dataset_id: id, descriptor_name: "DPA-2", scope: "dataset" }) as
      { job_id: string | null; cache: { existing_run_id: string } | null },
    [datasetId] as [string],
  );
  expect(cached.job_id).toBeNull();
  expect(cached.cache?.existing_run_id).toBeTruthy();

  const before = await page.evaluate(() => (window.__mdsMock?.call("result.list") as { id: string }[]).map((run) => run.id));
  const submitted = await page.evaluate(
    ([id]) => window.__mdsMock?.call("descriptor.submit", { dataset_id: id, descriptor_name: "DPA-2", scope: "dataset", force: true }) as
      { job_id: string | null },
    [datasetId] as [string],
  );
  expect(submitted.job_id).toBeTruthy();
  // The job id the mock hands out is its own, so the run is found as "the row
  // that was not there before" rather than by guessing an id from it.
  await expect.poll(async () => page.evaluate(
    ([prior]) => (window.__mdsMock?.call("result.list") as { id: string; status: string }[])
      .find((run) => !prior.includes(run.id))?.status ?? null,
    [before] as [string[]],
  ), "the finished compute never appeared as a run").toBe("COMPLETED");

  const refusal = await page.evaluate(() => {
    try {
      window.__mdsMock?.call("descriptor.submit", { dataset_id: "ds-none", descriptor_name: "DPA-2" });
      return "answered without refusing";
    } catch (error) {
      return (error as Error).message;
    }
  });
  expect(refusal).toContain("does not exist");

  const removed = await page.evaluate(
    ([id]) => [window.__mdsMock?.call("dataset.remove", { id }) as { ok: boolean }, (window.__mdsMock?.call("dataset.list") as { id: string }[]).map((row) => row.id)],
    [datasetId] as [string],
  );
  expect(removed[0]).toEqual({ ok: true });
  expect(removed[1]).not.toContain(datasetId);
  expect(before.length).toBeGreaterThan(0);
});

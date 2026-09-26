// buildSubmitPayload is the single serialization fact between the UI and the
// backend's parse_request. These tests pin the per-optimizer param mapping:
// a field missing (or misspelled) here silently reverts the run to defaults.
import { describe, expect, it } from "vitest";
import { buildSubmitPayload, parseAnchorFrames, validateConfig } from "./submission";
import { defaultGenerationConfig, defaultOptimizerConfig } from "./generationStore";
import type { GenerationConfig } from "./types";

const config = (optimizer: GenerationConfig["optimizer"]): GenerationConfig => ({
  ...defaultGenerationConfig(),
  source: { datasetId: "ds_x", descriptorRunId: "run_x", seedViewId: null },
  optimizer,
});

describe("buildSubmitPayload optimizer params", () => {
  it("maps the random knobs verbatim", () => {
    const payload = buildSubmitPayload(
      config({
        type: "random",
        childrenPerSeed: 4,
        batchAccept: 3,
        nSeeds: 32,
        reuseAcceptedSeeds: true,
      }),
    );
    expect(payload.optimizer).toBe("random");
    expect(payload.optimizer_params).toEqual({
      children_per_seed: 4,
      batch_accept: 3,
      n_seeds: 32,
      reuse_accepted_seeds: true,
    });
  });

  it("maps the genetic knobs and never leaks random-only keys", () => {
    const payload = buildSubmitPayload(
      config({
        type: "genetic",
        childrenPerSeed: 6,
        batchAccept: 4,
        nSeeds: 16,
        parentFraction: 0.5,
        immigrantFraction: 0.2,
      }),
    );
    expect(payload.optimizer).toBe("genetic");
    expect(payload.optimizer_params).toEqual({
      children_per_seed: 6,
      batch_accept: 4,
      n_seeds: 16,
      parent_fraction: 0.5,
      immigrant_fraction: 0.2,
    });
  });

  it("genetic defaults serialize to the backend catalog defaults", () => {
    const payload = buildSubmitPayload(config(defaultOptimizerConfig("genetic")));
    expect(payload.optimizer_params).toMatchObject({ parent_fraction: 0.7, immigrant_fraction: 0.15 });
  });
});

describe("buildSubmitPayload stopping defaults", () => {
  it("submits the configured generation and no-improvement limits", () => {
    const payload = buildSubmitPayload(config(defaultOptimizerConfig("random")));
    expect(payload.budget).toMatchObject({
      max_evaluations: 10_000,
      max_accepted: 500,
      max_generations: 200,
      no_improvement_rounds: 10,
    });
  });
});

describe("validateConfig optimizer checks", () => {
  it("keeps the reuse-seed rule random-only", () => {
    const genetic = config({
      type: "genetic",
      childrenPerSeed: 8,
      batchAccept: 8,
      nSeeds: 64,
      parentFraction: 0.7,
      immigrantFraction: 0.15,
    });
    genetic.searchSpace.atomicDisplacement = false;
    // Genetic has no reuse flag: displacement being off is not its problem.
    expect(validateConfig(genetic).ok).toBe(true);
    const random = config({ type: "random", childrenPerSeed: 8, batchAccept: 8, nSeeds: 64, reuseAcceptedSeeds: true });
    random.searchSpace.atomicDisplacement = false;
    expect(validateConfig(random)).toMatchObject({ ok: false });
  });

  it("accepts a target region with catalog optimizers while keeping seed reuse random-only", () => {
    const genetic = config(defaultOptimizerConfig("genetic"));
    genetic.searchTarget.anchorFrames = [0, 12];
    expect(validateConfig(genetic).ok).toBe(true);
    const random = config({ type: "random", childrenPerSeed: 8, batchAccept: 8, nSeeds: 64, reuseAcceptedSeeds: true });
    random.searchTarget.anchorFrames = [0];
    expect(validateConfig(random)).toMatchObject({ ok: false, reason: "Accepted-seed feedback is unavailable with a target region" });
  });
});

describe("parseAnchorFrames", () => {
  it("accepts strict zero-based frame indices", () => {
    expect(parseAnchorFrames("12, 345 6789", 7000)).toEqual({ ok: true, frames: [12, 345, 6789] });
  });

  it.each(["12abc", "2.5", "1,,2", "3, 3", "-1", "1e2"])("rejects malformed or repeated input %s", (input) => {
    expect(parseAnchorFrames(input, 100).ok).toBe(false);
  });

  it("checks source dataset bounds and the backend's 16-anchor limit", () => {
    expect(parseAnchorFrames("5", 5)).toMatchObject({ ok: false });
    expect(parseAnchorFrames(Array.from({ length: 17 }, (_, index) => String(index)).join(","))).toMatchObject({ ok: false });
  });
});

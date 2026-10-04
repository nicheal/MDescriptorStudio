// buildSubmitPayload is the single serialization fact between the UI and the
// backend's parse_request. These tests pin the per-optimizer param mapping:
// a field missing (or misspelled) here silently reverts the run to defaults.
import { describe, expect, it } from "vitest";
import { buildSubmitPayload, estimateRunResources, parseAnchorFrames, parseAnchorSpecies, validateConfig, validateConfigFields } from "./submission";
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

describe("buildSubmitPayload selection strategy", () => {
  it("omits the field for the fps baseline so older payloads stay byte-identical", () => {
    const payload = buildSubmitPayload(config(defaultOptimizerConfig("random")));
    expect("selection_strategy" in payload).toBe(false);
  });

  it("maps the local strategy into the payload", () => {
    const payload = buildSubmitPayload({
      ...config(defaultOptimizerConfig("random")),
      selectionStrategy: "local_incremental_maximin_v1",
    });
    expect(payload.selection_strategy).toBe("local_incremental_maximin_v1");
  });
});

describe("buildSubmitPayload local-environment anchors", () => {
  const withAnchors = (searchTarget: Partial<GenerationConfig["searchTarget"]> = {}) => ({
    ...config(defaultOptimizerConfig("random")),
    searchTarget: {
      ...defaultGenerationConfig().searchTarget,
      anchorFrames: [12, 345],
      ...searchTarget,
    },
  });

  it("omits the atomic-space fields for the default structure mode", () => {
    const payload = buildSubmitPayload(withAnchors());
    expect("target_mode" in payload).toBe(false);
    expect("anchor_species" in payload).toBe(false);
  });

  it("maps the local target mode and species filter", () => {
    const payload = buildSubmitPayload(
      withAnchors({ targetMode: "local_environment", anchorSpecies: "C, O" }),
    );
    expect(payload.target_mode).toBe("local_environment");
    expect(payload.anchor_species).toEqual(["C", "O"]);
  });

  it("omits empty species (all atoms of the anchor frames)", () => {
    const payload = buildSubmitPayload(withAnchors({ targetMode: "local_environment", anchorSpecies: "" }));
    expect("anchor_species" in payload).toBe(false);
  });

  it("rejects invalid species symbols", () => {
    expect(parseAnchorSpecies("carbon")).toBeNull();
    expect(parseAnchorSpecies("c")).toBeNull();
    expect(parseAnchorSpecies("C, O")).toEqual(["C", "O"]);
    expect(parseAnchorSpecies("")).toEqual([]);
  });

  it("flags a local target without anchors; genetic and pso are now supported (gen-5 E3)", () => {
    const issues = validateConfigFields({
      ...config(defaultOptimizerConfig("random")),
      searchTarget: { ...defaultGenerationConfig().searchTarget, targetMode: "local_environment", anchorSpecies: "C" },
    });
    expect(issues.some((i) => i.field === "searchTarget.anchorFrames")).toBe(true);
    for (const optimizer of ["genetic", "pso"] as const) {
      const genetic = validateConfigFields({
        ...config(defaultOptimizerConfig(optimizer)),
        searchTarget: {
          ...defaultGenerationConfig().searchTarget,
          anchorFrames: [1],
          targetMode: "local_environment",
          anchorSpecies: "C",
        },
      });
      // The optimizer gate is gone — the payload validation no longer
      // rejects non-random optimizers for local targeting.
      expect(genetic.some((i) => i.field === "searchTarget.targetMode")).toBe(false);
      expect(genetic.some((i) => i.field === "searchTarget.anchorSpecies")).toBe(false);
    }
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

describe("estimateRunResources", () => {
  it("derives rounds from the round size and caps by max generations", () => {
    const base = config(defaultOptimizerConfig("random")); // n_seeds 64 × children 8 = 512/round
    base.budget = { ...base.budget, maxEvaluations: 10_000, maxGenerations: 200 };
    const estimate = estimateRunResources(base);
    expect(estimate.evaluations).toBe(10_000);
    expect(estimate.rounds).toBe(20);
    expect(estimate.roundsCappedByGenerations).toBe(false);
    expect(estimate.wallSeconds).toBeGreaterThan(0);

    const capped = { ...base, budget: { ...base.budget, maxGenerations: 5 } };
    expect(estimateRunResources(capped).rounds).toBe(5);
    expect(estimateRunResources(capped).roundsCappedByGenerations).toBe(true);
  });

  it("reports the active scientific metric per objective", () => {
    const base = config(defaultOptimizerConfig("random")); // default objective: local_environment_novelty with threshold
    expect(estimateRunResources(base).activeMetric).toBe("unique_novel_environments");
    const structure = {
      ...base,
      objective: { ...base.objective, type: "novelty" as const, aggregation: "mean" as const },
    };
    expect(estimateRunResources(structure).activeMetric).toBe("structure_novelty");
  });
});
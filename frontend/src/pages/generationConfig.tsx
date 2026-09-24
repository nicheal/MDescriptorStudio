// The six-section expansion workflow form: SOURCE → SEARCH OBJECTIVE →
// SEARCH SPACE → PHYSICAL CONSTRAINTS → OPTIMIZER → COMPUTE BUDGET.
import { Button, Card, Input, InputNumber, Select, Slider, Switch } from "antd";
import { Play16Regular } from "@fluentui/react-icons";
import { useGenerationStore } from "../features/generation/generationStore";
import { GENERATION_OBJECTIVE_LABELS } from "../features/generation/labels";
import type { GenerationCatalog, GenerationObjectiveType } from "../features/generation/types";
import type { DatasetView, RunRow } from "../types/protocol";
import { useT } from "../i18n";

const sectionTitle = (index: number, label: string) => (
  <span style={{ fontSize: 15, fontWeight: 600 }}>
    <span style={{ color: "#0F6CBD", marginRight: 8 }}>{String(index).padStart(2, "0")}</span>
    {label}
  </span>
);

const rowStyle: React.CSSProperties = { display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginTop: 8 };
const labelStyle: React.CSSProperties = {
  color: "#616161",
  fontSize: 13,
  flex: "0 0 auto",
  whiteSpace: "nowrap",
};
const sourceRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  flexWrap: "wrap",
  marginTop: 8,
};
const sourceFieldStyle: React.CSSProperties = { display: "flex", alignItems: "center", gap: 8 };
const sourceLabelStyle: React.CSSProperties = { color: "#616161", fontSize: 13, flex: "0 0 auto" };
export default function GenerationConfigPanel({
  activeDatasetName,
  runs,
  views,
  catalog,
  onRun,
  running,
}: {
  activeDatasetName: string | null;
  runs: RunRow[];
  views: DatasetView[];
  catalog: GenerationCatalog | null;
  onRun: () => void;
  running: boolean;
}) {
  const { t } = useT();
  const config = useGenerationStore((s) => s.config);
  const update = useGenerationStore((s) => s.updateConfig);
  const objectiveOptions = (catalog?.objectives ?? [
    { name: "novelty", params: {} },
    { name: "local_environment_novelty", params: {} },
    { name: "composite", params: {} },
    { name: "coverage", params: {} },
  ]).map((o) => {
    const type = o.name as GenerationObjectiveType;
    return { value: type, label: t(GENERATION_OBJECTIVE_LABELS[type] ?? o.name) };
  });
  const fullDatasetScope = "__full_dataset__";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Card title={sectionTitle(1, t("SOURCE"))} size="small">
        <div style={sourceRowStyle}>
          <div style={sourceFieldStyle}>
            <span style={sourceLabelStyle}>{t("Dataset")}</span>
            <span
              title={activeDatasetName ?? undefined}
              style={{
                width: 150,
                fontSize: 13,
                color: "#242424",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                flex: "0 0 auto",
              }}
            >
              {activeDatasetName ?? t("No datasets")}
            </span>
          </div>
          <div style={sourceFieldStyle}>
            <span style={sourceLabelStyle}>{t("Target descriptor")}</span>
            <Select
              style={{ width: 250, flex: "0 0 250px" }}
              value={config.source.descriptorRunId ?? undefined}
              options={runs.map((run) => ({
                value: run.id,
                label: (
                  <span className="analysis-run-label">
                    <span className="analysis-run-name">{run.descriptor_name}</span>
                    <span className="analysis-run-shape">{run.shape ?? t("unknown shape")}</span>
                  </span>
                ),
              }))}
              onChange={(descriptorRunId) =>
                update((c) => ({ ...c, source: { ...c.source, descriptorRunId } }))
              }
              placeholder={runs.length ? undefined : t("No completed descriptor runs for this dataset")}
            />
          </div>
          <div style={sourceFieldStyle}>
            <span style={sourceLabelStyle}>{t("Seed scope")}</span>
            <Select
              style={{ width: 220, flex: "0 0 220px" }}
              disabled={!activeDatasetName}
              value={config.source.seedViewId ?? fullDatasetScope}
              options={[
                { value: fullDatasetScope, label: t("Full dataset") },
                ...views.map((view) => ({
                  value: view.id,
                  label: `${view.name} · ${view.number_of_frames}`,
                  disabled: view.number_of_frames === 0,
                })),
              ]}
              onChange={(value) =>
                update((c) => ({
                  ...c,
                  source: { ...c.source, seedViewId: value === fullDatasetScope ? null : value },
                }))
              }
            />
          </div>
        </div>
      </Card>

      <Card title={sectionTitle(2, t("SEARCH OBJECTIVE"))} size="small">
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Objective")}</span>
          <Select
            style={{ width: 280 }}
            value={config.objective.type}
            options={objectiveOptions}
            onChange={(type) => update((c) => ({ ...c, objective: { ...c.objective, type } }))}
          />
        </div>
        {(config.objective.type === "local_environment_novelty" || config.objective.type === "composite") && (
          <>
            <div style={rowStyle}>
              <span style={labelStyle}>{t("Aggregation")}</span>
              <Select
                style={{ minWidth: 200 }}
                value={config.objective.aggregation}
                options={[
                  { value: "top_fraction_mean", label: t("Top 20% mean") },
                  { value: "mean", label: t("Mean") },
                  { value: "quantile", label: t("Quantile") },
                  { value: "max", label: t("Maximum (diagnostic)") },
                ]}
                onChange={(aggregation) => update((c) => ({ ...c, objective: { ...c.objective, aggregation } }))}
              />
              <span style={labelStyle}>{t("Novel threshold")}</span>
              <InputNumber
                min={0.01}
                max={10}
                step={0.05}
                value={config.objective.noveltyThreshold}
                onChange={(v) => update((c) => ({ ...c, objective: { ...c.objective, noveltyThreshold: v ?? 0.25 } }))}
              />
            </div>
            {config.objective.type === "composite" && (
              <div style={rowStyle}>
                <span style={labelStyle}>{t("Local environment weight")}</span>
                <Slider
                  style={{ width: 220 }}
                  min={0}
                  max={1}
                  step={0.05}
                  value={config.objective.localWeight}
                  onChange={(localWeight) =>
                    update((c) => ({ ...c, objective: { ...c.objective, localWeight, structureWeight: Math.round((1 - localWeight) * 100) / 100 } }))
                  }
                />
                <span style={{ fontSize: 13, color: "#616161" }}>
                  {t("Local {local} · Structure {structure}", {
                    local: config.objective.localWeight.toFixed(2),
                    structure: config.objective.structureWeight.toFixed(2),
                  })}
                </span>
              </div>
            )}
          </>
        )}
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Feature scaling")}</span>
          <Select
            style={{ minWidth: 160 }}
            value={config.objective.scaling}
            options={[
              { value: "robust", label: t("Robust") },
              { value: "standardized", label: t("Standardized") },
              { value: "raw", label: t("Raw") },
            ]}
            onChange={(scaling) => update((c) => ({ ...c, objective: { ...c.objective, scaling } }))}
          />
        </div>
      </Card>

      <Card title={sectionTitle(3, t("SEARCH SPACE"))} size="small">
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Atomic displacement")}</span>
          <Switch
            checked={config.searchSpace.atomicDisplacement}
            onChange={(v) =>
              update((c) => ({
                ...c,
                searchSpace: { ...c.searchSpace, atomicDisplacement: v },
                optimizer:
                  c.optimizer.type === "random" && !v
                    ? { ...c.optimizer, reuseAcceptedSeeds: false }
                    : c.optimizer,
              }))
            }
          />
          <span style={{ fontSize: 13, color: "#616161" }}>0–{config.searchSpace.maxDisplacement} Å</span>
          <Slider
            style={{ width: 180 }}
            min={0.01}
            max={0.5}
            step={0.01}
            value={config.searchSpace.maxDisplacement}
            onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, maxDisplacement: v } }))}
          />
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Isotropic strain")}</span>
          <Switch
            checked={config.searchSpace.isotropicStrain}
            onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, isotropicStrain: v } }))}
          />
          <span style={labelStyle}>{t("Anisotropic strain")}</span>
          <Switch
            checked={config.searchSpace.anisotropicStrain}
            onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, anisotropicStrain: v } }))}
          />
          <span style={{ fontSize: 13, color: "#616161" }}>±{(config.searchSpace.maxStrain * 100).toFixed(0)} %</span>
          <Slider
            style={{ width: 160 }}
            min={0.005}
            max={0.2}
            step={0.005}
            value={config.searchSpace.maxStrain}
            onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, maxStrain: v } }))}
          />
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Cell shear")}</span>
          <Switch
            checked={config.searchSpace.cellShear}
            onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, cellShear: v } }))}
          />
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Vacancy")}</span>
          <Switch
            checked={config.searchSpace.vacancy}
            onChange={(v) =>
              update((c) => ({
                ...c,
                searchSpace: { ...c.searchSpace, vacancy: v },
                constraints: v
                  ? { ...c.constraints, compositionLocked: false, atomCountLocked: false }
                  : c.constraints,
              }))
            }
          />
          <span style={labelStyle}>{t("Interstitial atom")}</span>
          <Switch
            checked={config.searchSpace.interstitialAtom}
            onChange={(v) =>
              update((c) => ({
                ...c,
                searchSpace: { ...c.searchSpace, interstitialAtom: v },
                constraints: v
                  ? { ...c.constraints, compositionLocked: false, atomCountLocked: false }
                  : c.constraints,
              }))
            }
          />
          <Input
            style={{ width: 150 }}
            aria-label={t("Interstitial element")}
            value={config.searchSpace.interstitialElement}
            placeholder={t("Auto from parent")}
            disabled={!config.searchSpace.interstitialAtom}
            onChange={(event) =>
              update((c) => ({ ...c, searchSpace: { ...c.searchSpace, interstitialElement: event.target.value } }))
            }
          />
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Substitution")}</span>
          <Switch
            checked={config.searchSpace.substitution}
            onChange={(v) =>
              update((c) => ({
                ...c,
                searchSpace: { ...c.searchSpace, substitution: v },
                constraints: v ? { ...c.constraints, compositionLocked: false } : c.constraints,
              }))
            }
          />
          <Input
            style={{ width: 150 }}
            aria-label={t("Substitution element")}
            value={config.searchSpace.substitutionElement}
            placeholder={t("Auto: existing species")}
            disabled={!config.searchSpace.substitution}
            onChange={(event) =>
              update((c) => ({ ...c, searchSpace: { ...c.searchSpace, substitutionElement: event.target.value } }))
            }
          />
          <span style={labelStyle}>{t("Antisite swap")}</span>
          <Switch
            checked={config.searchSpace.antisiteSwap}
            onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, antisiteSwap: v } }))}
          />
        </div>
        <div style={{ fontSize: 12, color: "#616161", marginTop: 4 }}>
          {t("Vacancy and interstitial change both composition and atom count; substitution changes composition. Antisite swap preserves both.")}
          {" "}
          {t("Leave element blank to use species already present; automatic substitution needs multiple species.")}
        </div>
      </Card>

      <Card title={sectionTitle(4, t("PHYSICAL CONSTRAINTS"))} size="small">
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Minimum distance")}</span>
          <Select
            style={{ minWidth: 200 }}
            value={config.constraints.minDistanceMode}
            options={[
              { value: "covalent", label: t("Covalent radius × factor") },
              { value: "absolute", label: t("Absolute (Å)") },
              { value: "none", label: t("None") },
            ]}
            onChange={(minDistanceMode) => update((c) => ({ ...c, constraints: { ...c.constraints, minDistanceMode } }))}
          />
          {config.constraints.minDistanceMode === "covalent" && (
            <>
              <span style={{ fontSize: 13, color: "#616161" }}>×</span>
              <InputNumber
                min={0.3}
                max={1.5}
                step={0.05}
                value={config.constraints.minDistanceFactor}
                onChange={(v) => update((c) => ({ ...c, constraints: { ...c.constraints, minDistanceFactor: v ?? 0.7 } }))}
              />
            </>
          )}
          {config.constraints.minDistanceMode === "absolute" && (
            <InputNumber
              min={0.1}
              max={10}
              step={0.1}
              addonAfter="Å"
              value={config.constraints.minDistanceAbsolute}
              onChange={(v) => update((c) => ({ ...c, constraints: { ...c.constraints, minDistanceAbsolute: v ?? 1.0 } }))}
            />
          )}
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Element-pair distance overrides")}</span>
          <Input
            style={{ width: 320 }}
            value={config.constraints.minDistancePairs}
            placeholder={t("e.g. C-C=1.5, C-H=1.0")}
            onChange={(event) => update((c) => ({ ...c, constraints: { ...c.constraints, minDistancePairs: event.target.value } }))}
          />
          <span style={{ fontSize: 12, color: "#616161" }}>{t("Listed pairs use these Å cutoffs; other pairs use the selected mode.")}</span>
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Maximum volume change")}</span>
          <span style={{ fontSize: 13, color: "#616161" }}>±{(config.constraints.maxVolumeChange * 100).toFixed(0)} %</span>
          <Slider
            style={{ width: 200 }}
            min={0.01}
            max={0.5}
            step={0.01}
            value={config.constraints.maxVolumeChange}
            onChange={(v) => update((c) => ({ ...c, constraints: { ...c.constraints, maxVolumeChange: v } }))}
          />
          <span style={labelStyle}>{t("Volume per atom range")}</span>
          <span style={{ fontSize: 12, color: "#616161" }}>{t("Fully periodic structures; blank means unbounded.")}</span>
          <InputNumber
            min={0.001}
            max={10000}
            step={0.1}
            precision={3}
            addonBefore={t("Minimum")}
            addonAfter="Å³/atom"
            value={config.constraints.minVolumePerAtom}
            onChange={(minVolumePerAtom) => update((c) => ({ ...c, constraints: { ...c.constraints, minVolumePerAtom } }))}
          />
          <InputNumber
            min={0.001}
            max={10000}
            step={0.1}
            precision={3}
            addonBefore={t("Maximum")}
            addonAfter="Å³/atom"
            value={config.constraints.maxVolumePerAtom}
            onChange={(maxVolumePerAtom) => update((c) => ({ ...c, constraints: { ...c.constraints, maxVolumePerAtom } }))}
          />
          <span style={labelStyle}>{t("Lock composition")}</span>
          <Switch
            checked={config.constraints.compositionLocked}
            disabled={config.searchSpace.vacancy || config.searchSpace.interstitialAtom || config.searchSpace.substitution}
            onChange={(compositionLocked) =>
              update((c) => ({ ...c, constraints: { ...c.constraints, compositionLocked } }))
            }
          />
          <span style={labelStyle}>{t("Lock atom count")}</span>
          <Switch
            checked={config.constraints.atomCountLocked}
            disabled={config.searchSpace.vacancy || config.searchSpace.interstitialAtom}
            onChange={(atomCountLocked) => update((c) => ({ ...c, constraints: { ...c.constraints, atomCountLocked } }))}
          />
        </div>
      </Card>

      <Card title={sectionTitle(5, t("OPTIMIZER"))} size="small">
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Method")}</span>
          <Select
            style={{ minWidth: 280 }}
            value={config.optimizer.type}
            options={[{ value: "random", label: t("Descriptor-guided random") }]}
            onChange={() => undefined}
          />
          <span style={labelStyle}>{t("Candidate batch")}</span>
          <InputNumber
            min={1}
            max={512}
            value={config.optimizer.childrenPerSeed}
            onChange={(v) =>
              update((c) => ({
                ...c,
                optimizer: c.optimizer.type === "random" ? { ...c.optimizer, childrenPerSeed: v ?? 8 } : c.optimizer,
              }))
            }
          />
          <span style={labelStyle}>{t("Accepted / round")}</span>
          <InputNumber
            min={1}
            max={128}
            value={config.optimizer.batchAccept}
            onChange={(v) =>
              update((c) => ({
                ...c,
                optimizer: c.optimizer.type === "random" ? { ...c.optimizer, batchAccept: v ?? 8 } : c.optimizer,
              }))
            }
          />
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Reuse accepted structures as seeds")}</span>
          <Switch
            checked={config.optimizer.type === "random" && config.optimizer.reuseAcceptedSeeds}
            disabled={!config.searchSpace.atomicDisplacement}
            onChange={(reuseAcceptedSeeds) =>
              update((c) => ({
                ...c,
                optimizer:
                  c.optimizer.type === "random" ? { ...c.optimizer, reuseAcceptedSeeds } : c.optimizer,
              }))
            }
          />
          <span style={{ fontSize: 13, color: "#616161" }}>
            {t("About half of the parents come from diverse accepted structures and are shaken at random displacement scales.")}
          </span>
        </div>
      </Card>

      <Card title={sectionTitle(6, t("COMPUTE BUDGET"))} size="small">
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Max descriptor evaluations")}</span>
          <InputNumber
            min={1}
            max={10_000_000}
            step={1000}
            value={config.budget.maxEvaluations}
            onChange={(v) => update((c) => ({ ...c, budget: { ...c.budget, maxEvaluations: v ?? 10_000 } }))}
          />
          <span style={labelStyle}>{t("Max accepted structures")}</span>
          <InputNumber
            min={1}
            max={1_000_000}
            value={config.budget.maxAccepted}
            onChange={(v) => update((c) => ({ ...c, budget: { ...c.budget, maxAccepted: v ?? 500 } }))}
          />
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Target novelty")}</span>
          <InputNumber
            min={0}
            max={100}
            value={config.budget.targetNovelty ?? undefined}
            placeholder={t("none")}
            onChange={(v) => update((c) => ({ ...c, budget: { ...c.budget, targetNovelty: v ?? null } }))}
          />
          <span style={labelStyle}>{t("Seed")}</span>
          <Select
            style={{ minWidth: 120 }}
            value={config.seedMode}
            options={[
              { value: "fixed", label: t("Fixed") },
              { value: "random", label: t("Random") },
            ]}
            onChange={(seedMode) => update((c) => ({ ...c, seedMode }))}
          />
          {config.seedMode === "fixed" && (
            <InputNumber
              min={0}
              value={config.seed}
              onChange={(v) => update((c) => ({ ...c, seed: v ?? 42 }))}
            />
          )}
        </div>
      </Card>

      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Button type="primary" size="large" icon={<Play16Regular />} loading={running} onClick={onRun}>
          {t("Run Expansion")}
        </Button>
      </div>
    </div>
  );
}

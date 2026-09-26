// Three-group expansion workflow form:
// SOURCE/TARGET → STRUCTURE/GEOMETRY → SEARCH STRATEGY/STOPPING CONDITIONS.
import { Alert, Button, Card, Collapse, Input, InputNumber, Select, Slider, Switch, Tooltip } from "antd";
import { useEffect, useRef, useState } from "react";
import { Play16Regular } from "@fluentui/react-icons";
import { useGenerationStore } from "../features/generation/generationStore";
import { defaultOptimizerConfig } from "../features/generation/generationStore";
import { GENERATION_OBJECTIVE_LABELS, GENERATION_OPTIMIZER_LABELS } from "../features/generation/labels";
import type { GenerationCatalog, GenerationConfig, GenerationObjectiveType, GenerationOptimizer } from "../features/generation/types";
import type { DatasetView, RunRow } from "../types/protocol";
import { useT } from "../i18n";
import { parseAnchorFrames, validateConfigFields } from "../features/generation/submission";
import { useWorkspace } from "../stores/workspace";

const sectionTitle = (index: number, label: string) => (
  <span style={{ fontSize: 15, fontWeight: 600 }}>
    <span style={{ color: "#0F6CBD", marginRight: 8 }}>{String(index).padStart(2, "0")}</span>
    {label}
  </span>
);
const subsectionTitle = (label: string) => <span style={{ fontSize: 14, fontWeight: 600 }}>{label}</span>;

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
const sourceFieldStyle: React.CSSProperties = { display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", minWidth: 0 };
const sourceLabelStyle: React.CSSProperties = { color: "#616161", fontSize: 13, flex: "0 0 auto" };

function Subsection({ title, children, id }: { title: React.ReactNode; children: React.ReactNode; id?: string }) {
  return (
    <section id={id} tabIndex={id ? -1 : undefined} style={{ paddingTop: 12, borderTop: "1px solid #E5E5E5" }}>
      <div style={{ marginBottom: 8 }}>{title}</div>
      {children}
    </section>
  );
}

export default function GenerationConfigPanel({
  datasetId,
  activeDatasetName,
  datasetFrameCount,
  runs,
  views,
  viewsState,
  viewsError,
  onRetryViews,
  catalog,
  descriptorState,
  catalogFailed,
  catalogLoading,
  catalogError,
  onRetryCatalog,
  onRetryDescriptors,
  submitError,
  onRun,
  running,
  onDismissSubmitError,
}: {
  datasetId: string | null;
  activeDatasetName: string | null;
  datasetFrameCount: number | null;
  runs: RunRow[];
  views: DatasetView[];
  viewsState: "loading" | "ready" | "failed";
  viewsError: string | null;
  onRetryViews: () => void;
  catalog: GenerationCatalog | null;
  descriptorState: "noDataset" | "loading" | "failed" | "ready" | "stale" | "missing";
  catalogFailed: boolean;
  catalogLoading: boolean;
  catalogError: string | null;
  onRetryCatalog: () => void;
  onRetryDescriptors: () => void;
  submitError: string | null;
  onRun: () => void;
  running: boolean;
  onDismissSubmitError: () => void;
}) {
  const { t } = useT();
  const config = useGenerationStore((s) => s.config);
  const updateConfig = useGenerationStore((s) => s.updateConfig);
  const [anchorInput, setAnchorInput] = useState(config.searchTarget.anchorFrames.join(", "));
  const anchorInputRef = useRef(anchorInput);
  const [anchorError, setAnchorError] = useState<string | null>(null);
  const [anchorResetNotice, setAnchorResetNotice] = useState(false);
  const [fieldIssues, setFieldIssues] = useState<{ field: string; reason: string }[]>([]);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [objectiveAdvancedOpen, setObjectiveAdvancedOpen] = useState(false);
  const [geometryAdvancedOpen, setGeometryAdvancedOpen] = useState(false);
  const committedAnchorsRef = useRef(config.searchTarget.anchorFrames.join(","));
  const inputDatasetRef = useRef(datasetId);

  const refreshIssues = (nextConfig: GenerationConfig, rawAnchors = anchorInputRef.current) => {
    const parsedAnchors = parseAnchorFrames(rawAnchors, datasetFrameCount ?? undefined);
    const checked = parsedAnchors.ok
      ? { ...nextConfig, searchTarget: { ...nextConfig.searchTarget, anchorFrames: parsedAnchors.frames } }
      : nextConfig;
    setFieldIssues(validateConfigFields(checked).filter((issue) => issue.field !== "searchTarget.anchorFrames"));
    setAnchorError(parsedAnchors.ok || !rawAnchors.trim() ? null : parsedAnchors.reason);
  };
  const update = (fn: Parameters<typeof updateConfig>[0]) => {
    const nextConfig = fn(useGenerationStore.getState().config);
    updateConfig(() => nextConfig);
    refreshIssues(nextConfig);
  };

  useEffect(() => {
    if (inputDatasetRef.current === datasetId) return;
    inputDatasetRef.current = datasetId;
    if (anchorInputRef.current.trim()) setAnchorResetNotice(true);
    anchorInputRef.current = "";
    committedAnchorsRef.current = "";
    setAnchorInput("");
    setAnchorError(null);
    setFieldIssues((current) => current.filter((issue) => issue.field !== "searchTarget.anchorFrames"));
  }, [datasetId]);

  useEffect(() => {
    const committedAnchors = config.searchTarget.anchorFrames.join(",");
    if (committedAnchors === committedAnchorsRef.current) return;
    committedAnchorsRef.current = committedAnchors;
    const nextInput = config.searchTarget.anchorFrames.join(", ");
    anchorInputRef.current = nextInput;
    setAnchorInput(nextInput);
    setAnchorError(null);
  }, [config.searchTarget.anchorFrames]);
  const objectiveOptions = (catalog?.objectives ?? [
    { name: "novelty", params: {} },
    { name: "local_environment_novelty", params: {} },
    { name: "composite", params: {} },
    { name: "coverage", params: {} },
  ]).map((o) => {
    const type = o.name as GenerationObjectiveType;
    return { value: type, label: t(GENERATION_OBJECTIVE_LABELS[type] ?? o.name) };
  });
  // The optimizer vocabulary is the backend's runtime vocabulary: only what
  // generation.catalog actually offers is selectable (G3.5-2, review §25).
  const anchorsSet = anchorInput.trim().length > 0;
  // Search targets are accepted by every catalog optimizer; incompatible
  // random accepted-seed reuse is reported explicitly instead of rewritten.
  const optimizerOptions = (catalog?.optimizers ?? [{ name: "random" }]).map((o) => {
    const type = o.name as GenerationOptimizer;
    return {
      value: type,
      label: t(GENERATION_OPTIMIZER_LABELS[type] ?? o.name),
    };
  });
  const fullDatasetScope = "__full_dataset__";
  const selectedSeedView = views.find((view) => view.id === config.source.seedViewId);
  const seedScopeSummary = config.source.seedViewId
    ? `${selectedSeedView?.name ?? t("Selected view")} · ${selectedSeedView?.number_of_frames ?? "—"}`
    : `${t("Full dataset")} · ${datasetFrameCount ?? "—"}`;
  const anchorParse = parseAnchorFrames(anchorInput, datasetFrameCount ?? undefined);
  const anchorVisibleError = anchorError ?? (!anchorParse.ok && anchorInput.trim() ? anchorParse.reason : null);
  const reuseTargetConflict = anchorParse.ok && anchorParse.frames.length > 0 && config.optimizer.type === "random" && config.optimizer.reuseAcceptedSeeds;
  const reuseDisplacementConflict = config.optimizer.type === "random" && config.optimizer.reuseAcceptedSeeds && !config.searchSpace.atomicDisplacement;
  const reuseConflict = reuseTargetConflict
    ? "Accepted-seed feedback is unavailable with a target region"
    : reuseDisplacementConflict ? "Accepted-seed feedback requires atomic displacement" : null;
  const issues = Array.from(new Map([
    ...fieldIssues,
    ...(anchorVisibleError ? [{ field: "searchTarget.anchorFrames", reason: anchorVisibleError }] : []),
    ...(reuseConflict ? [{ field: "optimizer.reuseAcceptedSeeds", reason: reuseConflict }] : []),
  ].map((issue) => [`${issue.field}:${issue.reason}`, issue] as const)).values());
  const issueFor = (field: string) => issues.find((issue) => issue.field === field)?.reason;
  const issueMessageId = (field: string) => `generation-issue-${field.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const issueA11y = (field: string) => ({
    "aria-invalid": Boolean(issueFor(field)),
    "aria-describedby": issueFor(field) ? issueMessageId(field) : undefined,
  });
  const fieldTargetId = (field: string) => field === "searchTarget.anchorFrames"
    ? "generation-anchor-frames"
    : field === "optimizer.reuseAcceptedSeeds"
      ? "optimizer-reuseAcceptedSeeds"
      : field === "searchSpace.operators"
        ? "searchSpace-operators"
        : field === "constraints.volume"
          ? "constraints-volume"
      : field.replaceAll(".", "-");
  const focusIssue = (issue: { field: string; reason: string }) => {
    if (issue.field === "optimizer.reuseAcceptedSeeds") setAdvancedOpen(true);
    window.setTimeout(() => {
      const target = document.getElementById(fieldTargetId(issue.field));
      const focusable = target?.matches("input,button,[tabindex]")
        ? target
        : target?.querySelector<HTMLElement>("input,button,[tabindex='0'],.ant-select-selector");
      target?.scrollIntoView({ behavior: "smooth", block: "center" });
      focusable?.focus({ preventScroll: true });
    }, 0);
  };
  const handleRun = () => {
    const parsed = parseAnchorFrames(anchorInput, datasetFrameCount ?? undefined);
    if (!parsed.ok) {
      setAnchorError(parsed.reason);
      setFieldIssues(validateConfigFields(useGenerationStore.getState().config).filter((issue) => issue.field !== "searchTarget.anchorFrames"));
      window.requestAnimationFrame(() => document.getElementById("generation-error-summary")?.focus());
      return;
    }
    setAnchorError(null);
    committedAnchorsRef.current = parsed.frames.join(",");
    update((c) => ({ ...c, searchTarget: { ...c.searchTarget, anchorFrames: parsed.frames } }));
    const currentConfig = useGenerationStore.getState().config;
    const nextConfig = { ...currentConfig, searchTarget: { ...currentConfig.searchTarget, anchorFrames: parsed.frames } };
    const nextIssues = validateConfigFields(nextConfig);
    setFieldIssues(nextIssues);
    if (nextIssues.length > 0) {
      window.requestAnimationFrame(() => document.getElementById("generation-error-summary")?.focus());
      return;
    }
    onDismissSubmitError();
    onRun();
  };
  const changeOptimizer = (type: GenerationOptimizer) => update((c) => {
    const shared = {
      childrenPerSeed: c.optimizer.childrenPerSeed,
      batchAccept: c.optimizer.batchAccept,
      nSeeds: c.optimizer.nSeeds,
    };
    return { ...c, optimizer: { ...defaultOptimizerConfig(type), ...shared } };
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100%", height: "100%", minHeight: 0 }}>
    <div id="generation-config-fields" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 620px), 1fr))", gap: 12, paddingBottom: 12, flex: "1 1 auto", minHeight: 0, overflowY: "auto", overflowX: "hidden", alignContent: "start" }}>
      {submitError && <Alert type="error" showIcon closable onClose={onDismissSubmitError} message={t("Submission failed")}
        description={submitError} style={{ gridColumn: "1 / -1" }} />}
      {catalogFailed && <Alert type="error" showIcon message={t("Could not load the expansion catalog")}
        description={catalogError ?? t("Optimizer catalog could not be loaded; showing the default available option")}
        action={<Button size="small" onClick={onRetryCatalog}>{t("Retry")}</Button>}
        style={{ gridColumn: "1 / -1" }} />}
      {catalogLoading && <Alert type="info" showIcon message={t("Loading expansion catalog")} style={{ gridColumn: "1 / -1" }} />}
      <Card title={sectionTitle(1, t("Source and exploration target"))} size="small">
      <Subsection title={subsectionTitle(t("SOURCE"))}>
        <div style={sourceRowStyle}>
          <div style={sourceFieldStyle}>
            <span style={sourceLabelStyle}>{t("Dataset")}</span>
            <span
              id="source-dataset"
              tabIndex={-1}
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
              id="source-descriptorRunId"
              aria-label={t("Target descriptor")}
              {...issueA11y("source.descriptorRunId")}
              style={{ width: "min(250px, 100%)", maxWidth: "100%", flex: "1 1 180px" }}
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
              id="source-seedViewId"
              aria-label={t("Seed scope")}
              style={{ width: "min(220px, 100%)", maxWidth: "100%", flex: "1 1 180px" }}
              disabled={!activeDatasetName || viewsState !== "ready"}
              value={config.source.seedViewId ?? fullDatasetScope}
              options={[
                { value: fullDatasetScope, label: t("Full dataset") },
                ...views.map((view) => ({
                  value: view.id,
                  label: `${view.name} · ${view.number_of_frames}`,
                  disabled: view.stale || view.number_of_frames === 0,
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
        {viewsState !== "ready" && <Alert
          type={viewsState === "failed" ? "error" : "info"}
          showIcon
          style={{ marginTop: 10 }}
          message={t(viewsState === "failed" ? "Could not verify seed views" : "Loading seed views")}
          description={viewsState === "failed"
            ? <span>{viewsError ?? t("Could not load dataset views")} · {t("The selected seed scope is preserved, and submission stays disabled until its scope is verified.")}</span>
            : t("Wait while the seed scope is checked")}
          action={viewsState === "failed" ? <Button size="small" onClick={onRetryViews}>{t("Retry")}</Button> : undefined}
        />}
        {descriptorState !== "ready" && (
          <Alert
            type={descriptorState === "failed" ? "error" : descriptorState === "stale" ? "warning" : descriptorState === "missing" ? "warning" : "info"}
            showIcon
            style={{ marginTop: 10 }}
            message={t(({ noDataset: "Select a dataset before expanding structures", loading: "Loading descriptor runs", failed: "Could not load descriptor runs", stale: "No current descriptor run is available", missing: "Compute a descriptor run before expanding structures", ready: "" })[descriptorState])}
            description={descriptorState === "stale" || descriptorState === "missing" || descriptorState === "failed"
              ? <span style={{ display: "inline-flex", gap: 12, alignItems: "center" }}>
                  {descriptorState === "failed" && <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={onRetryDescriptors}>{t("Retry")}</Button>}
                  <Button type="link" size="small" style={{ paddingInline: 0 }} onClick={() => useWorkspace.getState().setPage("descriptors")}>{t("Open descriptors")}</Button>
                </span>
              : descriptorState === "loading" ? t("Wait while the source dataset is being checked") : undefined}
          />
        )}
      </div>
      </Subsection>

      <Subsection title={subsectionTitle(t("SEARCH OBJECTIVE"))}>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Objective")}</span>
          <Select
            id="objective-type"
            style={{ width: 280 }}
            aria-label={t("Objective")}
            value={config.objective.type}
            options={objectiveOptions}
            onChange={(type) => update((c) => ({ ...c, objective: { ...c.objective, type } }))}
          />
        </div>
        <Collapse ghost activeKey={objectiveAdvancedOpen ? ["objective"] : []} onChange={(keys) => setObjectiveAdvancedOpen(Array.isArray(keys) ? keys.includes("objective") : keys === "objective")} items={[{ key: "objective", label: t("Advanced objective scoring"), children: <div>
        {(config.objective.type === "local_environment_novelty" || config.objective.type === "composite") && (
          <>
            <div style={rowStyle}>
              <span style={labelStyle}>{t("Aggregation")}</span>
              <Select
                id="objective-aggregation"
                style={{ minWidth: 200 }}
                aria-label={t("Aggregation")}
                value={config.objective.aggregation}
                options={[
                  { value: "top_fraction_mean", label: t("Top fraction mean") },
                  { value: "mean", label: t("Mean") },
                  { value: "quantile", label: t("Quantile") },
                  { value: "max", label: t("Maximum (diagnostic)") },
                ]}
                onChange={(aggregation) => update((c) => ({ ...c, objective: { ...c.objective, aggregation } }))}
              />
              {config.objective.aggregation === "top_fraction_mean" && <InputNumber
                min={0.01} max={1} step={0.05} value={config.objective.topFraction}
                aria-label={t("Top fraction")}
                onChange={(topFraction) => update((c) => ({ ...c, objective: { ...c.objective, topFraction: topFraction ?? 0.2 } }))}
              />}
              {config.objective.aggregation === "quantile" && <InputNumber
                min={0} max={1} step={0.05} value={config.objective.quantile}
                addonBefore={t("Quantile")}
                aria-label={t("Quantile")}
                onChange={(quantile) => update((c) => ({ ...c, objective: { ...c.objective, quantile: quantile ?? 0.5 } }))}
              />}
              <span style={labelStyle}>{t("Novel threshold")}</span>
              <InputNumber
                id="objective-noveltyThreshold"
                min={0.01}
                max={10}
                step={0.05}
                value={config.objective.noveltyThreshold}
                aria-label={t("Novel threshold")}
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
            id="objective-scaling"
            style={{ minWidth: 160 }}
            aria-label={t("Feature scaling")}
            value={config.objective.scaling}
            options={[
              { value: "robust", label: t("Robust") },
              { value: "standardized", label: t("Standardized") },
              { value: "raw", label: t("Raw") },
            ]}
            onChange={(scaling) => update((c) => ({ ...c, objective: { ...c.objective, scaling } }))}
          />
        </div>
        </div> }]} />
      </Subsection>
      <Subsection title={subsectionTitle(t("TARGET REGION"))}>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Anchor dataset frames")}</span>
          <Input
            id="generation-anchor-frames"
            aria-label={t("Anchor dataset frames")}
            style={{ width: 240 }}
            placeholder={t("e.g. 12, 345, 6789")}
            value={anchorInput}
            status={anchorVisibleError ? "error" : undefined}
            aria-invalid={Boolean(anchorVisibleError)}
            aria-describedby={`generation-anchor-help${anchorVisibleError ? " generation-anchor-error" : ""}${issueFor("searchTarget.anchorFrames") ? ` ${issueMessageId("searchTarget.anchorFrames")}` : ""}`}
            onChange={(event) => {
              anchorInputRef.current = event.target.value;
              setAnchorInput(event.target.value);
              const parsed = parseAnchorFrames(event.target.value, datasetFrameCount ?? undefined);
              setAnchorError(parsed.ok || !event.target.value.trim() ? null : parsed.reason);
              refreshIssues(config, event.target.value);
            }}
            onBlur={() => {
              const parsed = parseAnchorFrames(anchorInput, datasetFrameCount ?? undefined);
              if (!parsed.ok) {
                setAnchorError(parsed.reason);
                return;
              }
              setAnchorError(null);
              committedAnchorsRef.current = parsed.frames.join(",");
              update((c) => ({ ...c, searchTarget: { ...c.searchTarget, anchorFrames: parsed.frames } }));
            }}
          />
          <span style={labelStyle}>{t("Region radius")}</span>
          <InputNumber
            id="searchTarget-regionRadius"
            min={0.01}
            max={100}
            step={0.5}
            value={config.searchTarget.regionRadius}
            disabled={!anchorsSet}
            aria-label={t("Region radius")}
            onChange={(v) => update((c) => ({ ...c, searchTarget: { ...c.searchTarget, regionRadius: v ?? 15.0 } }))}
          />
        </div>
        <div id="generation-anchor-help" style={{ fontSize: 12, color: "#616161", marginTop: 4 }}>
          {t("Anchor indices start at 0 and refer to the full source dataset. Anchors can sit outside the selected seed view; they are added to the candidate seed pool. Use up to 16 unique frames. The radius is in robust-scaled descriptor units.")}
        </div>
        {anchorResetNotice && <Alert type="info" showIcon closable onClose={() => setAnchorResetNotice(false)} style={{ marginTop: 6 }} message={t("Target anchor frames were cleared because they belong to the previous source dataset")} />}
        {anchorVisibleError && <div id="generation-anchor-error" role="alert" style={{ color: "#C42B1C", fontSize: 12 }}>{t(anchorVisibleError)}</div>}
        {reuseTargetConflict && <div id="generation-target-seed-conflict" role="alert" tabIndex={-1} style={{ color: "#9A6700", fontSize: 12, marginTop: 6 }}>{t("Accepted-seed feedback is unavailable with a target region")}</div>}
      </Subsection>
      </Card>

      <Card title={sectionTitle(2, t("Structure changes and geometry constraints"))} size="small">
      <Subsection id="searchSpace-operators" title={subsectionTitle(t("SEARCH SPACE"))}>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Atomic displacement")}</span>
          <Switch
            aria-label={t("Atomic displacement")}
            checked={config.searchSpace.atomicDisplacement}
            onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, atomicDisplacement: v } }))}
          />
          <span style={{ fontSize: 13, color: "#616161" }}>σ ≤ {config.searchSpace.maxDisplacement} Å</span>
          <Slider
            style={{ width: 180 }}
            aria-label={t("Maximum displacement")}
            min={0.01}
            max={0.5}
            step={0.01}
            value={config.searchSpace.maxDisplacement}
            disabled={!config.searchSpace.atomicDisplacement}
            onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, maxDisplacement: v } }))}
          />
          <span style={labelStyle}>{t("Hard displacement cutoff")}</span>
          <InputNumber
            style={{ width: 150 }}
            id="searchSpace-hardCutoff"
            aria-label={t("Hard displacement cutoff")}
            min={0.01}
            max={5}
            step={0.05}
            value={config.searchSpace.hardCutoff ?? undefined}
            placeholder={t("unbounded")}
            disabled={!config.searchSpace.atomicDisplacement}
            status={issueFor("searchSpace.hardCutoff") ? "error" : undefined}
            {...issueA11y("searchSpace.hardCutoff")}
            onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, hardCutoff: v ?? null } }))}
          />
        </div>
        {config.searchSpace.atomicDisplacement && (
          <div style={{ fontSize: 12, color: "#616161", marginTop: 4 }}>
            {t("σ caps the Gaussian spread per structure; individual displacements can still exceed σ. Set a hard cutoff to bound every atom's displacement norm.")}
          </div>
        )}
        {reuseDisplacementConflict && <div id="generation-displacement-seed-conflict" role="alert" tabIndex={-1} style={{ color: "#C42B1C", fontSize: 12, marginTop: 6 }}>{t("Accepted-seed feedback requires atomic displacement")}</div>}
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Isotropic strain")}</span>
          <Switch
            aria-label={t("Isotropic strain")}
            checked={config.searchSpace.isotropicStrain}
            onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, isotropicStrain: v } }))}
          />
          <span style={labelStyle}>{t("Anisotropic strain")}</span>
          <Switch
            aria-label={t("Anisotropic strain")}
            checked={config.searchSpace.anisotropicStrain}
            onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, anisotropicStrain: v } }))}
          />
          {(config.searchSpace.isotropicStrain || config.searchSpace.anisotropicStrain) && <>
            <span style={{ fontSize: 13, color: "#616161" }}>±{(config.searchSpace.maxStrain * 100).toFixed(0)} %</span>
            <Slider
              style={{ width: 160 }}
              aria-label={t("Maximum strain")}
              min={0.005}
              max={0.2}
              step={0.005}
              value={config.searchSpace.maxStrain}
              onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, maxStrain: v } }))}
            />
          </>}
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Cell shear")}</span>
          <Switch
            aria-label={t("Cell shear")}
            checked={config.searchSpace.cellShear}
            onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, cellShear: v } }))}
          />
          {config.searchSpace.cellShear && <>
            <span style={labelStyle}>{t("Maximum shear")}</span>
            <InputNumber
              min={0.5}
              max={20}
              step={0.5}
              value={config.searchSpace.maxShear * 100}
              addonAfter="%"
              aria-label={t("Maximum shear")}
              onChange={(maxShear) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, maxShear: (maxShear ?? 5) / 100 } }))}
            />
          </>}
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Vacancy")}</span>
          <Switch
            aria-label={t("Vacancy")}
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
            aria-label={t("Interstitial atom")}
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
            id="searchSpace-interstitialElement"
            aria-label={t("Interstitial element")}
            value={config.searchSpace.interstitialElement}
            placeholder={t("Auto from parent")}
            disabled={!config.searchSpace.interstitialAtom}
            status={issueFor("searchSpace.interstitialElement") ? "error" : undefined}
            {...issueA11y("searchSpace.interstitialElement")}
            onChange={(event) =>
              update((c) => ({ ...c, searchSpace: { ...c.searchSpace, interstitialElement: event.target.value } }))
            }
          />
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Substitution")}</span>
          <Switch
            aria-label={t("Substitution")}
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
            id="searchSpace-substitutionElement"
            aria-label={t("Substitution element")}
            value={config.searchSpace.substitutionElement}
            placeholder={t("Auto: existing species")}
            disabled={!config.searchSpace.substitution}
            status={issueFor("searchSpace.substitutionElement") ? "error" : undefined}
            {...issueA11y("searchSpace.substitutionElement")}
            onChange={(event) =>
              update((c) => ({ ...c, searchSpace: { ...c.searchSpace, substitutionElement: event.target.value } }))
            }
          />
          <span style={labelStyle}>{t("Antisite swap")}</span>
          <Switch
            aria-label={t("Antisite swap")}
            checked={config.searchSpace.antisiteSwap}
            onChange={(v) => update((c) => ({ ...c, searchSpace: { ...c.searchSpace, antisiteSwap: v } }))}
          />
        </div>
        <div style={{ fontSize: 12, color: "#616161", marginTop: 4 }}>
          {t("Vacancy and interstitial change both composition and atom count; substitution changes composition. Antisite swap preserves both.")}
          {" "}
          {t("Leave element blank to use species already present; automatic substitution needs multiple species.")}
        </div>
      </Subsection>

      <Subsection title={subsectionTitle(t("PHYSICAL CONSTRAINTS"))}>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Minimum distance")}</span>
            <Select
              id="constraints-minDistanceMode"
              style={{ minWidth: 200 }}
              aria-label={t("Minimum distance")}
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
                aria-label={t("Covalent radius factor")}
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
              id="constraints-minDistanceAbsolute"
              aria-label={t("Minimum distance")}
              min={0.1}
              max={10}
              step={0.1}
              addonAfter="Å"
              value={config.constraints.minDistanceAbsolute}
              onChange={(v) => update((c) => ({ ...c, constraints: { ...c.constraints, minDistanceAbsolute: v ?? 1.0 } }))}
            />
          )}
        </div>
        <Collapse ghost activeKey={geometryAdvancedOpen ? ["pairs"] : []} onChange={(keys) => setGeometryAdvancedOpen(Array.isArray(keys) ? keys.includes("pairs") : keys === "pairs")} items={[{ key: "pairs", label: t("Advanced element-pair distance overrides"), children: (
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Element-pair distance overrides")}</span>
          <Input
            id="constraints-minDistancePairs"
            style={{ width: 320 }}
            aria-label={t("Element-pair distance overrides")}
            status={issueFor("constraints.minDistancePairs") ? "error" : undefined}
            {...issueA11y("constraints.minDistancePairs")}
            value={config.constraints.minDistancePairs}
            placeholder={t("e.g. C-C=1.5, C-H=1.0")}
            onChange={(event) => update((c) => ({ ...c, constraints: { ...c.constraints, minDistancePairs: event.target.value } }))}
          />
          <span style={{ fontSize: 12, color: "#616161" }}>{t("Listed pairs use these Å cutoffs; other pairs use the selected mode.")}</span>
        </div>
        )}]} />
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Maximum volume change")}</span>
          <span style={{ fontSize: 13, color: "#616161" }}>±{(config.constraints.maxVolumeChange * 100).toFixed(0)} %</span>
          <Slider
            style={{ width: 200 }}
            aria-label={t("Maximum volume change")}
            min={0.01}
            max={0.5}
            step={0.01}
            value={config.constraints.maxVolumeChange}
            disabled={!config.searchSpace.isotropicStrain && !config.searchSpace.anisotropicStrain && !config.searchSpace.cellShear}
            onChange={(v) => update((c) => ({ ...c, constraints: { ...c.constraints, maxVolumeChange: v } }))}
          />
          <span style={labelStyle}>{t("Volume per atom range")}</span>
          <span style={{ fontSize: 12, color: "#616161" }}>{t("Fully periodic structures; blank means unbounded.")}</span>
          <InputNumber
            id="constraints-volume"
            min={0.001}
            max={10000}
            step={0.1}
            precision={3}
            status={issueFor("constraints.volume") ? "error" : undefined}
            aria-label={t("Minimum volume per atom")}
            {...issueA11y("constraints.volume")}
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
            status={issueFor("constraints.volume") ? "error" : undefined}
            aria-label={t("Maximum volume per atom")}
            {...issueA11y("constraints.volume")}
            addonBefore={t("Maximum")}
            addonAfter="Å³/atom"
            value={config.constraints.maxVolumePerAtom}
            onChange={(maxVolumePerAtom) => update((c) => ({ ...c, constraints: { ...c.constraints, maxVolumePerAtom } }))}
          />
          <span style={labelStyle}>{t("Lock composition")}</span>
          <Switch
            id="constraints-compositionLocked"
            aria-label={t("Lock composition")}
            checked={config.constraints.compositionLocked}
            disabled={config.searchSpace.vacancy || config.searchSpace.interstitialAtom || config.searchSpace.substitution}
            onChange={(compositionLocked) =>
              update((c) => ({ ...c, constraints: { ...c.constraints, compositionLocked } }))
            }
          />
          <span style={labelStyle}>{t("Lock atom count")}</span>
          <Switch
            id="constraints-atomCountLocked"
            aria-label={t("Lock atom count")}
            checked={config.constraints.atomCountLocked}
            disabled={config.searchSpace.vacancy || config.searchSpace.interstitialAtom}
            onChange={(atomCountLocked) => update((c) => ({ ...c, constraints: { ...c.constraints, atomCountLocked } }))}
          />
        </div>
      </Subsection>
      </Card>

      <Card title={sectionTitle(3, t("Search strategy and stopping conditions"))} size="small" style={{ gridColumn: "1 / -1" }}>
      <Subsection title={subsectionTitle(t("OPTIMIZER"))}>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Method")}</span>
          <Select
            style={{ minWidth: 280 }}
            aria-label={t("Method")}
            value={config.optimizer.type}
            options={optimizerOptions}
            onChange={changeOptimizer}
          />
          <span style={labelStyle}>{t("Candidate batch")}</span>
          <InputNumber
            id="optimizer-childrenPerSeed"
            min={1}
            max={512}
            precision={0}
            value={config.optimizer.childrenPerSeed}
            aria-label={t("Candidate batch")}
            onChange={(v) =>
              update((c) => ({ ...c, optimizer: { ...c.optimizer, childrenPerSeed: v ?? 8 } }))
            }
          />
          <span style={labelStyle}>{t("Accepted / round")}</span>
          <InputNumber
            id="optimizer-batchAccept"
            min={1}
            max={128}
            precision={0}
            value={config.optimizer.batchAccept}
            aria-label={t("Accepted / round")}
            onChange={(v) =>
              update((c) => ({ ...c, optimizer: { ...c.optimizer, batchAccept: v ?? 8 } }))
            }
          />
        </div>
        <div style={{ fontSize: 12, color: "#616161", marginTop: 4 }}>{t("Switching methods keeps candidate batch and accepted-per-round values; method-specific settings reset to that method's defaults.")}</div>
        <Collapse ghost activeKey={advancedOpen ? ["advanced"] : []} onChange={(keys) => setAdvancedOpen(Array.isArray(keys) ? keys.includes("advanced") : keys === "advanced")} items={[{ key: "advanced", label: t("Advanced optimizer parameters and method notes"), children: (
          <div>
            <div style={rowStyle}>
              <span style={labelStyle}>{t("Seed structures per run")}</span>
              <InputNumber id="optimizer-nSeeds" min={1} max={512} precision={0} value={config.optimizer.nSeeds} aria-label={t("Seed structures per run")}
                onChange={(nSeeds) => update((c) => ({ ...c, optimizer: { ...c.optimizer, nSeeds: nSeeds ?? 64 } }))} />
            </div>
        {config.optimizer.type === "genetic" && (
          <div style={rowStyle}>
            <span style={labelStyle}>{t("Parent fraction")}</span>
            <InputNumber
              min={0.1}
              max={1}
              step={0.05}
              value={config.optimizer.parentFraction}
              onChange={(v) =>
                update((c) => ({
                  ...c,
                  optimizer:
                    c.optimizer.type === "genetic" ? { ...c.optimizer, parentFraction: v ?? 0.7 } : c.optimizer,
                }))
              }
            />
            <span style={labelStyle}>{t("Immigrant fraction")}</span>
            <InputNumber
              min={0}
              max={0.9}
              step={0.05}
              value={config.optimizer.immigrantFraction}
              onChange={(v) =>
                update((c) => ({
                  ...c,
                  optimizer:
                    c.optimizer.type === "genetic" ? { ...c.optimizer, immigrantFraction: v ?? 0.15 } : c.optimizer,
                }))
              }
            />
          </div>
        )}
        {config.optimizer.type === "pso" && (
          <div style={rowStyle}>
            <span style={labelStyle}>{t("Pull: personal best")}</span>
            <InputNumber
              min={0}
              max={10}
              step={0.1}
              value={config.optimizer.psoWeightPbest}
              onChange={(v) =>
                update((c) => ({
                  ...c,
                  optimizer: c.optimizer.type === "pso" ? { ...c.optimizer, psoWeightPbest: v ?? 1.0 } : c.optimizer,
                }))
              }
            />
            <span style={labelStyle}>{t("Pull: global best")}</span>
            <InputNumber
              min={0}
              max={10}
              step={0.1}
              value={config.optimizer.psoWeightGbest}
              onChange={(v) =>
                update((c) => ({
                  ...c,
                  optimizer: c.optimizer.type === "pso" ? { ...c.optimizer, psoWeightGbest: v ?? 1.5 } : c.optimizer,
                }))
              }
            />
            <span style={labelStyle}>{t("Pull: own position")}</span>
            <InputNumber
              min={0}
              max={10}
              step={0.1}
              value={config.optimizer.psoWeightMut}
              onChange={(v) =>
                update((c) => ({
                  ...c,
                  optimizer: c.optimizer.type === "pso" ? { ...c.optimizer, psoWeightMut: v ?? 0.5 } : c.optimizer,
                }))
              }
            />
            <span style={labelStyle}>{t("Immigrant fraction")}</span>
            <InputNumber
              min={0}
              max={0.9}
              step={0.05}
              value={config.optimizer.immigrantFraction}
              onChange={(v) =>
                update((c) => ({
                  ...c,
                  optimizer: c.optimizer.type === "pso" ? { ...c.optimizer, immigrantFraction: v ?? 0.15 } : c.optimizer,
                }))
              }
            />
          </div>
        )}
        {config.optimizer.type === "random" && (
          <div style={rowStyle}>
            <span style={labelStyle}>{t("Reuse accepted structures as seeds")}</span>
            <Tooltip title={t("G4-2 benchmark: this mode lost novel-environment discovery in 20/20 paired seeds against plain random. Keep it off for exploration; it only helps when densely resampling around the accepted set.")}>
              <Switch
                id="optimizer-reuseAcceptedSeeds"
                aria-label={t("Reuse accepted structures as seeds")}
                {...issueA11y("optimizer.reuseAcceptedSeeds")}
                checked={config.optimizer.reuseAcceptedSeeds}
                onChange={(reuseAcceptedSeeds) =>
                  update((c) => ({
                    ...c,
                    optimizer:
                      c.optimizer.type === "random" ? { ...c.optimizer, reuseAcceptedSeeds } : c.optimizer,
                  }))
                }
              />
            </Tooltip>
            <span style={{ fontSize: 13, color: "#616161" }}>
              {t("About half of the parents come from diverse accepted structures and are shaken at random displacement scales.")}
            </span>
          </div>
        )}
        {config.optimizer.type === "genetic" && (
          <div style={{ fontSize: 12, color: "#616161", marginTop: 4 }}>
            {t(
              "Parents are drawn from accepted structures by rank-weighted roulette; displacement, strain and shear amplitudes plus the operator mask are inherited genes, and a fixed share of every round stays on fresh seed structures."
            )}
            <div>
              {t("Benchmark verdict (20 paired seeds): slower discovery than plain random (-6.5/100). Keep random for exploration; use the genetic algorithm as an evolution-strategy baseline or for experiments.")}
            </div>
          </div>
        )}
        {config.optimizer.type === "pso" && (
          <div style={{ fontSize: 12, color: "#616161", marginTop: 4 }}>
            {t(
              "Each round slot remembers its best structure (personal/global best); the farther the particle sits from a memory target, the stronger the pull to propose around it, and a fixed share of every round stays on fresh seed structures."
            )}
            <div>
              {t("Benchmark verdict (20 paired seeds): highest variance of all optimizers and worse coverage; without crossover the memory adds no gain over the genetic algorithm. Not recommended as a primary optimizer.")}
            </div>
          </div>
        )}
          </div>
        )}]} />
      </Subsection>

      <Subsection title={subsectionTitle(t("COMPUTE BUDGET"))}>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Max descriptor evaluations")}</span>
          <InputNumber
            id="budget-maxEvaluations"
            min={1}
            max={10_000_000}
            precision={0}
            step={1000}
            value={config.budget.maxEvaluations}
            status={issueFor("budget.maxEvaluations") ? "error" : undefined}
            aria-label={t("Max descriptor evaluations")}
            {...issueA11y("budget.maxEvaluations")}
            onChange={(v) => update((c) => ({ ...c, budget: { ...c.budget, maxEvaluations: v ?? 10_000 } }))}
          />
          <span style={labelStyle}>{t("Max accepted structures")}</span>
          <InputNumber
            id="budget-maxAccepted"
            min={1}
            max={1_000_000}
            precision={0}
            value={config.budget.maxAccepted}
            status={issueFor("budget.maxAccepted") ? "error" : undefined}
            aria-label={t("Max accepted structures")}
            {...issueA11y("budget.maxAccepted")}
            onChange={(v) => update((c) => ({ ...c, budget: { ...c.budget, maxAccepted: v ?? 500 } }))}
          />
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Maximum generations")}</span>
          <InputNumber
            id="budget-maxGenerations"
            min={1}
            max={100_000}
            precision={0}
            value={config.budget.maxGenerations}
            status={issueFor("budget.maxGenerations") ? "error" : undefined}
            aria-label={t("Maximum generations")}
            {...issueA11y("budget.maxGenerations")}
            onChange={(maxGenerations) => update((c) => ({ ...c, budget: { ...c.budget, maxGenerations: maxGenerations ?? 200 } }))}
          />
          <span style={labelStyle}>{t("No-improvement rounds")}</span>
          <InputNumber
            id="budget-noImprovementRounds"
            min={1}
            max={10_000}
            precision={0}
            value={config.budget.noImprovementRounds ?? undefined}
            placeholder={t("Disabled")}
            status={issueFor("budget.noImprovementRounds") ? "error" : undefined}
            aria-label={t("No-improvement rounds")}
            {...issueA11y("budget.noImprovementRounds")}
            onChange={(noImprovementRounds) => update((c) => ({ ...c, budget: { ...c.budget, noImprovementRounds } }))}
          />
        </div>
        <div style={rowStyle}>
          <span style={labelStyle}>{t("Target novelty")}</span>
          <InputNumber
            id="budget-targetNovelty"
            min={0}
            value={config.budget.targetNovelty ?? undefined}
            placeholder={t("none")}
            status={issueFor("budget.targetNovelty") ? "error" : undefined}
            aria-label={t("Target novelty")}
            {...issueA11y("budget.targetNovelty")}
            onChange={(v) => update((c) => ({ ...c, budget: { ...c.budget, targetNovelty: v ?? null } }))}
          />
          <span style={labelStyle}>{t("Seed")}</span>
          <Select
            id="seedMode"
            style={{ minWidth: 120 }}
            aria-label={t("Seed")}
            value={config.seedMode}
            options={[
              { value: "fixed", label: t("Fixed") },
              { value: "random", label: t("Random") },
            ]}
            onChange={(seedMode) => update((c) => ({ ...c, seedMode }))}
          />
          {config.seedMode === "fixed" && (
            <InputNumber
              id="fixedSeed"
              min={0}
              value={config.seed}
              aria-label={t("Seed")}
              onChange={(v) => update((c) => ({ ...c, seed: v ?? 42 }))}
            />
          )}
        </div>
      </Subsection>

      </Card>
    </div>
      <div id="generation-config-summary" style={{ position: "relative", zIndex: 4, flex: "0 0 auto", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexWrap: "wrap", marginTop: 8, padding: "12px 16px", background: "rgba(255,255,255,.98)", border: "1px solid #E5E5E5", borderRadius: 8, boxShadow: "0 -3px 14px rgba(0,0,0,.08)" }}>
        <div style={{ minWidth: 240, flex: 1, fontSize: 12, color: "#616161" }}>
          <div><b>{t("Run summary")}</b> · {t("Seed scope")}: {seedScopeSummary} · {t("Seed")}: {config.seedMode === "random" ? t("Random") : `${t("Fixed")} ${config.seed}`}</div>
          <div>{t("Anchor frames")}: {anchorParse.ok ? (anchorParse.frames.length ? anchorParse.frames.join(", ") : t("None")) : t("Invalid")}; {t("Enabled operators")}: {[
            config.searchSpace.atomicDisplacement && t("Atomic displacement"),
            config.searchSpace.isotropicStrain && t("Isotropic strain"),
            config.searchSpace.anisotropicStrain && t("Anisotropic strain"),
            config.searchSpace.cellShear && t("Cell shear"),
            config.searchSpace.vacancy && t("Vacancy"),
            config.searchSpace.interstitialAtom && t("Interstitial atom"),
            config.searchSpace.substitution && t("Substitution"),
            config.searchSpace.antisiteSwap && t("Antisite swap"),
          ].filter(Boolean).join(", ") || t("None")}</div>
          <div>{t("Stops at")}: {config.budget.maxEvaluations.toLocaleString()} {t("evaluations")}, {config.budget.maxAccepted} {t("accepted")}, {config.budget.maxGenerations} {t("generations")}{config.budget.noImprovementRounds != null ? ` · ${config.budget.noImprovementRounds} ${t("No-improvement rounds")}` : ""}{config.budget.targetNovelty != null ? ` · ${t("Target novelty")} ≥ ${config.budget.targetNovelty}` : ""}</div>
          {issues.length > 0 && <div id="generation-error-summary" role="alert" aria-live="assertive" tabIndex={-1} style={{ color: "#C42B1C", marginTop: 4 }}>
            {issues.map((issue) => <span key={`${issue.field}-${issue.reason}`} style={{ display: "inline-flex", alignItems: "center" }}>
              <Button type="link" danger size="small" style={{ paddingInline: 4 }} aria-label={t(issue.reason)} onClick={() => focusIssue(issue)}>{t(issue.reason)}</Button>
              <span id={issueMessageId(issue.field)} style={{ position: "absolute", width: 1, height: 1, padding: 0, margin: -1, overflow: "hidden", clip: "rect(0,0,0,0)", whiteSpace: "nowrap", border: 0 }}>{t(issue.reason)}</span>
            </span>)}
          </div>}
        </div>
        <Button type="primary" size="large" icon={<Play16Regular />} loading={running} disabled={descriptorState !== "ready" || catalogLoading || catalogFailed || viewsState !== "ready" || anchorError != null} onClick={handleRun}>
          {t("Run Expansion")}
        </Button>
      </div>
    </div>
  );
}

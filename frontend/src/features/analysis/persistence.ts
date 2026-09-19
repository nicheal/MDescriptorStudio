import { ipc } from "../../ipc/client";
import { analysisNavModuleForKey } from "./registry";
import { slotKey } from "./identity";
import type { AnalysisModuleKey, AnalysisSlot, OverviewAnalysis, TabKey } from "./types";

const VIEW_SETTINGS_KEY = "workspace.analysisUi";
const SLOTS_SETTINGS_KEY = "workspace.analysisSlots";
/** Keep headroom under the backend's 4096-character setting limit. */
export const MAX_PERSISTED_SLOT_CHARS = 3500;

type CompactSlot = [AnalysisModuleKey, string, string, string, number, number];

/**
 * Persist a whole-state blob, newest value only, one write at a time per key.
 *
 * The sidecar answers on a four-thread pool and `settings.set` is an
 * unconditional UPSERT, so two in-flight writes for one key can land in the
 * opposite order and last commit wins: dragging a stepper or switching module
 * quickly could leave a *stale* blob as the value that greets the next launch.
 * Serializing per key also coalesces, so a burst of view changes costs one
 * frame instead of one per keystroke.
 */
const writers = new Map<string, { latest: string | null; running: boolean }>();

function persistSetting(key: string, value: string): void {
  let writer = writers.get(key);
  if (!writer) {
    writer = { latest: null, running: false };
    writers.set(key, writer);
  }
  writer.latest = value;
  if (writer.running) return;
  writer.running = true;
  const active = writer;
  void (async () => {
    while (active.latest !== null) {
      const next = active.latest;
      active.latest = null;
      try {
        await ipc.request("settings.set", { key, value: next });
      } catch (error) {
        console.warn(`Could not persist ${key}`, error);
      }
    }
    active.running = false;
  })();
}

export function persistView(view: unknown): void {
  persistSetting(VIEW_SETTINGS_KEY, JSON.stringify(view));
}

export function serializeAnalysisSlots(slots: Record<string, AnalysisSlot>): string {
  return compactSerialize(pruneSlots(slots));
}

export function persistSlots(slots: Record<string, AnalysisSlot>): void {
  persistSetting(SLOTS_SETTINGS_KEY, serializeAnalysisSlots(slots));
}

export function parseAnalysisSlots(raw: unknown): Record<string, AnalysisSlot> | null {
  if (typeof raw !== "string" || !raw) return null;
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null || Array.isArray(data)) return null;
  const slots: Record<string, AnalysisSlot> = {};
  const compact = data as { v?: unknown; slots?: unknown };
  if (compact.v === 2 && Array.isArray(compact.slots)) {
    for (const value of compact.slots) {
      if (!Array.isArray(value) || value.length !== 6) continue;
      const [moduleKey, inputKey, parameterKey, analysisId, updatedAt, seq] = value;
      if (!isModuleKey(moduleKey) || typeof inputKey !== "string" || !inputKey || typeof parameterKey !== "string" || typeof analysisId !== "string" || !analysisId || !finiteNumber(updatedAt) || !finiteNumber(seq)) continue;
      const slot: AnalysisSlot = { analysisId, moduleKey, inputKey, parameterKey, updatedAt: Number(updatedAt), seq: Number(seq) };
      slots[slotKey(slot)] = slot;
    }
  } else {
    for (const value of Object.values(data as Record<string, unknown>)) {
      if (typeof value !== "object" || value === null || Array.isArray(value)) continue;
      const rec = value as Record<string, unknown>;
      const moduleKey = typeof rec.moduleKey === "string" ? analysisNavModuleForKey(rec.moduleKey)?.key : legacyModuleKey(rec.tab, rec.paramsKey);
      const inputKey = typeof rec.inputKey === "string" && rec.inputKey ? rec.inputKey : typeof rec.runId === "string" && rec.runId ? `legacy:${rec.runId}` : null;
      const parameterKey = typeof rec.parameterKey === "string" ? rec.parameterKey : typeof rec.paramsKey === "string" ? rec.paramsKey : null;
      if (!moduleKey || !inputKey || parameterKey === null || typeof rec.analysisId !== "string" || !rec.analysisId || !finiteNumber(rec.updatedAt)) continue;
      const slot: AnalysisSlot = { analysisId: rec.analysisId, moduleKey, inputKey, parameterKey, updatedAt: Number(rec.updatedAt), seq: finiteNumber(rec.seq) ? Number(rec.seq) : 0 };
      slots[slotKey(slot)] = slot;
    }
  }
  const fitted = pruneSlots(slots);
  return Object.keys(fitted).length ? fitted : null;
}

export function pruneSlots(slots: Record<string, AnalysisSlot>): Record<string, AnalysisSlot> {
  const ranked = Object.entries(slots)
    .map(([key, slot]) => ({ key, slot }))
    .sort((a, b) => (b.slot.updatedAt - a.slot.updatedAt) || (b.slot.seq - a.slot.seq));
  while (ranked.length) {
    const candidate = Object.fromEntries(ranked.map(({ key, slot }) => [key, slot]));
    if (compactSerialize(candidate).length <= MAX_PERSISTED_SLOT_CHARS) break;
    ranked.pop();
  }
  return Object.fromEntries(ranked.map(({ key, slot }) => [key, slot]));
}

function compactSerialize(slots: Record<string, AnalysisSlot>): string {
  const rows: CompactSlot[] = Object.values(slots).map((slot) => [slot.moduleKey, slot.inputKey, slot.parameterKey, slot.analysisId, slot.updatedAt, slot.seq]);
  return JSON.stringify({ v: 2, slots: rows });
}

function isModuleKey(value: unknown): value is AnalysisModuleKey {
  return typeof value === "string" && analysisNavModuleForKey(value) !== null;
}

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function legacyModuleKey(tab: unknown, paramsKey: unknown): AnalysisModuleKey | null {
  if (typeof tab !== "string" || typeof paramsKey !== "string") return null;
  const first = paramsKey.split("|", 1)[0];
  const byTab: Partial<Record<TabKey, AnalysisModuleKey>> = { projection: "descriptor_space", similarity: "similarity", clusters: "structural_clusters", outliers: "outlier_environments", sampling: "representative_sampling", compare: "descriptor_comparison", local: "local_environment", kernel: "kernel_analysis" };
  if (tab === "coverage") return first === "overlap" ? "train_test_overlap" : "data_coverage";
  if (tab === "overview") {
    const overview: Partial<Record<OverviewAnalysis, AnalysisModuleKey>> = { feature_variance: "feature_variance", feature_correlation: "feature_correlation", effective_dimension: "effective_dimension", property_correlation: "property_information", trajectory: "descriptor_trajectory", perturbation_sensitivity: "structural_perturbation_response", drift: "dataset_drift", sensitivity: "parameter_sensitivity" };
    return overview[first as OverviewAnalysis] ?? null;
  }
  return byTab[tab as TabKey] ?? null;
}

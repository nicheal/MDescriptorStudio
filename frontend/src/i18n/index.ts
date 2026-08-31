// Minimal bilingual layer (EN/ZH) for the GUI. English literals act as the
// translation keys: `t("Refresh")` returns the key itself in English mode, so
// the existing English UI is preserved verbatim, while Chinese mode looks the
// key up in zh.ts. The choice is mirrored to the backend settings KV
// (settings.get/set, key `ui.language`) and to localStorage so the start
// screen already renders in the chosen language before the backend answers.
import { useMemo } from "react";
import { create } from "zustand";
import { ipc } from "../ipc/client";
import { zhDict } from "./zh";

export type Lang = "en" | "zh";

const SETTINGS_KEY = "ui.language";
const LS_KEY = "mdescriptor.ui.language";

/** A label that carries both languages at its definition site. */
export interface Pair {
  en: string;
  zh: string;
}

export type Vars = Record<string, string | number>;

export interface T {
  lang: Lang;
  /** Locale for toLocaleString/toLocaleTimeString; undefined keeps the system default (today's behavior). */
  locale: string | undefined;
  t: (key: string, vars?: Vars) => string;
  tr: (pair: Pair) => string;
}

function readStoredLang(): Lang {
  try {
    return localStorage.getItem(LS_KEY) === "zh" ? "zh" : "en";
  } catch {
    return "en";
  }
}

function persist(lang: Lang) {
  try {
    localStorage.setItem(LS_KEY, lang);
  } catch {
    /* storage may be unavailable */
  }
  try {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  } catch {
    /* not in a DOM */
  }
}

interface I18nState {
  lang: Lang;
  setLang: (lang: Lang) => void;
}

export const useI18n = create<I18nState>((set) => ({
  lang: readStoredLang(),
  setLang: (lang) => {
    persist(lang);
    set({ lang });
    void ipc.request("settings.set", { key: SETTINGS_KEY, value: lang }).catch(() => {});
  },
}));

function interpolate(template: string, vars: Vars | undefined): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match,
  );
}

/** Resolve one key for a language (exported for tests). */
export function translateKey(lang: Lang, key: string, vars?: Vars): string {
  return interpolate(lang === "zh" ? (zhDict[key] ?? key) : key, vars);
}

function makeT(lang: Lang): T {
  return {
    lang,
    locale: lang === "zh" ? "zh-CN" : undefined,
    t: (key, vars) => translateKey(lang, key, vars),
    tr: (pair) => (lang === "zh" ? pair.zh : pair.en),
  };
}

/** Reactive translation helpers for components; re-renders on language switch. */
export function useT(): T {
  const lang = useI18n((s) => s.lang);
  return useMemo(() => makeT(lang), [lang]);
}

/** Translation helpers for callbacks registered once (event handlers etc.). */
export function getT(): T {
  return makeT(useI18n.getState().lang);
}

/**
 * Load the persisted language before the main UI mounts (backend.ready path).
 * The backend setting wins; a localStorage choice without a backend value is
 * mirrored back into the settings KV.
 */
export async function initLanguage(): Promise<void> {
  persist(useI18n.getState().lang);
  try {
    const r = await ipc.request<{ value: string | null }>("settings.get", { key: SETTINGS_KEY });
    const saved = r.value;
    if (saved === "en" || saved === "zh") {
      if (saved !== useI18n.getState().lang) persist(saved);
      useI18n.setState({ lang: saved });
    } else {
      const current = useI18n.getState().lang;
      if (current !== "en") {
        void ipc.request("settings.set", { key: SETTINGS_KEY, value: current }).catch(() => {});
      }
    }
  } catch {
    /* backend not reachable yet — keep the localStorage choice */
  }
}

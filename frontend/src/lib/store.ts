// localStorage-backed persistence for conversations and theme. Settings (system
// prompt, sampling, tools, context length) are stored *per conversation* — each
// chat owns its own, so a new chat starts from DEFAULT_SETTINGS and one chat's
// settings never leak into the next.

import type { Conversation } from "@/lib/types";

const CONVERSATIONS_KEY = "kodo.conversations";
const THEME_KEY = "kodo.theme";

export interface Settings {
  // null = use the project default (kodo.toml); "" = explicitly no system prompt;
  // a string = override. Kept distinct so a project's prompt applies by default.
  systemPrompt: string | null;
  maxTokens: number | null;
  temperature: number | null;
  topP: number | null;
  useTools: boolean;
  /** Namespaced tool names the user switched off (denylist; new tools default on). */
  disabledTools: string[];
  /** Preferred context window (tokens) applied when a model is loaded; null = runtime default. */
  contextLength: number | null;
}

export const DEFAULT_SETTINGS: Settings = {
  systemPrompt: null, // default to the project prompt; the user can override or clear it
  maxTokens: null,
  temperature: null,
  topP: null,
  useTools: true,
  disabledTools: [],
  contextLength: null,
};

export function uid(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

/** Coerce an untrusted/partial object into a valid Settings (missing → default). */
export function normalizeSettings(parsed: Partial<Settings> | undefined | null): Settings {
  if (!parsed || typeof parsed !== "object") return { ...DEFAULT_SETTINGS };
  return {
    systemPrompt: typeof parsed.systemPrompt === "string" ? parsed.systemPrompt : null,
    maxTokens: typeof parsed.maxTokens === "number" ? parsed.maxTokens : null,
    temperature: typeof parsed.temperature === "number" ? parsed.temperature : null,
    topP: typeof parsed.topP === "number" ? parsed.topP : null,
    useTools: typeof parsed.useTools === "boolean" ? parsed.useTools : true,
    disabledTools: Array.isArray(parsed.disabledTools)
      ? parsed.disabledTools.filter((t): t is string => typeof t === "string")
      : [],
    contextLength: typeof parsed.contextLength === "number" ? parsed.contextLength : null,
  };
}

export function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(CONVERSATIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Conversation[];
    if (!Array.isArray(parsed)) return [];
    // Back-fill settings for conversations saved before they were per-conversation.
    return parsed.map((c) => ({ ...c, settings: normalizeSettings(c.settings) }));
  } catch {
    return [];
  }
}

export function saveConversations(convs: Conversation[]): void {
  try {
    localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(convs));
  } catch {
    // storage full / unavailable — best-effort
  }
}

export type Theme = "dark" | "light";

export function loadTheme(): Theme {
  try {
    const raw = localStorage.getItem(THEME_KEY);
    return raw === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function saveTheme(theme: Theme): void {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // ignore
  }
}

/** Derive a short conversation title from the first user message. */
export function deriveTitle(text: string): string {
  const clean = text.trim().replace(/\s+/g, " ");
  if (!clean) return "New chat";
  return clean.length > 40 ? `${clean.slice(0, 40)}…` : clean;
}

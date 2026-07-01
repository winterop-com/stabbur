// localStorage-backed persistence for conversations, settings, and theme.
// The system prompt is stored once, app-wide (not per-conversation), and is
// prepended as a {role:"system"} message on every /api/chat request.

import type { Conversation } from "@/lib/types";

const CONVERSATIONS_KEY = "kodo.conversations";
const SETTINGS_KEY = "kodo.settings";
const THEME_KEY = "kodo.theme";

export interface Settings {
  systemPrompt: string;
  maxTokens: number | null;
}

export const DEFAULT_SETTINGS: Settings = { systemPrompt: "", maxTokens: null };

export function uid(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(CONVERSATIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Conversation[];
    if (!Array.isArray(parsed)) return [];
    return parsed;
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

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<Settings>;
    return {
      systemPrompt: typeof parsed.systemPrompt === "string" ? parsed.systemPrompt : "",
      maxTokens: typeof parsed.maxTokens === "number" ? parsed.maxTokens : null,
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(s: Settings): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
  } catch {
    // ignore
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

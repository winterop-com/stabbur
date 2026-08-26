// localStorage-backed persistence for conversations and theme. Settings (system
// prompt, sampling, tools, context length) are stored *per conversation* — each
// chat owns its own, so a new chat starts from DEFAULT_SETTINGS and one chat's
// settings never leak into the next.

import type { Conversation } from "@/lib/types";

const CONVERSATIONS_KEY = "heim.conversations";
const THEME_KEY = "heim.theme";
const PALETTE_KEY = "heim.theme_palette";

export interface Settings {
  // null = use the project default (heim.toml); "" = explicitly no system prompt;
  // a string = override. Kept distinct so a project's prompt applies by default.
  systemPrompt: string | null;
  maxTokens: number | null;
  /** Sampling overrides for this chat; null = the value the model (or heim) recommends, which the
   *  server resolves — the panel only ever *shows* that number, it never sends one it made up. */
  temperature: number | null;
  topP: number | null;
  topK: number | null;
  minP: number | null;
  repeatPenalty: number | null;
  useTools: boolean;
  /** MCP servers this chat may call (allowlist of server names); null = the baseline.
   *  An allowlist rather than a denylist because switching a server on is machine-wide and
   *  persistent — without this, starting one for a single question left it live in every
   *  later chat. `null` defers to `baselineServers()` so a chat created before a server
   *  existed does not silently gain it. */
  enabledServers: string[] | null;
  /** Namespaced tool names the user switched off, within an allowed server (denylist). */
  disabledTools: string[];
  /** Preferred context window (tokens) applied when a model is loaded; null = runtime default. */
  contextLength: number | null;
  /** Reasoning effort for thinking models; null = the model's default behavior. */
  reasoning: ReasoningLevel | null;
  /** Listen voice for this chat; null = inherit the default (Settings page / project). */
  ttsVoice: string | null;
  /** Listen speed for this chat; null = inherit the default. */
  ttsSpeed: number | null;
  /** Attach PDFs as rendered page images rather than extracted text. Per-chat because
   *  it only makes sense against the model this chat has loaded — it falls back to text
   *  automatically when that model has no vision. */
  pdfAsImage: boolean;
}

export type ReasoningLevel = "off" | "low" | "medium" | "high" | "max";
const REASONING_LEVELS: readonly string[] = ["off", "low", "medium", "high", "max"];

export const DEFAULT_SETTINGS: Settings = {
  systemPrompt: null, // default to the project prompt; the user can override or clear it
  maxTokens: null,
  temperature: null,
  topP: null,
  topK: null,
  minP: null,
  repeatPenalty: null,
  useTools: true,
  enabledServers: null, // the baseline; an explicit list only once the user chooses
  disabledTools: [],
  contextLength: null,
  reasoning: null,
  ttsVoice: null,
  ttsSpeed: null,
  pdfAsImage: false, // text is cheaper and works on every model; images are the opt-in
};

/** Server names a chat starts with when it has made no choice of its own.

 *  A machine-wide switch says which servers *run*; this says which a new conversation may
 *  *call*. Everything the user switched on for one question would otherwise stay live in every
 *  later chat. `datetime` is the safe baseline (heim seeds it, and it reaches nothing), and a
 *  project's own servers are included because a project assistant exists to use them — starting
 *  its chats with its tools off would break the thing the project is for.
 */
export function baselineServers(servers: { name: string; scope: string | null }[]): string[] {
  return servers.filter((s) => s.name === "datetime" || s.scope === "project").map((s) => s.name);
}

/** Every server the UI knows of, each with the scope `baselineServers` judges it by.
 *
 *  Two sources, because neither is the whole picture: the catalogue (/api/mcp/servers) is exactly
 *  the set heim ships — the only set the machine-wide switch can start, and the only one carrying a
 *  resolved scope — while the attached tools (/api/tools) also cover servers heim doesn't ship and
 *  therefore can't list. An attached server missing from the catalogue was written into a config
 *  file by hand, which the panel already labels as this project's `.mcp.json`, so it is treated as
 *  project scope here for the same reason: a project's own tools are what its chats are for. (The
 *  one case that mislabels is a third-party server hand-added to the *global* mcp.json — it starts
 *  on in new chats where a bundled global server would not.)
 */
export function serverScopes(
  catalogue: { name: string; scope: string | null }[],
  attached: string[],
): { name: string; scope: string | null }[] {
  const known = new Set(catalogue.map((s) => s.name));
  return [
    ...catalogue.map((s) => ({ name: s.name, scope: s.scope })),
    ...[...new Set(attached)].filter((name) => !known.has(name)).map((name) => ({ name, scope: "project" })),
  ];
}

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
    topK: typeof parsed.topK === "number" ? parsed.topK : null,
    minP: typeof parsed.minP === "number" ? parsed.minP : null,
    repeatPenalty: typeof parsed.repeatPenalty === "number" ? parsed.repeatPenalty : null,
    useTools: typeof parsed.useTools === "boolean" ? parsed.useTools : true,
    enabledServers: Array.isArray(parsed.enabledServers)
      ? parsed.enabledServers.filter((n): n is string => typeof n === "string")
      : null,
    disabledTools: Array.isArray(parsed.disabledTools)
      ? parsed.disabledTools.filter((t): t is string => typeof t === "string")
      : [],
    contextLength: typeof parsed.contextLength === "number" ? parsed.contextLength : null,
    reasoning:
      typeof parsed.reasoning === "string" && REASONING_LEVELS.includes(parsed.reasoning)
        ? parsed.reasoning
        : null,
    ttsVoice: typeof parsed.ttsVoice === "string" ? parsed.ttsVoice : null,
    ttsSpeed:
      typeof parsed.ttsSpeed === "number" && parsed.ttsSpeed >= 0.25 && parsed.ttsSpeed <= 2 ? parsed.ttsSpeed : null,
    pdfAsImage: typeof parsed.pdfAsImage === "boolean" ? parsed.pdfAsImage : false,
  };
}

export function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(CONVERSATIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Conversation[];
    if (!Array.isArray(parsed)) return [];
    // Back-fill settings for conversations saved before they were per-conversation, and drop any
    // still-pending confirmations: they belong to a stream that no longer exists (a reload mid-stream),
    // so their Approve/Deny buttons would post an id the server already dropped. Resolved notes stay.
    return parsed.map((c) => ({
      ...c,
      settings: normalizeSettings(c.settings),
      messages: c.messages.map((m) =>
        m.confirms ? { ...m, confirms: m.confirms.filter((cf) => cf.status !== "pending") } : m,
      ),
    }));
  } catch {
    return [];
  }
}

/** Outcome of a persistence attempt, so the UI can warn instead of silently losing data.
 *  "ok" = fully saved; "degraded" = quota hit, saved without inline media (attachments won't
 *  survive a reload, but the transcript + older chats did); "failed" = nothing could be saved. */
export type SaveResult = "ok" | "degraded" | "failed";

/** Drop inline image/audio data URLs (the multi-MB base64 that blows the ~5 MB quota) while
 *  keeping the text transcript and a marker so the message still renders sensibly on reload. */
function stripInlineMedia(convs: Conversation[]): Conversation[] {
  return convs.map((c) => ({
    ...c,
    messages: c.messages.map((m) =>
      m.images || m.audios
        ? { ...m, images: undefined, audios: undefined, mediaDropped: (m.images?.length ?? 0) + (m.audios?.length ?? 0) }
        : m,
    ),
  }));
}

export function saveConversations(convs: Conversation[]): SaveResult {
  try {
    localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(convs));
    return "ok";
  } catch {
    // Quota exceeded (usually one pasted image/audio data URL). Rather than lose every
    // conversation silently, retry with inline media stripped so the transcript and older
    // chats still persist — and report "degraded" so the UI can tell the user attachments
    // won't survive a reload.
    try {
      localStorage.setItem(CONVERSATIONS_KEY, JSON.stringify(stripInlineMedia(convs)));
      return "degraded";
    } catch {
      return "failed";
    }
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

/** Named colour themes (the palette; light/dark is a separate axis). */
export const THEME_PALETTES = ["default", "indigo", "paper", "contrast", "terminal"] as const;
export type ThemePalette = (typeof THEME_PALETTES)[number];

export function loadPalette(): ThemePalette {
  const raw = localStorage.getItem(PALETTE_KEY);
  return (THEME_PALETTES as readonly string[]).includes(raw ?? "") ? (raw as ThemePalette) : "default";
}

export function savePalette(palette: ThemePalette): void {
  try {
    if (palette === "default") localStorage.removeItem(PALETTE_KEY);
    else localStorage.setItem(PALETTE_KEY, palette);
  } catch {
    /* storage full/blocked: the choice still applies this session */
  }
}

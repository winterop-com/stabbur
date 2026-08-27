// Typed wrapper over chrome.storage.local for the panel's persisted settings.
//
// Schema v2 only: named backends (each its own base URL + token) plus an active-backend
// pointer, alongside the page-context toggles and the appearance pair. There is no v1
// migration — the extension never shipped a v1 build, and the migrate-on-read write-back it
// once carried raced fresh seeds and permanently shadowed later flat writes. Anything that
// still produces a flat {baseUrl, token} shape (the E2E fixtures, the try harness) translates
// to v2 at the seam.

import { DEFAULT_THEME, THEMES, type Theme } from "@/lib/store";

// Re-exported so panel code has ONE import for everything appearance: the theme list is the web
// UI's own (frontend/src/lib/store), which is the point — a palette added to index.css and named
// there is offered by the panel with no edit here. The panel does NOT reuse the web UI's Mode: see
// below. Nothing else in the extension reaches into the frontend's store.
export type { Theme };
export { DEFAULT_THEME, THEMES };

/**
 * The panel's light/dark axis. The web UI's is a plain light|dark toggle because its choice is
 * made in a window the reader already owns; the panel is a strip inside somebody else's browser,
 * so it keeps "system" as a first-class value — that is what it did before it had a choice at
 * all, and it must stay the default so an existing install looks unchanged.
 */
export type Mode = "system" | "light" | "dark";

/** The mode values, in the order the picker offers them. */
export const MODES = [
  { name: "system", label: "System" },
  { name: "light", label: "Light" },
  { name: "dark", label: "Dark" },
] as const;

export interface Backend {
  id: string;
  name: string;
  baseUrl: string;
  token: string;
}

export interface Settings {
  backends: Backend[];
  activeBackendId: string;
  pageContextEnabled: boolean;
  /** When on (and page context is on), also attach the page's visible text. */
  pageTextEnabled: boolean;
  /** The named colour set, stamped on <html data-theme>. The same list the web UI offers. */
  theme: Theme;
  /** Light/dark, or "system" to follow the OS. */
  mode: Mode;
}

export const DEFAULT_BASE_URL = "http://127.0.0.1:2222";

/** The backend synthesized when nothing is stored (fresh install, no legacy keys). */
const FALLBACK_BACKEND: Backend = { id: "default", name: "Default", baseUrl: DEFAULT_BASE_URL, token: "" };

export const DEFAULT_SETTINGS: Settings = {
  backends: [{ ...FALLBACK_BACKEND }],
  activeBackendId: FALLBACK_BACKEND.id,
  pageContextEnabled: false,
  pageTextEnabled: false,
  theme: DEFAULT_THEME,
  mode: "system",
};

// The v2 storage keys: the only keys read, written, or watched.
const V2_KEYS = ["backends", "activeBackendId", "pageContextEnabled", "pageTextEnabled", "theme", "mode"] as const;
const WATCH_KEYS: string[] = [...V2_KEYS];

// Hosts allowed to use plain http (loopback only). Everything else is "remote"
// and must use https, because extension pages block mixed (http) content.
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);

/**
 * Canonical form of a stabbur base URL for `apiFetch(baseUrl + path)`: trimmed, no
 * trailing slashes. A pasted "http://127.0.0.1:2222/" would otherwise produce
 * "//api/status", which Starlette does not match (every call 404s).
 */
export function normalizeBaseUrl(raw: string): string {
  return raw.trim().replace(/\/+$/, "");
}

/**
 * Validate a stabbur base URL. Returns null when acceptable, otherwise a
 * human-readable reason. Accepts any http(s) URL, but requires https for a
 * non-loopback (remote/cloud) host — an extension page can't fetch plain http
 * from a remote origin (mixed-content block).
 */
export function validateBaseUrl(raw: string): string | null {
  const value = raw.trim();
  if (!value) return "Enter a stabbur base URL (e.g. http://127.0.0.1:2222).";
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return "Enter a valid URL, e.g. http://127.0.0.1:2222 or https://stabbur.example.com.";
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return "Base URL must start with http:// or https://.";
  }
  const isLoopback = LOOPBACK_HOSTS.has(url.hostname);
  if (url.protocol === "http:" && !isLoopback) {
    return "Remote stabbur requires https (extension pages block mixed content). Use https:// or an SSH tunnel to 127.0.0.1.";
  }
  return null;
}

/** The active backend by id, falling back to the first backend (never undefined). */
export function activeBackend(s: Settings): Backend {
  return s.backends.find((b) => b.id === s.activeBackendId) ?? s.backends[0] ?? { ...FALLBACK_BACKEND };
}

/** Whether a stored value is a well-formed, non-empty backends array. */
function isBackendArray(value: unknown): value is Backend[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every(
      (b) =>
        b !== null &&
        typeof b === "object" &&
        typeof (b as Backend).id === "string" &&
        typeof (b as Backend).name === "string" &&
        typeof (b as Backend).baseUrl === "string" &&
        typeof (b as Backend).token === "string",
    )
  );
}

/**
 * A stored theme name, validated against THEMES. Never trusted: the panel stamps this straight
 * onto <html data-theme>, and a name no palette answers to would paint a theme that does not
 * exist (index.css has a block per name and no catch-all). Same rule for the mode.
 */
function readTheme(value: unknown): Theme {
  return THEMES.some((t) => t.name === value) ? (value as Theme) : DEFAULT_SETTINGS.theme;
}

function readMode(value: unknown): Mode {
  return MODES.some((m) => m.name === value) ? (value as Mode) : DEFAULT_SETTINGS.mode;
}

/**
 * Load settings, filling any missing key with its default. Pure read/validate — never writes
 * back (a migrate-on-read write-back once raced fresh seeds and shadowed later writes). When no
 * valid v2 backends are stored, fall back to the built-in default backend.
 */
export async function getSettings(): Promise<Settings> {
  const stored = await chrome.storage.local.get([...WATCH_KEYS]);

  const pageContextEnabled =
    typeof stored.pageContextEnabled === "boolean" ? stored.pageContextEnabled : DEFAULT_SETTINGS.pageContextEnabled;
  const pageTextEnabled =
    typeof stored.pageTextEnabled === "boolean" ? stored.pageTextEnabled : DEFAULT_SETTINGS.pageTextEnabled;
  const theme = readTheme(stored.theme);
  const mode = readMode(stored.mode);

  if (isBackendArray(stored.backends)) {
    const backends = stored.backends;
    const activeBackendId =
      typeof stored.activeBackendId === "string" && backends.some((b) => b.id === stored.activeBackendId)
        ? stored.activeBackendId
        : backends[0].id;
    return { backends, activeBackendId, pageContextEnabled, pageTextEnabled, theme, mode };
  }

  return {
    backends: DEFAULT_SETTINGS.backends.map((b) => ({ ...b })),
    activeBackendId: DEFAULT_SETTINGS.activeBackendId,
    pageContextEnabled,
    pageTextEnabled,
    theme,
    mode,
  };
}

/** Persist a partial settings patch (v2 keys only). */
export async function setSettings(patch: Partial<Settings>): Promise<void> {
  await chrome.storage.local.set(patch);
}

/** Subscribe to settings changes; returns an unsubscribe function. */
export function watchSettings(cb: (settings: Settings) => void): () => void {
  const listener = (changes: Record<string, chrome.storage.StorageChange>, area: string): void => {
    if (area !== "local") return;
    if (!WATCH_KEYS.some((k) => k in changes)) return;
    void getSettings().then(cb);
  };
  chrome.storage.onChanged.addListener(listener);
  return () => chrome.storage.onChanged.removeListener(listener);
}

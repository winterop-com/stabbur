// Tracks the active browser tab's URL for the TargetBanner's tab-match display.
// Extension/internal pages are ignored so the last real web tab stays "current"
// while the user interacts with the side panel.

export type TabMatch = "matched" | "mismatch" | "unknown";

function isTrackable(url: string | undefined): url is string {
  if (!url) return false;
  return !url.startsWith("chrome-extension://") && !url.startsWith("chrome://") && !url.startsWith("about:");
}

let current: string | null = null;
const listeners = new Set<(url: string | null) => void>();
let started = false;

function set(url: string | null): void {
  if (url === current) return;
  current = url;
  for (const cb of listeners) cb(current);
}

function adopt(url: string | undefined): void {
  if (isTrackable(url)) set(url);
}

async function refreshFromActive(): Promise<void> {
  try {
    const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    adopt(tabs[0]?.url);
  } catch {
    // No tabs permission / no window — leave last-seen value.
  }
}

function start(): void {
  if (started) return;
  started = true;
  chrome.tabs.onActivated.addListener(() => void refreshFromActive());
  chrome.tabs.onUpdated.addListener((_tabId, changeInfo, tab) => {
    // tab.active is PER-WINDOW: adopting tab.url directly would let an auto-refreshing page
    // in a background window hijack the tracked URL away from what the user is looking at.
    // Re-query the last-focused window instead so only its active tab can win.
    if ((changeInfo.url || changeInfo.status === "complete") && tab.active) {
      void refreshFromActive();
    }
  });
  chrome.windows.onFocusChanged.addListener(() => void refreshFromActive());
  void refreshFromActive();
}

/** Subscribe to active-tab URL changes; fires immediately with the current value. */
export function subscribeTabUrl(cb: (url: string | null) => void): () => void {
  start();
  listeners.add(cb);
  cb(current);
  return () => listeners.delete(cb);
}

/** Current tracked web-tab URL (null before the first trackable tab is seen). */
export function getTabUrl(): string | null {
  return current;
}

function normalizeOrigin(u: URL): string {
  return `${u.protocol}//${u.host}`.toLowerCase();
}

/**
 * Does `tabUrl` fall under `baseUrl`? Compares normalized origin, then requires
 * the tab path to start with the base path (base "/" matches anything).
 */
export function match(tabUrl: string | null, baseUrl: string | null): TabMatch {
  if (!tabUrl || !baseUrl) return "unknown";
  let tab: URL;
  let base: URL;
  try {
    tab = new URL(tabUrl);
    base = new URL(baseUrl);
  } catch {
    return "unknown";
  }
  if (normalizeOrigin(tab) !== normalizeOrigin(base)) return "mismatch";
  const basePath = base.pathname.replace(/\/+$/, "");
  if (basePath === "" || tab.pathname === basePath || tab.pathname.startsWith(`${basePath}/`)) {
    return "matched";
  }
  return "mismatch";
}

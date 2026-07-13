import { useEffect, useRef, useState } from "react";
import { Settings as SettingsIcon } from "lucide-react";
import { configureApi } from "@/lib/http";
import { flavorTitle } from "../../lib/flavor";
import {
  createConnection,
  defaultConnectionDeps,
  type Connection,
  type ConnectionSnapshot,
} from "../../lib/connection";
import { getAssistant, type AssistantInfo } from "../../lib/assistantApi";
import { activeBackend, normalizeBaseUrl, setSettings, watchSettings, type Settings } from "../../lib/settings";
import { getTabUrl, subscribeTabUrl } from "../../lib/tabTarget";
import { collect, formatPageContext } from "../../lib/pageContext";
import { formatSessionContext, whoAmIResolved, type SessionResult } from "../../lib/sessionReads";
import { ConnectionGate } from "../../components/ConnectionGate";
import { SettingsView } from "../../components/SettingsView";
import { TargetBanner } from "../../components/TargetBanner";
import type { BindBackendTarget } from "../../components/BindFlow";
import { ChatView } from "../../components/ChatView";

async function activeTabId(): Promise<number | null> {
  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    return tab?.id ?? null;
  } catch {
    return null;
  }
}

// One line naming the credentials the tools authenticate as — distinct from the browser session
// user, so the model doesn't conflate "who is viewing the page" with "who the tools act as".
function formatToolAccount(a: AssistantInfo): string {
  const name = a.name ?? "assistant";
  const auth = a.auth ?? "unknown auth";
  const mode = a.readonly ? "read-only" : "read-write";
  const at = a.base_url ? ` at ${a.base_url}` : "";
  return `Tool account: "${name}"${at} (${auth}, ${mode})`;
}

interface PanelAppProps {
  initialSettings: Settings;
}

const EMPTY_SNAPSHOT: ConnectionSnapshot = {
  phase: "connecting",
  status: null,
  error: null,
  guardBlocked: false,
  loadStartedAt: null,
  loadDeadline: null,
};

export function PanelApp({ initialSettings }: PanelAppProps) {
  const [settings, setSettingsState] = useState<Settings>(initialSettings);
  const [snapshot, setSnapshot] = useState<ConnectionSnapshot>(EMPTY_SNAPSHOT);
  const [showSettings, setShowSettings] = useState(false);
  const [assistant, setAssistant] = useState<AssistantInfo | null>(null);
  const [tabUrl, setTabUrl] = useState<string | null>(getTabUrl());
  const connRef = useRef<Connection | null>(null);
  const extensionId = chrome.runtime.id;
  const active = activeBackend(settings);

  // Own the connection for the component lifetime (StrictMode-safe: created and
  // disposed per mount).
  useEffect(() => {
    const conn = createConnection(defaultConnectionDeps());
    connRef.current = conn;
    const unsub = conn.subscribe(setSnapshot);
    return () => {
      unsub();
      conn.dispose();
      connRef.current = null;
    };
  }, []);

  // Reconfigure the shared client + (re)connect whenever the target changes. The assistant
  // record belongs to the old server the moment the target changes or the connection drops —
  // clear it so a stale banner (or its base_url feeding session probes) can't outlive it.
  useEffect(() => {
    configureApi({ baseUrl: normalizeBaseUrl(active.baseUrl), token: active.token || null });
    setAssistant(null);
    connRef.current?.retry();
  }, [active.id, active.baseUrl, active.token]);

  // Keep settings in sync with storage (edits here or elsewhere) — the single state-update
  // path; saveSettings only writes storage and lets this watcher deliver the new value.
  useEffect(() => watchSettings(setSettingsState), []);

  // Track the active web tab for the TargetBanner.
  useEffect(() => subscribeTabUrl(setTabUrl), []);

  // Load assistant metadata once the server is ready (null = generic mode); a connection
  // that leaves ready drops the record (it describes a server we are no longer talking to).
  useEffect(() => {
    if (snapshot.phase !== "ready") {
      setAssistant(null);
      return;
    }
    let cancelled = false;
    getAssistant(false)
      .then((a) => {
        if (!cancelled) setAssistant(a);
      })
      .catch(() => {
        if (!cancelled) setAssistant(null);
      });
    return () => {
      cancelled = true;
    };
  }, [snapshot.phase]);

  async function saveSettings(patch: Partial<Settings>): Promise<void> {
    // No direct setSettingsState: the watchSettings storage listener is the single update
    // path, so a save is one write + one delivered change instead of two state updates.
    await setSettings(patch);
  }

  // Token edits from the connection gate belong to the active backend now (token is
  // per-backend, not a top-level field); write it back through the same storage path.
  function saveActiveToken(token: string): void {
    const backends = settings.backends.map((b) => (b.id === active.id ? { ...b, token } : b));
    void saveSettings({ backends });
  }

  // Session reads are script injections + up to 4 same-origin fetches against the target
  // site; cache per (tab, url, target) so every chat send doesn't re-probe /api/me.
  const sessionCache = useRef<{ key: string; result: SessionResult; at: number } | null>(null);
  const SESSION_TTL_MS = 60_000;

  async function cachedWhoAmI(tabId: number, force: boolean): Promise<SessionResult> {
    // No probe -> generic backend: never inject a session read at all.
    const probe = assistant?.probe ?? null;
    if (!probe) return null;
    const key = `${active.id}|${tabId}|${tabUrl ?? ""}|${assistant?.base_url ?? ""}`;
    const hit = sessionCache.current;
    if (!force && hit && hit.key === key && Date.now() - hit.at < SESSION_TTL_MS) return hit.result;
    const result = await whoAmIResolved(tabId, tabUrl, assistant?.base_url ?? null, probe);
    // Never cache a missing-host-access result: the user can grant access at any moment (the
    // "Who am I here?" click, a bind Confirm), and the passive paths should recover immediately.
    if (result && "error" in result && result.error === "no_access") return result;
    sessionCache.current = { key, result, at: Date.now() };
    return result;
  }

  // Build the page-context block for a chat turn: page url/title/selection, the signed-in
  // browser-session user (when a probe declared one), and the tool account the assistant runs
  // as — the model is told these two identities are distinct.
  async function getContextBlock(): Promise<string | null> {
    const tabId = await activeTabId();
    if (tabId === null) return null;
    const ctx = await collect(tabId, settings.pageTextEnabled);
    const parts: string[] = [];
    if (ctx) parts.push(formatPageContext(ctx));
    const session = await cachedWhoAmI(tabId, false);
    if (session && !("error" in session)) parts.push(formatSessionContext(session));
    if (assistant) parts.push(formatToolAccount(assistant));
    return parts.length ? parts.join("\n\n") : null;
  }

  async function onWhoAmI(): Promise<SessionResult> {
    const tabId = await activeTabId();
    if (tabId === null) return null;
    return cachedWhoAmI(tabId, true); // explicit button click always re-probes
  }

  // Passive session read for the auto-probe on panel open and the bind flow's pre-mint gate: reuse
  // the shared 60s cache so one panel-open resolves the session once instead of force-probing three
  // times (auto-probe + gate + save).
  async function probeSession(): Promise<SessionResult> {
    const tabId = await activeTabId();
    if (tabId === null) return null;
    return cachedWhoAmI(tabId, false);
  }

  // Snapshot the active heim backend when a bind flow starts, so a mid-flow backend switch can't
  // redirect the minted token to a different server (BindFlow freezes this at consent-confirm).
  function captureTarget(): BindBackendTarget {
    const b = activeBackend(settings);
    return { backendId: b.id, baseUrl: normalizeBaseUrl(b.baseUrl), token: b.token || null };
  }

  if (showSettings) {
    return (
      <SettingsView
        settings={settings}
        extensionId={extensionId}
        onSave={(patch) => {
          void saveSettings(patch);
          setShowSettings(false);
        }}
        onClose={() => setShowSettings(false)}
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-[var(--border)] px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <h1 className="text-sm font-semibold">{flavorTitle()}</h1>
          {settings.backends.length > 1 ? (
            <select
              data-testid="backend-switcher"
              value={active.id}
              onChange={(e) => void saveSettings({ activeBackendId: e.target.value })}
              className="max-w-[10rem] truncate rounded border border-[var(--border)] bg-[var(--background)] px-1.5 py-0.5 text-xs"
            >
              {settings.backends.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name || b.baseUrl}
                </option>
              ))}
            </select>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => setShowSettings(true)}
          aria-label="Settings"
          className="rounded p-1 text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--foreground)]"
        >
          <SettingsIcon className="h-4 w-4" />
        </button>
      </header>

      {snapshot.phase === "ready" ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <TargetBanner
            assistant={assistant}
            tabUrl={tabUrl}
            backendId={active.id}
            captureTarget={captureTarget}
            onVerify={() =>
              getAssistant(true).then((a) => {
                setAssistant(a);
                return a;
              })
            }
            onWhoAmI={onWhoAmI}
            probeSession={probeSession}
            getActiveTabId={activeTabId}
          />
          <div className="min-h-0 flex-1">
            <ChatView
              key={active.id}
              backendId={active.id}
              pageContextEnabled={settings.pageContextEnabled}
              onTogglePageContext={(v) => void saveSettings({ pageContextEnabled: v })}
              pageTextEnabled={settings.pageTextEnabled}
              onTogglePageText={(v) => void saveSettings({ pageTextEnabled: v })}
              getContextBlock={getContextBlock}
            />
          </div>
        </div>
      ) : (
        <div className="flex-1">
          <ConnectionGate
            snapshot={snapshot}
            baseUrl={active.baseUrl}
            extensionId={extensionId}
            onRetry={() => connRef.current?.retry()}
            onLoadModel={(name) => connRef.current?.loadModel(name)}
            onSaveToken={(token) => saveActiveToken(token)}
            onOpenSettings={() => setShowSettings(true)}
          />
        </div>
      )}
    </div>
  );
}

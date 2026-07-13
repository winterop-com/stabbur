import { useEffect, useMemo, useRef, useState } from "react";
import { Settings as SettingsIcon } from "lucide-react";
import { configureApi } from "@/lib/http";
import { flavorTitle } from "../../lib/flavor";
import {
  createConnection,
  defaultConnectionDeps,
  type Connection,
  type ConnectionSnapshot,
} from "../../lib/connection";
import { getAssistant, getAssistants, getAssistantTarget, type AssistantTarget } from "../../lib/assistantApi";
import { activeBackend, normalizeBaseUrl, setSettings, watchSettings, type Settings } from "../../lib/settings";
import { getTabUrl, selectTarget, subscribeTabUrl } from "../../lib/tabTarget";
import { collect, formatPageContext } from "../../lib/pageContext";
import { requestHostAccess } from "../../lib/hostAccess";
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
function formatToolAccount(a: AssistantTarget): string {
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
  // The backend's assistant registry (one entry in the single-target / 404-compat case). `compat` is
  // set when it came from the old /api/assistant route so bind/verify use the un-scoped endpoints.
  const [targets, setTargets] = useState<AssistantTarget[]>([]);
  const [compat, setCompat] = useState(false);
  // The user's manual tie resolution: when >1 target matches the tab, they pick one here. Reset on any
  // tab / registry / backend change so a stale pick can't leak across contexts.
  const [pickedTargetId, setPickedTargetId] = useState<string | null>(null);
  const [tabUrl, setTabUrl] = useState<string | null>(getTabUrl());
  const connRef = useRef<Connection | null>(null);
  const extensionId = chrome.runtime.id;
  const active = activeBackend(settings);

  // Client-side target selection (the twin of heim.targets.select): recomputed on tab / registry
  // change. `matches` is the ranked list; `selected` is the single unambiguous pick (null on a tie).
  const selection = useMemo(() => selectTarget(tabUrl, targets), [tabUrl, targets]);
  const matches = selection.matches;
  // The target actually acted on: the unambiguous pick, or the user's tie resolution. Null mid-tie.
  const activeTarget: AssistantTarget | null =
    selection.selected ?? (pickedTargetId ? matches.find((t) => t.id === pickedTargetId) ?? null : null);
  const primaryTarget = targets[0] ?? null;
  // What the banner renders: the acted-on target, else — with no match — the primary, whose name drives
  // the collapsed "Not a <name> page." one-liner. Null only mid-tie, where the picker stands in for it.
  const bannerTarget: AssistantTarget | null = activeTarget ?? (matches.length === 0 ? primaryTarget : null);

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

  // Reconfigure the shared client + (re)connect whenever the backend changes. The registry belongs to
  // the old server the moment the backend changes or the connection drops — clear it so a stale banner
  // (or a target base_url feeding session probes) can't outlive it.
  useEffect(() => {
    configureApi({ baseUrl: normalizeBaseUrl(active.baseUrl), token: active.token || null });
    setTargets([]);
    setCompat(false);
    connRef.current?.retry();
  }, [active.id, active.baseUrl, active.token]);

  // Drop any manual tie-pick when the tab, registry, or backend changes (a pick is only valid for the
  // exact set of matches it was made against).
  useEffect(() => {
    setPickedTargetId(null);
  }, [tabUrl, targets, active.id]);

  // Keep settings in sync with storage (edits here or elsewhere) — the single state-update
  // path; saveSettings only writes storage and lets this watcher deliver the new value.
  useEffect(() => watchSettings(setSettingsState), []);

  // Track the active web tab for the TargetBanner.
  useEffect(() => subscribeTabUrl(setTabUrl), []);

  // Load the assistant registry once the server is ready (empty = generic mode); a connection that
  // leaves ready drops it (it describes a server we are no longer talking to).
  useEffect(() => {
    if (snapshot.phase !== "ready") {
      setTargets([]);
      setCompat(false);
      return;
    }
    let cancelled = false;
    getAssistants()
      .then((reg) => {
        if (!cancelled) {
          setTargets(reg.targets);
          setCompat(reg.compat === true);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setTargets([]);
          setCompat(false);
        }
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
    // No probe (or no selected target) -> generic backend: never inject a session read at all.
    const probe = activeTarget?.probe ?? null;
    if (!probe) return null;
    // Key includes the target id: two targets on one backend probe different instances, so their
    // session reads must never share a cache entry.
    const key = `${active.id}|${activeTarget?.id ?? ""}|${tabId}|${tabUrl ?? ""}|${activeTarget?.base_url ?? ""}`;
    const hit = sessionCache.current;
    if (!force && hit && hit.key === key && Date.now() - hit.at < SESSION_TTL_MS) return hit.result;
    const result = await whoAmIResolved(tabId, tabUrl, activeTarget?.base_url ?? null, probe);
    // Never cache a missing-host-access result: the user can grant access at any moment (the
    // "Who am I here?" click, a bind Confirm), and the passive paths should recover immediately.
    if (result && "error" in result && result.error === "no_access") return result;
    sessionCache.current = { key, result, at: Date.now() };
    return result;
  }

  // Build the page-context block for a chat turn: page url/title/selection, the signed-in
  // browser-session user (when a probe declared one), and the tool account the assistant runs
  // as — the model is told these two identities are distinct. `pageMissing` reports that the
  // PAGE part could not be captured (no host access to the site) so the composer chip can say
  // so instead of claiming the page was attached when only identity lines made it in.
  async function getContextBlock(): Promise<{ text: string | null; pageMissing: boolean }> {
    const tabId = await activeTabId();
    if (tabId === null) return { text: null, pageMissing: false };
    const ctx = await collect(tabId, settings.pageTextEnabled);
    const parts: string[] = [];
    if (ctx && ctx !== "no_access") parts.push(formatPageContext(ctx));
    const session = await cachedWhoAmI(tabId, false);
    if (session && !("error" in session)) parts.push(formatSessionContext(session));
    if (activeTarget) parts.push(formatToolAccount(activeTarget));
    return { text: parts.length ? parts.join("\n\n") : null, pageMissing: ctx === "no_access" };
  }

  // First await on the Send click (a user gesture): turn a missing host grant for the current
  // site into the durable optional permission so page capture works. Raced against a short
  // deadline — an unanswered native prompt must never block the send; the turn then proceeds
  // without the page and the chip reports it honestly.
  async function ensurePageAccess(): Promise<void> {
    const url = getTabUrl();
    if (!url) return;
    await Promise.race([
      requestHostAccess(url),
      new Promise<boolean>((resolve) => setTimeout(() => resolve(false), 3000)),
    ]);
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

  // Re-verify one target (?verify=1) and lift the refreshed record into the registry so the banner
  // re-renders with its `verified` result. Uses the per-target route, or the compat /api/assistant one
  // when the registry came from the 404 fallback.
  async function verifyTarget(targetId: string): Promise<AssistantTarget | null> {
    let updated: AssistantTarget | null;
    if (compat) {
      const one = await getAssistant(true);
      updated = one ? { ...one, id: targetId, mcp_servers: [] } : null;
    } else {
      updated = await getAssistantTarget(targetId, true);
    }
    if (updated) {
      const u = updated;
      setTargets((prev) => prev.map((t) => (t.id === u.id ? u : t)));
    }
    return updated;
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
          {matches.length > 1 ? (
            <div className="flex items-center gap-2 border-b border-[var(--border)] bg-[var(--muted)] px-3 py-2 text-xs">
              <span className="shrink-0 text-[var(--muted-foreground)]">{matches.length} targets match:</span>
              <select
                data-testid="target-picker"
                value={activeTarget?.id ?? ""}
                onChange={(e) => setPickedTargetId(e.target.value || null)}
                className="min-w-0 flex-1 truncate rounded border border-[var(--border)] bg-[var(--background)] px-1.5 py-0.5 text-xs"
              >
                <option value="" disabled>
                  Choose a target…
                </option>
                {matches.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name || t.base_url || t.id}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
          {bannerTarget ? (
            <TargetBanner
              target={bannerTarget}
              tabUrl={tabUrl}
              backendId={active.id}
              compat={compat}
              captureTarget={captureTarget}
              onVerify={verifyTarget}
              onWhoAmI={onWhoAmI}
              probeSession={probeSession}
              getActiveTabId={activeTabId}
            />
          ) : null}
          <div className="min-h-0 flex-1">
            <ChatView
              key={active.id}
              backendId={active.id}
              target={activeTarget?.id ?? null}
              pageContextEnabled={settings.pageContextEnabled}
              onTogglePageContext={(v) => void saveSettings({ pageContextEnabled: v })}
              pageTextEnabled={settings.pageTextEnabled}
              onTogglePageText={(v) => void saveSettings({ pageTextEnabled: v })}
              getContextBlock={getContextBlock}
              onEnsurePageAccess={ensurePageAccess}
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

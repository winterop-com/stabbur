import { useEffect, useMemo, useRef, useState } from "react";
import {
  BadgeCheck,
  ChevronDown,
  ExternalLink,
  Loader2,
  LogIn,
  Pencil,
  ShieldCheck,
  User,
  UserCheck,
} from "lucide-react";
import type { AssistantTarget } from "../lib/assistantApi";
import { TAB_MATCHED, TAB_MISMATCH_DETAIL, TAB_UNKNOWN, tabMismatchOneLiner } from "../lib/bannerText";
import { match } from "../lib/tabTarget";
import { formatSession, type SessionInfo, type SessionResult } from "../lib/sessionReads";
import { executeRevoke, parseRecipe, substitute } from "../lib/bindRecipe";
import { requestHostAccess } from "../lib/hostAccess";
import {
  clearBindDismissal,
  clearBinding,
  getBindDismissal,
  getBinding,
  getBindingStale,
  setBindDismissal,
  setBinding as persistBinding,
  watchBinding,
  type Binding,
  type BindDismissal,
} from "../lib/binding";
import { postUnbind } from "../lib/bindApi";
import { BindFlow, type BindBackendTarget } from "./BindFlow";

interface TargetBannerProps {
  /** The selected assistant target this banner acts on (null when there is nothing to act on —
   *  mid-tie or no registry; the parent renders the picker / nothing then). */
  target: AssistantTarget | null;
  tabUrl: string | null;
  /** The active backend id (with the target id, scopes the per-(backend,target) binding record). */
  backendId: string;
  /** Bumped by the parent whenever a host-access grant may have landed (e.g. a Send-gesture
   *  request was allowed) so the auto-probe re-runs instead of displaying a stale no_access. */
  probeEpoch?: number;
  /** True when the registry came from the 404-compat path: bind / verify use the /api/assistant*
   *  routes instead of /api/assistants/{id}. */
  compat: boolean;
  /** Snapshot the active stabbur backend for a bind flow (passed through to BindFlow). */
  captureTarget: () => BindBackendTarget;
  /** Re-fetch this target's metadata with ?verify=1; lifts the updated record into the parent and
   *  returns it. */
  onVerify: (targetId: string) => Promise<AssistantTarget | null>;
  /** Read the user's session on the active tab (explicit "Who am I here?" button — force-probes,
   *  bypassing the cache). */
  onWhoAmI: () => Promise<SessionResult>;
  /** Passive session read (uses the shared 60s cache): the auto-probe on panel open and the bind
   *  flow's pre-mint gate both go through this so one panel-open resolves the session once. */
  probeSession: () => Promise<SessionResult>;
  /** Resolve the active web tab's id (mint/revoke injection target). */
  getActiveTabId: () => Promise<number | null>;
}

// Friendly key/value view of a verify payload: keep scalar fields, and inline the fields of
// any string value that parses as a JSON object (the common CLI envelope shape, e.g.
// {exit_code, stdout: "<json>", stderr}). Empty/null values are dropped. Returns null when
// nothing displayable remains (the caller falls back to the raw JSON dump).
function flattenVerifyData(data: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!data) return null;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(data)) {
    if (typeof value === "string") {
      try {
        const parsed: unknown = JSON.parse(value);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          Object.assign(out, parsed as Record<string, unknown>);
          continue;
        }
      } catch {
        // Not JSON -- keep the string itself below.
      }
    }
    if (value !== "" && value !== null && typeof value !== "object") out[key] = value;
  }
  return Object.keys(out).length > 0 ? out : null;
}

// verified.ok only means "the verify tool ran without raising"; tools that report failure
// in-band use generic conventions this checks without any domain knowledge: a numeric
// exit_code != 0, or an explicit ok/success === false in the (flattened) payload.
function reportsFailure(flat: Record<string, unknown> | null): boolean {
  if (!flat) return false;
  if (typeof flat.exit_code === "number" && flat.exit_code !== 0) return true;
  return flat.ok === false || flat.success === false;
}

function openUrl(url: string): void {
  void chrome.tabs.create({ url });
}

/** Assistant-target header: metadata, verification, tab-match, session info, and the login binding. */
export function TargetBanner({
  target,
  tabUrl,
  backendId,
  compat,
  probeEpoch = 0,
  captureTarget,
  onVerify,
  onWhoAmI,
  probeSession,
  getActiveTabId,
}: TargetBannerProps) {
  // Local alias: the rest of the banner treats the selected target as "the assistant". `targetId` (the
  // registry id) joins backendId to scope the per-target binding record and the bind/verify routes.
  const assistant = target;
  const targetId = target?.id ?? null;
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [session, setSession] = useState<SessionResult>(null);
  const [loadingSession, setLoadingSession] = useState(false);
  // The target block is a compact status line by default in EVERY state; the chevron (target-expand)
  // reveals the metadata, verify, session, and full binding controls. Collapsed on each mount (no
  // persistence) so the block never eats the ~360px panel.
  const [expanded, setExpanded] = useState(false);
  const [binding, setBinding] = useState<Binding | null>(null);
  const [bindingStale, setBindingStale] = useState(false);
  const [dismissed, setDismissed] = useState<BindDismissal | null>(null);
  // Auto-offer state gates on the initial storage load (binding + stale + dismissal), so a probe
  // that resolves before storage does can't pop the consent card ahead of a remembered decline.
  const [bindLoaded, setBindLoaded] = useState(false);
  const [showBindFlow, setShowBindFlow] = useState(false);
  // How the current bind flow was opened: "auto" (proactive offer — a cancel is remembered as a
  // decline), "manual" (the button — a cancel is just a close), or "upgrade" (the explicit
  // write-scope re-mint — writes pre-checked, revokes the old read-only token on success). Null when
  // no flow is open.
  const [bindFlowSource, setBindFlowSource] = useState<"auto" | "manual" | "upgrade" | null>(null);
  const [confirmUnbind, setConfirmUnbind] = useState(false);
  const [unbinding, setUnbinding] = useState(false);
  // One auto-probe per (backend, tab url): drift re-check on panel open without re-probing on every
  // render (probeSession's identity changes each parent render).
  const autoProbedKey = useRef<string | null>(null);
  // probeSession is a fresh closure each parent render; hold it in a ref so the auto-probe effect
  // can call the latest one without listing it as a dep (which would re-run — and cancel — the probe
  // on every unrelated re-render).
  const probeSessionRef = useRef(probeSession);
  probeSessionRef.current = probeSession;

  // The parent owns the assistant record now (onVerify lifts the refreshed one up), so there is no
  // local shadow to sync — just clear a stale verify error when the record swaps.
  useEffect(() => {
    setVerifyError(null);
  }, [assistant]);

  // Load + track the per-(backend,target) binding; reset the transient bind UI on a backend/target
  // switch. Skipped when there is no target to act on (mid-tie / no registry).
  useEffect(() => {
    setShowBindFlow(false);
    setBindFlowSource(null);
    setConfirmUnbind(false);
    setBindLoaded(false);
    autoProbedKey.current = null;
    if (targetId === null) {
      setBinding(null);
      setBindingStale(false);
      setDismissed(null);
      return;
    }
    void Promise.all([
      getBinding(backendId, targetId),
      getBindingStale(backendId, targetId),
      getBindDismissal(backendId, targetId),
    ]).then(([b, s, d]) => {
      setBinding(b);
      setBindingStale(s);
      setDismissed(d);
      setBindLoaded(true);
    });
    return watchBinding(backendId, targetId, (b, s) => {
      setBinding(b);
      setBindingStale(s);
    });
  }, [backendId, targetId]);

  const recipe = useMemo(() => parseRecipe(assistant?.bind ?? null), [assistant?.bind]);
  const verified = assistant?.verified ?? null;
  const flat = useMemo(() => (verified?.ok ? flattenVerifyData(verified.data) : null), [verified]);

  // Derived target/session state (computed from the nullable assistant so the auto-offer hooks
  // below can depend on it — hooks must run before the assistant===null early return).
  const baseUrl = assistant && typeof assistant.base_url === "string" ? assistant.base_url : null;
  const tab = match(tabUrl, baseUrl);
  const canBind = assistant?.can_bind === true && recipe !== null;
  const targetName = assistant?.name ?? "the instance";

  // Expired heuristics: a stale flag (session cookie evicted by the background worker), a verify
  // that came back unauthorized, a PAT past its absolute expiry, or the current tab now signed in
  // as a different user (compared username-to-username, or name-to-name when usernames are absent).
  const verifyExpired = verified?.ok === false && /401|unauthor|expired/i.test(verified.error ?? "");
  const sessionInfo = session && !("error" in session) ? session : null;
  const usernameMismatch = binding
    ? binding.username && sessionInfo?.username
      ? binding.username !== sessionInfo.username
      : binding.name && sessionInfo?.name
        ? binding.name !== sessionInfo.name
        : false
    : false;
  const expiresPassed = typeof binding?.expiresAt === "number" && Date.now() > binding.expiresAt;
  const expired = binding !== null && (bindingStale || verifyExpired || usernameMismatch || expiresPassed);

  // Explicit write-scope re-mint affordance: a PAT method scope is fixed at mint, so a cached
  // read-only token cannot escalate. Offer an upgrade only when the assistant is write-enabled and
  // the current binding is a PAT bind granted read-only. Never for a writes-scoped binding (no
  // downgrade — keep the widest granted) nor for the session/fallback mode (a cookie cannot be
  // method-scoped; its per-action confirmation gate is the guardrail). Needs a matched tab and a
  // mint recipe (re-minting runs in the tab).
  const canUpgradeWrites =
    binding !== null &&
    binding.writes !== true &&
    assistant?.readonly === false &&
    tab === "matched" &&
    recipe?.mint != null &&
    binding.mode !== recipe.fallbackMode;
  // The upgrade re-mints regardless, but only a binding that carries a credential id can have its
  // old read-only token auto-revoked. When there is none (older/probe-less bind), the upgrade card
  // says so instead of the false "the old token will be revoked" promise — and confirmMint's revoke
  // guard already skips a missing id, so nothing is silently half-done.
  const upgradeCredentialId = binding?.credentialId ?? "";

  // The auto-offer opens the SAME bind consent card as the manual button; drift (a binding whose
  // user no longer matches the tab) re-offers a rebind, and an expired-but-same-user binding
  // re-offers with rebind framing too (ROADMAP: auto-offer on "no (or stale) binding").
  const driftRebind = binding !== null && usernameMismatch;
  const offerHeadline = driftRebind
    ? `You are now signed in as ${sessionInfo?.username || sessionInfo?.name || "a different user"}; rebind?`
    : expired
      ? `Your ${targetName} login expired; rebind?`
      : `Use your ${targetName} login?`;

  // A single amber line that pierces the collapsed banner so an expired/drift/sign-in/no-access hint
  // is never hidden behind the chevron — the most urgent one wins. Suppressed while the bind card is
  // open (it already frames the same drift/expiry) and when expanded (the detail below covers it).
  const sessionError = session && "error" in session ? session.error : null;
  const warningLine = driftRebind
    ? `You are now signed in as ${sessionInfo?.username || sessionInfo?.name || "a different user"}; rebind?`
    : expired
      ? "Binding expired — rebind?"
      : sessionError === "unauthenticated"
        ? "Not signed in on this tab."
        : sessionError === "no_access"
          ? 'stabbur cannot read this page yet (API tools are unaffected) — click "Who am I here?" and allow page access.'
          : null;

  // Auto-probe on panel open against a matched tab: this is the drift re-check (compare the live
  // browser identity against binding.username) and the identity the auto-offer needs. Guarded to run
  // once per (backend, tab url). Depends on a stable `hasProbe` scalar (not the probe OBJECT, whose
  // identity changes when the parent re-fetches the assistant) so an unrelated re-fetch can't cancel
  // an in-flight probe. The key is marked ONLY after a completed, non-cancelled resolve — a
  // cancelled probe (tab navigation, assistant swap) must not permanently suppress re-probing.
  const hasProbe = assistant?.probe != null;
  // localProbeNonce: bumped after a successful bind (the bind Confirm gesture may have just granted
  // host access), so the guarded auto-probe re-runs instead of leaving a stale no_access line under
  // a freshly bound, working panel. probeEpoch does the same for grants landed by the parent (Send).
  const [localProbeNonce, setLocalProbeNonce] = useState(0);
  useEffect(() => {
    if (!hasProbe || tab !== "matched" || !tabUrl || targetId === null) return;
    const probeKey = `${backendId}|${targetId}|${tabUrl}|${probeEpoch}|${localProbeNonce}`;
    if (autoProbedKey.current === probeKey) return;
    let cancelled = false;
    setLoadingSession(true);
    void probeSessionRef
      .current()
      .then((r) => {
        if (!cancelled) {
          setSession(r);
          autoProbedKey.current = probeKey; // mark only now: a cancelled probe stays re-probable
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingSession(false);
      });
    return () => {
      cancelled = true;
    };
  }, [hasProbe, tab, tabUrl, backendId, targetId, probeEpoch, localProbeNonce]);

  // Proactive "use your login?" offer: on a matched tab with a live identity and no usable binding
  // (none, or one that drifted to a different user), open the consent card automatically — unless the
  // user already declined it for THIS same signed-in user (decline memory, keyed on backend + target
  // + username). Never mints on its own; the user still confirms in the card.
  useEffect(() => {
    if (!bindLoaded || !canBind || tab !== "matched" || showBindFlow) return;
    if (!sessionInfo || !sessionInfo.username) return; // need a real, named identity to offer/attribute
    const noBinding = binding === null;
    // Offer on: no binding, drift (different user now), or an expired/stale same-user binding.
    // A fresh binding for the still-signed-in user is silently reused (no nag).
    if (!noBinding && !driftRebind && !expired) return;
    const declined =
      dismissed !== null && dismissed.username === sessionInfo.username && dismissed.targetBaseUrl === (baseUrl ?? "");
    if (declined) return;
    setBindFlowSource("auto");
    setShowBindFlow(true);
  }, [bindLoaded, canBind, tab, showBindFlow, sessionInfo, binding, driftRebind, expired, dismissed, baseUrl]);

  if (assistant === null) return null;
  const active = assistant;
  const verifiedOk = verified?.ok === true && !reportsFailure(flat);

  async function verify(): Promise<AssistantTarget | null> {
    setVerifying(true);
    setVerifyError(null);
    try {
      // onVerify lifts the updated record into the parent's state (which re-renders us).
      return await onVerify(active.id);
    } catch (err) {
      // The verify request itself failed (server restart, 401, 500) -- distinct from a
      // verified.ok=false outcome, which comes back as data.
      setVerifyError(err instanceof Error ? err.message : String(err));
      return null;
    } finally {
      setVerifying(false);
    }
  }

  async function whoAmI(): Promise<void> {
    // First await on the click gesture: turn a transient/missing activeTab grant into the durable
    // optional host permission, so the probe (and every later injection) works regardless of how
    // the panel was opened. Silently true when already granted; a decline just falls through and
    // the probe reports no_access with its pointer copy.
    if (baseUrl) await requestHostAccess(baseUrl);
    setLoadingSession(true);
    try {
      setSession(await onWhoAmI());
    } finally {
      setLoadingSession(false);
    }
  }

  // Open the bind flow from the manual button/Rebind: an explicit re-engagement, so forget any
  // remembered decline (the auto-offer becomes eligible again).
  function openManualBind(): void {
    void clearBindDismissal(backendId, active.id);
    setDismissed(null);
    setBindFlowSource("manual");
    setShowBindFlow(true);
  }

  // Open the explicit write-scope re-mint: the same consent card, but framed as a re-mint with writes
  // pre-checked and the replacement notice. A cancel is just a close (like manual), so no decline
  // memory is written.
  function openUpgradeBind(): void {
    setBindFlowSource("upgrade");
    setShowBindFlow(true);
  }

  // Close the bind flow. A cancel of an AUTO offer is remembered as a decline for the current signed-in
  // user (so we don't re-nag on the next open); a cancel of a MANUAL open is just a close.
  function closeBindFlow(): void {
    if (bindFlowSource === "auto" && sessionInfo?.username) {
      const d: BindDismissal = {
        backendId,
        targetId: active.id,
        targetBaseUrl: baseUrl ?? "",
        username: sessionInfo.username,
      };
      void setBindDismissal(d);
      setDismissed(d);
    }
    setBindFlowSource(null);
    setShowBindFlow(false);
  }

  async function doUnbind(): Promise<void> {
    if (!binding) return;
    // First await on the click gesture: the in-tab revoke needs host access (see whoAmI above).
    // Best-effort — a decline only skips the revoke, never the unbind itself.
    if (baseUrl) await requestHostAccess(baseUrl);
    setUnbinding(true);
    try {
      // Best-effort: revoke the token in the tab's context before removing the profile.
      if (binding.credentialId && recipe && baseUrl && match(tabUrl, baseUrl) === "matched") {
        const tabId = await getActiveTabId();
        if (tabId !== null) {
          await executeRevoke(tabId, baseUrl, substitute(recipe.revokePath, { credentialId: binding.credentialId }));
        }
      }
      await postUnbind({ targetId: binding.targetId, compat }, binding.mode);
    } finally {
      // Remember the unbind as a decline for the still-signed-in user, so the auto-offer doesn't
      // immediately re-nag right after they chose to unbind; a later different user still re-offers,
      // and the manual button clears it.
      const who = sessionInfo?.username || binding.username;
      if (who) {
        const d: BindDismissal = {
          backendId: binding.backendId,
          targetId: binding.targetId,
          targetBaseUrl: baseUrl ?? "",
          username: who,
        };
        await setBindDismissal(d);
        setDismissed(d);
      }
      await clearBinding(binding.backendId, binding.targetId);
      setUnbinding(false);
      setConfirmUnbind(false);
    }
  }

  async function onBound(boundBackendId: string): Promise<void> {
    // Refresh the binding from the authoritative store BEFORE clearing showBindFlow. The `binding`
    // state otherwise only updates via the async watchBinding storage listener, which has not fired
    // yet — so clearing showBindFlow would re-run the auto-offer effect with binding still null and
    // immediately reopen the consent card (a card that never closes). Setting it here first means
    // the effect sees the fresh, same-user binding and stays silent. watchBinding remains the
    // ongoing sync.
    const b = await getBinding(boundBackendId, active.id);
    if (b) {
      setBinding(b);
      setBindingStale(false);
    }
    setShowBindFlow(false);
    setBindFlowSource(null);
    // The bind Confirm gesture requests host access, so a grant may have just landed: re-run the
    // auto-probe (fresh identity for drift; clears a stale pre-grant no_access line).
    setLocalProbeNonce((n) => n + 1);
    // A successful bind supersedes any remembered decline for this (backend, target).
    void clearBindDismissal(boundBackendId, active.id);
    setDismissed(null);
    const updated = await verify(); // re-probe: the freshly-bound credential changes what verify reports
    // Probe-less bind (no session read to name the user): adopt a username from the verify payload
    // when the stored binding has none, so "Acting as <user>" is not left blank.
    if (b && !b.username) {
      const vflat = updated?.verified?.ok ? flattenVerifyData(updated.verified.data) : null;
      const uname = vflat && typeof vflat.username === "string" ? vflat.username : "";
      if (uname) await persistBinding({ ...b, username: uname });
    }
  }

  return (
    <div className="space-y-2 border-b border-[var(--border)] bg-[var(--muted)] px-3 py-2 text-xs">
      {/* Compact header (always visible): name + scope, a short tab-state chip, the acting-as chip,
          and the chevron. Collapsed by default in every state so the block never eats the panel. */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1">
          <span className="truncate font-medium text-[var(--foreground)]">{active.name ?? "Assistant"}</span>
          {active.readonly ? (
            <span className="inline-flex items-center gap-1 rounded bg-[var(--accent)] px-1.5 py-0.5 text-xs text-[var(--muted-foreground)]">
              <ShieldCheck className="h-3 w-3" /> read-only
            </span>
          ) : null}
          {tab === "matched" ? (
            <span className="inline-flex items-center rounded px-1.5 py-0.5 text-xs text-emerald-600">
              {TAB_MATCHED}
            </span>
          ) : tab === "mismatch" ? (
            <span className="truncate text-xs text-[var(--muted-foreground)]">
              {tabMismatchOneLiner(active.name ?? "target")}
            </span>
          ) : (
            <span className="text-xs text-[var(--muted-foreground)]">{TAB_UNKNOWN}</span>
          )}
          {binding ? (
            <span
              data-testid="bind-acting-as"
              title={`Acting as your ${active.name ?? "target"} login — ${binding.writes ? "writes enabled" : "read-only"}`}
              className="inline-flex items-center gap-1 rounded bg-[var(--accent)] px-1.5 py-0.5 text-xs text-[var(--foreground)]"
            >
              <UserCheck className="h-3 w-3" /> Acting as {binding.username || "your account"}
            </span>
          ) : null}
        </div>
        <button
          type="button"
          data-testid="target-expand"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
          title={expanded ? "Hide target details" : "Show target details"}
          className="shrink-0 rounded p-0.5 text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--foreground)]"
        >
          <ChevronDown className={`h-4 w-4 transition-transform ${expanded ? "rotate-180" : ""}`} />
        </button>
      </div>

      {/* One amber line that pierces the collapse so an expired/drift/sign-in/no-access hint is never
          hidden. Suppressed while the bind card is open (it says the same) and when expanded (the full
          session/binding detail below covers it). */}
      {!expanded && !showBindFlow && warningLine ? <div className="text-amber-600">{warningLine}</div> : null}

      {/* Bind ACTION area — renders regardless of collapse: the auto-offered / manual consent card
          and its trigger are actions, not status. */}
      {showBindFlow || (canBind && tab === "matched" && !binding) ? (
        <div className="space-y-2">
          {!binding && !showBindFlow ? (
            <button
              type="button"
              data-testid="bind-use-my-login"
              onClick={openManualBind}
              className="inline-flex items-center gap-1 rounded border border-[var(--primary)] px-2 py-0.5 text-[var(--foreground)] hover:bg-[var(--accent)]"
            >
              <LogIn className="h-3 w-3" /> Use my login
            </button>
          ) : null}
          {showBindFlow && recipe && baseUrl ? (
            // key={bindFlowSource}: remount on a mode switch (manual <-> upgrade <-> auto) so the
            // fresh source's initialAllowWrites/replaceCredentialId props seed a NEW instance's
            // state. Without it, switching source on the mounted flow leaves allowWrites stale-false
            // while replaceCredentialId updates -> a read-only mint that still revokes the old token.
            <BindFlow
              key={bindFlowSource ?? "none"}
              assistant={active}
              route={{ targetId: active.id, compat }}
              recipe={recipe}
              basePath={baseUrl}
              captureTarget={captureTarget}
              resolveSession={probeSession}
              getActiveTabId={getActiveTabId}
              onBound={(id) => void onBound(id)}
              onCancel={closeBindFlow}
              headline={
                bindFlowSource === "auto"
                  ? offerHeadline
                  : bindFlowSource === "upgrade"
                    ? `Re-mint your ${targetName} token with write access?`
                    : undefined
              }
              initialAllowWrites={bindFlowSource === "upgrade"}
              replaceCredentialId={
                bindFlowSource === "upgrade" && upgradeCredentialId ? upgradeCredentialId : undefined
              }
              replaceUnrevocable={bindFlowSource === "upgrade" && !upgradeCredentialId}
            />
          ) : null}
        </div>
      ) : null}

      {/* Expanded detail (chevron): metadata, verification, session, and the full binding controls. */}
      {expanded ? (
        <div className="space-y-2">
          <div className="space-y-0.5 text-[var(--muted-foreground)]">
            {baseUrl ? (
              <button
                type="button"
                onClick={() => openUrl(baseUrl)}
                className="inline-flex items-center gap-1 hover:text-[var(--foreground)] hover:underline"
              >
                <span className="truncate">{baseUrl}</span>
                <ExternalLink className="h-3 w-3 shrink-0" />
              </button>
            ) : null}
            {active.auth ? <div>auth: {active.auth}</div> : null}
            {active.source ? <div>source: {active.source}</div> : null}
          </div>

          {tab === "mismatch" ? (
            <div className="space-y-0.5 text-amber-600">
              <div>{TAB_MISMATCH_DETAIL}</div>
              <div className="truncate">tab: {tabUrl}</div>
              <div className="truncate">target: {baseUrl}</div>
            </div>
          ) : null}

          {active.can_verify ? (
            <button
              type="button"
              onClick={() => void verify()}
              disabled={verifying}
              className="inline-flex items-center gap-1 rounded border border-[var(--border)] px-2 py-0.5 hover:bg-[var(--accent)] disabled:opacity-50"
            >
              {verifying ? <Loader2 className="h-3 w-3 animate-spin" /> : <BadgeCheck className="h-3 w-3" />}
              Verify
            </button>
          ) : null}

          {verifyError ? <div className="text-[var(--destructive)]">Verify request failed: {verifyError}</div> : null}
          {verified ? (
            <div className={verifiedOk ? "text-emerald-600" : "text-[var(--destructive)]"}>
              {verifiedOk
                ? "Verified."
                : verified.ok
                  ? "Verification reported a failure."
                  : `Verification failed: ${verified.error ?? "unknown error"}`}
              {flat ? (
                <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[var(--muted-foreground)]">
                  {Object.entries(flat).map(([k, v]) => (
                    <div key={k} className="contents">
                      <dt className="font-medium">{k}</dt>
                      <dd className="truncate">{typeof v === "string" ? v : JSON.stringify(v)}</dd>
                    </div>
                  ))}
                </dl>
              ) : verified.data ? (
                <details className="mt-1">
                  <summary className="cursor-pointer text-[var(--muted-foreground)]">raw</summary>
                  <pre className="mt-1 max-h-40 overflow-auto rounded bg-[var(--background)] p-2 whitespace-pre-wrap">
                    {JSON.stringify(verified.data, null, 2)}
                  </pre>
                </details>
              ) : null}
            </div>
          ) : null}

          {/* Session info — only for backends that declared a probe */}
          {active.probe ? (
            <div className="flex flex-wrap items-center gap-2 border-t border-[var(--border)] pt-2">
              <button
                type="button"
                onClick={() => void whoAmI()}
                disabled={loadingSession}
                className="inline-flex items-center gap-1 rounded border border-[var(--border)] px-2 py-0.5 hover:bg-[var(--accent)] disabled:opacity-50"
              >
                {loadingSession ? <Loader2 className="h-3 w-3 animate-spin" /> : <User className="h-3 w-3" />}
                Who am I here?
              </button>
              {session && !("error" in session) ? (
                <span className="text-[var(--muted-foreground)]">
                  {formatSession(session as SessionInfo, active.probe?.label)}
                </span>
              ) : session && "error" in session ? (
                <span className="text-amber-600">
                  {session.error === "unauthenticated"
                    ? "Not signed in on this tab."
                    : session.error === "no_access"
                      ? "stabbur cannot read this page yet (API tools are unaffected) — click \"Who am I here?\" and allow page access."
                      : "Could not read your session on this tab."}
                </span>
              ) : null}
            </div>
          ) : null}

          {/* Full binding controls: scope, write-scope upgrade, Rebind, Unbind. */}
          {binding ? (
            <div className="space-y-2 border-t border-[var(--border)] pt-2">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  data-testid="bind-scope"
                  className={binding.writes ? "text-amber-600" : "text-[var(--muted-foreground)]"}
                >
                  {binding.writes ? "writes enabled" : "read-only"}
                </span>
                {canUpgradeWrites ? (
                  <button
                    type="button"
                    data-testid="bind-upgrade-writes"
                    onClick={openUpgradeBind}
                    className="inline-flex items-center gap-1 rounded border border-[var(--primary)] px-2 py-0.5 text-[var(--foreground)] hover:bg-[var(--accent)]"
                  >
                    <Pencil className="h-3 w-3" /> Enable writes
                  </button>
                ) : null}
                {/* While a bind card is open, hide the entry buttons that would open a competing
                    card or mutate the binding underneath it (Rebind, Unbind). Enable-writes stays —
                    it is the legitimate escalation the remount-on-source-switch (key) now handles
                    correctly. */}
                {recipe && !showBindFlow ? (
                  <button
                    type="button"
                    onClick={openManualBind}
                    className="rounded border border-[var(--border)] px-2 py-0.5 hover:bg-[var(--accent)]"
                  >
                    Rebind
                  </button>
                ) : null}
                {showBindFlow ? null : confirmUnbind ? (
                  <>
                    <button
                      type="button"
                      data-testid="bind-unbind-confirm"
                      onClick={() => void doUnbind()}
                      disabled={unbinding}
                      className="inline-flex items-center gap-1 rounded border border-[var(--destructive)] px-2 py-0.5 text-[var(--destructive)] hover:bg-[var(--accent)] disabled:opacity-50"
                    >
                      {unbinding ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                      Confirm unbind
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmUnbind(false)}
                      disabled={unbinding}
                      className="rounded border border-[var(--border)] px-2 py-0.5 hover:bg-[var(--accent)] disabled:opacity-50"
                    >
                      Keep
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    data-testid="bind-unbind"
                    onClick={() => setConfirmUnbind(true)}
                    className="rounded border border-[var(--border)] px-2 py-0.5 hover:bg-[var(--accent)]"
                  >
                    Unbind
                  </button>
                )}
              </div>
              {expired ? <div className="text-amber-600">Binding expired — rebind?</div> : null}
              {confirmUnbind ? (
                <div className="text-[var(--muted-foreground)]">
                  {active.bind?.unbind_notes?.[binding.mode] ??
                    "This removes the bound profile from the stabbur project. If an access token was created, revoke it from your account settings on the target instance."}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

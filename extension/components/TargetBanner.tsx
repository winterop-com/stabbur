import { useEffect, useMemo, useState } from "react";
import { BadgeCheck, ExternalLink, Loader2, LogIn, ShieldCheck, User, UserCheck } from "lucide-react";
import type { AssistantInfo } from "../lib/assistantApi";
import { match } from "../lib/tabTarget";
import { formatSession, type SessionInfo, type SessionResult } from "../lib/sessionReads";
import { executeRevoke, parseRecipe, substitute } from "../lib/bindRecipe";
import {
  clearBinding,
  getBinding,
  getBindingStale,
  setBinding as persistBinding,
  watchBinding,
  type Binding,
} from "../lib/binding";
import { postUnbind } from "../lib/bindApi";
import { BindFlow, type BindBackendTarget } from "./BindFlow";

interface TargetBannerProps {
  assistant: AssistantInfo | null;
  tabUrl: string | null;
  /** The active backend id (scopes the per-backend binding record). */
  backendId: string;
  /** Snapshot the active kodo backend for a bind flow (passed through to BindFlow). */
  captureTarget: () => BindBackendTarget;
  /** Re-fetch assistant metadata with ?verify=1; lifts the updated record into the parent and
   *  returns it. */
  onVerify: () => Promise<AssistantInfo | null>;
  /** Read the user's session on the active tab. */
  onWhoAmI: () => Promise<SessionResult>;
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
  assistant,
  tabUrl,
  backendId,
  captureTarget,
  onVerify,
  onWhoAmI,
  getActiveTabId,
}: TargetBannerProps) {
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [session, setSession] = useState<SessionResult>(null);
  const [loadingSession, setLoadingSession] = useState(false);
  const [binding, setBinding] = useState<Binding | null>(null);
  const [bindingStale, setBindingStale] = useState(false);
  const [showBindFlow, setShowBindFlow] = useState(false);
  const [confirmUnbind, setConfirmUnbind] = useState(false);
  const [unbinding, setUnbinding] = useState(false);

  // The parent owns the assistant record now (onVerify lifts the refreshed one up), so there is no
  // local shadow to sync — just clear a stale verify error when the record swaps.
  useEffect(() => {
    setVerifyError(null);
  }, [assistant]);

  // Load + track the per-backend binding; reset the transient bind UI on a backend switch.
  useEffect(() => {
    setShowBindFlow(false);
    setConfirmUnbind(false);
    void Promise.all([getBinding(backendId), getBindingStale(backendId)]).then(([b, s]) => {
      setBinding(b);
      setBindingStale(s);
    });
    return watchBinding(backendId, (b, s) => {
      setBinding(b);
      setBindingStale(s);
    });
  }, [backendId]);

  const recipe = useMemo(() => parseRecipe(assistant?.bind ?? null), [assistant?.bind]);
  const verified = assistant?.verified ?? null;
  const flat = useMemo(() => (verified?.ok ? flattenVerifyData(verified.data) : null), [verified]);

  if (assistant === null) return null;
  const active = assistant;

  async function verify(): Promise<AssistantInfo | null> {
    setVerifying(true);
    setVerifyError(null);
    try {
      // onVerify lifts the updated record into the parent's state (which re-renders us).
      return await onVerify();
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
    setLoadingSession(true);
    try {
      setSession(await onWhoAmI());
    } finally {
      setLoadingSession(false);
    }
  }

  const baseUrl = typeof active.base_url === "string" ? active.base_url : null;
  const tab = match(tabUrl, baseUrl);
  const verifiedOk = verified?.ok === true && !reportsFailure(flat);

  const canBind = active.can_bind === true && recipe !== null;

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

  async function doUnbind(): Promise<void> {
    if (!binding) return;
    setUnbinding(true);
    try {
      // Best-effort: revoke the token in the tab's context before removing the profile.
      if (binding.credentialId && recipe && baseUrl && match(tabUrl, baseUrl) === "matched") {
        const tabId = await getActiveTabId();
        if (tabId !== null) {
          await executeRevoke(tabId, baseUrl, substitute(recipe.revokePath, { credentialId: binding.credentialId }));
        }
      }
      await postUnbind(binding.mode);
    } finally {
      await clearBinding(binding.backendId);
      setUnbinding(false);
      setConfirmUnbind(false);
    }
  }

  async function onBound(boundBackendId: string): Promise<void> {
    setShowBindFlow(false);
    const updated = await verify(); // re-probe: the freshly-bound credential changes what verify reports
    // Probe-less bind (no session read to name the user): adopt a username from the verify payload
    // when the stored binding has none, so "Acting as <user>" is not left blank.
    const b = await getBinding(boundBackendId);
    if (b && !b.username) {
      const vflat = updated?.verified?.ok ? flattenVerifyData(updated.verified.data) : null;
      const uname = vflat && typeof vflat.username === "string" ? vflat.username : "";
      if (uname) await persistBinding({ ...b, username: uname });
    }
  }

  return (
    <div className="space-y-2 border-b border-[var(--border)] bg-[var(--muted)] px-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate font-medium text-[var(--foreground)]">{active.name ?? "Assistant"}</span>
          {active.readonly ? (
            <span className="inline-flex items-center gap-1 rounded bg-[var(--accent)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)]">
              <ShieldCheck className="h-3 w-3" /> read-only
            </span>
          ) : null}
        </div>
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
      </div>

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

      {/* Tab-match state */}
      <div className="text-[var(--muted-foreground)]">
        {tab === "matched" ? (
          <span className="text-emerald-600">This tab matches the assistant target.</span>
        ) : tab === "mismatch" ? (
          <div className="space-y-0.5">
            <span className="text-amber-600">This tab does not match the assistant target.</span>
            <div className="truncate">tab: {tabUrl}</div>
            <div className="truncate">target: {baseUrl}</div>
          </div>
        ) : (
          <span>Tab target unknown.</span>
        )}
      </div>

      {/* Verification result */}
      {verifyError ? (
        <div className="text-[var(--destructive)]">Verify request failed: {verifyError}</div>
      ) : null}
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
            <span className="text-amber-600">Not signed in on this tab.</span>
          ) : null}
        </div>
      ) : null}

      {/* Login binding — "Use my login" / bound state */}
      {binding || (canBind && tab === "matched") ? (
        <div className="space-y-2 border-t border-[var(--border)] pt-2">
          {binding ? (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  data-testid="bind-acting-as"
                  className="inline-flex items-center gap-1 rounded bg-[var(--accent)] px-1.5 py-0.5 text-[var(--foreground)]"
                >
                  <UserCheck className="h-3 w-3" /> Acting as {binding.username || "your account"} (your login)
                  <span
                    data-testid="bind-scope"
                    className={binding.writes ? "text-amber-600" : "text-[var(--muted-foreground)]"}
                  >
                    - {binding.writes ? "writes enabled" : "read-only"}
                  </span>
                </span>
                {recipe ? (
                  <button
                    type="button"
                    onClick={() => setShowBindFlow(true)}
                    className="rounded border border-[var(--border)] px-2 py-0.5 hover:bg-[var(--accent)]"
                  >
                    Rebind
                  </button>
                ) : null}
                {confirmUnbind ? (
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
              {expired ? (
                <div className="text-amber-600">Binding expired — rebind?</div>
              ) : null}
              {confirmUnbind ? (
                <div className="text-[var(--muted-foreground)]">
                  {active.bind?.unbind_notes?.[binding.mode] ??
                    "This removes the bound profile from the kodo project. If an access token was created, revoke it from your account settings on the target instance."}
                </div>
              ) : null}
            </div>
          ) : showBindFlow ? null : (
            <button
              type="button"
              data-testid="bind-use-my-login"
              onClick={() => setShowBindFlow(true)}
              className="inline-flex items-center gap-1 rounded border border-[var(--primary)] px-2 py-0.5 text-[var(--foreground)] hover:bg-[var(--accent)]"
            >
              <LogIn className="h-3 w-3" /> Use my login
            </button>
          )}

          {showBindFlow && recipe && baseUrl ? (
            <BindFlow
              assistant={active}
              recipe={recipe}
              basePath={baseUrl}
              captureTarget={captureTarget}
              resolveSession={onWhoAmI}
              getActiveTabId={getActiveTabId}
              onBound={(id) => void onBound(id)}
              onCancel={() => setShowBindFlow(false)}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

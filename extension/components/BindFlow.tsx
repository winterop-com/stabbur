import { useState } from "react";
import { KeyRound, Loader2, ShieldAlert } from "lucide-react";
import type { AssistantInfo } from "../lib/assistantApi";
import {
  classifyMint,
  executeMint,
  type BindRecipe,
} from "../lib/bindRecipe";
import { postBindTo, type BindApiResult, type BindTarget } from "../lib/bindApi";
import { setBinding, type Binding } from "../lib/binding";
import type { SessionResult } from "../lib/sessionReads";

/** The heim backend a bind is written to, snapshotted when the flow starts. */
export interface BindBackendTarget extends BindTarget {
  backendId: string;
}

interface BindFlowProps {
  assistant: AssistantInfo;
  recipe: BindRecipe;
  /** The target instance base URL (mint runs against this in the tab's context). */
  basePath: string;
  /** Snapshot the active heim backend at flow start — freezes {backendId, baseUrl, token} so a
   *  mid-mint backend switch can't misroute the minted token to a different server. */
  captureTarget: () => BindBackendTarget;
  /** Resolve the current tab's signed-in user (for the binding's identity). */
  resolveSession: () => Promise<SessionResult>;
  /** Resolve the active web tab's id (mint/injection target). */
  getActiveTabId: () => Promise<number | null>;
  /** Called after a successful bind (with the backend it landed on) so the parent can refresh. */
  onBound: (backendId: string) => void;
  onCancel: () => void;
}

type Stage =
  | { kind: "consent" }
  | { kind: "working"; label: string }
  | { kind: "unauthenticated" }
  | { kind: "fallback" }
  | { kind: "error"; message: string };

// DHIS2 issues its CSRF token in this cookie; a session-mode write bind captures it (alongside the
// session cookie) so the bound child can pass the CSRF check on POST/PUT/PATCH/DELETE. Reads and the
// PAT path never need it.
const XSRF_COOKIE = "XSRF-TOKEN";

function bindError(res: BindApiResult): string {
  const detail = (res.stderr || res.stdout || "").trim();
  return detail ? `Bind failed: ${detail}` : "Bind failed.";
}

function originOf(url: string): string | null {
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

/** The consent + progress card for "Use my login": mint a scoped credential (or fall back to the
 *  live session cookie) entirely in the target site's context, then hand heim only the secret. */
export function BindFlow({
  assistant,
  recipe,
  basePath,
  captureTarget,
  resolveSession,
  getActiveTabId,
  onBound,
  onCancel,
}: BindFlowProps) {
  // A session-only recipe (no PAT endpoint) opens straight at the session-fallback consent.
  const [stage, setStage] = useState<Stage>(recipe.mint ? { kind: "consent" } : { kind: "fallback" });
  const [allowWrites, setAllowWrites] = useState(false);
  const writable = assistant.readonly === false;
  const targetName = assistant.name ?? "the instance";

  async function saveBinding(
    target: BindBackendTarget,
    mode: string,
    extra: { credentialId?: string; cookieName?: string; expiresAt?: number; writes?: boolean },
  ): Promise<void> {
    const session = await resolveSession();
    const signedIn = session && !("error" in session) ? session : null;
    const binding: Binding = {
      backendId: target.backendId,
      targetBaseUrl: basePath,
      mode,
      username: signedIn?.username ?? "",
      name: signedIn?.name ?? "",
      credentialId: extra.credentialId,
      cookieName: extra.cookieName,
      expiresAt: extra.expiresAt,
      writes: extra.writes ?? false,
    };
    await setBinding(binding);
  }

  async function confirmMint(): Promise<void> {
    const target = captureTarget(); // freeze the destination backend before any await
    const tabId = await getActiveTabId();
    if (tabId === null) {
      setStage({ kind: "error", message: "No active web tab to mint the token in." });
      return;
    }
    setStage({ kind: "working", label: "Requesting a token in this tab…" });
    const writes = writable && allowWrites;
    const methods = writes ? recipe.methodsFull : recipe.methodsReadonly;
    // DHIS2's `expire` is an ABSOLUTE epoch-ms timestamp; compute once and reuse for the binding.
    const expiresMs = Date.now() + recipe.expiresInDays * 86_400_000;
    const mint = await executeMint(tabId, basePath, recipe, methods, expiresMs);
    const cls = classifyMint(mint.status, mint.token);
    if (cls === "minted") {
      setStage({ kind: "working", label: "Installing the token…" });
      const res = await postBindTo(target, recipe.mintMode, mint.token);
      if (!res.ok) {
        setStage({ kind: "error", message: bindError(res) });
        return;
      }
      await saveBinding(target, recipe.mintMode, { credentialId: mint.credentialId, expiresAt: expiresMs, writes });
      onBound(target.backendId);
      return;
    }
    if (cls === "unauthenticated") {
      setStage({ kind: "unauthenticated" });
      return;
    }
    if (cls === "unsupported" || cls === "forbidden") {
      setStage({ kind: "fallback" });
      return;
    }
    setStage({
      kind: "error",
      message: mint.error ? `Could not mint a token: ${mint.error}.` : `Token request failed (status ${mint.status}).`,
    });
  }

  // MUST be invoked directly from the click handler: chrome.permissions.request needs the user
  // gesture, so it is the FIRST await in this function (captureTarget is synchronous, so freezing
  // the destination backend before it does not consume the gesture).
  async function confirmFallback(): Promise<void> {
    const target = captureTarget();
    const origin = originOf(basePath);
    if (!origin) {
      setStage({ kind: "error", message: "The target base URL is not a valid origin." });
      return;
    }
    let granted = false;
    try {
      granted = await chrome.permissions.request({ permissions: ["cookies"], origins: [`${origin}/*`] });
    } catch {
      granted = false;
    }
    if (!granted) {
      setStage({ kind: "error", message: "Cookie access was declined, so the session can't be shared." });
      return;
    }
    setStage({ kind: "working", label: "Reading the session cookie…" });
    let value = "";
    try {
      const cookie = await chrome.cookies.get({ url: basePath, name: recipe.sessionCookie });
      value = cookie?.value ?? "";
    } catch {
      value = "";
    }
    if (!value) {
      setStage({ kind: "error", message: `No ${recipe.sessionCookie} cookie found — sign in to ${targetName} first.` });
      return;
    }
    // A session cookie is inherently full-authority; we don't downgrade methods for it (the
    // per-action confirmation gate is its guardrail). When the user opted into writes, also capture
    // the XSRF token so the bound child can satisfy DHIS2's CSRF check on writes.
    const writes = writable && allowWrites;
    let extraSecret: string | undefined;
    if (writes) {
      try {
        const xsrf = await chrome.cookies.get({ url: basePath, name: XSRF_COOKIE });
        if (xsrf?.value) extraSecret = xsrf.value;
      } catch {
        extraSecret = undefined; // no XSRF cookie readable -> bind without it
      }
    }
    setStage({ kind: "working", label: "Installing the session…" });
    const res = await postBindTo(target, recipe.fallbackMode, `${recipe.sessionCookie}=${value}`, extraSecret);
    if (!res.ok) {
      setStage({ kind: "error", message: bindError(res) });
      return;
    }
    await saveBinding(target, recipe.fallbackMode, { cookieName: recipe.sessionCookie, writes });
    onBound(target.backendId);
  }

  const baseUrl = assistant.base_url ?? basePath;

  return (
    <div data-testid="bind-flow" className="space-y-2 rounded border border-[var(--border)] bg-[var(--background)] p-2 text-xs">
      {stage.kind === "consent" ? (
        <div data-testid="bind-consent" className="space-y-2">
          <div className="flex items-center gap-1.5 font-medium text-[var(--foreground)]">
            <KeyRound className="h-3.5 w-3.5" /> Use my login on {targetName}
          </div>
          <div className="text-[var(--muted-foreground)]">
            <div className="truncate">Target: {baseUrl}</div>
            <p className="mt-1">
              This creates{" "}
              <strong className="text-[var(--foreground)]">
                a personal access token for your account, {writable && allowWrites ? "read-write" : "read-only (GET)"}
              </strong>
              , expires in {recipe.expiresInDays} days, stored as a profile in the heim project. It is minted in this
              tab using your existing login and never leaves your browser except as the token heim stores.
            </p>
          </div>
          {writable ? (
            <label className="flex items-center gap-1.5 text-[var(--muted-foreground)]">
              <input
                type="checkbox"
                data-testid="bind-allow-writes"
                checked={allowWrites}
                onChange={(e) => setAllowWrites(e.target.checked)}
              />
              Allow writes (POST/PUT/PATCH/DELETE), not just reads
            </label>
          ) : null}
          <div className="flex items-center gap-2 pt-1">
            <button
              type="button"
              data-testid="bind-confirm"
              onClick={() => void confirmMint()}
              className="rounded bg-[var(--primary)] px-2 py-0.5 text-[var(--primary-foreground)]"
            >
              Create token
            </button>
            <button
              type="button"
              onClick={onCancel}
              className="rounded border border-[var(--border)] px-2 py-0.5 hover:bg-[var(--accent)]"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {stage.kind === "working" ? (
        <div className="flex items-center gap-1.5 text-[var(--muted-foreground)]">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> {stage.label}
        </div>
      ) : null}

      {stage.kind === "unauthenticated" ? (
        <div data-testid="bind-unauthenticated" className="space-y-2">
          <div className="flex items-center gap-1.5 text-amber-600">
            <ShieldAlert className="h-3.5 w-3.5" /> Sign in to {targetName} in this tab first
          </div>
          <p className="text-[var(--muted-foreground)]">
            The token could not be minted because this tab is not signed in. Log in on {baseUrl}, then try again.
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setStage({ kind: "consent" })}
              className="rounded border border-[var(--border)] px-2 py-0.5 hover:bg-[var(--accent)]"
            >
              Try again
            </button>
            <button
              type="button"
              onClick={onCancel}
              className="rounded border border-[var(--border)] px-2 py-0.5 hover:bg-[var(--accent)]"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {stage.kind === "fallback" ? (
        <div data-testid="bind-fallback" className="space-y-2">
          <div className="flex items-center gap-1.5 font-medium text-[var(--foreground)]">
            <KeyRound className="h-3.5 w-3.5" /> Share your live session instead
          </div>
          <p className="text-[var(--muted-foreground)]">
            This instance would not mint a token, so heim can use your current login session instead. This asks for the{" "}
            <strong className="text-[var(--foreground)]">cookies</strong> permission on {originOf(basePath) ?? baseUrl},
            reads the <code>{recipe.sessionCookie}</code> cookie, and keeps it synced. The session dies when you log out
            of {targetName}.
          </p>
          {writable ? (
            <label className="flex items-center gap-1.5 text-[var(--muted-foreground)]">
              <input
                type="checkbox"
                data-testid="bind-allow-writes"
                checked={allowWrites}
                onChange={(e) => setAllowWrites(e.target.checked)}
              />
              Allow writes (POST/PUT/PATCH/DELETE); each write asks for confirmation
            </label>
          ) : null}
          <div className="flex items-center gap-2">
            <button
              type="button"
              data-testid="bind-fallback-confirm"
              onClick={() => void confirmFallback()}
              className="rounded bg-[var(--primary)] px-2 py-0.5 text-[var(--primary-foreground)]"
            >
              Use session login
            </button>
            <button
              type="button"
              onClick={onCancel}
              className="rounded border border-[var(--border)] px-2 py-0.5 hover:bg-[var(--accent)]"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {stage.kind === "error" ? (
        <div data-testid="bind-error" className="space-y-2">
          <div className="text-[var(--destructive)]">{stage.message}</div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setStage({ kind: "consent" })}
              className="rounded border border-[var(--border)] px-2 py-0.5 hover:bg-[var(--accent)]"
            >
              Back
            </button>
            <button
              type="button"
              onClick={onCancel}
              className="rounded border border-[var(--border)] px-2 py-0.5 hover:bg-[var(--accent)]"
            >
              Close
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

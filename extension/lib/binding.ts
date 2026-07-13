// The per-(backend, target) record of a "use my login" binding: which mode installed it, who it acts
// as, and the artifacts needed to reverse it (a PAT's credential id to revoke, a session's cookie name
// to re-sync). Persisted in chrome.storage.local under a COMPOSITE key `${backendId}:${targetId}`, so a
// backend that composes several assistant targets (the multi-target registry) keeps one binding per
// target. It survives panel reloads and is visible to the background service worker (which keeps a
// session cookie synced).
//
// Migration: pre-multi-target builds keyed by backend alone (`${PREFIX}${backendId}`, no target
// segment). On read those legacy records are adopted as the compat/primary target's binding — rewritten
// once to the composite key and then removed (see `migrateLegacy`).

const PREFIX = "heim-ext-binding:";
const STALE_PREFIX = "heim-ext-binding-stale:";
const DISMISS_PREFIX = "heim-ext-binding-dismissed:";

export interface Binding {
  backendId: string;
  /** The assistant target id (multi-target registry) this binding belongs to. */
  targetId: string;
  targetBaseUrl: string;
  /** The bind mode that installed it (project-declared, e.g. "pat" | "session"). */
  mode: string;
  /** The signed-in user's login/username at bind time (identity-mismatch check). */
  username: string;
  /** The signed-in user's display name at bind time (mismatch fallback when usernames are empty). */
  name: string;
  /** PAT mode: the minted token's id, so it can be revoked on unbind. */
  credentialId?: string;
  /** Session mode: the cookie whose value the background worker re-syncs on change. */
  cookieName?: string;
  /** PAT mode: absolute epoch ms the token expires (for the "expired — rebind?" hint). */
  expiresAt?: number;
  /** Whether the binding was granted write authority (PAT minted with the full method set, or a
   *  session bind with writes allowed). Optional/absent on older stored bindings -> read-only. */
  writes?: boolean;
  /** The registry came from the 404-compat path (old heim): unbind / re-sync use the un-scoped
   *  /api/assistant* routes. Absent -> per-target /api/assistants/{id} routes. */
  compat?: boolean;
}

/**
 * A remembered "no thanks" to the auto-offered "use your login?" prompt, keyed by (backend, target) and
 * carrying the target + the signed-in username it was declined for. The auto-offer stays suppressed
 * only while the SAME human is still logged in on the target; a different username (re-login, shared
 * machine) no longer matches, so re-offering is correct. Cleared when the user re-engages the manual
 * bind button or a bind succeeds.
 */
export interface BindDismissal {
  backendId: string;
  /** The assistant target id this decline was recorded for (absent on migrated legacy records). */
  targetId?: string;
  targetBaseUrl: string;
  /** The signed-in username the auto-offer was declined for ("" when the probe named none). */
  username: string;
}

function key(backendId: string, targetId: string): string {
  return `${PREFIX}${backendId}:${targetId}`;
}

function staleKey(backendId: string, targetId: string): string {
  return `${STALE_PREFIX}${backendId}:${targetId}`;
}

function dismissKey(backendId: string, targetId: string): string {
  return `${DISMISS_PREFIX}${backendId}:${targetId}`;
}

// Pre-multi-target keys had no target segment (`${PREFIX}${backendId}`); read-adopted below.
function legacyKey(backendId: string): string {
  return `${PREFIX}${backendId}`;
}
function legacyStaleKey(backendId: string): string {
  return `${STALE_PREFIX}${backendId}`;
}
function legacyDismissKey(backendId: string): string {
  return `${DISMISS_PREFIX}${backendId}`;
}

function isBinding(v: unknown): v is Binding {
  return (
    v !== null &&
    typeof v === "object" &&
    typeof (v as Binding).backendId === "string" &&
    typeof (v as Binding).mode === "string" &&
    typeof (v as Binding).targetBaseUrl === "string"
  );
}

/**
 * One-time adoption of a pre-multi-target record (`${PREFIX}${backendId}`, no target segment) as the
 * compat/primary target's binding: rewrite it (stamping `targetId`) plus its stale flag to the composite
 * keys, drop the legacy keys, and return it. Migrated exactly once — a later read finds the composite key.
 */
async function migrateLegacy(backendId: string, targetId: string): Promise<Binding | null> {
  const lk = legacyKey(backendId);
  const lsk = legacyStaleKey(backendId);
  const stored = await chrome.storage.local.get([lk, lsk]);
  if (!isBinding(stored[lk])) return null;
  const migrated: Binding = { ...(stored[lk] as Binding), targetId };
  await chrome.storage.local.set({
    [key(backendId, targetId)]: migrated,
    [staleKey(backendId, targetId)]: stored[lsk] === true,
  });
  await chrome.storage.local.remove([lk, lsk]);
  return migrated;
}

/** The binding for a (backend, target), or null when none is installed. */
export async function getBinding(backendId: string, targetId: string): Promise<Binding | null> {
  const k = key(backendId, targetId);
  const stored = await chrome.storage.local.get(k);
  if (isBinding(stored[k])) return stored[k] as Binding;
  return migrateLegacy(backendId, targetId);
}

/** Install/replace a binding (clears its stale flag). The composite key is derived from the record. */
export async function setBinding(binding: Binding): Promise<void> {
  await chrome.storage.local.set({
    [key(binding.backendId, binding.targetId)]: binding,
    [staleKey(binding.backendId, binding.targetId)]: false,
  });
}

/** Remove a binding and its stale flag. */
export async function clearBinding(backendId: string, targetId: string): Promise<void> {
  await chrome.storage.local.remove([key(backendId, targetId), staleKey(backendId, targetId)]);
}

/** Whether a binding has been flagged stale (e.g. its session cookie was evicted). Falls back to the
 *  legacy (un-migrated) stale flag so a not-yet-adopted binding still reports correctly. */
export async function getBindingStale(backendId: string, targetId: string): Promise<boolean> {
  const k = staleKey(backendId, targetId);
  const stored = await chrome.storage.local.get([k, legacyStaleKey(backendId)]);
  if (k in stored) return stored[k] === true;
  return stored[legacyStaleKey(backendId)] === true;
}

/** Flag/unflag a binding as stale. */
export async function setBindingStale(backendId: string, targetId: string, stale: boolean): Promise<void> {
  await chrome.storage.local.set({ [staleKey(backendId, targetId)]: stale });
}

function isDismissal(v: unknown): v is BindDismissal {
  return (
    v !== null &&
    typeof v === "object" &&
    typeof (v as BindDismissal).backendId === "string" &&
    typeof (v as BindDismissal).targetBaseUrl === "string" &&
    typeof (v as BindDismissal).username === "string"
  );
}

/** The remembered auto-offer dismissal for a (backend, target), or null. Falls back to the legacy
 *  (un-migrated) per-backend dismissal so an upgraded project's earlier decline still counts. */
export async function getBindDismissal(backendId: string, targetId: string): Promise<BindDismissal | null> {
  const k = dismissKey(backendId, targetId);
  const stored = await chrome.storage.local.get([k, legacyDismissKey(backendId)]);
  const v = k in stored ? stored[k] : stored[legacyDismissKey(backendId)];
  return isDismissal(v) ? (v as BindDismissal) : null;
}

/** Remember that the user declined the auto-offer for this backend/target/username. */
export async function setBindDismissal(dismissal: BindDismissal): Promise<void> {
  const targetId = dismissal.targetId ?? "";
  await chrome.storage.local.set({ [dismissKey(dismissal.backendId, targetId)]: dismissal });
}

/** Forget a (backend, target)'s auto-offer dismissal (re-enables the auto-offer); also clears any
 *  legacy per-backend decline so a re-engage truly starts fresh. */
export async function clearBindDismissal(backendId: string, targetId: string): Promise<void> {
  await chrome.storage.local.remove([dismissKey(backendId, targetId), legacyDismissKey(backendId)]);
}

/** Every installed binding (used by the background worker to find session bindings to sync). Enumerates
 *  both composite and any not-yet-migrated legacy keys. */
export async function listBindings(): Promise<Binding[]> {
  const all = await chrome.storage.local.get(null);
  const out: Binding[] = [];
  for (const [k, v] of Object.entries(all)) {
    if (k.startsWith(PREFIX) && isBinding(v)) out.push(v as Binding);
  }
  return out;
}

/** Subscribe to a (backend, target)'s binding + stale-flag changes; returns an unsubscribe function. */
export function watchBinding(
  backendId: string,
  targetId: string,
  cb: (binding: Binding | null, stale: boolean) => void,
): () => void {
  const listener = (changes: Record<string, chrome.storage.StorageChange>, area: string): void => {
    if (area !== "local") return;
    if (!(key(backendId, targetId) in changes || staleKey(backendId, targetId) in changes)) return;
    void Promise.all([getBinding(backendId, targetId), getBindingStale(backendId, targetId)]).then(([b, s]) =>
      cb(b, s),
    );
  };
  chrome.storage.onChanged.addListener(listener);
  return () => chrome.storage.onChanged.removeListener(listener);
}

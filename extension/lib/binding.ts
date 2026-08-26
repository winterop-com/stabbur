// The per-(backend, target) record of a "use my login" binding: which mode installed it, who it acts
// as, and the artifacts needed to reverse it (a PAT's credential id to revoke, a session's cookie name
// to re-sync). Persisted in chrome.storage.local under a COMPOSITE key `${backendId}:${targetId}`, so a
// backend that composes several assistant targets (the multi-target registry) keeps one binding per
// target. It survives panel reloads and is visible to the background service worker (which keeps a
// session cookie synced).
//
// Migration: pre-multi-target builds keyed by backend alone (`${PREFIX}${backendId}`, no target
// segment). Those legacy records are adopted as the PRIMARY target's binding by a single owner — the
// panel, once the registry resolves the primary id + compat mode — which rewrites the binding, its
// stale flag, and its auto-offer dismissal to the composite keys (stamping `targetId` + `compat`) and
// deletes every legacy key (see `migrateLegacyRecords`). The read paths below are purely composite-
// keyed: no per-read legacy fallback, so no reader can adopt a legacy record to the wrong target.

const PREFIX = "stabbur-ext-binding:";
const STALE_PREFIX = "stabbur-ext-binding-stale:";
const DISMISS_PREFIX = "stabbur-ext-binding-dismissed:";

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
  /** The registry came from the 404-compat path (old stabbur): unbind / re-sync use the un-scoped
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
  /** The assistant target id this decline was recorded for (stamped on migration; optional only for
   *  backward compatibility with records written before the target segment existed). */
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
 * The ONE owner of legacy adoption: migrate any pre-multi-target records (keyed by backend alone, no
 * target segment) to the PRIMARY target's composite keys. Rewrites the legacy binding (stamping
 * `targetId=primaryTargetId` and `compat` from the resolved registry mode), its stale flag, and its
 * auto-offer dismissal (stamping `targetId`), then deletes every legacy key. The panel calls this once
 * the registry loads, so every other reader (and the background worker) only ever sees composite keys.
 *
 * Idempotent and cheap: one storage read, and a no-op with zero writes once nothing legacy remains (the
 * steady state after the first upgrade). Never clobbers an existing composite record — a primary that was
 * already (re)bound post-upgrade wins, and the stray legacy key is simply dropped.
 */
export async function migrateLegacyRecords(
  backendId: string,
  primaryTargetId: string,
  compat: boolean,
): Promise<void> {
  const lk = legacyKey(backendId);
  const lsk = legacyStaleKey(backendId);
  const ldk = legacyDismissKey(backendId);
  const stored = await chrome.storage.local.get([lk, lsk, ldk]);
  // Nothing legacy present -> return without a single write (the common post-migration path).
  if (!(lk in stored) && !(lsk in stored) && !(ldk in stored)) return;

  // Read the composite targets we might write, so a freshly (re)bound primary is never clobbered.
  const bk = key(backendId, primaryTargetId);
  const dk = dismissKey(backendId, primaryTargetId);
  const existing = await chrome.storage.local.get([bk, dk]);

  const writes: Record<string, unknown> = {};
  if (isBinding(stored[lk]) && !isBinding(existing[bk])) {
    writes[bk] = { ...(stored[lk] as Binding), targetId: primaryTargetId, compat };
    writes[staleKey(backendId, primaryTargetId)] = stored[lsk] === true;
  }
  if (isDismissal(stored[ldk]) && !isDismissal(existing[dk])) {
    writes[dk] = { ...(stored[ldk] as BindDismissal), targetId: primaryTargetId };
  }
  if (Object.keys(writes).length > 0) await chrome.storage.local.set(writes);
  await chrome.storage.local.remove([lk, lsk, ldk]);
}

/** The binding for a (backend, target), or null when none is installed. Composite-keyed only —
 *  legacy adoption is the panel's one-time job (see `migrateLegacyRecords`). */
export async function getBinding(backendId: string, targetId: string): Promise<Binding | null> {
  const k = key(backendId, targetId);
  const stored = await chrome.storage.local.get(k);
  return isBinding(stored[k]) ? (stored[k] as Binding) : null;
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

/** Whether a binding has been flagged stale (e.g. its session cookie was evicted). Composite-keyed
 *  only; a legacy stale flag is adopted once by `migrateLegacyRecords`, never on read. */
export async function getBindingStale(backendId: string, targetId: string): Promise<boolean> {
  const k = staleKey(backendId, targetId);
  const stored = await chrome.storage.local.get(k);
  return stored[k] === true;
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

/** The remembered auto-offer dismissal for a (backend, target), or null. Composite-keyed only; a legacy
 *  per-backend decline is adopted once by `migrateLegacyRecords` (to the primary), never on read — so a
 *  decline can no longer leak across every target. */
export async function getBindDismissal(backendId: string, targetId: string): Promise<BindDismissal | null> {
  const k = dismissKey(backendId, targetId);
  const stored = await chrome.storage.local.get(k);
  return isDismissal(stored[k]) ? (stored[k] as BindDismissal) : null;
}

/** Remember that the user declined the auto-offer for this backend/target/username. */
export async function setBindDismissal(dismissal: BindDismissal): Promise<void> {
  const targetId = dismissal.targetId ?? "";
  await chrome.storage.local.set({ [dismissKey(dismissal.backendId, targetId)]: dismissal });
}

/** Forget a (backend, target)'s auto-offer dismissal (re-enables the auto-offer). Composite-keyed only;
 *  any legacy per-backend decline was already migrated to the primary and removed. */
export async function clearBindDismissal(backendId: string, targetId: string): Promise<void> {
  await chrome.storage.local.remove(dismissKey(backendId, targetId));
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

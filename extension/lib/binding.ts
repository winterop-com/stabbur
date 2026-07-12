// The per-backend record of a "use my login" binding: which mode installed it, who it acts as, and
// the artifacts needed to reverse it (a PAT's credential id to revoke, a session's cookie name to
// re-sync). Persisted in chrome.storage.local, keyed by backend id, so it survives panel reloads and
// is visible to the background service worker (which keeps a session cookie synced).

const PREFIX = "kodo-ext-binding:";
const STALE_PREFIX = "kodo-ext-binding-stale:";

export interface Binding {
  backendId: string;
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
}

function key(backendId: string): string {
  return `${PREFIX}${backendId}`;
}

function staleKey(backendId: string): string {
  return `${STALE_PREFIX}${backendId}`;
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

/** The binding for a backend, or null when none is installed. */
export async function getBinding(backendId: string): Promise<Binding | null> {
  const k = key(backendId);
  const stored = await chrome.storage.local.get(k);
  return isBinding(stored[k]) ? (stored[k] as Binding) : null;
}

/** Install/replace a binding (clears its stale flag). */
export async function setBinding(binding: Binding): Promise<void> {
  await chrome.storage.local.set({ [key(binding.backendId)]: binding, [staleKey(binding.backendId)]: false });
}

/** Remove a binding and its stale flag. */
export async function clearBinding(backendId: string): Promise<void> {
  await chrome.storage.local.remove([key(backendId), staleKey(backendId)]);
}

/** Whether a backend's binding has been flagged stale (e.g. its session cookie was evicted). */
export async function getBindingStale(backendId: string): Promise<boolean> {
  const k = staleKey(backendId);
  const stored = await chrome.storage.local.get(k);
  return stored[k] === true;
}

/** Flag/unflag a binding as stale. */
export async function setBindingStale(backendId: string, stale: boolean): Promise<void> {
  await chrome.storage.local.set({ [staleKey(backendId)]: stale });
}

/** Every installed binding (used by the background worker to find session bindings to sync). */
export async function listBindings(): Promise<Binding[]> {
  const all = await chrome.storage.local.get(null);
  const out: Binding[] = [];
  for (const [k, v] of Object.entries(all)) {
    if (k.startsWith(PREFIX) && isBinding(v)) out.push(v);
  }
  return out;
}

/** Subscribe to a backend's binding + stale-flag changes; returns an unsubscribe function. */
export function watchBinding(backendId: string, cb: (binding: Binding | null, stale: boolean) => void): () => void {
  const listener = (changes: Record<string, chrome.storage.StorageChange>, area: string): void => {
    if (area !== "local") return;
    if (!(key(backendId) in changes || staleKey(backendId) in changes)) return;
    void Promise.all([getBinding(backendId), getBindingStale(backendId)]).then(([b, s]) => cb(b, s));
  };
  chrome.storage.onChanged.addListener(listener);
  return () => chrome.storage.onChanged.removeListener(listener);
}

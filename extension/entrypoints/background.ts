import { listBindings, setBindingStale, type Binding } from "../lib/binding";
import { getSettings, normalizeBaseUrl } from "../lib/settings";
import { postBindTo, type BindTarget } from "../lib/bindApi";

// A cookie binding (b.cookieName present) shares the user's live login cookie with stabbur. Chrome
// expires/rotates that cookie out from under us, so the background worker watches it: on a change
// to a watched name it re-reads the AUTHORITATIVE cookie the target would send (never the change
// event's value, which may be a same-name cookie on a parent domain) and re-POSTs it (debounced +
// rate-ceilinged); when that cookie is gone it flags the binding stale so the panel prompts a
// rebind. The cookies API only exists once the optional "cookies" permission is granted.
//
// Cost control: an in-memory index (cookieName -> bindings) lets the cookies.onChanged handler
// early-return synchronously on any non-watched cookie name with ZERO storage reads, and the
// listener itself is only attached while at least one cookie binding exists.

const BINDING_PREFIX = "stabbur-ext-binding:";
const DEBOUNCE_MS = 2000; // coalesce a burst of changes into one POST
const REBIND_CEILING_MS = 30_000; // minimum gap between actual rebind POSTs per backend (anti-treadmill)

// Timers / rate-limits are keyed per (backend, target) since one backend can carry several session
// bindings (multi-target registry). The key is the composite `${backendId}:${targetId}` — mirroring the
// storage key's segment after the prefix — so the storage.onChanged removal cleanup (which slices the
// changed key) lines up. Un-migrated legacy records (no targetId) are never indexed (see rebuildIndex),
// so this worker never derives a key from a missing target.
const debounceTimers = new Map<string, ReturnType<typeof setTimeout>>();
const lastRebindAt = new Map<string, number>();
// cookieName -> the bindings watching it. Rebuilt from storage on startup + on any binding change.
let cookieIndex = new Map<string, Binding[]>();
let cookieListenerAdded = false;

function timerId(b: Binding): string {
  return `${b.backendId}:${b.targetId}`;
}

function clearDebounce(id: string): void {
  const t = debounceTimers.get(id);
  if (t) {
    clearTimeout(t);
    debounceTimers.delete(id);
  }
}

async function rebind(binding: Binding, value: string): Promise<void> {
  if (!binding.cookieName) return;
  // Never route an un-migrated legacy record (no targetId): it would POST /api/assistants/undefined/bind.
  // Adopting it to the primary is the panel's job (migrateLegacyRecords); until then it is ignored.
  if (!binding.targetId) return;
  const settings = await getSettings();
  const backend = settings.backends.find((b) => b.id === binding.backendId);
  if (!backend) return;
  const target: BindTarget = { baseUrl: normalizeBaseUrl(backend.baseUrl), token: backend.token || null };
  // postBindTo never throws (a transient failure comes back as a structured result); the next
  // cookie change retries.
  await postBindTo(
    target,
    { targetId: binding.targetId, compat: binding.compat ?? false },
    binding.mode,
    `${binding.cookieName}=${value}`,
  );
}

function scheduleRebind(binding: Binding, value: string): void {
  const id = timerId(binding);
  clearDebounce(id);
  const last = lastRebindAt.get(id) ?? 0;
  // Fire after the 2s debounce, but never sooner than 30s since the last POST — a fast cookie
  // rotation coalesces AND is rate-limited (the newest value still lands, just later).
  const delay = Math.max(DEBOUNCE_MS, REBIND_CEILING_MS - (Date.now() - last));
  debounceTimers.set(
    id,
    setTimeout(() => {
      debounceTimers.delete(id);
      lastRebindAt.set(id, Date.now());
      void rebind(binding, value);
    }, delay),
  );
}

function handleCookieChange(info: chrome.cookies.CookieChangeInfo): void {
  // Synchronous early-return on a non-watched name: zero storage reads on the vast majority of
  // browser-wide cookie changes.
  const bindings = cookieIndex.get(info.cookie.name);
  if (!bindings || bindings.length === 0) return;
  void (async () => {
    for (const binding of bindings) {
      const name = binding.cookieName;
      if (!name) continue;
      // Defensive: rebuildIndex already drops legacy records (no targetId); never flag-stale/rebind one
      // here either (a `${backendId}:undefined` stale key no reader consults; a /undefined/bind POST).
      if (!binding.targetId) continue;
      // Re-read the authoritative cookie the target URL would actually send. This resolves
      // parent-domain shadowing (the event's cookie may be a different-scope same-name cookie)
      // and yields null exactly when the target has no such cookie anymore.
      let value = "";
      try {
        const cookie = await chrome.cookies.get({ url: binding.targetBaseUrl, name });
        value = cookie?.value ?? "";
      } catch {
        value = "";
      }
      if (!value) {
        // Gone -> drop any pending rebind and flag stale so the panel prompts a rebind.
        clearDebounce(timerId(binding));
        await setBindingStale(binding.backendId, binding.targetId, true);
        continue;
      }
      scheduleRebind(binding, value);
    }
  })();
}

function syncListenerState(): void {
  const wanted = cookieIndex.size > 0;
  if (wanted && !cookieListenerAdded) {
    // chrome.cookies is undefined until the optional permission is granted.
    if (chrome.cookies?.onChanged) {
      chrome.cookies.onChanged.addListener(handleCookieChange);
      cookieListenerAdded = true;
    }
  } else if (!wanted && cookieListenerAdded) {
    chrome.cookies?.onChanged.removeListener(handleCookieChange);
    cookieListenerAdded = false;
  }
}

function rebuildIndex(bindings: Binding[]): void {
  const idx = new Map<string, Binding[]>();
  for (const b of bindings) {
    if (!b.cookieName) continue;
    // Skip un-migrated legacy records (no targetId): the panel owns adoption (migrateLegacyRecords).
    // Indexing one would let handleCookieChange rebind/flag-stale it with an undefined target segment.
    if (!b.targetId) continue;
    const list = idx.get(b.cookieName) ?? [];
    list.push(b);
    idx.set(b.cookieName, list);
  }
  cookieIndex = idx;
}

async function refreshIndex(): Promise<void> {
  rebuildIndex(await listBindings());
  syncListenerState();
}

export default defineBackground(() => {
  // Clicking the toolbar action opens the side panel (MV3 side-panel behavior).
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch((err) => console.error(err));

  void refreshIndex(); // build the index + arm the cookies listener if any cookie binding exists

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    const bindingKeys = Object.keys(changes).filter((k) => k.startsWith(BINDING_PREFIX));
    if (bindingKeys.length === 0 && !("backends" in changes)) return;
    // A removed binding: cancel any pending rebind so a queued timer can't resurrect the profile
    // after an unbind.
    for (const k of bindingKeys) {
      if (changes[k].newValue === undefined) {
        // The key segment after the prefix is the (backend, target) timer id (see `timerId`).
        const id = k.slice(BINDING_PREFIX.length);
        clearDebounce(id);
        lastRebindAt.delete(id);
      }
    }
    void refreshIndex();
  });

  // The cookies permission may be granted after startup (during a session-fallback consent).
  chrome.permissions.onAdded.addListener(() => void refreshIndex());
});

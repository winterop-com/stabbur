// Bearer-token auth for a LAN-exposed server (V-14). When `stabbur serve` binds a non-loopback
// address it requires a token; the SPA is opened via a tokenized URL (`?token=…`, like Jupyter).
// This module captures that token into localStorage, strips it from the address bar, and wraps
// `fetch` so every same-origin API call carries `Authorization: Bearer <token>`. On the default
// loopback bind there's no token and nothing changes.

const TOKEN_KEY = "stabbur.authToken";
const GUARDED = /^\/(api|v1|models)(\/|$)/;

function readStored(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/** Pull a `?token=` (or `#token=`) out of the URL, persist it, and scrub it from the bar. */
function captureTokenFromUrl(): void {
  try {
    const url = new URL(window.location.href);
    const fromQuery = url.searchParams.get("token");
    const hashMatch = url.hash.match(/[#&]token=([^&]+)/);
    const token = fromQuery ?? (hashMatch ? decodeURIComponent(hashMatch[1]) : null);
    if (!token) return;
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* storage unavailable — the wrap below still uses the in-URL value this session */
    }
    // Don't leave the token sitting in the address bar / history / a bookmarked URL.
    url.searchParams.delete("token");
    if (hashMatch) url.hash = url.hash.replace(/[#&]token=[^&]+/, "").replace(/^#$/, "");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  } catch {
    /* malformed URL — ignore */
  }
}

function isSameOriginGuarded(rawUrl: string): boolean {
  try {
    const u = new URL(rawUrl, window.location.origin);
    return u.origin === window.location.origin && GUARDED.test(u.pathname);
  } catch {
    return false;
  }
}

/** Capture any token in the URL and install a fetch wrapper that attaches the bearer header. */
export function initAuth(): void {
  captureTokenFromUrl();
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const token = readStored();
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (token && isSameOriginGuarded(rawUrl)) {
      const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
      if (!headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
      return nativeFetch(input, { ...init, headers });
    }
    return nativeFetch(input, init);
  };
}

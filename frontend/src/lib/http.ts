// The base-URL seam for the API client. The web build runs same-origin, so the
// defaults (baseUrl "", token null) make apiFetch identical to a bare fetch —
// the LAN bearer flow stays in lib/auth.ts's window.fetch monkeypatch. A packaged
// build that can't be same-origin (the Chrome extension) calls configureApi() once
// at startup with the server's absolute origin + token; only then does apiFetch
// prefix the base URL and attach the Authorization header itself.

export interface ApiConfig {
  baseUrl: string;
  token: string | null;
}

const GUARDED = /^\/(api|v1|models)(\/|$)/;

const config: ApiConfig = { baseUrl: "", token: null };

/** Merge overrides into the module-local API config. The web app never calls this. */
export function configureApi(c: Partial<ApiConfig>): void {
  Object.assign(config, c);
}

/** A snapshot of the current API config (base URL + token), for callers that need to build a
 *  request against the configured target themselves (e.g. an explicit status probe). */
export function apiConfig(): ApiConfig {
  return { ...config };
}

/** Fetch `${baseUrl}${path}`, attaching a bearer header on guarded paths when a token is set. */
export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const url = `${config.baseUrl}${path}`;
  if (config.token && GUARDED.test(path)) {
    const headers = new Headers(init?.headers);
    if (!headers.has("Authorization")) headers.set("Authorization", `Bearer ${config.token}`);
    return fetch(url, { ...init, headers });
  }
  return fetch(url, init);
}

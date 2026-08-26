// Phase 3: read the user's own session on the active tab's site, entirely in the page's own
// security context. The injected function receives a base path plus the project-declared probe
// (paths + field map) and re-validates every path itself -- no arbitrary URL is ever trusted
// blindly. Generic (stabbur interprets nothing): the probe spec comes from the assistant metadata.

export interface SessionInfo {
  username: string;
  name: string;
  version: string;
  instanceName: string;
}

// A probe outcome. "unauthenticated" is a CONFIDENT logged-out signal (401, a 302-to-login, or a
// token-less HTML page where JSON was expected); "probe_failed" is the AMBIGUOUS case (a 500, a
// network blip, a non-JSON error) where we simply can't tell — callers must not treat it as
// logged-out (e.g. the bind pre-gate falls through to the mint on it). "no_access" means the
// injection itself was refused (no host access to the tab — the panel was not opened via the
// toolbar icon on this tab and no optional grant exists yet); user-gesture paths fix it by
// calling requestHostAccess (lib/hostAccess.ts) first.
export type SessionResult = SessionInfo | { error: "unauthenticated" | "probe_failed" | "no_access" } | null;

/** The project-declared session probe (echoed verbatim by GET /api/assistant; stabbur never runs it). */
export interface ProbeSpec {
  /** Same-origin paths to fetch; the first is the identity signal (its failure fails the probe). */
  paths: string[];
  /** SessionInfo key -> ordered "i.dotted.path" candidates (i indexes into `paths` results). */
  fields: Record<string, string[]>;
  /** Optional display template with {field} tokens for formatSession. */
  label?: string;
}

/** Trim a trailing slash so `${base}/api/...` never doubles up. */
function trimTrailingSlash(s: string): string {
  return s.replace(/\/+$/, "");
}

/**
 * Ordered base-path candidates to probe. Prefer the assistant's declared
 * base_url when the tab is under it; otherwise try `<origin>/<firstSegment>`
 * (a DHIS2 context path) then the bare `<origin>`.
 */
export function candidateBasePaths(tabUrl: string | null, assistantBaseUrl: string | null): string[] {
  if (!tabUrl) return [];
  // Path-boundary prefix match (same rule as tabTarget.match): base ".../dev" must NOT claim a
  // tab at ".../dev-2-42/..." — a bare startsWith would return only the wrong base and skip the
  // origin/first-segment fallback that actually identifies the session.
  if (assistantBaseUrl) {
    const base = trimTrailingSlash(assistantBaseUrl);
    if (tabUrl === base || tabUrl.startsWith(`${base}/`) || tabUrl.startsWith(`${base}?`)) {
      return [base];
    }
  }
  let u: URL;
  try {
    u = new URL(tabUrl);
  } catch {
    return [];
  }
  const origin = u.origin;
  const first = u.pathname.split("/").filter(Boolean)[0];
  const candidates: string[] = [];
  if (first) candidates.push(`${origin}/${first}`);
  candidates.push(origin);
  return candidates;
}

// Injected into the page (MAIN world). Fetches the declared paths with the user's cookies and
// maps the results into a fixed SessionInfo. Fully self-contained + serializable-args-only, and
// re-validates every path in the page context -- never trusts the injected list blindly.
async function runProbe(
  basePath: string,
  paths: string[],
  fields: Record<string, string[]>,
): Promise<SessionResult> {
  // Inline mirror of paths.validSameOriginPath (the source of truth). executeScript serializes
  // this function, so it can't import — keep this copy identical to lib/paths.ts.
  function validPath(p: string): boolean {
    if (typeof p !== "string" || !p.startsWith("/") || p.startsWith("//")) return false;
    if (p.includes("\\") || p.includes("..")) return false;
    const q = p.indexOf("?");
    const beforeQuery = q === -1 ? p : p.slice(0, q);
    return !beforeQuery.includes(":");
  }

  // redirect:"manual": these are JSON API endpoints, so ANY redirect is the login bounce — never
  // follow it. Following broke on real deployments whose proxy issues the Location as plain http
  // (play does): the https page fetch then dies on the mixed-content block and the confident
  // logged-out signal degraded into an opaque thrown error.
  const opts: RequestInit = {
    credentials: "include",
    headers: { Accept: "application/json" },
    redirect: "manual",
  };

  // Classify a first-path (identity) response. A CONFIDENT logged-out signal maps to
  // "unauthenticated"; anything else that merely failed (a 500, a non-JSON error page that is not a
  // login redirect) is "probe_failed" so the caller doesn't misread a transient blip as logged-out.
  // Mirror of bindRecipe.mintInPage's login detection (redirect/non-JSON => no session); keep the
  // two in sync.
  function classifyFirst(res: Response): "ok" | "unauthenticated" | "probe_failed" {
    if (res.status === 401) return "unauthenticated";
    // An unfollowed redirect (opaqueredirect under redirect:"manual", or an explicit 3xx) on a
    // JSON API read is the login bounce.
    if (res.type === "opaqueredirect" || (res.status >= 300 && res.status < 400)) return "unauthenticated";
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("json")) return "unauthenticated"; // HTML login page served where JSON was expected
    if (!res.ok) return "probe_failed"; // e.g. a 500 with a JSON error body — ambiguous, not logged-out
    return "ok";
  }

  const results: unknown[] = [];
  for (let i = 0; i < paths.length; i++) {
    const p = paths[i];
    if (!validPath(p)) {
      if (i === 0) return { error: "probe_failed" }; // a malformed probe spec is our fault, not a logout
      results.push(null);
      continue;
    }
    try {
      const res = await fetch(`${basePath}${p}`, opts);
      if (i === 0) {
        // Identity signal: a confident logged-out signal fails the probe as "unauthenticated"; a
        // transient/ambiguous failure fails it as "probe_failed" (callers treat the two differently).
        const cls = classifyFirst(res);
        if (cls !== "ok") return { error: cls };
        results.push((await res.json()) as unknown);
      } else if (res.ok && (res.headers.get("content-type") ?? "").includes("json")) {
        results.push((await res.json()) as unknown);
      } else {
        results.push(null); // later paths are best-effort
      }
    } catch {
      if (i === 0) return { error: "probe_failed" }; // a network throw is ambiguous, never a confident logout
      results.push(null);
    }
  }

  // Walk the "i.key.path" candidates for one field; first string value wins.
  function pick(candidates: string[]): string {
    for (const cand of candidates) {
      const segs = cand.split(".");
      const idx = Number(segs[0]);
      if (!Number.isInteger(idx) || idx < 0 || idx >= results.length) continue;
      let cur: unknown = results[idx];
      for (let s = 1; s < segs.length; s++) {
        if (cur === null || typeof cur !== "object") {
          cur = undefined;
          break;
        }
        cur = (cur as Record<string, unknown>)[segs[s]];
      }
      if (typeof cur === "string") return cur;
    }
    return "";
  }

  return {
    username: pick(fields.username ?? []),
    name: pick(fields.name ?? []),
    version: pick(fields.version ?? []),
    instanceName: pick(fields.instanceName ?? []),
  };
}

/** Probe one base path in the active tab's context using the declared probe spec. */
export async function whoAmI(tabId: number, basePath: string, probe: ProbeSpec): Promise<SessionResult> {
  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      func: runProbe,
      args: [trimTrailingSlash(basePath), probe.paths, probe.fields],
      world: "MAIN",
    });
    return (result?.result as SessionResult | undefined) ?? null;
  } catch {
    // executeScript itself was refused — overwhelmingly a missing host grant for the tab's origin
    // (no activeTab, no optional grant). Distinct from "probe_failed" so callers can point the user
    // at granting access rather than at a flaky network.
    return { error: "no_access" };
  }
}

/**
 * Resolve the best base path from the candidates and return the first probe that
 * identifies a signed-in user; falls back to the last result otherwise. A null/undefined
 * probe means the backend declared none — generic backends get no session reads at all.
 */
export async function whoAmIResolved(
  tabId: number,
  tabUrl: string | null,
  assistantBaseUrl: string | null,
  probe: ProbeSpec | null | undefined,
): Promise<SessionResult> {
  if (!probe) return null;
  const candidates = candidateBasePaths(tabUrl, assistantBaseUrl);
  let last: SessionResult = null;
  for (const base of candidates) {
    last = await whoAmI(tabId, base, probe);
    if (last && !("error" in last) && (last.username || last.name)) return last;
  }
  return last;
}

/**
 * One-line summary for the TargetBanner. With a label template, substitutes {field} tokens
 * from the SessionInfo; otherwise the built-in "Browsing as <user> on <instance> (<version>)".
 */
export function formatSession(info: SessionInfo, label?: string): string {
  if (label) {
    return label
      .replace(/\{(\w+)\}/g, (_m, key: string) => {
        const v = (info as unknown as Record<string, unknown>)[key];
        return typeof v === "string" ? v : "";
      })
      .replace(/\s+/g, " ")
      .trim();
  }
  const who = info.name || info.username || "unknown user";
  const where = info.instanceName || "this instance";
  const version = info.version ? ` (${info.version})` : "";
  return `Browsing as ${who} on ${where}${version}`;
}

/**
 * The signed-in-user line folded into a chat turn's page context. Explicitly labels this as the
 * person viewing the page (distinct from the tool account). Fields are omitted gracefully when
 * the probe couldn't fill them.
 */
export function formatSessionContext(info: SessionInfo): string {
  const who = info.name || info.username || "unknown user";
  const userSuffix = info.name && info.username ? ` (${info.username})` : "";
  let line = `Browser session user: ${who}${userSuffix}`;
  if (info.instanceName) line += ` on ${info.instanceName}`;
  if (info.version) line += ` (${info.version})`;
  return line;
}

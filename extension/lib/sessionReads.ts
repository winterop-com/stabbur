// Phase 3: read the user's own session on the active tab's site, entirely in the page's own
// security context. The injected function receives a base path plus the project-declared probe
// (paths + field map) and re-validates every path itself -- no arbitrary URL is ever trusted
// blindly. Generic (heim interprets nothing): the probe spec comes from the assistant metadata.

export interface SessionInfo {
  username: string;
  name: string;
  version: string;
  instanceName: string;
}

export type SessionResult = SessionInfo | { error: "unauthenticated" } | null;

/** The project-declared session probe (echoed verbatim by GET /api/assistant; heim never runs it). */
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

  const opts: RequestInit = { credentials: "include", headers: { Accept: "application/json" } };

  function looksUnauthenticated(res: Response): boolean {
    // 401, or a 302-to-login that fetch followed into an HTML login page.
    if (res.status === 401) return true;
    if (res.redirected && /login/i.test(res.url)) return true;
    const ct = res.headers.get("content-type") ?? "";
    return !ct.includes("json");
  }

  const results: unknown[] = [];
  for (let i = 0; i < paths.length; i++) {
    const p = paths[i];
    if (!validPath(p)) {
      if (i === 0) return { error: "unauthenticated" };
      results.push(null);
      continue;
    }
    try {
      const res = await fetch(`${basePath}${p}`, opts);
      if (i === 0) {
        // Identity signal: unauthenticated/redirect/non-JSON on the first path fails the probe.
        if (looksUnauthenticated(res) || !res.ok) return { error: "unauthenticated" };
        results.push((await res.json()) as unknown);
      } else if (res.ok && (res.headers.get("content-type") ?? "").includes("json")) {
        results.push((await res.json()) as unknown);
      } else {
        results.push(null); // later paths are best-effort
      }
    } catch {
      if (i === 0) return { error: "unauthenticated" };
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
    return null;
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

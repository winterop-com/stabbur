// Thin client for the heim bind endpoints. POST /api/assistants/{id}/bind installs a bound credential
// (running the mode's server-side argv with the secret in its env); .../unbind reverses it. heim redacts
// the secret from the returned output. A non-2xx becomes a structured failure so the caller renders one
// shape. Old heim (the 404-compat registry) has no per-target routes, so `AssistantRoute.compat` falls
// back to the un-scoped /api/assistant/{bind,unbind}.

import { apiFetch } from "@/lib/http";

export interface BindApiResult {
  ok: boolean;
  exit_code?: number | null;
  stdout?: string;
  stderr?: string;
}

/** An explicit bind endpoint (base URL + optional bearer). Lets a caller with no module API
 *  config (the background worker) — and the panel with a target captured at flow start — POST
 *  to a specific backend instead of whatever apiFetch's shared config currently points at. */
export interface BindTarget {
  baseUrl: string;
  token: string | null;
}

/** Which assistant target the bind/unbind acts on, and whether to use the old un-scoped routes. */
export interface AssistantRoute {
  /** The registry target id for /api/assistants/{id}/... ; ignored in compat mode. */
  targetId: string;
  /** Old heim (404-compat registry): use the un-scoped /api/assistant/... routes instead. */
  compat: boolean;
}

function bindPath(route: AssistantRoute, action: "bind" | "unbind"): string {
  return route.compat ? `/api/assistant/${action}` : `/api/assistants/${encodeURIComponent(route.targetId)}/${action}`;
}

function failure(err: unknown): BindApiResult {
  return { ok: false, exit_code: null, stdout: "", stderr: err instanceof Error ? err.message : String(err) };
}

async function readResult(res: Response): Promise<BindApiResult> {
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    return { ok: false, exit_code: null, stdout: "", stderr: `${res.status} ${detail}`.trim() };
  }
  return (await res.json()) as BindApiResult;
}

/** POST a bind/unbind body to an explicit target, attaching its own bearer (never apiFetch's). */
async function postToTarget(target: BindTarget, path: string, body: Record<string, unknown>): Promise<BindApiResult> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (target.token) headers.Authorization = `Bearer ${target.token}`;
  let res: Response;
  try {
    res = await fetch(`${target.baseUrl}${path}`, { method: "POST", headers, body: JSON.stringify(body) });
  } catch (err) {
    return failure(err);
  }
  return readResult(res);
}

/**
 * Install a bound credential on an explicit backend + assistant target, handing heim the secret for the
 * child env. The single bind contract shared by the panel (backend captured when the flow starts, so a
 * mid-mint backend switch can't misroute the token) and the background worker. `route` picks the
 * per-target endpoint (or the compat un-scoped one for old heim).
 */
export function postBindTo(
  target: BindTarget,
  route: AssistantRoute,
  mode: string,
  secret: string,
  extraSecret?: string,
): Promise<BindApiResult> {
  const body: Record<string, unknown> = { mode, secret };
  // A session-mode write bind ships the captured XSRF token here (heim redacts it too), so the
  // bound child can satisfy DHIS2's CSRF check on writes. Omitted for reads and the PAT path.
  if (extraSecret) body.extra_secret = extraSecret;
  return postToTarget(target, bindPath(route, "bind"), body);
}

/** Reverse a bound credential by running the mode's unbind_command (on the active backend). */
export async function postUnbind(route: AssistantRoute, mode: string): Promise<BindApiResult> {
  let res: Response;
  try {
    res = await apiFetch(bindPath(route, "unbind"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
  } catch (err) {
    return failure(err);
  }
  return readResult(res);
}

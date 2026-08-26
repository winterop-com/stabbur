// Typed client for GET /api/assistant. A 404 means the stabbur project carries no
// [assistant] metadata -> the panel runs in generic mode (returns null).

import { apiFetch } from "@/lib/http";
import type { ProbeSpec } from "./sessionReads";

/** Result of a server-side verification probe of the assistant target. */
export interface AssistantVerification {
  ok: boolean;
  data: Record<string, unknown> | null;
  error: string | null;
  checked_at: number;
}

/**
 * The sanitized bind recipe echoed by GET /api/assistant: the browser-side mint recipe plus only
 * the mode *names* (a mode's argv / secret_env stay server-side). Extra keys pass through.
 */
export interface AssistantBindEcho {
  mint_mode?: string;
  fallback_mode?: string;
  mint_path?: string;
  mint_method?: string;
  mint_payload?: string;
  mint_token_field?: string;
  mint_id_field?: string;
  revoke_path?: string;
  expires_in_days?: number;
  methods_readonly?: string[];
  methods_full?: string[];
  session_cookie?: string;
  modes?: string[];
  /** Per-mode human note shown in the unbind dialog (mode name -> note), echoed by the server. */
  unbind_notes?: Record<string, string>;
  [key: string]: unknown;
}

/** Assistant metadata bound to the stabbur project (shape is open — extra keys pass through). */
export interface AssistantInfo {
  name?: string;
  base_url?: string;
  auth?: string;
  readonly?: boolean;
  source?: string;
  can_verify: boolean;
  verified: AssistantVerification | null;
  /** Session-read probe spec, echoed verbatim for the client to run (null/absent = generic). */
  probe?: ProbeSpec;
  /** Whether a bind recipe with at least one runnable mode is declared. */
  can_bind?: boolean;
  /** Sanitized bind recipe (mint recipe + mode names only). */
  bind?: AssistantBindEcho;
  [key: string]: unknown;
}

/**
 * Fetch assistant metadata. Pass verify=true to trigger a server-side probe
 * (populates `verified`; HTTP stays 200 even when verification failed).
 * Returns null on 404 (generic mode).
 */
export async function getAssistant(verify = false): Promise<AssistantInfo | null> {
  const path = verify ? "/api/assistant?verify=1" : "/api/assistant";
  const res = await apiFetch(path);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as AssistantInfo;
}

/** One assistant target in a multi-target registry: metadata plus its registry-assigned `id` and the
 *  `.mcp.json` server names whose namespaced tools route to it ([] = compat, all servers). */
export type AssistantTarget = AssistantInfo & { id: string; mcp_servers: string[] };

/**
 * The sanitized multi-target registry as returned by GET /api/assistants. `selected` / `matches` are
 * only present on a `?url=` query (server-side selection); the extension does client-side selection
 * (see `selectTarget`), so it ignores them. `compat` is set only on the 404 fallback below.
 */
export interface AssistantRegistry {
  targets: AssistantTarget[];
  selected?: string | null;
  matches?: string[];
  /** True when the registry was synthesized from the old single-assistant 404-compat path: bind /
   *  verify then use the un-scoped /api/assistant* routes instead of /api/assistants/{id}. */
  compat?: boolean;
}

const SLUG_RE = /[^a-z0-9]+/g;

/** URL/id-safe slug of a target name — the TS twin of `stabbur.project._slugify` (lower-cased, runs of
 *  non-[a-z0-9] collapsed to "-", trimmed). Used only to derive the compat target's id below. */
export function slugify(name: string): string {
  return name.toLowerCase().replace(SLUG_RE, "-").replace(/^-+|-+$/g, "");
}

/**
 * Fetch the assistant registry (GET /api/assistants). On 404 (old stabbur without the registry endpoint)
 * fall back to the single-assistant route and wrap it as a one-entry registry with a derived id (the
 * slugified name, or `target-0`) and a `compat: true` marker so bind/verify use the /api/assistant*
 * routes. `url` is forwarded as `?url=` for parity with server-side selection, but the panel selects
 * client-side and does not pass it.
 */
export async function getAssistants(url?: string): Promise<AssistantRegistry> {
  const path = url ? `/api/assistants?url=${encodeURIComponent(url)}` : "/api/assistants";
  const res = await apiFetch(path);
  if (res.status === 404) {
    const one = await getAssistant();
    if (!one) return { targets: [], compat: true };
    const id = (one.name ? slugify(one.name) : "") || "target-0";
    return { targets: [{ ...one, id, mcp_servers: [] }], compat: true };
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as AssistantRegistry;
}

/**
 * Fetch one target's metadata (GET /api/assistants/{id}); pass verify=true for a server-side probe.
 * Returns null on 404 (unknown id). The compat single-assistant path uses `getAssistant` instead.
 */
export async function getAssistantTarget(id: string, verify = false): Promise<AssistantTarget | null> {
  const path = `/api/assistants/${encodeURIComponent(id)}${verify ? "?verify=1" : ""}`;
  const res = await apiFetch(path);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as AssistantTarget;
}

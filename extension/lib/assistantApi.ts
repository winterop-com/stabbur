// Typed client for GET /api/assistant. A 404 means the heim project carries no
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

/** Assistant metadata bound to the heim project (shape is open — extra keys pass through). */
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

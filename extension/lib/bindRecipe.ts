// The browser-side mint recipe. GET /api/assistant echoes a project's [assistant.bind] block
// (sanitized): the paths, payload template, and dotted extraction fields the extension needs to
// mint a credential in the target site's own security context (the user's cookies), plus the mode
// names. Minting never leaves the page: heim only receives the extracted token via /api/assistant/bind.

import type { AssistantBindEcho } from "./assistantApi";
import { validSameOriginPath } from "./paths";
import { match } from "./tabTarget";

/** The mint half of a recipe (present only when the project declared a mint mode + endpoint). */
export interface MintRecipe {
  path: string;
  method: string;
  payloadTemplate: string;
  tokenField: string;
  idField: string;
}

/** A parsed, typed bind recipe (camelCase mirror of AssistantBindEcho). `mint` is null for a
 *  session-only recipe (no PAT endpoint), in which case only the session fallback is offered. */
export interface BindRecipe {
  /** PAT mint recipe, or null when the project declares only the session fallback. */
  mint: MintRecipe | null;
  mintMode: string;
  fallbackMode: string;
  revokePath: string;
  expiresInDays: number;
  methodsReadonly: string[];
  methodsFull: string[];
  sessionCookie: string;
}

/** The (redacted) description written into a minted token so a user can recognize it later. */
export const MINT_DESCRIPTION = "heim assistant token";

/**
 * Parse the echoed bind block into a typed recipe, or null when nothing is usable (neither a
 * mint nor a session fallback). A recipe with `mint: null` is valid — the panel opens straight
 * at the session-fallback consent. The `modes` echo field is ignored (mode names are project-
 * declared and the extension never keys off them).
 */
export function parseRecipe(bind: AssistantBindEcho | undefined | null): BindRecipe | null {
  if (!bind) return null;
  const canMint =
    typeof bind.mint_mode === "string" && typeof bind.mint_path === "string" && typeof bind.mint_payload === "string";
  const mint: MintRecipe | null = canMint
    ? {
        path: bind.mint_path as string,
        method: typeof bind.mint_method === "string" ? bind.mint_method : "POST",
        payloadTemplate: bind.mint_payload as string,
        tokenField: typeof bind.mint_token_field === "string" ? bind.mint_token_field : "",
        idField: typeof bind.mint_id_field === "string" ? bind.mint_id_field : "",
      }
    : null;
  const mintMode = typeof bind.mint_mode === "string" ? bind.mint_mode : "";
  const fallbackMode = typeof bind.fallback_mode === "string" ? bind.fallback_mode : "";
  const sessionCookie = typeof bind.session_cookie === "string" ? bind.session_cookie : "";
  // A session fallback is usable only with both a mode name and a cookie to read.
  const canSession = fallbackMode !== "" && sessionCookie !== "";
  if (!mint && !canSession) return null;
  return {
    mint,
    mintMode,
    fallbackMode,
    revokePath: typeof bind.revoke_path === "string" ? bind.revoke_path : "",
    expiresInDays: typeof bind.expires_in_days === "number" ? bind.expires_in_days : 30,
    methodsReadonly: Array.isArray(bind.methods_readonly) ? bind.methods_readonly : ["GET"],
    methodsFull: Array.isArray(bind.methods_full) ? bind.methods_full : ["GET"],
    sessionCookie,
  };
}

/** Exact-token literal substitution for the mint payload / revoke path templates. */
export function substitute(
  template: string,
  vars: { expiresMs?: number; allowedMethods?: string[]; description?: string; credentialId?: string },
): string {
  let out = template;
  if (vars.expiresMs !== undefined) out = out.split("{expires_ms}").join(String(vars.expiresMs));
  if (vars.allowedMethods !== undefined) out = out.split("{allowed_methods}").join(JSON.stringify(vars.allowedMethods));
  if (vars.description !== undefined) out = out.split("{description}").join(JSON.stringify(vars.description));
  // Credential ids ride in a URL path segment, so percent-encode them.
  if (vars.credentialId !== undefined) {
    out = out.split("{credential_id}").join(encodeURIComponent(vars.credentialId));
  }
  return out;
}

export interface MintResult {
  status: number;
  token: string;
  credentialId: string;
  /** Set when the mint never ran (bad path / tab mismatch / injection failure). */
  error?: string;
}

export type MintClass = "minted" | "unauthenticated" | "unsupported" | "forbidden" | "error";

/** Classify a mint attempt from its HTTP status + whether a token came back. */
export function classifyMint(status: number, token: string): MintClass {
  if (status >= 200 && status < 300 && token) return "minted";
  if (status === 401) return "unauthenticated";
  if (status === 404 || status === 405 || status === 501) return "unsupported";
  if (status === 403) return "forbidden";
  return "error";
}

// Injected into the page (MAIN world): POST the substituted payload with the user's cookies and
// return ONLY the extracted {status, token, credentialId} -- never the full response body. The
// caller (executeMint) has already validated the URL with paths.validSameOriginPath.
async function mintInPage(
  url: string,
  method: string,
  payload: string,
  tokenField: string,
  idField: string,
): Promise<{ status: number; token: string; credentialId: string }> {
  function walk(obj: unknown, dotted: string): string {
    if (!dotted) return "";
    let cur: unknown = obj;
    for (const seg of dotted.split(".")) {
      if (cur === null || typeof cur !== "object") return "";
      cur = (cur as Record<string, unknown>)[seg];
    }
    return typeof cur === "string" ? cur : "";
  }
  try {
    const res = await fetch(url, {
      method,
      credentials: "include",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: payload,
    });
    let token = "";
    let credentialId = "";
    try {
      const body = (await res.json()) as unknown;
      token = walk(body, tokenField);
      credentialId = walk(body, idField);
    } catch {
      // Non-JSON body -> no token extracted; status still classifies the outcome.
    }
    return { status: res.status, token, credentialId };
  } catch {
    return { status: 0, token: "", credentialId: "" };
  }
}

/**
 * Mint a credential in the tab's context. Validates the tab really sits under basePath first.
 * `expiresMs` is an ABSOLUTE epoch-ms expiry (DHIS2's `expire` is an absolute timestamp, not a
 * duration); the caller computes it once and reuses the same value for the stored binding.
 */
export async function executeMint(
  tabId: number,
  basePath: string,
  recipe: BindRecipe,
  methods: string[],
  expiresMs: number,
): Promise<MintResult> {
  const base = basePath.replace(/\/+$/, "");
  const mint = recipe.mint;
  if (!mint) return { status: 0, token: "", credentialId: "", error: "no mint recipe" };
  if (!validSameOriginPath(mint.path)) return { status: 0, token: "", credentialId: "", error: "invalid mint path" };
  let tabUrl: string | null = null;
  try {
    const tab = await chrome.tabs.get(tabId);
    tabUrl = tab.url ?? null;
  } catch {
    tabUrl = null;
  }
  if (match(tabUrl, base) !== "matched") {
    return { status: 0, token: "", credentialId: "", error: "tab does not match target" };
  }
  const payload = substitute(mint.payloadTemplate, {
    expiresMs,
    allowedMethods: methods,
    description: MINT_DESCRIPTION,
  });
  const url = `${base}${mint.path}`;
  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      func: mintInPage,
      args: [url, mint.method || "POST", payload, mint.tokenField, mint.idField],
      world: "MAIN",
    });
    const r = result?.result as { status: number; token: string; credentialId: string } | undefined;
    return r ?? { status: 0, token: "", credentialId: "", error: "no result" };
  } catch {
    return { status: 0, token: "", credentialId: "", error: "injection failed" };
  }
}

/** Revoke a credential in the tab's context (DELETE the already-substituted revoke path). */
export async function executeRevoke(
  tabId: number,
  basePath: string,
  revokePath: string,
): Promise<{ status: number }> {
  const base = basePath.replace(/\/+$/, "");
  if (!validSameOriginPath(revokePath)) return { status: 0 };
  const url = `${base}${revokePath}`;
  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      func: async (u: string): Promise<{ status: number }> => {
        try {
          const res = await fetch(u, { method: "DELETE", credentials: "include", headers: { Accept: "application/json" } });
          return { status: res.status };
        } catch {
          return { status: 0 };
        }
      },
      args: [url],
      world: "MAIN",
    });
    return (result?.result as { status: number } | undefined) ?? { status: 0 };
  } catch {
    return { status: 0 };
  }
}

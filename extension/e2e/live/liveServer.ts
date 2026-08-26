// Live-tier harness: writes a throwaway stabbur project (bound to a real GGUF model +
// the DHIS2 CLI bridge, pointed at the public play demo) and runs `stabbur serve`
// against it, so the extension panel can be driven end-to-end.

import { spawn, type ChildProcess } from "node:child_process";
import { execFileSync } from "node:child_process";
import {
  closeSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Derived, not hardcoded: this was an absolute path to one machine's checkout, which broke the
// moment the repo moved (it pointed at a directory that no longer existed) and could never work
// for anyone else. This file sits at <repo>/extension/e2e/<dir>/, so the root is three up.
export const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
export const LIBRARY_ROOT = path.join(process.env.HOME ?? "", ".local/share/stabbur/library");
export const LIVE_MODEL = "lmstudio-community/gemma-4-12B-it-QAT-GGUF";
export const LIVE_PORT = 4599;

// The public DHIS2 demo the assistant targets.
export const PLAY_BASE_URL = "https://play.im.dhis2.org/dev-2-42";
export const PLAY_PROFILE = "play42";
export const FALLBACK_INSTANCES_URL = "https://im.dhis2.org/public/instances";

// The second demo instance the multi-target tier adds. Same host as play42, told apart by path
// (/dev-2-41 vs /dev-2-42), so one origin-wide host_permissions grant covers both.
export const PLAY41_BASE_URL = "https://play.im.dhis2.org/dev-2-41";
export const PLAY41_PROFILE = "play41";

// The multi-target tier runs the shipped `dhis2-multi` model (Ornith-1.0-9B; won the tools-dhis2
// benchmark and is the template's default), not the single-tier gemma — so the target-scoped chat
// exercises the same model a real `stabbur project new --template dhis2-multi` ships.
export const LIVE_MULTI_MODEL = "deepreinforce-ai/Ornith-1.0-9B-GGUF";

// System prompts. READ_SYSTEM_PROMPT mirrors the `dhis2` template (read-only play demo);
// WRITE_SYSTEM_PROMPT mirrors the `dhis2-write` template (read+write local instance). Kept in
// sync with src/stabbur/project/templates.py so the live tiers exercise the shipped prompts.
export const READ_SYSTEM_PROMPT =
  "You are a DHIS2 assistant for a connected DHIS2 instance. For questions about DHIS2 data or metadata - counts, UIDs, names, analytics, system details - use the dhis2 tools (the dhis2_cli tool) to look up real values; never invent counts, UIDs, or metadata. To use a name in analytics or a filter, resolve it to a UID first with a metadata search or a filtered list. Messages may begin with page context supplied by the user's browser: lines labeled 'Page URL:', 'Page title:', 'Selected text:', 'Page text (truncated):', 'Browser session user:', and 'Tool account:'. Treat that context as information the user gave you: answer questions about the current page, its visible content, or the signed-in user directly from it, without calling tools, and answer general questions normally instead of refusing. Two accounts can differ: the 'Browser session user' is the person viewing the page in their browser; your tools authenticate separately as the 'Tool account'. When asked 'who am I', answer with the browser session user from the context when present; report the tool account only when asked which credentials the tools use. Keep answers concise and state the values you retrieved.";

export const WRITE_SYSTEM_PROMPT =
  "You are a DHIS2 assistant that can READ and WRITE metadata on a connected DHIS2 instance via the dhis2 tools (the dhis2_cli tool). For DHIS2 data or metadata - counts, UIDs, names, analytics - use the tools for real values; never invent them. Resolve any name to its UID before acting on it. Before creating, updating, or DELETING anything, state exactly what you are about to change; after a write, confirm the result by reading it back. Prefer NUMBER value types and sensible defaults when creating. Messages may begin with page context supplied by the user's browser: lines labeled 'Page URL:', 'Page title:', 'Selected text:', 'Page text (truncated):', 'Browser session user:', and 'Tool account:'. Answer questions about the current page or the signed-in user directly from that context, without calling tools, and answer general questions normally instead of refusing. The 'Browser session user' is the person viewing the page; your tools authenticate separately as the 'Tool account' - writes happen as the tool account, so say so when it matters. When asked 'who am I', prefer the browser session user from the context. Keep answers concise and report the UIDs and outcomes you got.";

// MULTI_SYSTEM_PROMPT mirrors the shipped `dhis2-multi` template (two read-only targets, tools
// namespaced per instance, e.g. play42__dhis2_cli / play41__dhis2_cli). Kept in sync with
// src/stabbur/project/templates.py so the live multi tier exercises the shipped prompt.
export const MULTI_SYSTEM_PROMPT =
  "You are a DHIS2 assistant that can talk to more than one connected DHIS2 instance. Each instance has its own set of dhis2 tools (a dhis2_cli tool namespaced per instance, e.g. play42__dhis2_cli and play41__dhis2_cli); use the tools for the instance the question is about. For questions about DHIS2 data or metadata - counts, UIDs, names, analytics, system details - use those tools to look up real values; never invent counts, UIDs, or metadata. To use a name in analytics or a filter, resolve it to a UID first with a metadata search or a filtered list. When a question compares two instances, query each with its own tools and report both. Messages may begin with page context supplied by the user's browser: lines labeled 'Page URL:', 'Page title:', 'Selected text:', 'Page text (truncated):', 'Browser session user:', and 'Tool account:'. Treat that context as information the user gave you: answer questions about the current page, its visible content, or the signed-in user directly from it, without calling tools, and answer general questions normally instead of refusing. The 'Browser session user' is the person viewing the page in their browser; your tools authenticate separately as the 'Tool account'. When asked 'who am I', answer with the browser session user from the context when present; report the tool account only when asked which credentials the tools use. Keep answers concise and state the values you retrieved and which instance they came from.";

/** Options controlling the fixture project `startLiveServer` writes + serves. Defaults reproduce
 *  the read-only play42 configuration the live tier has always used, so existing callers are
 *  unchanged; the write tier overrides them for a local, mutable instance. */
export interface LiveServerOptions {
  /** d2w profile name (also the `[assistant].name`). */
  profile: string;
  /** DHIS2 base URL the assistant + profile target. */
  baseUrl: string;
  /** `[assistant].readonly` flag — when false the server arms the per-write confirm gate. */
  readonly: boolean;
  /** Whether the MCP bridge is launched with `DHIS2_MCP_READONLY=1` (blocks writes at the tool). */
  mintReadonly: boolean;
  /** Locked chat model name. */
  model: string;
  /** `[project].system_prompt`. */
  systemPrompt: string;
  /** Multi-target registry: when set (>=1 entry) the fixture writes an `[[assistants]]` array (one
   *  block per target, each owning its own bridge) instead of the single `[assistant]` table, and the
   *  `.mcp.json` / `.dhis2/profiles.toml` grow one server / profile per target. Omitted (undefined) for
   *  every single-target caller, so their config is byte-for-byte unchanged. */
  targets?: TargetSpec[];
}

/** One target in a multi-target fixture: its d2w profile name (== the `[[assistants]].name`, the
 *  registry id, and the `.mcp.json` server name), the DHIS2 base URL it (and its profile) point at,
 *  and the read-only flags. */
export interface TargetSpec {
  profile: string;
  baseUrl: string;
  readonly: boolean;
  mintReadonly: boolean;
}

/** The read-only play42 defaults (today's behavior). */
export const DEFAULT_LIVE_OPTIONS: LiveServerOptions = {
  profile: PLAY_PROFILE,
  baseUrl: PLAY_BASE_URL,
  readonly: true,
  mintReadonly: true,
  model: LIVE_MODEL,
  systemPrompt: READ_SYSTEM_PROMPT,
};

/** The shipped `dhis2-multi` two-target registry: play42 (/dev-2-42) + play41 (/dev-2-41), both
 *  read-only, each with its own bridge + profile. */
export const MULTI_TARGETS: TargetSpec[] = [
  { profile: PLAY_PROFILE, baseUrl: PLAY_BASE_URL, readonly: true, mintReadonly: true },
  { profile: PLAY41_PROFILE, baseUrl: PLAY41_BASE_URL, readonly: true, mintReadonly: true },
];

const SCRATCH =
  process.env.STABBUR_E2E_SCRATCH ??
  "/private/tmp/claude-502/-Users-morteoh-dev-local-stabbur/180a1f72-7889-42d9-bb03-f191e8f9cc1f/scratchpad";

/** Reachability preflight: any HTTP response counts as reachable; only a network
 *  failure means the instance is down. Returns null when reachable, else a reason. */
export async function preflight(baseUrl: string): Promise<string | null> {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 25_000);
    await fetch(`${baseUrl}/api/system/info.json`, { signal: ctrl.signal, redirect: "manual" });
    clearTimeout(t);
    return null;
  } catch (err) {
    return `DHIS2 demo unreachable at ${baseUrl} (${String(err)}). Pick a live instance from ${FALLBACK_INSTANCES_URL}.`;
  }
}

/** Stricter than {@link preflight}: an instance is *usable* only when it answers with a status
 *  below 500. A hibernated im.dhis2.org instance replies 503 (reachable, but not serving) — that
 *  must count as down so the caller can degrade rather than verify/chat against a dead target. A
 *  redirect to login (302) or any 4xx is a live, serving instance. Retries a few times so a target
 *  that is mid-wake gets a chance to come up. Returns null when usable, else a reason. */
export async function preflightUsable(baseUrl: string, attempts = 3, delayMs = 8000): Promise<string | null> {
  let last = "no response";
  for (let i = 0; i < attempts; i += 1) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 25_000);
      const res = await fetch(`${baseUrl}/api/system/info.json`, { signal: ctrl.signal, redirect: "manual" });
      clearTimeout(t);
      if (res.status < 500) return null;
      last = `HTTP ${res.status} (instance not serving — likely hibernated)`;
    } catch (err) {
      last = String(err);
    }
    if (i < attempts - 1) await new Promise((r) => setTimeout(r, delayMs));
  }
  return `DHIS2 demo not serving at ${baseUrl} (${last}). Pick a live instance from ${FALLBACK_INSTANCES_URL}.`;
}

/** Best-effort: prime the uvx cache for the DHIS2 bridge so the first tool call
 *  doesn't pay the download cost inside the timed chat step. */
export function warmBridge(): void {
  try {
    execFileSync("uvx", ["dhis2w-mcp-bridge", "--help"], { timeout: 120_000, stdio: "ignore" });
  } catch {
    // The bridge may not support --help (it may just boot); the point is to warm
    // the uvx download cache. Ignore any non-zero/ timeout.
  }
}

// The fixture manifest mirrors the dhis2 / dhis2-write template's render_manifest — so the live
// tiers exercise the same [assistant.probe] + [assistant.bind] blocks a real `stabbur project new
// --template {dhis2,dhis2-write}` produces. Only the profile name, base_url, readonly flag, model,
// and system_prompt vary between the read (play42) and write (local_basic) configs; the probe +
// mint recipe are identical (matching _dhis2_assistant in templates.py). String.raw keeps the
// JSON-escaped backslashes in the mint_payload literal. A `project.load` of this text is implicitly
// asserted by stabbur serve booting against it.
// One `[[assistants]]` array element for the multi-target fixture. Mirrors _dhis2_assistant(...,
// owns_server=True) in src/stabbur/project/templates.py: the verify tool + `mcp_servers` are namespaced
// to this target's own bridge (server name == profile), so per-turn routing sends only its tools.
function buildAssistantBlock(t: TargetSpec): string {
  return String.raw`
[[assistants]]
name = "${t.profile}"
base_url = ${JSON.stringify(t.baseUrl)}
auth = "basic"
readonly = ${t.readonly ? "true" : "false"}
source = "d2w profile ${t.profile}"
mcp_servers = ["${t.profile}"]

[assistants.verify]
tool = "${t.profile}__dhis2_cli"
timeout = 20.0
args = { args = ["profile", "verify", "${t.profile}"] }

[assistants.probe]
paths = ["/api/me.json?fields=name,username", "/api/system/info.json"]
label = "Browsing as {name} on {instanceName} ({version})"
fields = { username = ["0.username"], name = ["0.name"], version = ["1.version"], instanceName = ["1.systemName", "1.instanceName"] }

[assistants.bind]
mint_mode = "pat"
fallback_mode = "session"
mint_path = "/api/apiToken"
mint_method = "POST"
mint_payload = "{\"type\":\"PERSONAL_ACCESS_TOKEN_V2\",\"expire\":{expires_ms},\"attributes\":[{\"type\":\"MethodAllowedList\",\"allowedMethods\":{allowed_methods}}],\"description\":{description}}"
mint_token_field = "response.key"
mint_id_field = "response.uid"
revoke_path = "/api/apiToken/{credential_id}"
expires_in_days = 30
methods_readonly = ["GET"]
methods_full = ["GET", "POST", "PUT", "PATCH", "DELETE"]
session_cookie = "JSESSIONID"

[assistants.bind.modes.pat]
command = ["d2w", "profile", "add", "${t.profile}", "--url", "{base_url}", "--auth", "pat", "--local"]
secret_env = "DHIS2_PAT"
unbind_command = ["d2w", "profile", "remove", "${t.profile}", "--local", "--yes"]
unbind_note = "Restore the shared demo profile with: cp examples/dhis2-profiles.toml .dhis2/profiles.toml"
timeout = 60.0

[assistants.bind.modes.session]
command = ["d2w", "profile", "add", "${t.profile}", "--url", "{base_url}", "--auth", "session", "--local"]
secret_env = "DHIS2_SESSION_COOKIE"
unbind_command = ["d2w", "profile", "remove", "${t.profile}", "--local", "--yes"]
unbind_note = "Restore the shared demo profile with: cp examples/dhis2-profiles.toml .dhis2/profiles.toml"
timeout = 60.0
`;
}

// The multi-target stabbur.toml: one [project] head + an [[assistants]] array (one block per target).
// Mirrors the shipped dhis2-multi template (render_manifest with a registry); stabbur loads it as an
// N-target AssistantRegistry, so /api/assistants lists every target and each chat turn routes by id.
function buildMultiStabburToml(opts: LiveServerOptions): string {
  const blocks = (opts.targets ?? []).map(buildAssistantBlock).join("");
  return String.raw`# stabbur project — a multi-target assistant (model + system prompt + N targets).
# Portable + committable: no machine-specific paths. Tools live in .mcp.json.

# Uses your machine library (STABBUR_LIBRARY_ROOT). To also read a project-local
# store, add:  libraries = ["models", "@shared"]  (relative to this file).

[project]
model = ${JSON.stringify(opts.model)}
system_prompt = ${JSON.stringify(opts.systemPrompt)}

# [[assistants]] - target metadata for UI clients; stabbur echoes it, never interprets it.
${blocks}`;
}

function buildStabburToml(opts: LiveServerOptions): string {
  if (opts.targets && opts.targets.length > 0) return buildMultiStabburToml(opts);
  // JSON.stringify emits a valid TOML basic string (escapes ", \\, control chars) for the model +
  // system_prompt, so we never hand-escape the long prompt.
  return String.raw`# stabbur project — a purpose-built assistant (model + system prompt).
# Portable + committable: no machine-specific paths. Tools live in .mcp.json.

# Uses your machine library (STABBUR_LIBRARY_ROOT). To also read a project-local
# store, add:  libraries = ["models", "@shared"]  (relative to this file).

[project]
model = ${JSON.stringify(opts.model)}
system_prompt = ${JSON.stringify(opts.systemPrompt)}

# [assistant] - target metadata for UI clients; stabbur echoes it, never interprets it.
[assistant]
name = "${opts.profile}"
base_url = ${JSON.stringify(opts.baseUrl)}
auth = "basic"
readonly = ${opts.readonly ? "true" : "false"}
source = "d2w profile ${opts.profile}"

[assistant.verify]
tool = "dhis2__dhis2_cli"
timeout = 20.0
args = { args = ["profile", "verify", "${opts.profile}"] }

[assistant.probe]
paths = ["/api/me.json?fields=name,username", "/api/system/info.json"]
label = "Browsing as {name} on {instanceName} ({version})"
fields = { username = ["0.username"], name = ["0.name"], version = ["1.version"], instanceName = ["1.systemName", "1.instanceName"] }

[assistant.bind]
mint_mode = "pat"
fallback_mode = "session"
mint_path = "/api/apiToken"
mint_method = "POST"
mint_payload = "{\"type\":\"PERSONAL_ACCESS_TOKEN_V2\",\"expire\":{expires_ms},\"attributes\":[{\"type\":\"MethodAllowedList\",\"allowedMethods\":{allowed_methods}}],\"description\":{description}}"
mint_token_field = "response.key"
mint_id_field = "response.uid"
revoke_path = "/api/apiToken/{credential_id}"
expires_in_days = 30
methods_readonly = ["GET"]
methods_full = ["GET", "POST", "PUT", "PATCH", "DELETE"]
session_cookie = "JSESSIONID"

[assistant.bind.modes.pat]
command = ["d2w", "profile", "add", "${opts.profile}", "--url", "{base_url}", "--auth", "pat", "--local"]
secret_env = "DHIS2_PAT"
unbind_command = ["d2w", "profile", "remove", "${opts.profile}", "--local", "--yes"]
unbind_note = "Restore the shared demo profile with: cp examples/dhis2-profiles.toml .dhis2/profiles.toml"
timeout = 60.0

[assistant.bind.modes.session]
command = ["d2w", "profile", "add", "${opts.profile}", "--url", "{base_url}", "--auth", "session", "--local"]
secret_env = "DHIS2_SESSION_COOKIE"
unbind_command = ["d2w", "profile", "remove", "${opts.profile}", "--local", "--yes"]
unbind_note = "Restore the shared demo profile with: cp examples/dhis2-profiles.toml .dhis2/profiles.toml"
timeout = 60.0
`;
}

function mcpServerEntry(profile: string, mintReadonly: boolean): { command: string; args: string[] } {
  const args = [`DHIS2_PROFILE=${profile}`];
  if (mintReadonly) args.push("DHIS2_MCP_READONLY=1"); // omit -> bridge runs read-write
  args.push("uvx", "dhis2w-mcp-bridge");
  return { command: "env", args };
}

function buildMcpJson(opts: LiveServerOptions): string {
  // Multi: one server per target, named by profile (matches each [[assistants]].mcp_servers entry).
  if (opts.targets && opts.targets.length > 0) {
    const servers: Record<string, { command: string; args: string[] }> = {};
    for (const t of opts.targets) servers[t.profile] = mcpServerEntry(t.profile, t.mintReadonly);
    return JSON.stringify({ mcpServers: servers }, null, 2);
  }
  return JSON.stringify({ mcpServers: { dhis2: mcpServerEntry(opts.profile, opts.mintReadonly) } }, null, 2);
}

function profileBlock(profile: string, baseUrl: string): string {
  return `[profiles.${profile}]
base_url = ${JSON.stringify(baseUrl)}
auth = "basic"
username = "admin"
password = "district"
`;
}

function buildProfilesToml(opts: LiveServerOptions): string {
  // Multi: a d2w profile per target (the default is the first/primary), each admin/district on the
  // public demo, so every target's verify + bridge resolves its own credentials.
  if (opts.targets && opts.targets.length > 0) {
    const blocks = opts.targets.map((t) => profileBlock(t.profile, t.baseUrl)).join("\n");
    return `default = "${opts.targets[0].profile}"\n\n${blocks}`;
  }
  return `default = "${opts.profile}"

${profileBlock(opts.profile, opts.baseUrl)}`;
}

export interface LiveServer {
  dir: string;
  logPath: string;
  child: ChildProcess;
  stop: () => Promise<void>;
  tailLog: (lines?: number) => string;
}

/** Create the fixture project and spawn `stabbur serve` with CORS allowing the
 *  extension origin. Does NOT wait for readiness — the panel drives that. The optional `options`
 *  select the config emitted; omitted fields fall back to the read-only play42 defaults, so the
 *  read tier's `startLiveServer(extensionId)` call is unchanged. */
export function startLiveServer(extensionId: string, options: Partial<LiveServerOptions> = {}): LiveServer {
  const opts: LiveServerOptions = { ...DEFAULT_LIVE_OPTIONS, ...options };
  const root = existsSync(SCRATCH) ? SCRATCH : tmpdir();
  const dir = mkdtempSync(path.join(root, "stabbur-live-fixture-"));
  writeFileSync(path.join(dir, "stabbur.toml"), buildStabburToml(opts));
  writeFileSync(path.join(dir, ".mcp.json"), buildMcpJson(opts));
  mkdirSync(path.join(dir, ".dhis2"), { recursive: true });
  writeFileSync(path.join(dir, ".dhis2", "profiles.toml"), buildProfilesToml(opts));

  const logPath = path.join(dir, "stabbur-serve.log");
  const logFd = openSync(logPath, "a");

  const child = spawn(
    "uv",
    ["run", "--project", REPO_ROOT, "stabbur", "serve", "--port", String(LIVE_PORT)],
    {
      cwd: dir,
      env: {
        ...process.env,
        STABBUR_LIBRARY_ROOT: LIBRARY_ROOT,
        STABBUR_CORS_ORIGINS: `chrome-extension://${extensionId}`,
      },
      detached: true, // own process group, so we can group-kill spawned runtimes
      stdio: ["ignore", logFd, logFd],
    },
  );

  const tailLog = (lines = 40): string => {
    try {
      const text = readFileSync(logPath, "utf8");
      return text.split("\n").slice(-lines).join("\n");
    } catch {
      return "(no log)";
    }
  };

  const stop = async (): Promise<void> => {
    try {
      if (child.pid) process.kill(-child.pid, "SIGTERM"); // whole group
    } catch {
      /* already gone */
    }
    // Give stabbur's supervisor time to reap the runtime, then hard-kill if needed.
    await new Promise((r) => setTimeout(r, 4000));
    try {
      if (child.pid) process.kill(-child.pid, "SIGKILL");
    } catch {
      /* gone */
    }
    try {
      closeSync(logFd);
    } catch {
      /* ignore */
    }
    rmSync(dir, { recursive: true, force: true });
  };

  return { dir, logPath, child, stop, tailLog };
}

/** Count stray llama-server processes (orphan check after teardown). */
export function countLlamaServers(): number {
  try {
    const out = execFileSync("pgrep", ["-fl", "llama-server"], { encoding: "utf8" });
    return out.split("\n").filter((l) => l.trim().length > 0).length;
  } catch {
    return 0; // pgrep exits non-zero when nothing matches
  }
}

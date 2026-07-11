// Live-tier harness: writes a throwaway kodo project (bound to a real GGUF model +
// the DHIS2 CLI bridge, pointed at the public play demo) and runs `kodo serve`
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

export const REPO_ROOT = "/Users/morteoh/dev/local/kodo";
export const LIBRARY_ROOT = path.join(process.env.HOME ?? "", ".local/share/kodo/library");
export const LIVE_MODEL = "lmstudio-community/gemma-4-12B-it-QAT-GGUF";
export const LIVE_PORT = 4599;

// The public DHIS2 demo the assistant targets.
export const PLAY_BASE_URL = "https://play.im.dhis2.org/dev-2-42";
export const PLAY_PROFILE = "play42";
export const FALLBACK_INSTANCES_URL = "https://im.dhis2.org/public/instances";

const SCRATCH =
  process.env.KODO_E2E_SCRATCH ??
  "/private/tmp/claude-502/-Users-morteoh-dev-local-kodo/180a1f72-7889-42d9-bb03-f191e8f9cc1f/scratchpad";

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

// The fixture manifest is the EXACT output of the dhis2 template's render_manifest — so the live
// tier exercises the same [assistant.probe] + [assistant.bind] blocks a real `kodo project new
// --template dhis2` produces. String.raw keeps the JSON-escaped backslashes in mint_payload literal.
// The model / base_url / profile values match the exported constants above (LIVE_MODEL,
// PLAY_BASE_URL, PLAY_PROFILE); a `project.load` of this text is implicitly asserted by kodo serve
// booting against it. Regenerate with:
//   uv run --project /Users/morteoh/dev/local/kodo python -c "from kodo import project; \
//     from kodo.project.templates import TEMPLATES; t=TEMPLATES['dhis2']; \
//     print(project.render_manifest(model='lmstudio-community/gemma-4-12B-it-QAT-GGUF', \
//       system_prompt=t.system_prompt, assistant=project.AssistantInfo.model_validate(t.assistant)))"
const KODO_TOML = String.raw`# kodo project — a purpose-built assistant (model + system prompt).
# Portable + committable: no machine-specific paths. Tools live in .mcp.json.

# Uses your machine library (KODO_LIBRARY_ROOT). To also read a project-local
# store, add:  libraries = ["models", "@shared"]  (relative to this file).

[project]
model = "lmstudio-community/gemma-4-12B-it-QAT-GGUF"
system_prompt = "You are a DHIS2 assistant for a connected DHIS2 instance. For questions about DHIS2 data or metadata - counts, UIDs, names, analytics, system details - use the dhis2 tools (the dhis2_cli tool) to look up real values; never invent counts, UIDs, or metadata. To use a name in analytics or a filter, resolve it to a UID first with a metadata search or a filtered list. Messages may begin with page context supplied by the user's browser: lines labeled 'Page URL:', 'Page title:', 'Selected text:', 'Page text (truncated):', 'Browser session user:', and 'Tool account:'. Treat that context as information the user gave you: answer questions about the current page, its visible content, or the signed-in user directly from it, without calling tools, and answer general questions normally instead of refusing. Two accounts can differ: the 'Browser session user' is the person viewing the page in their browser; your tools authenticate separately as the 'Tool account'. When asked 'who am I', answer with the browser session user from the context when present; report the tool account only when asked which credentials the tools use. Keep answers concise and state the values you retrieved."

# [assistant] - target metadata for UI clients; kodo echoes it, never interprets it.
[assistant]
name = "play42"
base_url = "https://play.im.dhis2.org/dev-2-42"
auth = "basic"
readonly = true
source = "d2w profile play42"

[assistant.verify]
tool = "dhis2__dhis2_cli"
timeout = 20.0
args = { args = ["profile", "verify", "play42"] }

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
command = ["d2w", "profile", "add", "play42", "--url", "{base_url}", "--auth", "pat", "--local"]
secret_env = "DHIS2_PAT"
unbind_command = ["d2w", "profile", "remove", "play42", "--local"]
unbind_note = "Restore the shared demo profile with: cp examples/dhis2-profiles.toml .dhis2/profiles.toml"
timeout = 60.0

[assistant.bind.modes.session]
command = ["d2w", "profile", "add", "play42", "--url", "{base_url}", "--auth", "session", "--local"]
secret_env = "DHIS2_SESSION_COOKIE"
unbind_command = ["d2w", "profile", "remove", "play42", "--local"]
unbind_note = "Restore the shared demo profile with: cp examples/dhis2-profiles.toml .dhis2/profiles.toml"
timeout = 60.0
`;

const MCP_JSON = JSON.stringify(
  {
    mcpServers: {
      dhis2: {
        command: "env",
        args: ["DHIS2_PROFILE=" + PLAY_PROFILE, "DHIS2_MCP_READONLY=1", "uvx", "dhis2w-mcp-bridge"],
      },
    },
  },
  null,
  2,
);

const PROFILES_TOML = `default = "${PLAY_PROFILE}"

[profiles.${PLAY_PROFILE}]
base_url = ${JSON.stringify(PLAY_BASE_URL)}
auth = "basic"
username = "admin"
password = "district"
`;

export interface LiveServer {
  dir: string;
  logPath: string;
  child: ChildProcess;
  stop: () => Promise<void>;
  tailLog: (lines?: number) => string;
}

/** Create the fixture project and spawn `kodo serve` with CORS allowing the
 *  extension origin. Does NOT wait for readiness — the panel drives that. */
export function startLiveServer(extensionId: string): LiveServer {
  const root = existsSync(SCRATCH) ? SCRATCH : tmpdir();
  const dir = mkdtempSync(path.join(root, "kodo-live-fixture-"));
  writeFileSync(path.join(dir, "kodo.toml"), KODO_TOML);
  writeFileSync(path.join(dir, ".mcp.json"), MCP_JSON);
  mkdirSync(path.join(dir, ".dhis2"), { recursive: true });
  writeFileSync(path.join(dir, ".dhis2", "profiles.toml"), PROFILES_TOML);

  const logPath = path.join(dir, "kodo-serve.log");
  const logFd = openSync(logPath, "a");

  const child = spawn(
    "uv",
    ["run", "--project", REPO_ROOT, "kodo", "serve", "--port", String(LIVE_PORT)],
    {
      cwd: dir,
      env: {
        ...process.env,
        KODO_LIBRARY_ROOT: LIBRARY_ROOT,
        KODO_CORS_ORIGINS: `chrome-extension://${extensionId}`,
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
    // Give kodo's supervisor time to reap the runtime, then hard-kill if needed.
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

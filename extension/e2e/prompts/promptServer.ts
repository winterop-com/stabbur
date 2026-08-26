// Spawns `heim serve --model <gemma>` locked to a single model with NO project
// (generic free-play chat) so the prompt harness and the UI spot-check drive the
// same backend the extension targets. Mirrors e2e/live/liveServer.ts.

import { spawn, type ChildProcess } from "node:child_process";
import { closeSync, existsSync, mkdtempSync, openSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Derived, not hardcoded: this was an absolute path to one machine's checkout, which broke the
// moment the repo moved (it pointed at a directory that no longer existed) and could never work
// for anyone else. This file sits at <repo>/extension/e2e/<dir>/, so the root is three up.
export const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
export const DEFAULT_LIBRARY_ROOT = path.join(process.env.HOME ?? "", ".local/share/heim/library");
export const DEFAULT_MODEL = "lmstudio-community/gemma-4-12B-it-QAT-GGUF";
export const PROMPT_PORT = Number(process.env.HEIM_PROMPT_PORT ?? 4611);

const SCRATCH =
  process.env.HEIM_E2E_SCRATCH ??
  "/private/tmp/claude-502/-Users-morteoh-dev-local-heim/180a1f72-7889-42d9-bb03-f191e8f9cc1f/scratchpad";

export interface PromptServer {
  baseUrl: string;
  logPath: string | null;
  stop: () => Promise<void>;
  tailLog: (lines?: number) => string;
}

/** Poll /api/status until state === "ready" (or timeout). */
export async function waitForReady(baseUrl: string, timeoutMs = 600_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const res = await fetch(`${baseUrl}/api/status`);
      if (res.ok) {
        const s = (await res.json()) as { state: string };
        if (s.state === "ready") return;
      }
    } catch {
      /* not up yet */
    }
    if (Date.now() > deadline) throw new Error(`heim serve not ready within ${timeoutMs}ms at ${baseUrl}`);
    await new Promise((r) => setTimeout(r, 2000));
  }
}

interface StartOpts {
  model?: string;
  libraryRoot?: string;
  port?: number;
  /** Extra CORS origins (e.g. the extension) allowed through the cross-site guard. */
  corsOrigin?: string;
}

/**
 * Start a locked-model heim serve. When HEIM_PROMPT_BASE_URL is set, reuse that
 * already-running server instead of spawning (no teardown). Returns a handle whose
 * `stop()` group-kills the spawned server (and reaped runtimes).
 */
export function startPromptServer(opts: StartOpts = {}): PromptServer {
  const existing = process.env.HEIM_PROMPT_BASE_URL;
  if (existing) {
    return { baseUrl: existing, logPath: null, stop: async () => {}, tailLog: () => "(external server)" };
  }

  const model = opts.model ?? DEFAULT_MODEL;
  const libraryRoot = opts.libraryRoot ?? DEFAULT_LIBRARY_ROOT;
  const port = opts.port ?? PROMPT_PORT;
  const root = existsSync(SCRATCH) ? SCRATCH : tmpdir();
  const dir = mkdtempSync(path.join(root, "heim-prompts-"));
  const logPath = path.join(dir, "heim-serve.log");
  const logFd = openSync(logPath, "a");

  const env: Record<string, string> = {
    ...process.env,
    HEIM_LIBRARY_ROOT: libraryRoot,
  };
  if (opts.corsOrigin) env.HEIM_CORS_ORIGINS = opts.corsOrigin;

  const child: ChildProcess = spawn(
    "uv",
    ["run", "--project", REPO_ROOT, "heim", "serve", "--model", model, "--port", String(port)],
    { cwd: dir, env, detached: true, stdio: ["ignore", logFd, logFd] },
  );

  const tailLog = (lines = 40): string => {
    try {
      return readFileSync(logPath, "utf8").split("\n").slice(-lines).join("\n");
    } catch {
      return "(no log)";
    }
  };

  const stop = async (): Promise<void> => {
    try {
      if (child.pid) process.kill(-child.pid, "SIGTERM");
    } catch {
      /* gone */
    }
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

  return { baseUrl: `http://127.0.0.1:${port}`, logPath, stop, tailLog };
}

// A tiny in-process heim API mock for the mock E2E tier. It speaks just enough of
// the heim contract (GET /api/status, POST /api/load/{name}, GET /api/tools,
// POST /api/chat SSE, GET /api/assistant) for the extension panel to drive, and
// exposes a mutable `state` so each test scripts its own scenario.
//
// It is deliberately framework-free (Node's http only) so it starts instantly on
// an ephemeral port and has no external deps.

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { AddressInfo, createServer as createNetServer } from "node:net";

export const MOCK_MODEL = "lmstudio-community/gemma-4-12B-it-QAT-GGUF";

export type Phase = "stopped" | "loading" | "ready";

export interface StatusBody {
  state: Phase;
  model: string | null;
  locked: boolean;
  n_ctx: number | null;
  error: string | null;
  default_system_prompt: string;
  project_model: string | null;
  default_chat_voice: string | null;
  voice_enabled: boolean;
  runtime_load_timeout: number;
}

/** A single SSE frame payload object (serialized as `data: {json}\n\n`). */
export type ChatFrame =
  | { type: "token"; text: string }
  | { type: "reasoning"; text: string }
  | { type: "tool"; kind: "call" | "result"; detail: string }
  | { type: "confirm"; id: string; tool: string; args: Record<string, unknown> }
  | { type: "confirm_resolved"; id: string; approved: boolean; reason: "user" | "timeout" }
  | { type: "error"; detail: string }
  | { type: "done" };

export interface MockState {
  /** Bearer token required on guarded routes; null = auth disabled. */
  token: string | null;
  /** Current lifecycle phase reported by GET /api/status. */
  phase: Phase;
  /** project_model advertised in status (drives the needs-model Load button). */
  projectModel: string | null;
  /** locked flag reported in status. */
  locked: boolean;
  /** error string reported in status (drives the error phase). */
  statusError: string | null;
  /** runtime_load_timeout advertised in status. */
  loadTimeout: number;
  /** When true, every mutating POST answers 403 (heim cross-site guard). */
  block403Post: boolean;
  /** POST /api/load: ms after which phase auto-advances to ready (loading -> ready). */
  loadReadyAfterMs: number;
  /** POST /api/load explicit HTTP status override (e.g. 409/404); null = normal. */
  loadCode: number | null;
  /** GET /api/assistant body, or "missing" for a 404. */
  assistant: Record<string, unknown> | "missing";
  /** Extra `verified` block attached when GET /api/assistant?verify=1. */
  assistantVerified: Record<string, unknown> | null;
  /** POST /api/chat: SSE frames to emit in order. */
  chatFrames: ChatFrame[];
  /** POST /api/chat: gap in ms between frames. */
  chatGapMs: number;
  /** POST /api/chat explicit HTTP status override (e.g. 409); null = stream. */
  chatCode: number | null;
  /** Raw request bodies recorded for each POST /api/chat (newest last). */
  chatRequests: string[];
  /** After a `confirm` frame, the stream blocks until a matching POST /api/chat/confirm arrives
   *  (or this many ms elapse -> an auto-denied timeout resolution). */
  confirmWaitMs: number;
  /** Frames streamed after a confirm resolves APPROVED (the tool result + any follow-up). */
  confirmApprovedTail: ChatFrame[];
  /** Frames streamed after a confirm resolves DENIED (or timed out). */
  confirmDeniedTail: ChatFrame[];
  /** Parsed bodies recorded for each POST /api/chat/confirm (newest last). */
  confirmCalls: { id: string; approve: boolean }[];
  /** Response returned by POST /api/assistant/{bind,unbind}. */
  bindResult: { ok: boolean; exit_code: number; stdout: string; stderr: string };
  /** Parsed bodies recorded for each bind/unbind call (newest last). */
  bindCalls: { endpoint: "bind" | "unbind"; body: Record<string, unknown> }[];
}

function defaultState(): MockState {
  return {
    token: null,
    phase: "ready",
    projectModel: MOCK_MODEL,
    locked: false,
    statusError: null,
    loadTimeout: 600,
    block403Post: false,
    loadReadyAfterMs: 600,
    loadCode: null,
    assistant: "missing",
    assistantVerified: null,
    chatFrames: [{ type: "token", text: "ok" }, { type: "done" }],
    chatGapMs: 5,
    chatCode: null,
    chatRequests: [],
    confirmWaitMs: 8000,
    confirmApprovedTail: [],
    confirmDeniedTail: [],
    confirmCalls: [],
    bindResult: { ok: true, exit_code: 0, stdout: "", stderr: "" },
    bindCalls: [],
  };
}

/** Read a request body to a string (used for POST bodies the tests assert on). */
async function readBody(req: IncomingMessage): Promise<string> {
  return new Promise<string>((resolve) => {
    let buf = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => {
      buf += chunk;
    });
    req.on("end", () => resolve(buf));
    req.on("error", () => resolve(buf));
  });
}

function statusBody(s: MockState): StatusBody {
  return {
    state: s.phase,
    model: s.phase === "ready" ? s.projectModel : null,
    locked: s.locked,
    n_ctx: s.phase === "ready" ? 4096 : null,
    error: s.statusError,
    default_system_prompt: "",
    project_model: s.projectModel,
    default_chat_voice: null,
    voice_enabled: false,
    runtime_load_timeout: s.loadTimeout,
  };
}

/** Reserve a free localhost port and release it (so a test can point at a URL the
 *  server is not yet listening on — the disconnected -> auto-connect scenario). */
export async function reservePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createNetServer();
    srv.once("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const port = (srv.address() as AddressInfo).port;
      srv.close(() => resolve(port));
    });
  });
}

export class HeimMock {
  state: MockState = defaultState();
  private server: Server | null = null;
  private loadTimer: ReturnType<typeof setTimeout> | null = null;

  /** Restore the default scenario (call in beforeEach). */
  reset(): void {
    if (this.loadTimer) clearTimeout(this.loadTimer);
    this.loadTimer = null;
    this.state = defaultState();
  }

  /** Start listening. Pass a reserved port to bind it, else pick an ephemeral one. */
  async start(port = 0): Promise<number> {
    this.server = createServer((req, res) => this.handle(req, res));
    return new Promise((resolve, reject) => {
      this.server!.once("error", reject);
      this.server!.listen(port, "127.0.0.1", () => {
        resolve((this.server!.address() as AddressInfo).port);
      });
    });
  }

  async stop(): Promise<void> {
    if (this.loadTimer) clearTimeout(this.loadTimer);
    this.loadTimer = null;
    const srv = this.server;
    this.server = null;
    if (!srv) return;
    await new Promise<void>((resolve) => srv.close(() => resolve()));
  }

  /** Base URL a client should target (uses 127.0.0.1 by convention). */
  baseUrl(host = "127.0.0.1"): string {
    const addr = this.server?.address() as AddressInfo | undefined;
    if (!addr) throw new Error("mock server not started");
    return `http://${host}:${addr.port}`;
  }

  private authFailed(req: IncomingMessage): boolean {
    if (!this.state.token) return false;
    const header = req.headers["authorization"] ?? "";
    const [scheme, value] = header.split(" ");
    return scheme?.toLowerCase() !== "bearer" || value?.trim() !== this.state.token;
  }

  private json(res: ServerResponse, code: number, body: unknown): void {
    const payload = JSON.stringify(body);
    res.writeHead(code, {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Headers": "*",
    });
    res.end(payload);
  }

  private async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    const path = url.pathname;
    const method = req.method ?? "GET";

    if (method === "OPTIONS") {
      res.writeHead(204, {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "*",
      });
      res.end();
      return;
    }

    // Auth gate (applies to guarded /api routes).
    if (path.startsWith("/api") && this.authFailed(req)) {
      this.json(res, 401, { detail: "unauthorized" });
      return;
    }

    // Cross-site guard on mutating POSTs.
    if (method === "POST" && this.state.block403Post) {
      this.json(res, 403, { detail: "cross-site request blocked" });
      return;
    }

    if (path === "/api/status" && method === "GET") {
      this.json(res, 200, statusBody(this.state));
      return;
    }

    if (path === "/api/tools" && method === "GET") {
      this.json(res, 200, []);
      return;
    }

    if (path.startsWith("/api/load/") && method === "POST") {
      if (this.state.loadCode !== null) {
        this.json(res, this.state.loadCode, { detail: "load rejected" });
        return;
      }
      // Enter loading and schedule the advance to ready.
      this.state.phase = "loading";
      if (this.loadTimer) clearTimeout(this.loadTimer);
      this.loadTimer = setTimeout(() => {
        this.state.phase = "ready";
      }, this.state.loadReadyAfterMs);
      this.json(res, 200, statusBody(this.state));
      return;
    }

    if (path === "/api/assistant" && method === "GET") {
      if (this.state.assistant === "missing") {
        this.json(res, 404, { detail: "No assistant metadata" });
        return;
      }
      const body: Record<string, unknown> = { ...this.state.assistant };
      if (url.searchParams.get("verify") === "1" && this.state.assistantVerified) {
        body.verified = this.state.assistantVerified;
      }
      this.json(res, 200, body);
      return;
    }

    if ((path === "/api/assistant/bind" || path === "/api/assistant/unbind") && method === "POST") {
      const raw = await readBody(req);
      let body: Record<string, unknown> = {};
      try {
        body = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        body = {};
      }
      this.state.bindCalls.push({ endpoint: path.endsWith("unbind") ? "unbind" : "bind", body });
      this.json(res, 200, this.state.bindResult);
      return;
    }

    if (path === "/api/chat" && method === "POST") {
      await this.streamChat(req, res);
      return;
    }

    if (path === "/api/chat/confirm" && method === "POST") {
      const raw = await readBody(req);
      let body: { id?: unknown; approve?: unknown } = {};
      try {
        body = JSON.parse(raw) as { id?: unknown; approve?: unknown };
      } catch {
        body = {};
      }
      this.state.confirmCalls.push({ id: String(body.id ?? ""), approve: body.approve === true });
      this.json(res, 200, { ok: true });
      return;
    }

    // Non-API GET: serve a tiny stub HTML page (used for tab-match tests).
    if (method === "GET") {
      res.writeHead(200, { "Content-Type": "text/html", "Access-Control-Allow-Origin": "*" });
      res.end(`<!doctype html><title>heim mock ${path}</title><body>stub page</body>`);
      return;
    }

    this.json(res, 404, { detail: "not found" });
  }

  private async streamChat(req: IncomingMessage, res: ServerResponse): Promise<void> {
    // Read and record the request body (tests assert on the message payload,
    // e.g. that the page-text context block reached the server).
    const body = await readBody(req);
    this.state.chatRequests.push(body);

    if (this.state.chatCode !== null) {
      this.json(res, this.state.chatCode, { detail: "chat rejected" });
      return;
    }

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Access-Control-Allow-Origin": "*",
    });

    // Detect a client disconnect (Stop button) via the response socket closing
    // before we finish. `req.on("close")` is unreliable — it can fire as soon as
    // the request body is fully received, aborting the stream prematurely.
    let clientGone = false;
    res.on("close", () => {
      if (!res.writableFinished) clientGone = true;
    });

    for (const frame of this.state.chatFrames) {
      if (clientGone || res.destroyed) return;
      res.write(`data: ${JSON.stringify(frame)}\n\n`);
      // A `confirm` frame pauses the stream (like the real server holding the tool call) until the
      // client POSTs /api/chat/confirm, then resolves it and streams the outcome-specific tail.
      if (frame.type === "confirm") {
        const approve = await this.waitForConfirm(frame.id, this.state.confirmWaitMs);
        if (clientGone || res.destroyed) return;
        const resolved: ChatFrame =
          approve === null
            ? { type: "confirm_resolved", id: frame.id, approved: false, reason: "timeout" }
            : { type: "confirm_resolved", id: frame.id, approved: approve, reason: "user" };
        res.write(`data: ${JSON.stringify(resolved)}\n\n`);
        const tail = approve ? this.state.confirmApprovedTail : this.state.confirmDeniedTail;
        for (const tf of tail) {
          if (clientGone || res.destroyed) return;
          if (this.state.chatGapMs > 0) await new Promise((r) => setTimeout(r, this.state.chatGapMs));
          res.write(`data: ${JSON.stringify(tf)}\n\n`);
        }
        if (!clientGone && !res.destroyed) res.end();
        return;
      }
      if (this.state.chatGapMs > 0) {
        await new Promise((r) => setTimeout(r, this.state.chatGapMs));
      }
    }
    if (!clientGone && !res.destroyed) res.end();
  }

  /** Poll for a POST /api/chat/confirm matching `id`; returns the approve flag, or null on timeout. */
  private async waitForConfirm(id: string, timeoutMs: number): Promise<boolean | null> {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const call = this.state.confirmCalls.find((c) => c.id === id);
      if (call) return call.approve;
      await new Promise((r) => setTimeout(r, 20));
    }
    return null;
  }
}

/**
 * A tiny "target site" (a stand-in DHIS2 instance) the content tab opens, so the bind flow and the
 * session probe run against a real cross-origin server with the browser's cookies. Serves the two
 * probe endpoints (me.json + system/info.json), the PAT mint endpoint (POST /api/apiToken, with a
 * configurable outcome), and its revoke (DELETE /api/apiToken/<id>). Non-API GETs return an HTML
 * stub so the tab navigates and script injection works.
 */
export class TargetSiteMock {
  private server: Server | null = null;
  meBody: Record<string, unknown> = { name: "Admin User", username: "admin" };
  infoBody: Record<string, unknown> = { version: "2.42", systemName: "Play Sierra Leone" };
  /** POST /api/apiToken outcome: a 2xx body that carries the token, or an error status. */
  tokenResponse: { status: number; body: unknown } = { status: 200, body: { response: { key: "d2p_test", uid: "u1" } } };
  /** Revoke calls observed (the DELETE path). */
  deleteCalls: string[] = [];
  /** Raw POST /api/apiToken bodies observed. */
  mintCalls: string[] = [];

  reset(): void {
    this.meBody = { name: "Admin User", username: "admin" };
    this.infoBody = { version: "2.42", systemName: "Play Sierra Leone" };
    this.tokenResponse = { status: 200, body: { response: { key: "d2p_test", uid: "u1" } } };
    this.deleteCalls = [];
    this.mintCalls = [];
  }

  async start(port = 0): Promise<number> {
    this.server = createServer((req, res) => void this.handle(req, res));
    return new Promise((resolve, reject) => {
      this.server!.once("error", reject);
      this.server!.listen(port, "127.0.0.1", () => {
        resolve((this.server!.address() as AddressInfo).port);
      });
    });
  }

  async stop(): Promise<void> {
    const srv = this.server;
    this.server = null;
    if (!srv) return;
    await new Promise<void>((resolve) => srv.close(() => resolve()));
  }

  baseUrl(host = "127.0.0.1"): string {
    const addr = this.server?.address() as AddressInfo | undefined;
    if (!addr) throw new Error("target site mock not started");
    return `http://${host}:${addr.port}`;
  }

  private json(res: ServerResponse, code: number, body: unknown): void {
    res.writeHead(code, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
  }

  private async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    const path = url.pathname;
    const method = req.method ?? "GET";

    if (path === "/api/me.json" && method === "GET") {
      this.json(res, 200, this.meBody);
      return;
    }
    if (path === "/api/system/info.json" && method === "GET") {
      this.json(res, 200, this.infoBody);
      return;
    }
    if (path === "/api/apiToken" && method === "POST") {
      this.mintCalls.push(await readBody(req));
      this.json(res, this.tokenResponse.status, this.tokenResponse.body);
      return;
    }
    if (path.startsWith("/api/apiToken/") && method === "DELETE") {
      this.deleteCalls.push(path);
      res.writeHead(204).end();
      return;
    }
    // Any other GET: an HTML page so the tab can navigate here and script injection works.
    if (method === "GET") {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(`<!doctype html><title>target site ${path}</title><body>signed-in stub page</body>`);
      return;
    }
    this.json(res, 404, { detail: "not found" });
  }
}

/**
 * A read-only DHIS2-style assistant record (probe + full PAT mint/bind recipe) pointed at `baseUrl`
 * — the shape GET /api/assistant returns to drive the bind flow. Lives next to TargetSiteMock
 * because its probe paths (/api/me.json, /api/system/info.json) and mint/revoke endpoints
 * (/api/apiToken[/id]) must agree with the routes that mock serves. Mirrors the dhis2 project
 * template's [assistant] blocks (see e2e/live/liveServer.ts's TOML literal, which stays in its own
 * format).
 */
export function bindAssistant(baseUrl: string): Record<string, unknown> {
  return {
    name: "play42",
    base_url: baseUrl,
    auth: "basic",
    readonly: true,
    source: "d2w profile play42",
    can_verify: false,
    verified: null,
    probe: {
      paths: ["/api/me.json?fields=name,username", "/api/system/info.json"],
      fields: {
        username: ["0.username"],
        name: ["0.name"],
        version: ["1.version"],
        instanceName: ["1.systemName", "1.instanceName"],
      },
      label: "Browsing as {name} on {instanceName} ({version})",
    },
    can_bind: true,
    bind: {
      mint_mode: "pat",
      fallback_mode: "session",
      mint_path: "/api/apiToken",
      mint_method: "POST",
      mint_payload:
        '{"type":"PERSONAL_ACCESS_TOKEN_V2","expire":{expires_ms},' +
        '"attributes":[{"type":"MethodAllowedList","allowedMethods":{allowed_methods}}],' +
        '"description":{description}}',
      mint_token_field: "response.key",
      mint_id_field: "response.uid",
      revoke_path: "/api/apiToken/{credential_id}",
      expires_in_days: 30,
      methods_readonly: ["GET"],
      methods_full: ["GET", "POST", "PUT", "PATCH", "DELETE"],
      session_cookie: "JSESSIONID",
      modes: ["pat", "session"],
    },
  };
}

// Thin client for kodo's server: status/library/load + a hand-rolled SSE chat
// loop against /api/chat (kodo's tool-aware endpoint). /api/chat emits its own
// event envelope (token/tool/error/done), NOT raw OpenAI SSE, so we parse that.

export type Role = "user" | "assistant" | "system";

export interface Msg {
  role: Role;
  content: string;
}

export interface Status {
  state: "stopped" | "loading" | "ready";
  model: string | null;
  locked: boolean;
}

export interface LibModel {
  name: string;
  model_format: string;
  size_bytes: number;
  size_human: string;
}

/** Detailed info for a single model, incl. its markdown card. */
export interface ModelInfo {
  name: string;
  model_format: string;
  size_human: string;
  path: string;
  card: string | null;
  metadata: Record<string, unknown> | null;
}

/** Options forwarded to /api/chat as sampling / tool parameters. */
export interface ChatOptions {
  maxTokens?: number;
  temperature?: number;
  topP?: number;
  useTools?: boolean;
}

/** A parsed /api/chat SSE event. */
export type ChatEvent =
  | { type: "token"; text: string }
  | { type: "reasoning"; text: string }
  | { type: "tool"; kind: "call" | "result"; detail: string }
  | { type: "error"; detail: string }
  | { type: "done" };

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export const getStatus = () => fetch("/api/status").then(json<Status>);
export const getLibrary = () => fetch("/api/library").then(json<LibModel[]>);

/** Fetch detailed info (card + metadata) for one model by name. */
export const getModelInfo = (name: string) =>
  fetch(`/api/model?name=${encodeURIComponent(name)}`).then(json<ModelInfo>);

export async function loadModel(name: string): Promise<Status> {
  // /api/load/{name:path} accepts slashes; don't encode them away.
  const res = await fetch(`/api/load/${name}`, { method: "POST" });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
  return json<Status>(res);
}

/**
 * Stream a chat completion from /api/chat, yielding typed events.
 *
 * Args:
 *   messages: the full turn list (prepend a system message upstream if wanted).
 *   signal: abort the in-flight fetch (Stop button).
 *   options: sampling / tool params; blank ones are omitted, use_tools always sent.
 */
export async function* streamChat(
  messages: Msg[],
  signal: AbortSignal,
  options: ChatOptions = {},
): AsyncGenerator<ChatEvent> {
  const body: {
    messages: Msg[];
    max_tokens?: number;
    temperature?: number;
    top_p?: number;
    use_tools: boolean;
  } = { messages, use_tools: options.useTools ?? true };
  if (options.maxTokens != null) body.max_tokens = options.maxTokens;
  if (options.temperature != null) body.temperature = options.temperature;
  if (options.topP != null) body.top_p = options.topP;

  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const s = line.trim();
      if (!s.startsWith("data:")) continue; // ignore keepalives / blank lines
      const payload = s.slice(5).trim();
      if (!payload || payload === "[DONE]") {
        if (payload === "[DONE]") yield { type: "done" };
        continue;
      }
      let evt: unknown;
      try {
        evt = JSON.parse(payload);
      } catch {
        continue; // partial chunk split across reads — skip
      }
      const parsed = parseEvent(evt);
      if (parsed) yield parsed;
    }
  }
}

function parseEvent(evt: unknown): ChatEvent | null {
  if (typeof evt !== "object" || evt === null) return null;
  const e = evt as Record<string, unknown>;
  switch (e.type) {
    case "token":
      return { type: "token", text: typeof e.text === "string" ? e.text : "" };
    case "reasoning":
      return { type: "reasoning", text: typeof e.text === "string" ? e.text : "" };
    case "tool":
      return {
        type: "tool",
        kind: e.kind === "result" ? "result" : "call",
        detail: typeof e.detail === "string" ? e.detail : "",
      };
    case "error":
      return { type: "error", detail: typeof e.detail === "string" ? e.detail : "unknown error" };
    case "done":
      return { type: "done" };
    default:
      return null;
  }
}

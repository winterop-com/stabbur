// Thin client for kodo's server: status/library/load + a hand-rolled SSE chat
// loop against /api/chat (kodo's tool-aware endpoint). /api/chat emits its own
// event envelope (token/tool/error/done), NOT raw OpenAI SSE, so we parse that.

export type Role = "user" | "assistant" | "system";

/** An OpenAI multimodal content part (text, an image data/URL, or audio). */
export type ContentPart =
  | { type: "text"; text: string }
  | { type: "image_url"; image_url: { url: string } }
  | { type: "input_audio"; input_audio: { data: string; format: string } };

export interface Msg {
  role: Role;
  content: string | ContentPart[];
}

/** Parse an audio data URL into an OpenAI input_audio part ({data, format}). */
function audioPart(dataUrl: string): ContentPart {
  let format = "wav";
  let data = dataUrl;
  if (dataUrl.startsWith("data:")) {
    const [header, payload] = dataUrl.split(",");
    data = payload ?? dataUrl;
    const sub = header.slice(5).split(";")[0].split("/")[1] || "wav"; // audio/wav -> wav
    format = ({ mpeg: "mp3", "x-wav": "wav", wave: "wav" } as Record<string, string>)[sub] ?? sub;
  }
  return { type: "input_audio", input_audio: { data, format } };
}

/** Build a message's content: plain string, or multimodal parts for images/audio. */
/** Prepend attached text/doc files to the prompt as fenced blocks (context any
 *  model can read), before the user's own text. */
function inlineFiles(text: string, files?: { name: string; text: string }[]): string {
  if (!files?.length) return text;
  const blocks = files.map((f) => `Attached file: ${f.name}\n\`\`\`\n${f.text}\n\`\`\``).join("\n\n");
  return text ? `${blocks}\n\n${text}` : blocks;
}

export function buildContent(
  text: string,
  images?: string[],
  audios?: string[],
  files?: { name: string; text: string }[],
): string | ContentPart[] {
  const body = inlineFiles(text, files);
  if ((!images || !images.length) && (!audios || !audios.length)) return body;
  const parts: ContentPart[] = [];
  if (body) parts.push({ type: "text", text: body });
  for (const url of images ?? []) parts.push({ type: "image_url", image_url: { url } });
  for (const url of audios ?? []) parts.push(audioPart(url));
  return parts;
}

export interface Status {
  state: "stopped" | "loading" | "ready";
  model: string | null;
  locked: boolean;
  n_ctx: number | null;
  error: string | null;
  default_system_prompt: string;
  project_model: string | null; // the project's bound model (kodo.toml), to auto-load on open
}

export interface LibModel {
  name: string;
  model_format: string;
  size_bytes: number;
  size_human: string;
  vision: boolean;
  audio: boolean;
  tools: boolean;
  context_length: number | null;
}

/** Model-recommended sampling defaults (from generation_config.json). */
export interface ModelSampling {
  temperature: number | null;
  top_p: number | null;
  top_k: number | null;
  min_p: number | null;
  repeat_penalty: number | null;
}

/** Detailed info for a single model, incl. its markdown card. */
export interface ModelInfo {
  name: string;
  model_format: string;
  size_human: string;
  path: string;
  card: string | null;
  metadata: Record<string, unknown> | null;
  sampling: ModelSampling;
}

export type CheckStatus = "ok" | "warn" | "fail";

/** One health check from /api/doctor. */
export interface HealthCheck {
  name: string;
  status: CheckStatus;
  detail: string;
  hint: string | null;
}

/** The full system-health report. */
export interface DoctorReport {
  checks: HealthCheck[];
}

/** One MCP tool attached to the server (namespaced <server>__<tool>). */
export interface ToolInfo {
  name: string;
  server: string;
  tool: string;
  description: string;
}

/** Options forwarded to /api/chat as sampling / tool parameters. */
export interface ChatOptions {
  maxTokens?: number;
  temperature?: number;
  topP?: number;
  useTools?: boolean;
  /** Allow-list of namespaced tool names; undefined → all attached tools. */
  enabledTools?: string[];
  /** Authoritative system prompt ("" = none); undefined → server's project default. */
  systemPrompt?: string;
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

/** List the MCP tools attached to the server (empty if none configured). */
export const getTools = () => fetch("/api/tools").then(json<ToolInfo[]>);

/** Fetch the system-health report (runtimes, library, project). */
export const getDoctor = () => fetch("/api/doctor").then(json<DoctorReport>);

/** A selectable Listen voice, spanning both TTS engines (Kokoro + OuteTTS). */
export interface Voice {
  id: string; // "kokoro:<name>" | "oute" | "oute:<model>"
  label: string;
  engine: string; // "kokoro" | "oute"
  language: string;
  gender: string;
}

/** List every available voice (Kokoro built-ins + OuteTTS); empty if no engine. */
export const getVoices = () => fetch("/api/voices").then(json<Voice[]>);

/** Synthesize text to speech for a chosen voice id; returns a WAV blob to play. */
export async function speak(text: string, voice?: string | null): Promise<Blob> {
  const res = await fetch("/api/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, ...(voice ? { voice } : {}) }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
  return res.blob();
}

/** Roll up a report to its worst status (fail > warn > ok). */
export function overallStatus(report: DoctorReport | null): CheckStatus | null {
  if (!report) return null;
  if (report.checks.some((c) => c.status === "fail")) return "fail";
  if (report.checks.some((c) => c.status === "warn")) return "warn";
  return "ok";
}

/** Fetch detailed info (card + metadata) for one model by name. */
export const getModelInfo = (name: string) =>
  fetch(`/api/model?name=${encodeURIComponent(name)}`).then(json<ModelInfo>);

/** Eject the loaded model (stops its runtime, frees memory). */
export async function unloadModel(): Promise<Status> {
  const res = await fetch("/api/unload", { method: "POST" });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
  return json<Status>(res);
}

export async function loadModel(name: string, nCtx?: number | null): Promise<Status> {
  // /api/load/{name:path} accepts slashes; don't encode them away.
  const query = nCtx != null ? `?n_ctx=${nCtx}` : "";
  const res = await fetch(`/api/load/${name}${query}`, { method: "POST" });
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
    enabled_tools?: string[];
    system_prompt?: string;
  } = { messages, use_tools: options.useTools ?? true };
  if (options.maxTokens != null) body.max_tokens = options.maxTokens;
  if (options.temperature != null) body.temperature = options.temperature;
  if (options.topP != null) body.top_p = options.topP;
  if (options.enabledTools != null) body.enabled_tools = options.enabledTools;
  if (options.systemPrompt !== undefined) body.system_prompt = options.systemPrompt;

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

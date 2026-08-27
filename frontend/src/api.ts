// Thin client for stabbur's server: status/library/load + a hand-rolled SSE chat
// loop against /api/chat (stabbur's tool-aware endpoint). /api/chat emits its own
// event envelope (token/tool/error/done), NOT raw OpenAI SSE, so we parse that.

import { apiFetch } from "@/lib/http";
import type { TagRegistry } from "@/lib/tags";

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
  // The remote /v1 this stabbur fronts (serve --upstream), or null/absent when it runs its own
  // runtimes. Optional: a backend older than the field simply doesn't send it, which reads as local.
  upstream?: string | null;
  default_system_prompt: string;
  project_model: string | null; // the project's bound model (stabbur.toml), to auto-load on open
  default_chat_voice: string | null; // the project's [project] chat_voice; UI defaults the Listen voice to it
  voice_enabled: boolean; // the project's [voice] enabled; false hides the Voice surface (text-only assistant)
  runtime_load_timeout: number; // seconds a load may take; the UI polls at least this long
  default_max_tokens?: number; // cap applied when a request omits max_tokens (0 = unbounded)
  // Stabbur's own sampling defaults — the values in force for a model that recommends none of its
  // own, so the settings panel can label an untouched control without a second copy of the
  // numbers. Optional: a backend older than the field simply doesn't send it.
  default_sampling?: ModelSampling;
}

export interface LibModel {
  name: string;
  model_format: string;
  /** Which backend this row came from — the qualifier half of a `model@backend` id.
   *  Always present, including single-backend, where it is just "local". */
  backend: string;
  /** Set only when this row is NOT a model: a declared backend that could not be listed.
   *  Such a row names the backend and must never be offered as loadable. */
  error: string | null;
  size_bytes: number;
  size_human: string;
  vision: boolean;
  audio: boolean;
  tools: boolean;
  context_length: number | null;
  tags: string[];
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
  /** The `name` of the check this one nests under, when it is a detail of another one (an MCP
   *  server under the tools row). OPTIONAL on purpose: a stabbur older than the field sends none, and
   *  HealthMenu then renders every row flat rather than losing the ones it can't place. */
  group?: string | null;
}

/** The full system-health report. */
export interface DoctorReport {
  checks: HealthCheck[];
  /** Which stabbur answered. Absent on a server older than the field. */
  version?: string;
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
  /** Sampling extensions llama.cpp / mlx accept; omit to run the model's recommended value. */
  topK?: number;
  minP?: number;
  repeatPenalty?: number;
  useTools?: boolean;
  /** Allow-list of namespaced tool names; undefined → all attached tools, `[]` → none. */
  enabledTools?: string[];
  /** Authoritative system prompt ("" = none); null/undefined → server's project default. */
  systemPrompt?: string | null;
  /** Reasoning effort for thinking models; undefined → the model's default behavior. */
  reasoning?: "off" | "low" | "medium" | "high" | "max" | null;
  /** Which tool calls require a per-action confirmation. Omit to let the server derive it from the
   *  bound assistant (the right default for the extension); only set to override that policy. */
  confirmTools?: "all" | "writes" | "none";
  /** The selected assistant target id (multi-target registry) whose MCP servers this turn routes to;
   *  null narrows to the primary target's servers + shared. Omit entirely (undefined) to leave routing
   *  to the server default (the full-library web app does this). */
  target?: string | null;
}

/** A parsed /api/chat SSE event. */
export type ChatEvent =
  | { type: "token"; text: string }
  | { type: "reasoning"; text: string }
  | {
      type: "usage";
      promptTokens: number;
      completionTokens: number;
      /** llama.cpp's own decode timings, when the runtime reports them. */
      timings?: { predictedTokens: number; predictedMs: number; promptMs: number };
    }
  | { type: "tool"; kind: "call" | "result"; detail: string }
  | { type: "confirm"; id: string; tool: string; args: Record<string, unknown> }
  /** A tool the model called that must run in the USER'S BROWSER, not on the server — the
   *  server has no DOM. Carries an action NAME and arguments, never code: what a model can do
   *  in a logged-in tab is fixed at extension-build time, not synthesised per turn
   *  (WEBMCP.md 5b). Only the extension acts on this; the web app has no tab to act on and
   *  parses it purely so the shared client stays one implementation. */
  | { type: "page_action"; id: string; action: string; args: Record<string, unknown> }
  | { type: "confirm_resolved"; id: string; approved: boolean; reason: "user" | "timeout" }
  | { type: "error"; detail: string }
  | { type: "done" };

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export const getStatus = () => apiFetch("/api/status").then(json<Status>);
export const getLibrary = () => apiFetch("/api/library").then(json<LibModel[]>);

/** Replace a model's user tags (full list). Returns the normalized saved tags. */
export const setModelTags = (model: string, tags: string[]) =>
  apiFetch("/api/tags", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, tags }),
  }).then(json<{ model: string; tags: string[] }>);

/** The tag style registry ({tag: {color, icon, description}}); set via `stabbur library tag-style`. */
export const getTagRegistry = () => apiFetch("/api/tags/registry").then(json<TagRegistry>);

/** List the MCP tools attached to the server (empty if none configured). */
export const getTools = () => apiFetch("/api/tools").then(json<ToolInfo[]>);

/**
 * One first-party MCP server stabbur ships (GET /api/mcp/servers). `/api/tools` only answers "what
 * can the agent call right now", which is empty on a fresh machine; this is the other half — the
 * whole shipped set, so the Tools panel can render a catalogue instead of a void. `enabled` is the
 * resolved truth (global mcp.json + the project's .mcp.json), `scope` names the file that switches
 * it on, and `installed: false` marks an optional server whose extra isn't built yet (`setup` says
 * how to install it).
 */
export interface McpServerInfo {
  name: string;
  command: string;
  description: string;
  enabled: boolean;
  scope: "global" | "project" | null;
  installed: boolean;
  setup: string;
  /** Env persisted in the mcp.json entry that resolves this server — usually `{}`. */
  env: Record<string, string>;
  settings: McpSetting[];
}

/**
 * One environment variable a bundled server understands, as declared by the server itself, plus the
 * value in force. `effective` is the point of the whole thing: a server's env decides what it can
 * reach (`STABBUR_FILES_ROOT` is the *only* directory the assistant can browse), but an unset default
 * like "." is invisible from outside the process — so the card can only say "a configured workspace
 * root" while the user wonders why they got a listing of the stabbur checkout. `effective` is the
 * configured value when there is one, else the resolved default (a path resolved absolute against
 * the directory `stabbur serve` runs in). Every value is a string: that is all a spawned process gets.
 * `boolean` settings are always exactly "true" / "false".
 */
export interface McpSetting {
  env: string;
  label: string;
  description: string;
  type: "text" | "path" | "boolean";
  default: string;
  effective: string;
}

/**
 * The outcome of a change, which is deliberately not just "ok". Enabling attaches the server live
 * (`applied: true`, tools callable next turn); disabling persists but cannot detach an already-
 * spawned subprocess, so it answers `applied: false, restart_required: true`; a failed spawn is
 * `applied: false` with the reason in `detail`. A settings change is the same story — a running
 * subprocess can't be handed a new environment, so it needs a restart, while a server that hasn't
 * spawned yet picks it up. Callers must render this, never a blanket success.
 */
export interface McpUpdateResult {
  server: McpServerInfo;
  applied: boolean;
  restart_required: boolean;
  detail: string;
}

/** Every bundled MCP server with its resolved on/off state (the Tools panel's catalogue). */
export const getMcpServers = () => apiFetch("/api/mcp/servers").then(json<McpServerInfo[]>);

/** POST one change to a bundled server, surfacing the server's own refusal message. */
async function postMcpServer(name: string, body: Record<string, unknown>): Promise<McpUpdateResult> {
  const res = await apiFetch(`/api/mcp/servers/${encodeURIComponent(name)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    // The refusals carry the fix in `detail` — which file owns this server (409), that it must be
    // switched on first (409), which variable it doesn't have (400) — and that is the only part
    // that makes them actionable, so surface it rather than the bare status.
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
  return json<McpUpdateResult>(res);
}

/** Switch one bundled MCP server on/off (machine-global). Returns what actually happened. */
export const setMcpServer = (name: string, enabled: boolean) => postMcpServer(name, { enabled });

/**
 * Set declared env settings on one bundled MCP server, persisted to the same machine-global
 * mcp.json. Only variables the server declares are accepted; `""` clears one back to its default.
 */
export const setMcpServerEnv = (name: string, env: Record<string, string>) => postMcpServer(name, { env });

/**
 * One assistant target in a multi-target project registry ([[assistants]]), as sanitized by
 * GET /api/assistants. Minimal mirror of the extension's shape (kept independent — the web app
 * must not import extension code); `id` is the registry's collision-safe id, `mcp_servers` names
 * the servers whose namespaced tools route to it. Extra project keys ride along untyped.
 */
export interface AssistantTarget {
  id: string;
  name?: string | null;
  base_url?: string | null;
  readonly?: boolean | null;
  mcp_servers: string[];
  [key: string]: unknown;
}

/**
 * A backend with no `/api/assistants` route at all (an older server) answers 404. Distinguished from an
 * empty registry (a current server with no project → `{targets: []}` at 200) so a caller can stop polling
 * a route that will never exist on this backend, rather than re-requesting a 404 forever.
 */
export class AssistantsUnavailableError extends Error {
  constructor() {
    super("assistant registry route not available (404)");
    this.name = "AssistantsUnavailableError";
  }
}

/**
 * List the project's assistant targets ([[assistants]]). Returns [] for a generic or single-target
 * server (an empty registry answers `{targets: []}` at 200 — "no picker"). A 404 means the route itself
 * is absent (an older backend); that throws {@link AssistantsUnavailableError} so the caller can stop
 * polling it, rather than being silently folded into the same empty list as a live-but-empty registry.
 */
export async function getAssistants(): Promise<AssistantTarget[]> {
  const res = await apiFetch("/api/assistants");
  if (res.status === 404) throw new AssistantsUnavailableError();
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const data = (await res.json()) as { targets?: AssistantTarget[] };
  return data.targets ?? [];
}

/** Fetch the system-health report (runtimes, library, project). */
export const getDoctor = () => apiFetch("/api/doctor").then(json<DoctorReport>);

/** A selectable Listen voice (Kokoro's built-in voices). */
export interface Voice {
  id: string; // "kokoro:<name>"
  label: string;
  engine: string; // "kokoro"
  language: string;
  gender: string;
}

/** List every available Listen voice (Kokoro built-ins); empty if the engine is missing. */
export const getVoices = () => apiFetch("/api/voices").then(json<Voice[]>);

/** Synthesize text to speech for a chosen voice id; returns a WAV blob to play. */
export async function speak(text: string, voice?: string | null, speed?: number | null): Promise<Blob> {
  const res = await apiFetch("/api/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, ...(voice ? { voice } : {}), ...(speed && speed !== 1 ? { speed } : {}) }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
  return res.blob();
}

/** A library voice model (TTS/STT), enriched with registry metadata, for the Voice view. */
export interface VoiceModelInfo {
  name: string;
  kind: "tts" | "stt";
  backend: string; // "kokoro-onnx" | "mlx-audio" | "llama-tts"
  display_name: string;
  description: string;
  size_human: string;
  cloneable: boolean;
  multi_speaker: boolean;
  seeded: boolean;
  voices: string[];
  languages: string[];
  chat_default: boolean;
  supported: boolean;
}

/** List library voice models (TTS + STT) for the Voice section. */
export const getVoiceModels = () => apiFetch("/api/voice").then(json<VoiceModelInfo[]>);

/** Options for /v1/audio/speech: a model + text, an optional preset voice, or a clone clip. */
export interface SpeechOptions {
  model: string; // a registry voice id, or a library repo
  input: string;
  voice?: string | null; // preset voice (Kokoro); ignored when cloning
  responseFormat?: string; // wav | mp3 | flac | opus | ogg | aac
  refAudioB64?: string | null; // base64 WAV to clone a voice from (cloneable models)
  refText?: string | null; // exact transcript of refAudioB64
  seed?: number | null; // pin a seeded model's random voice
  speed?: number | null; // playback speed multiplier (0.25-2.0); default 1
}

/** Synthesize speech via the OpenAI /v1/audio/speech endpoint; returns an audio blob. */
export async function synthesizeSpeech(opts: SpeechOptions): Promise<Blob> {
  const body: Record<string, unknown> = { model: opts.model, input: opts.input };
  if (opts.voice) body.voice = opts.voice;
  if (opts.responseFormat) body.response_format = opts.responseFormat;
  if (opts.refAudioB64) body.ref_audio_b64 = opts.refAudioB64;
  if (opts.refText) body.ref_text = opts.refText;
  if (opts.seed != null) body.seed = opts.seed;
  if (opts.speed != null && opts.speed !== 1) body.speed = opts.speed;
  const res = await apiFetch("/v1/audio/speech", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
  return res.blob();
}

/** Transcribe an audio file via /v1/audio/transcriptions (Whisper); returns the text. */
export async function transcribeAudio(file: Blob, model = "whisper", filename = "audio.wav"): Promise<string> {
  const form = new FormData();
  form.append("file", file, filename);
  form.append("model", model);
  const res = await apiFetch("/v1/audio/transcriptions", { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
  const data = await json<{ text: string }>(res);
  return data.text;
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
  apiFetch(`/api/model?name=${encodeURIComponent(name)}`).then(json<ModelInfo>);

/** Eject the loaded model (stops its runtime, frees memory). */
export async function unloadModel(): Promise<Status> {
  const res = await apiFetch("/api/unload", { method: "POST" });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
  return json<Status>(res);
}

export async function loadModel(name: string, nCtx?: number | null): Promise<Status> {
  // /api/load/{name:path} accepts slashes, so keep them — but encode each segment
  // so reserved characters (?, #, %, spaces) in a name can't break the URL.
  const path = name.split("/").map(encodeURIComponent).join("/");
  const query = nCtx != null ? `?n_ctx=${nCtx}` : "";
  const res = await apiFetch(`/api/load/${path}${query}`, { method: "POST" });
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
    top_k?: number;
    min_p?: number;
    repeat_penalty?: number;
    use_tools: boolean;
    enabled_tools?: string[];
    system_prompt?: string;
    confirm_tools?: "all" | "writes" | "none";
    target?: string | null;
    reasoning?: "off" | "low" | "medium" | "high" | "max";
  } = { messages, use_tools: options.useTools ?? true };
  if (options.maxTokens != null) body.max_tokens = options.maxTokens;
  if (options.temperature != null) body.temperature = options.temperature;
  if (options.topP != null) body.top_p = options.topP;
  if (options.topK != null) body.top_k = options.topK;
  if (options.minP != null) body.min_p = options.minP;
  if (options.repeatPenalty != null) body.repeat_penalty = options.repeatPenalty;
  // An empty list is meaningful (this chat may call *no* tools), so test for null/undefined, not
  // truthiness — `[]` narrows the toolset to nothing, while omitting the field means "all of them".
  if (options.enabledTools != null) body.enabled_tools = options.enabledTools;
  if (options.systemPrompt != null) body.system_prompt = options.systemPrompt; // null → omit (use project default)
  if (options.reasoning != null) body.reasoning = options.reasoning; // null → omit (model default)
  // Send `target` whenever the caller sets it (including an explicit null = narrow to primary+shared);
  // undefined means "leave routing to the server" (the full-library web app), so omit it then.
  if (options.target !== undefined) body.target = options.target;
  // Omit confirm_tools unless explicitly overridden so the server derives the policy from the
  // bound assistant (the extension always omits it).
  if (options.confirmTools != null) body.confirm_tools = options.confirmTools;

  const res = await apiFetch("/api/chat", {
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

  // Parse one complete SSE line into at most one event. Kept as a generator so both the
  // per-chunk loop and the final-flush path below share identical parsing.
  function* parseLine(line: string): Generator<ChatEvent> {
    const s = line.trim();
    if (!s.startsWith("data:")) return; // ignore keepalives / blank lines
    const payload = s.slice(5).trim();
    if (!payload || payload === "[DONE]") {
      if (payload === "[DONE]") yield { type: "done" };
      return;
    }
    let evt: unknown;
    try {
      evt = JSON.parse(payload);
    } catch {
      return; // unparseable line — skip it
    }
    const parsed = parseEvent(evt);
    if (parsed) yield parsed;
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? ""; // keep the trailing partial line for the next read
    for (const line of lines) yield* parseLine(line);
  }
  // Flush the decoder (release any buffered multibyte char) and process the leftover buffer:
  // a terminal `error`/`done` event or `[DONE]` that arrives without a trailing newline lands
  // here, and on a truncated connection that terminal event is exactly the one that matters.
  buffer += decoder.decode();
  if (buffer.trim()) yield* parseLine(buffer);
}

/**
 * Resolve a pending tool confirmation for an in-flight chat stream. The server holds the tool
 * call until this lands (or it times out); the stream then resumes on its own, so callers must
 * NOT abort the stream. A 404 (unknown or already-resolved id) surfaces as an error.
 */
/**
 * Report a page action's outcome, unblocking the agent loop that is waiting on it.
 *
 * The mirror of {@link confirmAction}: the server registered a future when it streamed the
 * `page_action` event and is blocked until this lands. A FAILURE still has to be reported —
 * silence is only distinguishable from a slow tab by the server's timeout, which costs the
 * user that wait for nothing.
 */
export async function reportPageAction(
  id: string,
  outcome: { ok: true; result: unknown } | { ok: false; error: string },
): Promise<void> {
  const res = await apiFetch("/api/chat/page-action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, ...outcome }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
  }
}

export async function confirmAction(id: string, approve: boolean): Promise<void> {
  const res = await apiFetch("/api/chat/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, approve }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `${res.status} ${res.statusText}`);
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
    case "usage": {
      const u = (e.usage ?? {}) as Record<string, unknown>;
      const n = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : 0);
      const t = u.timings as Record<string, unknown> | undefined;
      return {
        type: "usage",
        promptTokens: n(u.prompt_tokens),
        completionTokens: n(u.completion_tokens),
        timings: t
          ? { predictedTokens: n(t.predicted_n), predictedMs: n(t.predicted_ms), promptMs: n(t.prompt_ms) }
          : undefined,
      };
    }
    case "tool":
      return {
        type: "tool",
        kind: e.kind === "result" ? "result" : "call",
        detail: typeof e.detail === "string" ? e.detail : "",
      };
    case "page_action":
      return {
        type: "page_action",
        id: typeof e.id === "string" ? e.id : "",
        action: typeof e.action === "string" ? e.action : "",
        args: e.args !== null && typeof e.args === "object" ? (e.args as Record<string, unknown>) : {},
      };
    case "confirm":
      return {
        type: "confirm",
        id: typeof e.id === "string" ? e.id : "",
        tool: typeof e.tool === "string" ? e.tool : "",
        args: e.args !== null && typeof e.args === "object" ? (e.args as Record<string, unknown>) : {},
      };
    case "confirm_resolved":
      return {
        type: "confirm_resolved",
        id: typeof e.id === "string" ? e.id : "",
        approved: e.approved === true,
        reason: e.reason === "timeout" ? "timeout" : "user",
      };
    case "error":
      return { type: "error", detail: typeof e.detail === "string" ? e.detail : "unknown error" };
    case "done":
      return { type: "done" };
    default:
      return null;
  }
}

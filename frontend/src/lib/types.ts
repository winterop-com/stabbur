import type { Role } from "@/api";
import type { Settings } from "@/lib/store";

/** An inline tool-activity marker shown within an assistant turn. */
export interface ToolMarker {
  kind: "call" | "result";
  detail: string;
}

/** A per-action write confirmation the server is holding a tool call on. `pending` shows the
 *  Approve/Deny buttons; `resolved` carries the outcome (a user decision clears itself once the
 *  server echoes it; a timeout stays as an auto-denied note). Transient by nature — pending ones
 *  are stripped when the stream ends. */
export interface PendingConfirm {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  status: "pending" | "resolved";
  approved?: boolean;
  reason?: "user" | "timeout";
}

/** A pending composer attachment. Image/audio are data URLs sent as content
 *  parts (need a vision/audio model); text/doc files are inlined into the prompt
 *  (work with any model), so they carry a filename + decoded contents instead.
 *  A PDF is not a kind of its own: it resolves to text or to rendered page images
 *  before it ever gets here (see lib/attachments). */
export type MediaKind = "image" | "audio" | "text";
export interface Attachment {
  kind: MediaKind;
  url?: string; // data URL for image/audio (used as <img>/<audio> src)
  name?: string; // source filename, shown on the preview chip (all kinds)
  text?: string; // decoded file contents, inlined into the message on send (text)
  pages?: number; // page count for text extracted from a PDF (chip detail only)
}

/** A text/doc file attached to a sent message: filename + contents, inlined into
 *  the prompt as a fenced block so any model can use it as context. */
export interface AttachedFile {
  name: string;
  text: string;
}

/** One message in a conversation. Assistant turns may carry tool markers. */
/** What a finished turn cost: the runtime's token counts plus measured wall time. */
export interface GenerationStats {
  promptTokens: number;
  completionTokens: number;
  /** Wall time for the whole turn, prompt processing included. */
  seconds: number;
  /** Seconds until the first token arrived (prompt processing / queueing). */
  ttftSeconds: number;
  /**
   * Decode rate: tokens per second measured from the FIRST token, not from the request.
   * Including prompt processing would make the figure ramp up from ~0 instead of reading
   * the model's actual generation speed.
   */
  tokensPerSecond: number;
}

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  images?: string[]; // attached image data URLs (user turns, vision models)
  audios?: string[]; // attached audio data URLs (user turns, audio models)
  files?: AttachedFile[]; // attached text/doc files (inlined into the prompt on send)
  reasoning?: string; // reasoning-model thinking (shown collapsed)
  tools?: ToolMarker[];
  confirms?: PendingConfirm[]; // per-action write confirmations awaiting (or reflecting) a decision
  stats?: GenerationStats; // token accounting + wall time for a finished assistant turn
  error?: boolean;
  model?: string; // the model that produced this turn (assistant turns), for export fidelity
}

/**
 * How a conversation's title came to say what it says. Recorded rather than inferred, because the
 * only question anyone asks of it — may this title be replaced? — cannot be answered by looking at
 * the string: a name the user typed is indistinguishable from one the model produced, and a
 * comparison against `deriveTitle`'s output would make a hand-typed name that happens to match
 * fair game for overwriting.
 *
 * - `derived` — the first 40 characters of the first message (lib/store's `deriveTitle`), and the
 *   placeholder every conversation starts on. Replaceable.
 * - `model` — named by the model that answered the first exchange (lib/title). Replaceable, so a
 *   later attempt can improve on it; nothing currently makes one.
 * - `user` — typed into the sidebar's rename. NEVER replaced by anything automatic.
 */
export type TitleSource = "derived" | "model" | "user";

/** A persisted conversation. Settings are per-conversation, not global, so each
 *  chat starts fresh and its system prompt / sampling never leak into the next. */
export interface Conversation {
  id: string;
  title: string;
  /** Where `title` came from, and therefore whether it may be replaced — see {@link TitleSource}.
   *  A record written before this field existed reads back as `derived`. */
  titledBy: TitleSource;
  messages: ChatMessage[];
  settings: Settings;
  createdAt: number;
  updatedAt: number;
}

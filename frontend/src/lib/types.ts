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
 *  (work with any model), so they carry a filename + decoded contents instead. */
export type MediaKind = "image" | "audio" | "text";
export interface Attachment {
  kind: MediaKind;
  url?: string; // data URL for image/audio (used as <img>/<audio> src)
  name?: string; // filename (text/doc attachments)
  text?: string; // decoded file contents, inlined into the message on send (text)
}

/** A text/doc file attached to a sent message: filename + contents, inlined into
 *  the prompt as a fenced block so any model can use it as context. */
export interface AttachedFile {
  name: string;
  text: string;
}

/** One message in a conversation. Assistant turns may carry tool markers. */
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
  error?: boolean;
  model?: string; // the model that produced this turn (assistant turns), for export fidelity
  mediaDropped?: number; // inline attachments stripped to fit the storage quota (see saveConversations)
}

/** A persisted conversation. Settings are per-conversation, not global, so each
 *  chat starts fresh and its system prompt / sampling never leak into the next. */
export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  settings: Settings;
  createdAt: number;
  updatedAt: number;
}

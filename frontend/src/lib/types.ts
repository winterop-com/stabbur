import type { Role } from "@/api";
import type { Settings } from "@/lib/store";

/** An inline tool-activity marker shown within an assistant turn. */
export interface ToolMarker {
  kind: "call" | "result";
  detail: string;
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
  error?: boolean;
  model?: string; // the model that produced this turn (assistant turns), for export fidelity
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

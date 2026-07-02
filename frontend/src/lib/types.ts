import type { Role } from "@/api";
import type { Settings } from "@/lib/store";

/** An inline tool-activity marker shown within an assistant turn. */
export interface ToolMarker {
  kind: "call" | "result";
  detail: string;
}

/** A pending composer attachment (image or audio), as a data URL. */
export type MediaKind = "image" | "audio";
export interface Attachment {
  url: string;
  kind: MediaKind;
}

/** One message in a conversation. Assistant turns may carry tool markers. */
export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  images?: string[]; // attached image data URLs (user turns, vision models)
  audios?: string[]; // attached audio data URLs (user turns, audio models)
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

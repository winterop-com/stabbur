import type { Role } from "@/api";

/** An inline tool-activity marker shown within an assistant turn. */
export interface ToolMarker {
  kind: "call" | "result";
  detail: string;
}

/** One message in a conversation. Assistant turns may carry tool markers. */
export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  reasoning?: string; // reasoning-model thinking (shown collapsed)
  tools?: ToolMarker[];
  error?: boolean;
}

/** A persisted conversation. */
export interface Conversation {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
}

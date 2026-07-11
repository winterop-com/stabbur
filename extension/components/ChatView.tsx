import { memo, useEffect, useRef, useState } from "react";
import { FileText, Send, Square } from "lucide-react";
import { streamChat, type Msg, type Role } from "@/api";
import { cn } from "@/lib/utils";
import { Markdown } from "@/components/Markdown";
import { ToolMarkerChip } from "@/components/ToolMarkerChip";

// Per-backend transcript keys (`${STORAGE_PREFIX}${backendId}`); the bare legacy key
// held the single pre-multi-backend transcript and is adopted once, then removed.
const STORAGE_PREFIX = "kodo-ext-conversation:";
const LEGACY_STORAGE_KEY = "kodo-ext-conversation";

interface ToolEvent {
  kind: "call" | "result";
  detail: string;
}

interface ChatMessage {
  role: Role;
  content: string;
  /** Transient page-context block folded into the API turn (not persisted). */
  context?: string;
  /** Transient reasoning stream (not persisted). */
  reasoning?: string;
  /** Transient tool call/result markers (not persisted). */
  tools?: ToolEvent[];
}

interface ChatViewProps {
  /** Which backend this transcript belongs to; scopes the localStorage key. */
  backendId: string;
  pageContextEnabled: boolean;
  onTogglePageContext: (value: boolean) => void;
  /** Sub-option of page context: also attach the visible page text. */
  pageTextEnabled: boolean;
  onTogglePageText: (value: boolean) => void;
  /** Build the page-context block to fold into the next user turn (null = none). */
  getContextBlock: () => Promise<string | null>;
}

function loadStored(storageKey: string): ChatMessage[] {
  try {
    let raw = localStorage.getItem(storageKey);
    // One-time legacy adoption: the first backend to mount with no transcript of its own
    // claims the pre-multi-backend key's content, then removes it so no other backend does.
    if (raw === null) {
      const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
      if (legacy !== null) {
        localStorage.setItem(storageKey, legacy);
        localStorage.removeItem(LEGACY_STORAGE_KEY);
        raw = legacy;
      }
    }
    if (!raw) return [];
    const parsed = JSON.parse(raw) as { messages?: { role: Role; content: string }[] };
    // Drop empty turns (failed streams persisted by older builds) so they are not replayed.
    return (parsed.messages ?? []).filter((m) => m.content !== "").map((m) => ({ role: m.role, content: m.content }));
  } catch {
    return [];
  }
}

function toApiMessages(messages: ChatMessage[]): Msg[] {
  return messages.map((m) => ({
    role: m.role,
    content: m.context ? `${m.context}\n\n${m.content}` : m.content,
  }));
}

/** Tool call/result chip: the shared ToolMarkerChip wrapped so E2E can target it
 *  by data-testid (the shared component carries none). */
function ToolChip({ event }: { event: ToolEvent }) {
  return (
    <div data-testid={event.kind === "call" ? "tool-chip-call" : "tool-chip-result"}>
      <ToolMarkerChip marker={event} />
    </div>
  );
}

// Memoized: patchLast preserves object identity for every message but the streaming one, so
// earlier bubbles skip re-rendering on each token frame (a long transcript would otherwise
// reconcile every bubble per token).
const MessageBubble = memo(function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={cn("flex flex-col gap-1", isUser ? "items-end" : "items-start")}>
      {message.reasoning ? (
        <details className="w-full rounded border border-[var(--border)] bg-[var(--muted)] px-2 py-1 text-xs">
          <summary className="cursor-pointer text-[var(--muted-foreground)]">Reasoning</summary>
          <div className="mt-1 whitespace-pre-wrap text-[var(--muted-foreground)]">{message.reasoning}</div>
        </details>
      ) : null}

      {message.tools?.length ? (
        <div className="w-full space-y-1">
          {message.tools.map((t, i) => (
            <ToolChip key={i} event={t} />
          ))}
        </div>
      ) : null}

      {message.content ? (
        <div
          className={cn(
            "max-w-[90%] rounded-lg px-3 py-2 text-sm",
            isUser
              ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
              : "bg-[var(--muted)] text-[var(--foreground)]",
          )}
        >
          {isUser ? (
            <div className="whitespace-pre-wrap break-words">{message.content}</div>
          ) : (
            <div className="break-words">
              <Markdown content={message.content} />
            </div>
          )}
        </div>
      ) : null}

      {isUser && message.context ? (
        <span className="inline-flex items-center gap-1 text-[10px] text-[var(--muted-foreground)]">
          <FileText className="h-3 w-3" /> page context attached
        </span>
      ) : null}
    </div>
  );
});

/** The transcript + composer, sized for a ~360px side panel. */
export function ChatView({
  backendId,
  pageContextEnabled,
  onTogglePageContext,
  pageTextEnabled,
  onTogglePageText,
  getContextBlock,
}: ChatViewProps) {
  // PanelApp keys this component by backendId, so a switch remounts it and this runs
  // fresh against the new backend's transcript key.
  const storageKey = `${STORAGE_PREFIX}${backendId}`;
  const [messages, setMessages] = useState<ChatMessage[]>(() => loadStored(storageKey));
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Persist only the durable role+content (context/reasoning/tools are transient), and only
  // between streams: patchLast fires setMessages per SSE frame, so persisting on every change
  // would JSON.stringify the whole transcript once per token (O(n^2) per reply). Empty
  // assistant bubbles (a stream that died before its first token) are dropped, not stored.
  useEffect(() => {
    if (streaming) return;
    try {
      const durable = messages
        .filter((m) => m.content !== "")
        .map((m) => ({ role: m.role, content: m.content }));
      localStorage.setItem(storageKey, JSON.stringify({ messages: durable }));
    } catch {
      // Storage full / unavailable -- history simply won't persist.
    }
  }, [messages, streaming]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  // Update the last (assistant) message in place as events stream in.
  function patchLast(fn: (m: ChatMessage) => ChatMessage): void {
    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const next = prev.slice();
      next[next.length - 1] = fn(next[next.length - 1]);
      return next;
    });
  }

  async function send(): Promise<void> {
    const text = input.trim();
    if (!text || streaming) return;
    setError(null);

    const context = pageContextEnabled ? await getContextBlock() : null;
    const userMsg: ChatMessage = { role: "user", content: text, context: context ?? undefined };
    const history = [...messages, userMsg];
    setMessages([...history, { role: "assistant", content: "" }]);
    setInput("");

    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    try {
      for await (const evt of streamChat(toApiMessages(history), controller.signal, {
        useTools: true,
      })) {
        switch (evt.type) {
          case "token":
            patchLast((m) => ({ ...m, content: m.content + evt.text }));
            break;
          case "reasoning":
            patchLast((m) => ({ ...m, reasoning: (m.reasoning ?? "") + evt.text }));
            break;
          case "tool":
            patchLast((m) => ({ ...m, tools: [...(m.tools ?? []), { kind: evt.kind, detail: evt.detail }] }));
            break;
          case "error":
            setError(evt.detail);
            break;
          case "done":
            break;
        }
      }
    } catch (err) {
      if (!(err instanceof DOMException && err.name === "AbortError")) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      // A stream that produced no reply leaves its empty placeholder bubble; drop it so it
      // is neither rendered nor replayed to /api/chat as an empty assistant turn.
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        return last && last.role === "assistant" && last.content === "" && !last.reasoning && !last.tools?.length
          ? prev.slice(0, -1)
          : prev;
      });
      setStreaming(false);
      abortRef.current = null;
    }
  }

  function stop(): void {
    abortRef.current?.abort();
  }

  function clear(): void {
    if (streaming) return;
    setMessages([]);
    setError(null);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-3">
        {messages.length === 0 ? (
          <p className="mt-8 text-center text-sm text-[var(--muted-foreground)]">
            Ask your local kodo assistant anything.
          </p>
        ) : (
          messages.map((m, i) => <MessageBubble key={i} message={m} />)
        )}
        {streaming && messages[messages.length - 1]?.content === "" ? (
          <div className="flex items-center gap-1 px-1 text-[var(--muted-foreground)]">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current [animation-delay:150ms]" />
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current [animation-delay:300ms]" />
          </div>
        ) : null}
      </div>

      {error ? (
        <div className="mx-3 mb-2 rounded border border-[var(--destructive)] bg-[var(--destructive)]/10 px-2 py-1.5 text-xs text-[var(--destructive)]">
          {error}
        </div>
      ) : null}

      <div className="border-t border-[var(--border)] p-2">
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => onTogglePageContext(!pageContextEnabled)}
              className={cn(
                "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]",
                pageContextEnabled
                  ? "border-[var(--primary)] bg-[var(--accent)] text-[var(--foreground)]"
                  : "border-[var(--border)] text-[var(--muted-foreground)]",
              )}
            >
              <FileText className="h-3 w-3" />
              Page context {pageContextEnabled ? "on" : "off"}
            </button>
            {pageContextEnabled ? (
              <button
                type="button"
                onClick={() => onTogglePageText(!pageTextEnabled)}
                title="Include the page's visible text (up to 8000 chars)"
                className={cn(
                  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]",
                  pageTextEnabled
                    ? "border-[var(--primary)] bg-[var(--accent)] text-[var(--foreground)]"
                    : "border-[var(--border)] text-[var(--muted-foreground)]",
                )}
              >
                Page text {pageTextEnabled ? "on" : "off"}
              </button>
            ) : null}
          </div>
          <button
            type="button"
            onClick={clear}
            disabled={streaming || messages.length === 0}
            className="text-[11px] text-[var(--muted-foreground)] hover:text-[var(--foreground)] disabled:opacity-40"
          >
            Clear
          </button>
        </div>
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            placeholder="Message (Enter to send, Shift+Enter for newline)"
            className="max-h-40 flex-1 resize-none rounded border border-[var(--border)] bg-[var(--background)] px-2 py-1.5 text-sm outline-none focus:border-[var(--primary)]"
          />
          {streaming ? (
            <button
              type="button"
              onClick={stop}
              aria-label="Stop"
              className="rounded bg-[var(--destructive)] p-2 text-white"
            >
              <Square className="h-4 w-4" />
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void send()}
              disabled={!input.trim()}
              aria-label="Send"
              className="rounded bg-[var(--primary)] p-2 text-[var(--primary-foreground)] disabled:opacity-40"
            >
              <Send className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { FileText, Send, ShieldAlert, Square } from "lucide-react";
import { confirmAction, streamChat, type Msg, type Role } from "@/api";
import { cn } from "@/lib/utils";
import { Markdown } from "@/components/Markdown";
import { ToolMarkerChip } from "@/components/ToolMarkerChip";

// Per-backend transcript keys (`${STORAGE_PREFIX}${backendId}`); the bare legacy key
// held the single pre-multi-backend transcript and is adopted once, then removed.
const STORAGE_PREFIX = "stabbur-ext-conversation:";
const LEGACY_STORAGE_KEY = "stabbur-ext-conversation";

interface ToolEvent {
  kind: "call" | "result";
  detail: string;
}

/** A per-action write confirmation the server is holding a tool call on. `pending` shows the
 *  Approve/Deny buttons; `resolved` carries the outcome (a user decision clears itself once the
 *  server echoes it; a timeout stays as an auto-denied note). Transient, never persisted. */
interface PendingConfirm {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  status: "pending" | "resolved";
  approved?: boolean;
  reason?: "user" | "timeout";
}

interface ChatMessage {
  role: Role;
  content: string;
  /** Transient page-context block folded into the API turn (not persisted). */
  context?: string;
  /** The PAGE part of the context could not be captured (no host access to the site). */
  pageMissing?: boolean;
  /** Transient reasoning stream (not persisted). */
  reasoning?: string;
  /** Transient tool call/result markers (not persisted). */
  tools?: ToolEvent[];
  /** Transient per-action confirmations awaiting (or reflecting) a decision (not persisted). */
  confirms?: PendingConfirm[];
}

/** One-line, clipped rendering of a confirmation's args (mirrors the tool-chip digest style). */
function compactArgs(args: Record<string, unknown>): string {
  const summarize = (v: unknown): string => {
    if (Array.isArray(v)) return `[${v.length} items]`;
    if (v !== null && typeof v === "object") return "{...}";
    const s = typeof v === "string" ? v : String(v);
    return s.length > 40 ? `${s.slice(0, 40)}...` : s;
  };
  const pairs = Object.entries(args).map(([k, v]) => `${k}: ${summarize(v)}`).join(", ");
  return pairs.length > 120 ? `${pairs.slice(0, 120)}...` : pairs;
}

interface ChatViewProps {
  /** Which backend this transcript belongs to; scopes the localStorage key. */
  backendId: string;
  /** The selected assistant target id whose MCP servers this turn routes to (null = primary + shared,
   *  or free-play when the backend has no registry). Sent as `target` on each /api/chat turn. */
  target: string | null;
  pageContextEnabled: boolean;
  onTogglePageContext: (value: boolean) => void;
  /** Sub-option of page context: also attach the visible page text. */
  pageTextEnabled: boolean;
  onTogglePageText: (value: boolean) => void;
  /** Build the page-context block to fold into the next user turn (text null = none). */
  getContextBlock: () => Promise<{ text: string | null; pageMissing: boolean }>;
  /** FIRST await of a Send with page context on: request host access on the click gesture. */
  onEnsurePageAccess: () => Promise<void>;
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

/** Inline Approve/Deny card for a write action the server is holding. Resolving does NOT abort the
 *  stream — the server resumes and streams the tool result once a decision (or timeout) lands. */
function ConfirmCard({
  confirm,
  onResolve,
}: {
  confirm: PendingConfirm;
  onResolve: (id: string, approve: boolean) => void;
}) {
  const args = compactArgs(confirm.args);
  const resolved = confirm.status === "resolved";
  const outcome = confirm.reason === "timeout" ? "Auto-denied (timed out)." : confirm.approved ? "Approved." : "Denied.";
  return (
    <div
      data-testid="chat-confirm"
      className="w-full rounded-md border border-amber-500/50 bg-amber-500/10 px-2 py-1.5 text-xs"
    >
      <div className="flex items-center gap-1.5 font-medium text-[var(--foreground)]">
        <ShieldAlert className="h-3.5 w-3.5 text-amber-600" /> Confirm action
      </div>
      <div className="mt-1 break-all font-mono text-[var(--muted-foreground)]">
        <span className="font-medium text-[var(--foreground)]">{confirm.tool}</span>
        {args ? `(${args})` : null}
      </div>
      {resolved ? (
        <div data-testid="chat-confirm-outcome" className="mt-1 text-[var(--muted-foreground)]">
          {outcome}
        </div>
      ) : (
        <div className="mt-1.5 flex items-center gap-2">
          <button
            type="button"
            data-testid="chat-confirm-approve"
            onClick={() => onResolve(confirm.id, true)}
            className="rounded bg-[var(--primary)] px-2 py-0.5 text-[var(--primary-foreground)]"
          >
            Approve
          </button>
          <button
            type="button"
            data-testid="chat-confirm-deny"
            onClick={() => onResolve(confirm.id, false)}
            className="rounded border border-[var(--border)] px-2 py-0.5 hover:bg-[var(--accent)]"
          >
            Deny
          </button>
        </div>
      )}
    </div>
  );
}

// Memoized: patchLast preserves object identity for every message but the streaming one, so
// earlier bubbles skip re-rendering on each token frame (a long transcript would otherwise
// reconcile every bubble per token).
const MessageBubble = memo(function MessageBubble({
  message,
  onResolveConfirm,
}: {
  message: ChatMessage;
  onResolveConfirm: (id: string, approve: boolean) => void;
}) {
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

      {message.confirms?.length ? (
        <div className="w-full space-y-1">
          {message.confirms.map((c) => (
            <ConfirmCard key={c.id} confirm={c} onResolve={onResolveConfirm} />
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

      {isUser && message.pageMissing ? (
        <span
          data-testid="page-context-missing"
          className="inline-flex items-center gap-1 text-xs text-amber-600"
        >
          <FileText className="h-3 w-3" /> page not captured — stabbur lacks page access here
        </span>
      ) : isUser && message.context ? (
        <span className="inline-flex items-center gap-1 text-xs text-[var(--muted-foreground)]">
          <FileText className="h-3 w-3" /> page context attached
        </span>
      ) : null}
    </div>
  );
});

/** The transcript + composer, sized for a ~360px side panel. */
export function ChatView({
  backendId,
  target,
  pageContextEnabled,
  onTogglePageContext,
  pageTextEnabled,
  onTogglePageText,
  getContextBlock,
  onEnsurePageAccess,
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

  // Approve/Deny a pending confirmation. Stable (setMessages/setError/confirmAction are all stable)
  // so passing it to the memoized bubbles doesn't bust their memoization. Optimistically flips the
  // card to resolved so the buttons disable immediately; the server's confirm_resolved echo then
  // removes it. The stream is NOT aborted — the server resumes and streams the tool result.
  const resolveConfirm = useCallback((id: string, approve: boolean): void => {
    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const next = prev.slice();
      const last = next[next.length - 1];
      next[next.length - 1] = {
        ...last,
        confirms: last.confirms?.map((c) =>
          c.id === id ? { ...c, status: "resolved", approved: approve, reason: "user" } : c,
        ),
      };
      return next;
    });
    void confirmAction(id, approve).catch((err) => {
      setError(err instanceof Error ? err.message : String(err));
    });
  }, []);

  async function send(): Promise<void> {
    const text = input.trim();
    if (!text || streaming) return;
    setError(null);

    // Host-access request rides the Send click's user gesture (first await); without it the page
    // capture below silently fails on any site the extension was never granted. Time-boxed inside
    // onEnsurePageAccess so an unanswered prompt never blocks the turn.
    if (pageContextEnabled) await onEnsurePageAccess();
    const ctx = pageContextEnabled ? await getContextBlock() : null;
    const userMsg: ChatMessage = {
      role: "user",
      content: text,
      context: ctx?.text ?? undefined,
      pageMissing: ctx?.pageMissing || undefined,
    };
    const history = [...messages, userMsg];
    setMessages([...history, { role: "assistant", content: "" }]);
    setInput("");

    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    try {
      for await (const evt of streamChat(toApiMessages(history), controller.signal, {
        useTools: true,
        target,
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
          case "confirm":
            patchLast((m) => ({
              ...m,
              confirms: [...(m.confirms ?? []), { id: evt.id, tool: evt.tool, args: evt.args, status: "pending" }],
            }));
            break;
          case "confirm_resolved":
            // A user decision clears the card (the tool call/result chips carry it forward); a
            // timeout leaves an auto-denied note so the outcome is visible.
            patchLast((m) => ({
              ...m,
              confirms:
                evt.reason === "timeout"
                  ? m.confirms?.map((c) =>
                      c.id === evt.id ? { ...c, status: "resolved", approved: evt.approved, reason: "timeout" } : c,
                    )
                  : m.confirms?.filter((c) => c.id !== evt.id),
            }));
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
      // The stream is over: strip any confirmation still awaiting a decision (nothing will resolve
      // it now), keeping only resolved ones (e.g. an auto-denied timeout note). A stream that
      // produced no reply leaves its empty placeholder bubble; drop it so it is neither rendered
      // nor replayed to /api/chat as an empty assistant turn.
      setMessages((prev) => {
        if (prev.length === 0) return prev;
        const last = prev[prev.length - 1];
        if (last.role !== "assistant") return prev;
        const confirms = last.confirms?.filter((c) => c.status === "resolved");
        const patched: ChatMessage = { ...last, confirms: confirms?.length ? confirms : undefined };
        const empty = patched.content === "" && !patched.reasoning && !patched.tools?.length && !patched.confirms?.length;
        if (empty) return prev.slice(0, -1);
        const next = prev.slice();
        next[next.length - 1] = patched;
        return next;
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
            Ask your local stabbur assistant anything.
          </p>
        ) : (
          messages.map((m, i) => <MessageBubble key={i} message={m} onResolveConfirm={resolveConfirm} />)
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
                "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
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
                  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
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
            className="text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)] disabled:opacity-40"
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

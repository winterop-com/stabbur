import { Clock, FileText, Gauge, Hash, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ChatImage } from "@/components/ChatImage";
import { ConfirmCard } from "@/components/ConfirmCard";
import { CopyButton } from "@/components/CopyButton";
import { Markdown } from "@/components/Markdown";
import { SpeakButton } from "@/components/SpeakButton";
import { ToolMarkerChip } from "@/components/ToolMarkerChip";
import type { ChatMessage, GenerationStats } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * What the turn cost, LM Studio / llama.cpp style: tokens, wall time, and the rate. Ticks live
 * while streaming (estimated from deltas) and settles on the runtime's own count when it ends.
 *
 * THE THREE NUMBERS DO NOT DIVIDE INTO EACH OTHER, and that is not a bug: "29 tokens · 14s ·
 * 28.0 t/s" reads as a contradiction because the seconds are the whole turn — queueing and
 * prompt processing included — while the rate is measured from the first token onward. The
 * sentence that reconciles them used to live in a `title` on a plain div, which is to say
 * nowhere a keyboard, a screen reader or a touch device could reach it. It is a disclosure now:
 * the row is the summary, so the default is still one quiet line, and the explanation is one
 * Enter away.
 */
function StatsRow({ stats }: { stats: GenerationStats }) {
  return (
    <details className="mt-1.5 w-full">
      <summary className="w-fit cursor-pointer text-xs text-muted-foreground hover:text-foreground">
        <span className="inline-flex items-center gap-2.5 tabular-nums align-middle">
          <span className="inline-flex items-center gap-1">
            <Hash className="h-3 w-3" />
            {stats.completionTokens.toLocaleString()} tokens
          </span>
          <span className="inline-flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {stats.seconds < 10 ? `${stats.seconds.toFixed(1)}s` : `${Math.round(stats.seconds)}s`}
          </span>
          <span className="inline-flex items-center gap-1">
            <Gauge className="h-3 w-3" />
            {stats.tokensPerSecond.toFixed(1)} t/s
          </span>
        </span>
      </summary>
      <p className="mt-1 text-sm text-muted-foreground">
        {stats.promptTokens.toLocaleString()} prompt + {stats.completionTokens.toLocaleString()} completion tokens. The
        time is the whole turn; the rate is measured from the first token, which arrived after{" "}
        {stats.ttftSeconds.toFixed(2)}s.
      </p>
    </details>
  );
}

/**
 * One turn. User turns render as a right-aligned muted bubble; assistant turns
 * render full-width as Markdown (ChatGPT style) with inline tool chips and a
 * hover action row (copy, + regenerate on the last assistant turn).
 */
export function MessageItem({
  message,
  streaming,
  canRegenerate,
  onRegenerate,
  onResolveConfirm,
  ttsVoice,
  ttsSpeed,
}: {
  message: ChatMessage;
  streaming: boolean;
  canRegenerate: boolean;
  onRegenerate: () => void;
  /** Approve/Deny a pending per-action write confirmation (does not abort the stream). */
  onResolveConfirm: (id: string, approve: boolean) => void;
  ttsVoice?: string;
  ttsSpeed?: number;
}) {
  if (message.role === "user") {
    const images = message.images ?? [];
    const audios = message.audios ?? [];
    const files = message.files ?? [];
    return (
      <div className="group flex flex-col items-end">
        {files.length > 0 && (
          <div className="mb-1.5 flex max-w-[85%] flex-wrap justify-end gap-2">
            {files.map((f, i) => (
              <div
                key={i}
                className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-2.5 py-1.5"
                title={`${f.name} · ${f.text.length.toLocaleString()} chars`}
              >
                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="max-w-[12rem] truncate text-xs">{f.name}</span>
              </div>
            ))}
          </div>
        )}
        {images.length > 0 && (
          <div className="mb-1.5 flex max-w-[85%] flex-wrap justify-end gap-2">
            {images.map((src, i) => (
              <ChatImage
                key={i}
                src={src}
                alt={`attachment ${i + 1}`}
                className="max-h-48 rounded-xl border border-border object-contain"
              />
            ))}
          </div>
        )}
        {audios.length > 0 && (
          <div className="mb-1.5 flex max-w-[85%] flex-col items-end gap-1.5">
            {audios.map((src, i) => (
              <audio key={i} src={src} controls className="h-9 w-64 rounded-full" />
            ))}
          </div>
        )}
        {message.content && (
          <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-muted px-4 py-2.5 text-base leading-relaxed text-foreground">
            {message.content}
          </div>
        )}
        {message.content && (
          <div className="mt-1">
            <CopyButton text={message.content} />
          </div>
        )}
      </div>
    );
  }

  const hasTools = message.tools && message.tools.length > 0;
  const hasConfirms = message.confirms && message.confirms.length > 0;
  const hasReasoning = !!message.reasoning;
  // While only thinking is streaming, the pulsing "Thinking…" box (and the live stats row)
  // already carry liveness — a cursor below them just draws an empty line under the box.
  const showCursor = streaming && !message.content && !hasReasoning;
  const showEllipsis = !message.content && !showCursor && !hasTools && !hasConfirms && !hasReasoning;

  return (
    <div className="group flex flex-col items-start">
      {message.reasoning && (
        /* Collapsed by default (uncontrolled <details>): thinking is a debugging aid, not the
           answer. The summary pulses while the model is still reasoning so liveness is visible
           without expanding; click to open at any time (React never forces it shut again). */
        <details className="mb-2 w-full rounded-lg border border-border/60 bg-muted/30 px-3 py-2">
          <summary className="cursor-pointer select-none text-xs font-medium text-muted-foreground">
            {streaming && !message.content ? <span className="animate-pulse">Thinking…</span> : "Thinking"}
          </summary>
          <div className="mt-1.5 whitespace-pre-wrap text-xs italic leading-relaxed text-muted-foreground">
            {message.reasoning}
          </div>
        </details>
      )}

      {hasTools && (
        <div className="mb-2 flex w-full flex-col gap-1.5">
          {message.tools!.map((t, i) => (
            <ToolMarkerChip key={i} marker={t} />
          ))}
        </div>
      )}

      {hasConfirms && (
        <div className="mb-2 flex w-full flex-col gap-1.5">
          {message.confirms!.map((c) => (
            <ConfirmCard key={c.id} confirm={c} onResolve={onResolveConfirm} />
          ))}
        </div>
      )}

      {/* Mounted only when it has something to draw, so a turn that is still thinking
          doesn't reserve a blank line for content that hasn't started. */}
      {(message.content || message.error || showCursor || showEllipsis) && (
        <div className={cn("w-full", message.error && "text-destructive")}>
          {message.error ? (
            <p className="text-sm">{message.content}</p>
          ) : message.content ? (
            <Markdown content={message.content} streaming={streaming} />
          ) : showCursor ? (
            <span className="inline-block h-4 w-2 animate-pulse rounded-sm bg-muted-foreground align-middle" />
          ) : (
            <span className="text-sm text-muted-foreground">…</span>
          )}
        </div>
      )}

      {/* One line, the Note recipe: a turn that ends early has to say why, or it reads as the
          model having answered with nothing. */}
      {message.stopped && !streaming && (
        <p className="mt-1.5 text-sm text-muted-foreground">Stopped.</p>
      )}

      {message.stats && <StatsRow stats={message.stats} />}

      {/* Always visible, not hover-gated: Listen is a control you reach for while reading (and
          can run for many seconds), so it must not vanish when the pointer moves away. A stopped
          turn earns the row too — regenerate is exactly what you want after pressing Stop, and it
          was the one turn that had no way to reach it. */}
      {!streaming && (message.content || hasTools || message.stopped) && (
        <div className="mt-1 flex items-center">
          {message.content && !message.error && <CopyButton text={message.content} />}
          {message.content && !message.error && (
            <SpeakButton text={message.content} voice={ttsVoice} speed={ttsSpeed} />
          )}
          {canRegenerate && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={onRegenerate}
                  className="text-muted-foreground"
                  aria-label="Regenerate"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Regenerate</TooltipContent>
            </Tooltip>
          )}
        </div>
      )}
    </div>
  );
}

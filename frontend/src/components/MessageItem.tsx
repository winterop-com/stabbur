import { FileText, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ChatImage } from "@/components/ChatImage";
import { CopyButton } from "@/components/CopyButton";
import { Markdown } from "@/components/Markdown";
import { SpeakButton } from "@/components/SpeakButton";
import { ToolMarkerChip } from "@/components/ToolMarkerChip";
import type { ChatMessage } from "@/lib/types";
import { cn } from "@/lib/utils";

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
  ttsVoice,
}: {
  message: ChatMessage;
  streaming: boolean;
  canRegenerate: boolean;
  onRegenerate: () => void;
  ttsVoice?: string;
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
          <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-muted px-4 py-2.5 text-[0.95rem] leading-relaxed text-foreground">
            {message.content}
          </div>
        )}
        {message.content && (
          <div className="mt-1 opacity-0 transition-opacity group-hover:opacity-100">
            <CopyButton text={message.content} />
          </div>
        )}
      </div>
    );
  }

  const hasTools = message.tools && message.tools.length > 0;
  const showCursor = streaming && !message.content;

  return (
    <div className="group flex flex-col items-start">
      {message.reasoning && (
        <details
          open={streaming && !message.content}
          className="mb-2 w-full rounded-lg border border-border/60 bg-muted/30 px-3 py-2"
        >
          <summary className="cursor-pointer select-none text-xs font-medium text-muted-foreground">
            Thinking
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

      <div className={cn("w-full", message.error && "text-destructive")}>
        {message.error ? (
          <p className="text-sm">{message.content}</p>
        ) : message.content ? (
          <Markdown content={message.content} streaming={streaming} />
        ) : showCursor ? (
          <span className="inline-block h-4 w-2 animate-pulse rounded-sm bg-muted-foreground align-middle" />
        ) : (
          !hasTools && <span className="text-sm text-muted-foreground">…</span>
        )}
      </div>

      {!streaming && (message.content || hasTools) && (
        <div className="mt-1 flex items-center opacity-0 transition-opacity group-hover:opacity-100">
          {message.content && !message.error && <CopyButton text={message.content} />}
          {message.content && !message.error && <SpeakButton text={message.content} voice={ttsVoice} />}
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

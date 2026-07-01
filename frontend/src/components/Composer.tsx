import { useEffect, useRef } from "react";
import { ArrowUp, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Rounded, elevated composer pinned at the bottom center. Holds the textarea
 * and a circular send button (swaps to Stop while streaming). The model is
 * chosen from the top bar, not here.
 */
export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  streaming,
  ready,
  autoFocus,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop: () => void;
  streaming: boolean;
  ready: boolean;
  autoFocus?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Auto-grow the textarea to fit content (capped by max-height via CSS).
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  useEffect(() => {
    if (autoFocus) ref.current?.focus();
  }, [autoFocus]);

  const canSend = ready && !streaming && value.trim().length > 0;

  return (
    <div className="rounded-3xl border border-border bg-card shadow-sm">
      <div className="px-4 pt-3">
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (canSend) onSend();
            }
          }}
          rows={1}
          placeholder={ready ? "Message kodo…" : "Select a model to start"}
          className="max-h-[200px] w-full resize-none bg-transparent text-[0.95rem] leading-relaxed outline-none placeholder:text-muted-foreground disabled:opacity-60"
        />
      </div>
      <div className="flex items-center justify-end gap-2 px-2.5 pb-2.5 pt-1">
        {streaming ? (
          <Button
            size="icon"
            onClick={onStop}
            className="h-9 w-9 rounded-full"
            aria-label="Stop generating"
          >
            <Square className="h-4 w-4 fill-current" />
          </Button>
        ) : (
          <Button
            size="icon"
            onClick={onSend}
            disabled={!canSend}
            className={cn("h-9 w-9 rounded-full")}
            aria-label="Send message"
          >
            <ArrowUp className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}

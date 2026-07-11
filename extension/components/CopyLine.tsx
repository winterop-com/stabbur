import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

interface CopyLineProps {
  text: string;
  className?: string;
}

/** A monospace line with a copy-to-clipboard button (used for the cors_origins hint). */
export function CopyLine({ text, className }: CopyLineProps) {
  const [copied, setCopied] = useState(false);

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard unavailable; the text is still selectable.
    }
  }

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded border border-[var(--border)] bg-[var(--muted)] px-2 py-1.5",
        className,
      )}
    >
      <code className="flex-1 overflow-x-auto whitespace-nowrap font-mono text-xs text-[var(--foreground)]">
        {text}
      </code>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label="Copy"
        className="shrink-0 rounded p-1 text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--foreground)]"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
}

import { ShieldAlert } from "lucide-react";

import type { PendingConfirm } from "@/lib/types";

/** One-line, clipped rendering of a confirmation's args (mirrors the tool-chip digest style). */
function compactArgs(args: Record<string, unknown>): string {
  const summarize = (v: unknown): string => {
    if (Array.isArray(v)) return `[${v.length} items]`;
    if (v !== null && typeof v === "object") return "{...}";
    const s = typeof v === "string" ? v : String(v);
    return s.length > 40 ? `${s.slice(0, 40)}...` : s;
  };
  const pairs = Object.entries(args)
    .map(([k, v]) => `${k}: ${summarize(v)}`)
    .join(", ");
  return pairs.length > 120 ? `${pairs.slice(0, 120)}...` : pairs;
}

/** Inline Approve/Deny card for a write action the server is holding. Resolving does NOT abort the
 *  stream — the server resumes and streams the tool result once a decision (or timeout) lands. */
export function ConfirmCard({
  confirm,
  onResolve,
}: {
  confirm: PendingConfirm;
  onResolve: (id: string, approve: boolean) => void;
}) {
  const args = compactArgs(confirm.args);
  const resolved = confirm.status === "resolved";
  const outcome =
    confirm.reason === "timeout" ? "Auto-denied (timed out)." : confirm.approved ? "Approved." : "Denied.";
  return (
    // A decision the reader has to read before making, so the card is prose-sized; only the args
    // digest inside it stays at the annotation size, because that is a machine string being quoted
    // rather than a sentence.
    <div
      data-testid="chat-confirm"
      className="w-full rounded-md border border-warning/50 bg-warning/10 px-3 py-2.5 text-sm"
    >
      <div className="flex items-center gap-1.5 font-medium text-foreground">
        <ShieldAlert className="h-4 w-4 text-warning-ink" /> Confirm action
      </div>
      <div className="mt-1.5 break-all font-mono text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{confirm.tool}</span>
        {args ? `(${args})` : null}
      </div>
      {resolved ? (
        <div data-testid="chat-confirm-outcome" className="mt-1 text-muted-foreground">
          {outcome}
        </div>
      ) : (
        <div className="mt-2 flex items-center gap-2">
          <button
            type="button"
            data-testid="chat-confirm-approve"
            onClick={() => onResolve(confirm.id, true)}
            className="rounded bg-primary px-2.5 py-1 font-medium text-primary-foreground hover:opacity-90"
          >
            Approve
          </button>
          <button
            type="button"
            data-testid="chat-confirm-deny"
            onClick={() => onResolve(confirm.id, false)}
            className="rounded border border-border px-2.5 py-1 hover:bg-accent"
          >
            Deny
          </button>
        </div>
      )}
    </div>
  );
}

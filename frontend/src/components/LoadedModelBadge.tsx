import { Cpu, Loader2, X } from "lucide-react";

import type { Status } from "@/api";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

function shortName(name: string): string {
  return name.split("/").pop() ?? name;
}

/**
 * A compact pill in the top bar showing the currently-loaded runtime model, so
 * it's clear what's in memory from any view (Chat / Models / Voice) and after a
 * page refresh. Click the model name to open the model browser; the ✕ ejects it
 * (frees memory) — hidden in locked single-model mode.
 */
export function LoadedModelBadge({
  status,
  loadingName,
  onEject,
  onShowModels,
}: {
  status: Status | null;
  loadingName: string | null;
  onEject: () => void;
  onShowModels: () => void;
}) {
  const loading = !!loadingName || status?.state === "loading";
  const name = loadingName ?? status?.model ?? null;

  if (!name && !loading) {
    // No chat model in the runtime — a subtle affordance to go load one. (Voice models
    // aren't "loaded" here; they run on demand, so this badge is only about chat.)
    return (
      <button
        type="button"
        onClick={onShowModels}
        title="No chat model loaded. Pick one to chat — voice models run on their own, no loading needed."
        className="hidden items-center gap-1.5 rounded-full border border-dashed border-border px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground sm:inline-flex"
      >
        <Cpu className="h-3.5 w-3.5" />
        No chat model
      </button>
    );
  }

  return (
    <div
      className={cn(
        "hidden items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs sm:inline-flex",
        loading ? "border-primary/40 bg-primary/5 text-foreground" : "border-border bg-muted/60",
      )}
    >
      {loading ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
      ) : (
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden />
      )}
      <button
        type="button"
        onClick={onShowModels}
        title={name ?? undefined}
        className="max-w-[14rem] truncate font-medium hover:underline"
      >
        {name ? shortName(name) : "loading…"}
      </button>
      {status?.locked ? (
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">locked</span>
      ) : (
        !loading &&
        status?.model && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={onEject}
                aria-label="Eject model"
                className="rounded-full p-0.5 text-muted-foreground hover:text-destructive"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </TooltipTrigger>
            <TooltipContent>Eject model (free memory)</TooltipContent>
          </Tooltip>
        )
      )}
    </div>
  );
}

import { useMemo } from "react";
import { Check, ChevronDown, Loader2 } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { LibModel, Status } from "@/api";
import { cn } from "@/lib/utils";

const STATE_COLOR: Record<Status["state"], string> = {
  ready: "bg-emerald-400",
  loading: "bg-amber-400",
  stopped: "bg-zinc-500",
};

function shortName(name: string): string {
  return name.split("/").pop() ?? name;
}

/**
 * Inline model picker (ChatGPT-style): shows the current model + a colored
 * state dot; opens a grouped-by-format menu to switch. Load progress renders
 * inline (spinner + "loading…"). Disabled while locked or a load is in flight.
 */
export function ModelSelector({
  status,
  library,
  loadingName,
  onPick,
}: {
  status: Status | null;
  library: LibModel[];
  loadingName: string | null;
  onPick: (name: string) => void;
}) {
  const grouped = useMemo(() => {
    const by: Record<string, LibModel[]> = {};
    for (const m of library) (by[m.model_format] ??= []).push(m);
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
  }, [library]);

  const locked = status?.locked ?? false;
  const busy = loadingName != null || status?.state === "loading";
  const label = loadingName
    ? shortName(loadingName)
    : status?.model
      ? shortName(status.model)
      : "Select a model";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        disabled={locked || busy}
        className={cn(
          "inline-flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-70",
        )}
        title={status?.model ?? undefined}
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-400" />
        ) : (
          <span className={cn("h-2 w-2 rounded-full", STATE_COLOR[status?.state ?? "stopped"])} />
        )}
        <span className="max-w-[16rem] truncate">{label}</span>
        {busy ? (
          <span className="text-xs text-muted-foreground">loading…</span>
        ) : (
          !locked && <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        )}
        {locked && <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">locked</span>}
      </DropdownMenuTrigger>

      <DropdownMenuContent align="start" className="max-h-[70vh] w-72 overflow-y-auto">
        {library.length === 0 && (
          <div className="px-2 py-3 text-sm text-muted-foreground">No models in the library.</div>
        )}
        {grouped.map(([fmt, models], gi) => (
          <div key={fmt}>
            {gi > 0 && <DropdownMenuSeparator />}
            <div className="px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              {fmt}
            </div>
            {models.map((m) => {
              const active = status?.model === m.name;
              return (
                <DropdownMenuItem key={m.name} onSelect={() => onPick(m.name)} title={m.name}>
                  <span className="flex-1 truncate">{shortName(m.name)}</span>
                  <span className="ml-2 shrink-0 text-[11px] text-muted-foreground">{m.size_human}</span>
                  {active && <Check className="ml-1 h-3.5 w-3.5 text-primary" />}
                </DropdownMenuItem>
              );
            })}
          </div>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

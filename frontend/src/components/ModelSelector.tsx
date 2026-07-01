import { useMemo } from "react";
import { Check, ChevronDown, Eye, Loader2, Power, Wrench } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

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

/** Format a context length in tokens as a compact label (262144 → "262K"). */
function ctxLabel(n: number | null): string | null {
  if (!n) return null;
  if (n >= 1000) return `${Math.round(n / 1000)}K`;
  return String(n);
}

/**
 * Capability hints (tool calling / vision), each in its own fixed-width slot so
 * every icon stays in a consistent column and the size column stays aligned no
 * matter which capabilities a row has. Detail lives in the row's tooltip.
 */
function CapabilityIcons({ tools, vision }: { tools: boolean; vision: boolean }) {
  return (
    <span className="ml-2 flex shrink-0 items-center gap-1 text-muted-foreground">
      <span className="flex w-3.5 justify-center">{tools && <Wrench className="h-3.5 w-3.5" />}</span>
      <span className="flex w-3.5 justify-center">{vision && <Eye className="h-3.5 w-3.5" />}</span>
    </span>
  );
}

/** Rich tooltip content describing a model (full name + specs + capabilities). */
function ModelTooltip({ model }: { model: LibModel }) {
  const ctx = ctxLabel(model.context_length);
  return (
    <div className="max-w-xs space-y-1">
      <div className="break-all font-medium">{model.name}</div>
      <div className="text-muted-foreground">
        {model.model_format.toUpperCase()} · {model.size_human}
        {ctx && ` · ${ctx} context`}
      </div>
      <div className="flex flex-col gap-0.5 text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <Wrench className="h-3 w-3" />
          {model.tools ? "Trained for tool calling" : "No tool-calling template"}
        </span>
        <span className="flex items-center gap-1.5">
          <Eye className="h-3 w-3" />
          {model.vision ? "Vision (accepts images)" : "Text only"}
        </span>
      </div>
    </div>
  );
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
  onEject,
}: {
  status: Status | null;
  library: LibModel[];
  loadingName: string | null;
  onPick: (name: string) => void;
  onEject: () => void;
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
        <span className="max-w-[22rem] truncate">{label}</span>
        {busy ? (
          <span className="text-xs text-muted-foreground">loading…</span>
        ) : (
          !locked && <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        )}
        {locked && <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">locked</span>}
      </DropdownMenuTrigger>

      <DropdownMenuContent align="start" className="max-h-[70vh] w-[30rem] overflow-y-auto">
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
                <Tooltip key={m.name}>
                  <TooltipTrigger asChild>
                    <DropdownMenuItem onSelect={() => onPick(m.name)}>
                      <span className="flex-1 truncate">{shortName(m.name)}</span>
                      <CapabilityIcons tools={m.tools} vision={m.vision} />
                      <span className="ml-2 w-16 shrink-0 text-right text-[11px] text-muted-foreground">
                        {m.size_human}
                      </span>
                      {/* Fixed slot so the size column aligns whether or not a row is active. */}
                      <span className="ml-1 flex w-4 shrink-0 justify-center">
                        {active && <Check className="h-3.5 w-3.5 text-primary" />}
                      </span>
                    </DropdownMenuItem>
                  </TooltipTrigger>
                  <TooltipContent side="right">
                    <ModelTooltip model={m} />
                  </TooltipContent>
                </Tooltip>
              );
            })}
          </div>
        ))}
        {status?.model && !locked && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={onEject} className="text-muted-foreground">
              <Power className="mr-2 h-3.5 w-3.5" />
              Eject model
              <span className="ml-auto truncate text-[11px]">{shortName(status.model)}</span>
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

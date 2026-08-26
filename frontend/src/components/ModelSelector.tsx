import { useMemo, useState } from "react";
import { AudioLines, Boxes, Check, ChevronDown, Eye, Loader2, Lock, Power, Tag, Wrench } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { LibModel, Status } from "@/api";
import { allTagsOf, tagColor } from "@/lib/tags";
import { cn } from "@/lib/utils";

// The runtime's three states. `stopped` is not a semantic colour — nothing is
// wrong with a runtime that isn't running — so it takes the muted ink.
const STATE_COLOR: Record<Status["state"], string> = {
  ready: "bg-good",
  loading: "bg-warning",
  stopped: "bg-muted-foreground",
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
function CapabilityIcons({ tools, vision, audio }: { tools: boolean; vision: boolean; audio: boolean }) {
  return (
    <span className="ml-2 flex shrink-0 items-center gap-1 text-muted-foreground">
      <span className="flex w-3.5 justify-center">{tools && <Wrench className="h-3.5 w-3.5" />}</span>
      <span className="flex w-3.5 justify-center">{vision && <Eye className="h-3.5 w-3.5" />}</span>
      <span className="flex w-3.5 justify-center">{audio && <AudioLines className="h-3.5 w-3.5" />}</span>
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
          {model.vision ? "Vision (accepts images)" : "No vision"}
        </span>
        <span className="flex items-center gap-1.5">
          <AudioLines className="h-3 w-3" />
          {model.audio ? "Audio (accepts speech)" : "No audio"}
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
  onShowLibrary,
}: {
  status: Status | null;
  library: LibModel[];
  loadingName: string | null;
  onPick: (name: string) => void;
  onEject: () => void;
  /** The way to the Library from here. Inherited from the top-bar badge this replaced, which
   *  carried the only route out of an empty runtime; losing it with the badge would have been a
   *  dead end on the one screen where the reader has nothing to pick from. */
  onShowLibrary: () => void;
}) {
  // Capability filters: when a chip is on, only models with that capability show.
  const [filters, setFilters] = useState({ tools: false, vision: false, audio: false });
  const toggleFilter = (key: "tools" | "vision" | "audio") =>
    setFilters((f) => ({ ...f, [key]: !f[key] }));
  // Tag filters (AND): a model must carry every selected tag.
  const [tagFilters, setTagFilters] = useState<Set<string>>(new Set());
  const toggleTag = (t: string) =>
    setTagFilters((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  const allTags = useMemo(() => allTagsOf(library), [library]);

  const grouped = useMemo(() => {
    const active = [...tagFilters];
    const shown = library.filter(
      (m) =>
        (!filters.tools || m.tools) &&
        (!filters.vision || m.vision) &&
        (!filters.audio || m.audio) &&
        active.every((t) => (m.tags ?? []).includes(t)),
    );
    const by: Record<string, LibModel[]> = {};
    for (const m of shown) (by[m.model_format] ??= []).push(m);
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
  }, [library, filters, tagFilters]);

  const locked = status?.locked ?? false;
  const busy = loadingName != null || status?.state === "loading";
  const label = loadingName
    ? shortName(loadingName)
    : status?.model
      ? shortName(status.model)
      : "Select a model";

  // Locked (a project assistant, or `--model`): there is nothing to select, so the control states
  // the binding instead of offering it. It used to render NOTHING here, on the grounds that the
  // top bar's pill said which model was bound — with that pill gone, returning null would have
  // left a locked run with no statement of its own model anywhere on screen.
  if (locked) {
    return (
      <span
        className="inline-flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm font-medium text-muted-foreground"
        title={status?.model ?? undefined}
      >
        <Lock className="h-3.5 w-3.5" />
        <span className="max-w-[22rem] truncate">{label}</span>
      </span>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        disabled={busy}
        className={cn(
          "inline-flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-70",
        )}
        title={status?.model ?? undefined}
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-warning-ink" />
        ) : (
          <span className={cn("h-2 w-2 rounded-full", STATE_COLOR[status?.state ?? "stopped"])} />
        )}
        <span className="max-w-[22rem] truncate">{label}</span>
        {busy ? (
          <span className="text-xs text-muted-foreground">loading…</span>
        ) : (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        )}
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="start"
        collisionPadding={12}
        className="max-h-[var(--radix-dropdown-menu-content-available-height)] w-[30rem] overflow-y-auto"
      >
        {/* Capability filter chips */}
        {library.length > 0 && (
          <div className="mb-1 flex items-center gap-1 border-b border-border px-2 pb-2 pt-1">
            <span className="mr-1 text-xs uppercase tracking-wide text-muted-foreground">Filter</span>
            {(
              [
                ["tools", Wrench, "Tools"],
                ["vision", Eye, "Vision"],
                ["audio", AudioLines, "Audio"],
              ] as const
            ).map(([key, Icon, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => toggleFilter(key)}
                aria-pressed={filters[key]}
                className={cn(
                  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition-colors",
                  filters[key]
                    ? "border-primary bg-primary/10 text-foreground"
                    : "border-border text-muted-foreground hover:bg-accent",
                )}
              >
                <Icon className="h-3 w-3" />
                {label}
              </button>
            ))}
          </div>
        )}
        {/* Tag filter chips (only when the library has tags) */}
        {allTags.length > 0 && (
          <div className="mb-1 flex flex-wrap items-center gap-1 border-b border-border px-2 pb-2">
            <span className="mr-1 flex items-center gap-1 text-xs uppercase tracking-wide text-muted-foreground">
              <Tag className="h-3 w-3" />
              Tags
            </span>
            {allTags.map((t) => {
              const on = tagFilters.has(t);
              return (
                <button
                  key={t}
                  type="button"
                  onClick={() => toggleTag(t)}
                  aria-pressed={on}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-xs transition-all",
                    tagColor(t),
                    on ? "font-medium ring-1 ring-inset ring-current" : "opacity-70 hover:opacity-100",
                  )}
                >
                  {t}
                </button>
              );
            })}
          </div>
        )}
        {library.length === 0 && (
          <div className="px-2 py-3 text-sm text-muted-foreground">No models in the library.</div>
        )}
        {library.length > 0 && grouped.length === 0 && (
          <div className="px-2 py-3 text-sm text-muted-foreground">No models match the filters.</div>
        )}
        {grouped.map(([fmt, models], gi) => (
          <div key={fmt}>
            {gi > 0 && <DropdownMenuSeparator />}
            <div className="px-2 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {fmt}
            </div>
            {models.map((m) => {
              const active = status?.model === m.name;
              return (
                <Tooltip key={m.name}>
                  <TooltipTrigger asChild>
                    <DropdownMenuItem onSelect={() => onPick(m.name)}>
                      <span className="flex-1 truncate">{shortName(m.name)}</span>
                      <CapabilityIcons tools={m.tools} vision={m.vision} audio={m.audio} />
                      <span className="ml-2 w-16 shrink-0 text-right text-xs text-muted-foreground">
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
        <DropdownMenuSeparator />
        {status?.model && (
          <DropdownMenuItem onSelect={onEject} className="text-muted-foreground">
            <Power className="mr-2 h-3.5 w-3.5" />
            Eject model
            <span className="ml-auto truncate text-xs">{shortName(status.model)}</span>
          </DropdownMenuItem>
        )}
        {/* Always offered, and last: a reader whose library is empty, or who wants a model card,
            a tag or a size before choosing, needs a way out of a menu that can otherwise say only
            "No models in the library." */}
        <DropdownMenuItem onSelect={onShowLibrary} className="text-muted-foreground">
          <Boxes className="mr-2 h-3.5 w-3.5" />
          Browse the library
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

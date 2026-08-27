import { useMemo, useState } from "react";
import {
  AudioLines,
  Boxes,
  Check,
  ChevronDown,
  Eye,
  Loader2,
  Lock,
  Power,
  Server,
  Tag,
  Wrench,
} from "lucide-react";

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

/** The capability chips' state. A `false` flag filters on nothing. */
export interface CapabilityFilters {
  tools: boolean;
  vision: boolean;
  audio: boolean;
}

/** One backend's slice of the picker. */
export interface BackendGroup {
  /** The qualifier half of a `model@backend` id, spelled exactly as the server sends it. */
  backend: string;
  /** Rows that are NOT models: this backend could not be listed. Never loadable. */
  unavailable: LibModel[];
  /** `[format, models]`, alphabetical — the same shelves the picker had before backends existed. */
  formats: [string, LibModel[]][];
}

export interface PickerGroups {
  /** More than one backend is declared, so a row has to say where it came from. */
  multi: boolean;
  /** Backends in the order the server declared them; a backend with nothing to show is dropped. */
  groups: BackendGroup[];
  /** Models surviving the filters, across every backend. */
  shown: number;
}

/**
 * Split the library into per-backend shelves.
 *
 * Two rules the render depends on, both decided here rather than in JSX:
 *
 * - **`multi` is counted before filtering.** Every backend gets a group the moment one of its
 *   rows is seen, so ticking "Tools" until only one backend still has a match cannot make the
 *   headings and badges vanish mid-session. Origin is a property of the server's configuration,
 *   not of what happens to be on screen.
 * - **An `error` row is never filtered.** It is not a model, so no capability or tag can apply
 *   to it, and hiding "this host is down" behind a filter chip hides the very fact that explains
 *   why the list looks short.
 *
 * A server too old to send `backend` yields one group keyed on the same missing value, so
 * `multi` is false and the picker renders exactly as it did before backends existed.
 */
export function groupForPicker(
  library: LibModel[],
  filters: CapabilityFilters,
  tagFilters: ReadonlySet<string>,
): PickerGroups {
  const wanted = [...tagFilters];
  const order: string[] = [];
  const byBackend = new Map<string, { unavailable: LibModel[]; formats: Map<string, LibModel[]> }>();
  let shown = 0;

  for (const m of library) {
    let group = byBackend.get(m.backend);
    if (!group) {
      group = { unavailable: [], formats: new Map() };
      byBackend.set(m.backend, group);
      order.push(m.backend);
    }
    if (m.error != null) {
      group.unavailable.push(m);
      continue;
    }
    if (filters.tools && !m.tools) continue;
    if (filters.vision && !m.vision) continue;
    if (filters.audio && !m.audio) continue;
    if (!wanted.every((t) => (m.tags ?? []).includes(t))) continue;
    const shelf = group.formats.get(m.model_format);
    if (shelf) shelf.push(m);
    else group.formats.set(m.model_format, [m]);
    shown += 1;
  }

  const groups: BackendGroup[] = [];
  for (const backend of order) {
    const group = byBackend.get(backend);
    if (!group) continue;
    // `.sort()` on a fresh copy, not `.toSorted()`: the tsconfig's lib predates ES2023, and this
    // is the same spread-then-sort the rest of the SPA uses.
    const formats = [...group.formats.entries()].sort(([a], [b]) => a.localeCompare(b));
    if (group.unavailable.length === 0 && formats.length === 0) continue; // everything filtered out
    groups.push({ backend, unavailable: group.unavailable, formats });
  }
  return { multi: byBackend.size > 1, groups, shown };
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
 * The heading over one backend's models. Not uppercased, unlike the format shelves under it:
 * this string is a machine identifier — the qualifier in `model@backend` — so it is shown with
 * the spelling the reader would have to type. Weight and full ink, plus the icon, are what put
 * it above the muted format shelves; size stays where the scale puts an annotation.
 */
function BackendHeading({ backend }: { backend: string }) {
  return (
    <div className="flex items-center gap-1.5 px-2 pb-0.5 pt-1.5 text-xs font-semibold tracking-wide">
      <Server className="h-3 w-3 shrink-0" />
      <span className="truncate">{backend}</span>
    </div>
  );
}

/**
 * Which backend a row came from. Rendered only when there is more than one, and duplicated from
 * the heading on purpose: the menu scrolls, so the heading is gone by the time a reader is deep
 * in a long list, and two hosts serving the same model name are otherwise identical rows.
 */
function BackendBadge({ backend }: { backend: string }) {
  return (
    <span className="ml-2 shrink-0 rounded-full border border-border bg-muted/60 px-1.5 py-0.5 text-xs text-muted-foreground">
      {backend}
    </span>
  );
}

/**
 * A declared backend that could not be listed. Deliberately not a `DropdownMenuItem`, not even a
 * disabled one: a menu item is a thing you can land on, and there is nothing here to select or
 * load. A plain block is skipped by the menu's keyboard navigation for free, and cannot fire
 * `onPick` even if a future edit forgets why.
 *
 * `--critical` rather than `--destructive`: this is a state stabbur observed and is reporting,
 * not an action the reader can press.
 */
function UnavailableRow({ model }: { model: LibModel }) {
  return (
    <div className="px-2 py-2">
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 shrink-0 rounded-full bg-critical" />
        <span className="flex-1 truncate text-sm font-medium text-muted-foreground">{model.name}</span>
        <span className="shrink-0 rounded-full border border-critical/30 bg-critical/10 px-1.5 py-0.5 text-xs text-critical">
          unavailable
        </span>
      </div>
      <div className="mt-1 break-words text-sm text-muted-foreground">{model.error}</div>
    </div>
  );
}

/**
 * Inline model picker (ChatGPT-style): shows the current model + a colored
 * state dot; opens a menu grouped by backend, then by format, to switch. Load progress renders
 * inline (spinner + "loading…"). Disabled while locked or a load is in flight.
 *
 * With one backend the menu is exactly the format shelves it always was: `groupForPicker`
 * reports `multi: false`, which is the single gate on every heading and badge below.
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
  /** Called with the model's **bare** name. `POST /api/load/{name}` does not parse a
   *  `model@backend` qualifier yet (ROADMAP, "Multiple backends at once"), so sending one would
   *  404 rather than disambiguate. Until it does, two backends serving the same name are told
   *  apart on screen but not in the request, and the server answers 409 naming both. */
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

  const { multi, groups, shown } = useMemo(
    () => groupForPicker(library, filters, tagFilters),
    [library, filters, tagFilters],
  );
  // The filter chips and the two empty states count *models*, not rows: a backend that could not
  // be listed contributes a row, and it must not make an empty library look stocked.
  const modelCount = useMemo(() => library.filter((m) => m.error == null).length, [library]);

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
        {modelCount > 0 && (
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
        {modelCount === 0 && (
          <div className="px-2 py-3 text-sm text-muted-foreground">No models in the library.</div>
        )}
        {modelCount > 0 && shown === 0 && (
          <div className="px-2 py-3 text-sm text-muted-foreground">No models match the filters.</div>
        )}
        {groups.map((group, bi) => (
          <div key={group.backend}>
            {bi > 0 && <DropdownMenuSeparator />}
            {/* One backend, or a group that is only a down host: the heading would be the second
                statement of a name the rows already carry. It earns its place only over models. */}
            {multi && group.formats.length > 0 && <BackendHeading backend={group.backend} />}
            {group.unavailable.map((m) => (
              <UnavailableRow key={m.name} model={m} />
            ))}
            {group.formats.map(([fmt, models], gi) => (
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
                          {multi && <BackendBadge backend={m.backend} />}
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

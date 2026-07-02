import { useEffect, useMemo, useState } from "react";
import { AudioLines, Eye, Info, Loader2, MessageSquare, Play, Plus, Tag, Wrench, X } from "lucide-react";

import { getModelInfo, type LibModel, type ModelInfo, type Status } from "@/api";
import { Markdown } from "@/components/Markdown";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** Format a context length in tokens as a compact label (262144 -> "256K"). */
function ctxLabel(n: number | null): string | null {
  if (!n) return null;
  if (n >= 1024) return `${Math.round(n / 1024)}K`;
  return String(n);
}

function shortName(name: string): string {
  return name.split("/").pop() ?? name;
}
function publisher(name: string): string | null {
  const i = name.lastIndexOf("/");
  return i > 0 ? name.slice(0, i) : null;
}
function normalizeTag(t: string): string {
  return t
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 32);
}

// Format badge accent, mirroring the CLI's per-format colors (gguf cyan, mlx
// fuchsia, safetensors amber).
const FORMAT_ACCENT: Record<string, string> = {
  gguf: "border-cyan-500/30 bg-cyan-500/10 text-cyan-600 dark:text-cyan-400",
  mlx: "border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-600 dark:text-fuchsia-400",
  safetensors: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
};
const FALLBACK_ACCENT = "border-border bg-muted text-muted-foreground";

// A tag's color is *derived* from its name (stable hash -> fixed palette), so every
// "tested" is the same color everywhere with zero storage. Full class strings (not
// built dynamically) so Tailwind keeps them. A future tag registry (see ROADMAP)
// can override this per tag; until then, derivation is the whole feature.
const TAG_PALETTE = [
  "border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400",
  "border-orange-500/30 bg-orange-500/10 text-orange-600 dark:text-orange-400",
  "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  "border-lime-500/30 bg-lime-500/10 text-lime-600 dark:text-lime-400",
  "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  "border-teal-500/30 bg-teal-500/10 text-teal-600 dark:text-teal-400",
  "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
  "border-blue-500/30 bg-blue-500/10 text-blue-600 dark:text-blue-400",
  "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400",
  "border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-600 dark:text-fuchsia-400",
  "border-pink-500/30 bg-pink-500/10 text-pink-600 dark:text-pink-400",
];

function tagColor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return TAG_PALETTE[h % TAG_PALETTE.length];
}

function CapChip({ icon: Icon, label, className }: { icon: typeof Wrench; label: string; className: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}

/** A roomy tag editor dialog: current tags (removable), a free-text adder, and
 *  one-click chips for tags already used elsewhere in the library. */
function TagDialog({
  label,
  tags,
  suggestions,
  open,
  onOpenChange,
  onChange,
}: {
  label: string;
  tags: string[];
  suggestions: string[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChange: (tags: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const add = (raw: string) => {
    const t = normalizeTag(raw);
    if (t && !tags.includes(t)) onChange([...tags, t]);
    setDraft("");
  };
  const remove = (t: string) => onChange(tags.filter((x) => x !== t));
  const unused = suggestions.filter((s) => !tags.includes(s));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-sm">Tags</DialogTitle>
          <DialogDescription className="break-all">{label}</DialogDescription>
        </DialogHeader>

        <div className="flex min-h-8 flex-wrap gap-1.5">
          {tags.length ? (
            tags.map((t) => (
              <span
                key={t}
                className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs", tagColor(t))}
              >
                {t}
                <button
                  type="button"
                  aria-label={`Remove tag ${t}`}
                  onClick={() => remove(t)}
                  className="opacity-60 hover:text-destructive hover:opacity-100"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))
          ) : (
            <span className="text-xs text-muted-foreground">No tags yet.</span>
          )}
        </div>

        <Input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add(draft);
            }
          }}
          placeholder="Add a tag and press Enter"
          className="h-9"
        />

        {unused.length > 0 && (
          <div>
            <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Existing tags
            </div>
            <div className="flex flex-wrap gap-1.5">
              {unused.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => add(s)}
                  className={cn(
                    "inline-flex items-center gap-0.5 rounded-full border border-dashed px-2 py-0.5 text-xs opacity-80 hover:opacity-100",
                    tagColor(s),
                  )}
                >
                  <Plus className="h-3 w-3" />
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** The card's tag display: chips (read-only) + an edit trigger that opens the dialog. */
function TagRow({
  label,
  tags,
  suggestions,
  onChange,
}: {
  label: string;
  tags: string[];
  suggestions: string[];
  onChange: (tags: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Edit tags"
        className="mt-2 flex flex-wrap items-center gap-1 text-left"
      >
        {tags.map((t) => (
          <span key={t} className={cn("rounded-full border px-1.5 py-0.5 text-[10px]", tagColor(t))}>
            {t}
          </span>
        ))}
        <span className="inline-flex items-center gap-0.5 rounded-full border border-dashed border-border px-1.5 py-0.5 text-[10px] text-muted-foreground hover:border-primary/50 hover:text-foreground">
          <Plus className="h-2.5 w-2.5" />
          {tags.length ? "edit" : "tag"}
        </span>
      </button>
      <TagDialog
        label={label}
        tags={tags}
        suggestions={suggestions}
        open={open}
        onOpenChange={setOpen}
        onChange={onChange}
      />
    </>
  );
}

/** Full model details (facts + rendered model card), fetched lazily on open. */
function ModelDetailsDialog({
  model,
  open,
  onOpenChange,
}: {
  model: LibModel;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [info, setInfo] = useState<ModelInfo | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!open) return;
    setInfo(null);
    setLoading(true);
    getModelInfo(model.name)
      .then(setInfo)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [open, model.name]);

  const ctx = ctxLabel(model.context_length);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle className="break-all pr-6 text-sm">{model.name}</DialogTitle>
          <DialogDescription>
            {model.model_format.toUpperCase()} · {model.size_human}
            {ctx && ` · ${ctx} context`}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-center gap-2.5 text-[11px] text-muted-foreground">
          {model.tools && <CapChip icon={Wrench} label="tools" className="text-cyan-600 dark:text-cyan-400" />}
          {model.vision && <CapChip icon={Eye} label="vision" className="text-fuchsia-600 dark:text-fuchsia-400" />}
          {model.audio && <CapChip icon={AudioLines} label="audio" className="text-emerald-600 dark:text-emerald-400" />}
          {model.tags.map((t) => (
            <span key={t} className="rounded-full border border-border bg-muted/60 px-1.5 py-0.5 text-[10px]">
              {t}
            </span>
          ))}
        </div>
        {info?.path && <div className="break-all text-[11px] text-muted-foreground">{info.path}</div>}

        <div className="max-h-[70vh] min-h-[40vh] overflow-y-auto rounded-lg border border-border bg-muted/20 p-4 text-sm">
          {loading ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading model card…
            </div>
          ) : info?.card ? (
            <Markdown content={info.card} allowHtml />
          ) : (
            <span className="text-muted-foreground">No model card available.</span>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ModelCard({
  model,
  active,
  loading,
  blocked,
  suggestions,
  onPick,
  onSetTags,
}: {
  model: LibModel;
  active: boolean;
  loading: boolean;
  blocked: boolean;
  suggestions: string[];
  onPick: (name: string) => void;
  onSetTags: (name: string, tags: string[]) => void;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const ctx = ctxLabel(model.context_length);
  const pub = publisher(model.name);
  const actDisabled = loading || (!active && blocked);
  return (
    <div
      className={cn(
        "flex flex-col rounded-xl border p-3 transition-colors",
        active
          ? "border-primary/60 bg-primary/5"
          : "border-border hover:border-primary/40 hover:bg-accent/40",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span
          className={cn(
            "rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
            FORMAT_ACCENT[model.model_format] ?? FALLBACK_ACCENT,
          )}
        >
          {model.model_format}
        </span>
        <span className="text-xs text-muted-foreground">{model.size_human}</span>
      </div>

      <div className="mt-2 break-words text-sm font-medium leading-snug" title={model.name}>
        {shortName(model.name)}
      </div>
      {pub && <div className="truncate text-[11px] text-muted-foreground">{pub}</div>}

      <div className="mt-2 flex items-center gap-2.5 text-[11px] text-muted-foreground">
        {model.tools && <CapChip icon={Wrench} label="tools" className="text-cyan-600 dark:text-cyan-400" />}
        {model.vision && <CapChip icon={Eye} label="vision" className="text-fuchsia-600 dark:text-fuchsia-400" />}
        {model.audio && <CapChip icon={AudioLines} label="audio" className="text-emerald-600 dark:text-emerald-400" />}
        {ctx && <span className="ml-auto">{ctx} ctx</span>}
      </div>

      <TagRow
        label={model.name}
        tags={model.tags}
        suggestions={suggestions}
        onChange={(t) => onSetTags(model.name, t)}
      />

      {/* Explicit actions: details (any model) + load/chat (deliberate, never on card click). */}
      <div className="mt-3 flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setDetailsOpen(true)}
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
        >
          <Info className="h-3.5 w-3.5" /> Details
        </button>
        <button
          type="button"
          onClick={() => onPick(model.name)}
          disabled={actDisabled}
          className={cn(
            "inline-flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors",
            active
              ? "bg-primary/15 text-primary hover:bg-primary/25"
              : "bg-primary text-primary-foreground hover:bg-primary/90",
            actDisabled && "cursor-not-allowed opacity-60",
          )}
        >
          {loading ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> loading…
            </>
          ) : active ? (
            <>
              <MessageSquare className="h-3.5 w-3.5" /> Chat
            </>
          ) : (
            <>
              <Play className="h-3.5 w-3.5" /> Load
            </>
          )}
        </button>
      </div>

      <ModelDetailsDialog model={model} open={detailsOpen} onOpenChange={setDetailsOpen} />
    </div>
  );
}

/**
 * Full-panel "Models" browser: every library model as a card, grouped by format
 * (like `kodo ls`). Clicking a card loads it and drops into chat; the loaded one
 * is marked. Cards carry editable user tags, with a tag filter bar on top.
 */
export function ModelsView({
  library,
  loaded,
  status,
  loadingName,
  onPick,
  onSetTags,
}: {
  library: LibModel[];
  loaded: boolean;
  status: Status | null;
  loadingName: string | null;
  onPick: (name: string) => void;
  onSetTags: (name: string, tags: string[]) => void;
}) {
  const locked = status?.locked ?? false;
  const busy = loadingName != null || status?.state === "loading";
  const [activeTags, setActiveTags] = useState<Set<string>>(new Set());

  // Normalize once so a model with no `tags` field (e.g. an older server that
  // predates tags) can't crash the grid.
  const models = useMemo(() => library.map((m) => ({ ...m, tags: m.tags ?? [] })), [library]);

  const allTags = useMemo(() => {
    const s = new Set<string>();
    for (const m of models) for (const t of m.tags) s.add(t);
    return [...s].sort();
  }, [models]);

  const toggleFilter = (t: string) =>
    setActiveTags((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });

  const filtered = useMemo(
    () => (activeTags.size === 0 ? models : models.filter((m) => [...activeTags].every((t) => m.tags.includes(t)))),
    [models, activeTags],
  );

  const totalHuman = useMemo(() => {
    const bytes = filtered.reduce((sum, m) => sum + m.size_bytes, 0);
    const gb = bytes / 1024 ** 3;
    return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  }, [filtered]);

  const grouped = useMemo(() => {
    const by: Record<string, LibModel[]> = {};
    for (const m of filtered) (by[m.model_format] ??= []).push(m);
    for (const list of Object.values(by)) list.sort((a, b) => a.name.localeCompare(b.name));
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-6">
        <div className="mb-3 flex items-baseline gap-2">
          <h1 className="text-lg font-semibold tracking-tight">Models</h1>
          {models.length > 0 && (
            <span className="text-sm text-muted-foreground">
              {filtered.length}
              {filtered.length !== models.length && ` / ${models.length}`} · {totalHuman}
            </span>
          )}
        </div>

        {allTags.length > 0 && (
          <div className="mb-4 flex flex-wrap items-center gap-1.5">
            <Tag className="h-3.5 w-3.5 text-muted-foreground" />
            {allTags.map((t) => {
              const on = activeTags.has(t);
              return (
                <button
                  key={t}
                  type="button"
                  onClick={() => toggleFilter(t)}
                  aria-pressed={on}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-[11px] transition-all",
                    tagColor(t),
                    on ? "font-medium ring-1 ring-inset ring-current" : "opacity-70 hover:opacity-100",
                  )}
                >
                  {t}
                </button>
              );
            })}
            {activeTags.size > 0 && (
              <button
                type="button"
                onClick={() => setActiveTags(new Set())}
                className="ml-1 text-[11px] text-muted-foreground hover:text-foreground"
              >
                clear
              </button>
            )}
          </div>
        )}

        {!loaded && models.length === 0 ? (
          <div className="flex items-center gap-2 px-1 py-10 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading models…
          </div>
        ) : models.length === 0 ? (
          <div className="rounded-lg border border-border bg-muted/40 px-4 py-6 text-sm text-muted-foreground">
            No models in the library yet. Pull one with <code className="font-mono">kodo pull</code>.
          </div>
        ) : grouped.length === 0 ? (
          <div className="px-1 py-6 text-sm text-muted-foreground">No models match the selected tags.</div>
        ) : (
          <div className="space-y-6">
            {grouped.map(([fmt, models]) => (
              <section key={fmt}>
                <div className="mb-2 flex items-center gap-2">
                  <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{fmt}</span>
                  <span className="text-[11px] text-muted-foreground">{models.length}</span>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {models.map((m) => (
                    <ModelCard
                      key={m.name}
                      model={m}
                      active={status?.model === m.name}
                      loading={loadingName === m.name}
                      blocked={locked || (busy && loadingName !== m.name)}
                      suggestions={allTags}
                      onPick={onPick}
                      onSetTags={onSetTags}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
        {locked && (
          <p className="mt-4 text-[11px] text-muted-foreground">
            The server is locked to a single model; switching is disabled.
          </p>
        )}
      </div>
    </div>
  );
}

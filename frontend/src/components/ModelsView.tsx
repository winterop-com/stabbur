import { useMemo, useState } from "react";
import { AudioLines, Check, Eye, Loader2, Plus, Tag, Wrench, X } from "lucide-react";

import type { LibModel, Status } from "@/api";
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

function CapChip({ icon: Icon, label, className }: { icon: typeof Wrench; label: string; className: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}

/** Tag chips + an inline adder, editable. Kept out of the load button so nested
 *  clicks (remove / add) don't trigger a model load. */
function TagRow({
  tags,
  suggestions,
  onChange,
}: {
  tags: string[];
  suggestions: string[];
  onChange: (tags: string[]) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");

  const commit = () => {
    const t = normalizeTag(draft);
    if (t && !tags.includes(t)) onChange([...tags, t]);
    setDraft("");
    setAdding(false);
  };
  const remove = (t: string) => onChange(tags.filter((x) => x !== t));

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1">
      {tags.map((t) => (
        <span
          key={t}
          className="group/tag inline-flex items-center gap-1 rounded-full border border-border bg-muted/60 px-1.5 py-0.5 text-[10px] text-muted-foreground"
        >
          {t}
          <button
            type="button"
            aria-label={`Remove tag ${t}`}
            onClick={() => remove(t)}
            className="text-muted-foreground/60 hover:text-destructive"
          >
            <X className="h-2.5 w-2.5" />
          </button>
        </span>
      ))}
      {adding ? (
        <>
          <Input
            autoFocus
            list="kodo-tag-suggestions"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") {
                setDraft("");
                setAdding(false);
              }
            }}
            onBlur={commit}
            placeholder="tag"
            className="h-5 w-20 border-transparent bg-background px-1.5 py-0 text-[11px]"
          />
          <datalist id="kodo-tag-suggestions">
            {suggestions.map((s) => (
              <option key={s} value={s} />
            ))}
          </datalist>
        </>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          aria-label="Add tag"
          className="inline-flex items-center gap-0.5 rounded-full border border-dashed border-border px-1.5 py-0.5 text-[10px] text-muted-foreground hover:border-primary/50 hover:text-foreground"
        >
          <Plus className="h-2.5 w-2.5" />
          tag
        </button>
      )}
    </div>
  );
}

function ModelCard({
  model,
  active,
  loading,
  disabled,
  suggestions,
  onPick,
  onSetTags,
}: {
  model: LibModel;
  active: boolean;
  loading: boolean;
  disabled: boolean;
  suggestions: string[];
  onPick: (name: string) => void;
  onSetTags: (name: string, tags: string[]) => void;
}) {
  const ctx = ctxLabel(model.context_length);
  const pub = publisher(model.name);
  return (
    <div
      className={cn(
        "relative flex flex-col rounded-xl border p-3 transition-colors",
        active ? "border-primary/60 bg-primary/5" : "border-border hover:border-primary/40",
      )}
    >
      <button
        type="button"
        onClick={() => onPick(model.name)}
        disabled={disabled}
        title={disabled ? model.name : `Load ${model.name}`}
        className={cn("-m-1 rounded-lg p-1 text-left", disabled ? "cursor-not-allowed opacity-70" : "hover:bg-accent/40")}
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

        <div className="mt-2 break-words text-sm font-medium leading-snug">{shortName(model.name)}</div>
        {pub && <div className="truncate text-[11px] text-muted-foreground">{pub}</div>}

        <div className="mt-2 flex items-center gap-2.5 text-[11px] text-muted-foreground">
          {model.tools && <CapChip icon={Wrench} label="tools" className="text-cyan-600 dark:text-cyan-400" />}
          {model.vision && <CapChip icon={Eye} label="vision" className="text-fuchsia-600 dark:text-fuchsia-400" />}
          {model.audio && (
            <CapChip icon={AudioLines} label="audio" className="text-emerald-600 dark:text-emerald-400" />
          )}
          {ctx && <span className="ml-auto">{ctx} ctx</span>}
        </div>
      </button>

      <TagRow tags={model.tags} suggestions={suggestions} onChange={(t) => onSetTags(model.name, t)} />

      {/* Status corner: loaded badge or a loading spinner. */}
      {loading ? (
        <span className="absolute right-2 top-2 text-amber-500">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        </span>
      ) : active ? (
        <span className="absolute right-2 top-7 inline-flex items-center gap-1 rounded-full bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium text-primary">
          <Check className="h-3 w-3" /> loaded
        </span>
      ) : null}
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
  status,
  loadingName,
  onPick,
  onSetTags,
}: {
  library: LibModel[];
  status: Status | null;
  loadingName: string | null;
  onPick: (name: string) => void;
  onSetTags: (name: string, tags: string[]) => void;
}) {
  const locked = status?.locked ?? false;
  const busy = loadingName != null || status?.state === "loading";
  const [activeTags, setActiveTags] = useState<Set<string>>(new Set());

  const allTags = useMemo(() => {
    const s = new Set<string>();
    for (const m of library) for (const t of m.tags) s.add(t);
    return [...s].sort();
  }, [library]);

  const toggleFilter = (t: string) =>
    setActiveTags((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });

  const filtered = useMemo(
    () => (activeTags.size === 0 ? library : library.filter((m) => [...activeTags].every((t) => m.tags.includes(t)))),
    [library, activeTags],
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
          {library.length > 0 && (
            <span className="text-sm text-muted-foreground">
              {filtered.length}
              {filtered.length !== library.length && ` / ${library.length}`} · {totalHuman}
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
                    "rounded-full border px-2 py-0.5 text-[11px] transition-colors",
                    on
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border text-muted-foreground hover:bg-accent",
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

        {library.length === 0 ? (
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
                      disabled={locked || (busy && loadingName !== m.name)}
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

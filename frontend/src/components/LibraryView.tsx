import { useEffect, useMemo, useState } from "react";
import { AudioLines, Eye, Info, Loader2, MessageSquare, Play, Plus, Tag, Wrench, X } from "lucide-react";

import {
  getModelInfo,
  getVoiceModels,
  type LibModel,
  type ModelFile,
  type ModelInfo,
  type Status,
  type VoiceModelInfo,
} from "@/api";
import { allTagsOf, normalizeTag, tagColor, tagStyle } from "@/lib/tags";
import type { TagRegistry } from "@/lib/tags";
import { Markdown } from "@/components/Markdown";
import { VoiceCard } from "@/components/VoiceCard";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { usePublishViewTitle } from "@/lib/view-title";
import { cn, formatBytes } from "@/lib/utils";

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

// Format badge accent, mirroring the CLI's per-format colors (gguf cyan, mlx
// fuchsia, safetensors amber). safetensors takes `--warning` rather than a
// literal amber because that is what its amber has always meant: not
// ready-to-run, and 2-4x the size of the quant it was converted from.
const FORMAT_ACCENT: Record<string, string> = {
  gguf: "border-cyan-500/30 bg-cyan-500/10 text-cyan-600 dark:text-cyan-400",
  mlx: "border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-600 dark:text-fuchsia-400",
  safetensors: "border-warning/30 bg-warning/10 text-warning-ink",
};
const FALLBACK_ACCENT = "border-border bg-muted text-muted-foreground";

// --- The backend axis -------------------------------------------------------
//
// /api/library merges every declared backend into one flat list, each row naming its
// origin. Backend is chosen as the OUTER grouping level and format stays the inner one,
// for three reasons:
//
//   - It is the axis that answers the question the merge created. "gguf" used to mean one
//     thing; with a laptop's library beside two remote hosts it names three unrelated
//     collections, and a flat format section would interleave them with nothing on the card
//     to tell them apart. Backend answers "where does this run", which is what a user picks
//     a host for; format answers "how is it stored", which only matters once you are there.
//   - It is where a name collides. `model@backend` (ROADMAP) exists because two hosts can
//     both serve `gemma-4-12b`; a grouping that puts the qualifier in the heading lets the
//     card keep the bare name, so nothing on screen has to grow a suffix.
//   - A degraded backend has a place to live. An `error` row is a backend, not a model, so
//     it cannot be a card in a format grid at all — under a backend heading it is simply
//     that heading with a reason instead of models.
//
// Not a column: the grid is cards, not a table. Not a filter: a filter hides the other
// backends, and the whole point of the merge is seeing them at once (the tag filter is
// already the "show me less" control, and it composes with this).
//
// The level is not paid for when it is not used. With exactly one backend and nothing
// degraded, `needsBackendAxis` is false and the page renders the format sections directly —
// the same `FormatGroups` element, from the same `groupByFormat` list, as before backends
// existed. See LibraryView.test.ts, which pins that equivalence.

const LOCAL_BACKEND = "local";
/** Stand-in when a degraded row arrives without a reason (the server always sends one). */
const NO_REASON = "no reason reported";

/** `[format, models]`, formats sorted, models sorted by name — one section of the grid. */
export type FormatGroup = [string, LibModel[]];

/** One backend's contribution to the page: its models, or why it has none. */
export interface BackendSection {
  backend: string;
  /** Non-null means this backend could NOT be listed: no models here, only an explanation. */
  reason: string | null;
  groups: FormatGroup[];
  count: number;
}

export type LibraryShape =
  | { kind: "formats"; groups: FormatGroup[] }
  | { kind: "backends"; sections: BackendSection[] };

/** Is this row a backend that could not be listed rather than a model?
 *
 *  `error` is the documented discriminator; `model_format` is checked too so that a row the
 *  server marks `unavailable` can never reach a card — a "Load" button on a host that is down
 *  is a worse failure than a redundant condition.
 */
export function isDegraded(row: LibModel): boolean {
  return row.error != null || row.model_format === "unavailable";
}

/** Split a listing into real models and the backends that could not be listed.
 *
 *  Everything downstream — the tag vocabulary, the model count and byte total in the top bar,
 *  the cards — is fed from `models` alone, so a degraded row cannot be counted as one.
 */
export function partitionLibrary(rows: LibModel[]): { models: LibModel[]; degraded: LibModel[] } {
  const models: LibModel[] = [];
  const degraded: LibModel[] = [];
  for (const row of rows) (isDegraded(row) ? degraded : models).push(row);
  return { models, degraded };
}

/** Group models by format, as the page did before backends existed. */
export function groupByFormat(models: LibModel[]): FormatGroup[] {
  const by: Record<string, LibModel[]> = {};
  for (const m of models) (by[m.model_format] ??= []).push(m);
  for (const list of Object.values(by)) list.sort((a, b) => a.name.localeCompare(b.name));
  return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
}

/** Does this listing need the backend level at all?
 *
 *  Deliberately asked of the WHOLE listing, never the tag-filtered one: the page's structure
 *  is a property of the deployment, and a filter that happens to leave one backend standing
 *  must not silently collapse a level and re-flow the page under the reader.
 */
export function needsBackendAxis(rows: LibModel[]): boolean {
  const names = new Set<string>();
  for (const row of rows) {
    if (isDegraded(row)) return true; // a degraded backend is only legible under its own heading
    names.add(row.backend || LOCAL_BACKEND);
  }
  return names.size > 1;
}

/** Group rows by backend, in the order the server listed them (local first, by convention).
 *
 *  A healthy backend left empty by the tag filter is dropped — a filter is expected to shrink
 *  the page, and an empty section per backend is noise. A degraded one is never dropped, which
 *  is the entire point of it being a row: a host that silently vanishes from the listing is
 *  indistinguishable from a host with no models.
 */
export function backendSections(rows: LibModel[]): BackendSection[] {
  const order: string[] = [];
  const byBackend = new Map<string, LibModel[]>();
  const reasons = new Map<string, string>();
  for (const row of rows) {
    const backend = row.backend || LOCAL_BACKEND;
    let list = byBackend.get(backend);
    if (list === undefined) {
      list = [];
      byBackend.set(backend, list);
      order.push(backend);
    }
    // A degraded row and model rows for one backend is not something the server emits, but if
    // it ever did, showing both beats dropping either: the reason explains, the models still load.
    if (isDegraded(row)) reasons.set(backend, reasons.get(backend) ?? row.error ?? NO_REASON);
    else list.push(row);
  }
  return order
    .map((backend) => {
      const list = byBackend.get(backend) ?? [];
      return { backend, reason: reasons.get(backend) ?? null, groups: groupByFormat(list), count: list.length };
    })
    .filter((s) => s.reason !== null || s.count > 0);
}

/** How to render this listing: `visible` is what survived the tag filter, `multi` comes from
 *  `needsBackendAxis` over the unfiltered listing. */
export function libraryShape(visible: LibModel[], multi: boolean): LibraryShape {
  if (multi) return { kind: "backends", sections: backendSections(visible) };
  return { kind: "formats", groups: groupByFormat(visible.filter((m) => !isDegraded(m))) };
}

/** A capability marker: one icon and one word, in the `--capability` ink.
 *
 *  ONE INK FOR ALL THREE, and the icon is what says which. They used to be written as literals —
 *  the same cyan as the `gguf` badge and the same fuchsia as `mlx`, sitting on the same card, so
 *  a hue meant "this is a GGUF" in one corner and "this calls tools" in the other. The literals
 *  carve-out (docs/ui-conventions.md) covers format badges alone, because those mirror an
 *  identity the CLI already has; a capability mirrors nothing outside the theme.
 */
function CapChip({ icon: Icon, label }: { icon: typeof Wrench; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-capability">
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}

/** What a backend is doing with a model right now — a state stabbur observed, not a user's tag.
 *
 *  This arrived as `tags: ["loaded"]` until the API grew a field for it, which meant a word the
 *  server synthesised sat in the filter row beside hand-written tags, took a colour from the hash
 *  palette, and was offered in the tag editor as "+ loaded" — something a reader could persist
 *  onto a model, where it would then be wrong the moment the backend moved on.
 */
function LoadedChip() {
  return (
    <span className="rounded-full border border-good/30 bg-good/10 px-2 py-0.5 text-xs text-good-ink">resident</span>
  );
}

/** A list with nothing in it. One shape, whatever emptied it — the two used to differ (a bordered
 *  box for "nothing pulled yet", bare text for "nothing matched"), which read as two different
 *  kinds of thing happening rather than one list with two reasons for being short. */
function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-muted/40 px-4 py-6 text-sm text-muted-foreground">
      {children}
    </div>
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
            <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
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
  tagRegistry,
}: {
  label: string;
  tags: string[];
  suggestions: string[];
  onChange: (tags: string[]) => void;
  tagRegistry: TagRegistry;
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
        {tags.map((t) => {
          const s = tagStyle(t, tagRegistry);
          return (
            <span
              key={t}
              className={cn("rounded-full border px-2 py-0.5 text-xs", s.className)}
              style={s.style}
            >
              {s.icon && <span className="mr-0.5">{s.icon}</span>}
              {t}
            </span>
          );
        })}
        <span className="inline-flex items-center gap-0.5 rounded-full border border-dashed border-border px-2 py-0.5 text-xs text-muted-foreground hover:border-primary/50 hover:text-foreground">
          <Plus className="h-3 w-3" />
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

/** Strip a leading YAML front-matter block from a model card.
 *
 *  Hugging Face READMEs open with `---`-fenced YAML (license, tags, base_model, the eval table's
 *  raw source), which is metadata for the Hub, not prose for a reader. Markdown has no notion of
 *  it, so it rendered as the first paragraph of the card — a wall of `key: value` lines above the
 *  actual description. Only a block the file OPENS with is dropped: a `---` further down is a
 *  horizontal rule and means what it says.
 */
export function stripFrontMatter(card: string): string {
  const text = card.replace(/^﻿/, "");
  if (!/^---[ \t]*\r?\n/.test(text)) return card;
  const end = text.slice(4).search(/\r?\n---[ \t]*(\r?\n|$)/);
  if (end < 0) return card; // an unterminated fence is not front matter; leave it alone
  return text.slice(4 + end).replace(/^\r?\n---[ \t]*(\r?\n)?/, "");
}

/** The weights on disk, largest first — what the directory holds and what a Load will run.
 *
 *  A repo pulled at two quants is ONE card with ONE size, and that size is the pair's sum while
 *  Load runs exactly one of the two. Until the picker exists (ROADMAP), this is where the reader
 *  finds out which: the row marked "loads" is the file the runtime opens.
 */
function FileList({ files }: { files: ModelFile[] }) {
  if (files.length === 0) return null;
  return (
    <div>
      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Files</div>
      <ul className="divide-y divide-border rounded-lg border border-border">
        {files.map((f) => (
          <li key={f.name} className="flex items-center gap-2 px-2.5 py-2">
            <span className="min-w-0 flex-1 truncate text-xs" title={f.name}>
              {f.name}
            </span>
            {f.role && (
              <span
                className={cn(
                  "shrink-0 rounded-full border px-2 py-0.5 text-xs",
                  f.role === "loads" ? "border-good/30 bg-good/10 text-good-ink" : "border-border bg-muted/60",
                )}
              >
                {f.role}
              </span>
            )}
            <span className="w-20 shrink-0 text-right text-xs tabular-nums text-muted-foreground">{f.size_human}</span>
          </li>
        ))}
      </ul>
    </div>
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

        <div className="flex flex-wrap items-center gap-2.5 text-xs text-muted-foreground">
          {model.tools && <CapChip icon={Wrench} label="tools" />}
          {model.vision && <CapChip icon={Eye} label="vision" />}
          {model.audio && <CapChip icon={AudioLines} label="audio" />}
          {model.loaded && <LoadedChip />}
          {model.tags.map((t) => (
            <span key={t} className="rounded-full border border-border bg-muted/60 px-2 py-0.5 text-xs">
              {t}
            </span>
          ))}
        </div>
        {info?.path && <div className="break-all text-xs text-muted-foreground">{info.path}</div>}

        <FileList files={info?.files ?? []} />

        <div className="max-h-[70vh] min-h-[40vh] overflow-y-auto rounded-lg border border-border bg-muted/20 p-4 text-sm">
          {loading ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading model card…
            </div>
          ) : info?.card ? (
            <Markdown content={stripFrontMatter(info.card)} allowHtml />
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
  onLoad,
  onChat,
  onSetTags,
  tagRegistry,
}: {
  model: LibModel;
  active: boolean;
  loading: boolean;
  blocked: boolean;
  suggestions: string[];
  onLoad: (name: string) => void;
  onChat: () => void;
  onSetTags: (name: string, tags: string[]) => void;
  tagRegistry: TagRegistry;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const ctx = ctxLabel(model.context_length);
  const pub = publisher(model.name);
  const weights = model.weight_count ?? 1; // absent on a backend older than the field: one weight
  const actDisabled = loading || (!active && blocked);
  return (
    <div
      className={cn(
        "relative flex flex-col rounded-xl border p-4 transition-colors",
        loading && "border-primary/50 ring-2 ring-primary/30",
        active
          ? "border-primary/60 bg-primary/5"
          : "border-border hover:border-primary/40 hover:bg-accent/40",
      )}
    >
      {loading && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 rounded-xl bg-background/75 backdrop-blur-[1px]">
          <Loader2 className="h-6 w-6 animate-spin text-primary" />
          <span className="text-xs font-medium text-foreground">Loading model…</span>
        </div>
      )}
      <div className="flex items-center justify-between gap-2">
        <span
          className={cn(
            // The badge recipe exactly (docs/ui-conventions.md): px-1.5, not px-2.
            "rounded border px-1.5 py-0.5 text-xs font-medium uppercase tracking-wide",
            FORMAT_ACCENT[model.model_format] ?? FALLBACK_ACCENT,
          )}
        >
          {model.model_format}
        </span>
        {/* ONE NUMBER, TWO MEANINGS, so say which. With a single weight `size_human` is what Load
            runs; with several it is the sum of alternatives, and the card was advertising the
            total for a load that opens one of them (an 18 GB quant beside a 31 GB one read as a
            47.7 GB model). The Details dialog lists the files and marks the one that loads. */}
        <span className="text-xs text-muted-foreground">
          {weights > 1 ? `${weights} quants · ${model.size_human} total` : model.size_human}
        </span>
      </div>

      <div className="mt-2 break-words text-sm font-medium leading-snug" title={model.name}>
        {shortName(model.name)}
      </div>
      {pub && <div className="truncate text-xs text-muted-foreground">{pub}</div>}

      <div className="mt-2 flex items-center gap-2.5 text-xs text-muted-foreground">
        {model.tools && <CapChip icon={Wrench} label="tools" />}
        {model.vision && <CapChip icon={Eye} label="vision" />}
        {model.audio && <CapChip icon={AudioLines} label="audio" />}
        {ctx && <span className="ml-auto">{ctx} ctx</span>}
      </div>

      {model.loaded && (
        <div className="mt-2">
          <LoadedChip />
        </div>
      )}

      <TagRow
        label={model.name}
        tags={model.tags}
        suggestions={suggestions}
        onChange={(t) => onSetTags(model.name, t)}
        tagRegistry={tagRegistry}
      />

      {/* Explicit actions: details (any model) + load/chat (deliberate, never on card click).
          `mt-auto` pins the row to the foot of the card, so Load and Details line up across a grid
          row whatever the cards above them are: a name that wraps to two lines, a publisher line,
          a capability row, and a tag row are each optional, and without this every card in a row
          put its buttons at a different height. */}
      <div className="mt-auto flex items-center justify-between gap-2 pt-3">
        <button
          type="button"
          onClick={() => setDetailsOpen(true)}
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <Info className="h-3.5 w-3.5" /> Details
        </button>
        {active ? (
          // Already loaded: go start a fresh chat with it (never auto-switched on load).
          <button
            type="button"
            onClick={onChat}
            className="inline-flex items-center gap-1 rounded-lg bg-primary/15 px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/25"
          >
            <MessageSquare className="h-3.5 w-3.5" /> Chat
          </button>
        ) : (
          // Loading stays on this view; the card flips to "Chat" when it's ready.
          <button
            type="button"
            onClick={() => onLoad(model.name)}
            disabled={actDisabled}
            className={cn(
              "inline-flex items-center gap-1 rounded-lg bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90",
              actDisabled && "cursor-not-allowed opacity-60",
            )}
          >
            {loading ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> loading…
              </>
            ) : (
              <>
                <Play className="h-3.5 w-3.5" /> Load
              </>
            )}
          </button>
        )}
      </div>

      <ModelDetailsDialog model={model} open={detailsOpen} onOpenChange={setDetailsOpen} />
    </div>
  );
}

/** Everything a card needs that isn't the model itself, bundled so the grid can be rendered
 *  from two places (flat, and nested under a backend) without drilling nine props twice. */
interface GridContext {
  activeName: string | null;
  loadingName: string | null;
  locked: boolean;
  busy: boolean;
  suggestions: string[];
  onLoad: (name: string) => void;
  onChat: () => void;
  onSetTags: (name: string, tags: string[]) => void;
  tagRegistry: TagRegistry;
}

/** The format sections and their card grids — the page's original body, unchanged. */
function FormatGroups({ groups, ctx }: { groups: FormatGroup[]; ctx: GridContext }) {
  return (
    <div className="space-y-6">
      {groups.map(([fmt, list]) => (
        <section key={fmt}>
          <div className="mb-2 flex items-center gap-2">
            <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{fmt}</span>
            <span className="text-xs text-muted-foreground">{list.length}</span>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {list.map((m) => (
              <ModelCard
                key={m.name}
                model={m}
                active={ctx.activeName === m.name}
                loading={ctx.loadingName === m.name}
                blocked={ctx.locked || (ctx.busy && ctx.loadingName !== m.name)}
                suggestions={ctx.suggestions}
                onLoad={ctx.onLoad}
                onChat={ctx.onChat}
                onSetTags={ctx.onSetTags}
                tagRegistry={ctx.tagRegistry}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

/** One backend's block: its name, then either its format sections or why it has none.
 *
 *  The heading ladder is weight and case, not size (docs/ui-conventions.md): "Chat" is
 *  title-case `text-sm font-semibold`, a backend is `text-xs font-semibold uppercase` in the
 *  page's ink, a format is the same eyebrow at `font-medium` and muted. Nothing grew a pixel.
 */
function BackendGroup({ section, ctx }: { section: BackendSection; ctx: GridContext }) {
  const down = section.reason !== null;
  return (
    <section className="border-t border-border pt-5 first:border-t-0 first:pt-0">
      <div className="mb-3 flex flex-wrap items-baseline gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-foreground">{section.backend}</h3>
        {down ? (
          // `--critical`, not `--destructive`: a backend being down is a state stabbur observed
          // and is reporting, not an affordance the reader can press.
          <span className="rounded-full border border-critical/30 bg-critical/10 px-2 py-0.5 text-xs text-critical">
            unavailable
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">
            {section.count} model{section.count === 1 ? "" : "s"}
          </span>
        )}
      </div>
      {down && (
        // The chip carries the state; this sentence carries the reason. A sentence is not a chip.
        <p className="text-sm text-muted-foreground">Declared, but its models could not be listed: {section.reason}</p>
      )}
      {section.groups.length > 0 && <FormatGroups groups={section.groups} ctx={ctx} />}
    </section>
  );
}

/**
 * Full-panel "Models" browser: every library model as a card, grouped by backend and then by
 * format (like `stabbur ls`), or by format alone when there is only one backend. Clicking a
 * card loads it and drops into chat; the loaded one is marked. Cards carry editable user tags,
 * with a tag filter bar on top.
 */
export function LibraryView({
  library,
  loaded,
  error,
  status,
  loadingName,
  onLoad,
  onChat,
  onSetTags,
  tagRegistry,
}: {
  library: LibModel[];
  loaded: boolean;
  error?: string | null;
  status: Status | null;
  loadingName: string | null;
  onLoad: (name: string) => void;
  onChat: () => void;
  onSetTags: (name: string, tags: string[]) => void;
  tagRegistry: TagRegistry;
}) {
  const locked = status?.locked ?? false;
  const busy = loadingName != null || status?.state === "loading";
  const [activeTags, setActiveTags] = useState<Set<string>>(new Set());

  // Normalize once so a model with no `tags` field (e.g. an older server that
  // predates tags) can't crash the grid. `backend` gets the same treatment: a server older
  // than the field sends none, and one backend named "local" is exactly what it had.
  const rows = useMemo(
    () => library.map((m) => ({ ...m, tags: m.tags ?? [], backend: m.backend || LOCAL_BACKEND })),
    [library],
  );

  // The split that keeps a down backend from ever being mistaken for a model: `models` is what
  // the counts, the tag vocabulary and the cards see, and it contains no degraded rows.
  const { models } = useMemo(() => partitionLibrary(rows), [rows]);
  const multiBackend = useMemo(() => needsBackendAxis(rows), [rows]);

  const allTags = useMemo(() => allTagsOf(models), [models]);

  // The Library also lists voice models (TTS/STT) — a separate category from chat LLMs.
  const [voiceModels, setVoiceModels] = useState<VoiceModelInfo[]>([]);
  useEffect(() => {
    getVoiceModels().then(setVoiceModels).catch(() => {});
  }, []);
  const voiceGroups = useMemo(() => {
    const by: Record<string, VoiceModelInfo[]> = {};
    for (const m of voiceModels) (by[m.kind] ??= []).push(m);
    return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
  }, [voiceModels]);

  const toggleFilter = (t: string) =>
    setActiveTags((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });

  // A degraded row survives the tag filter: it has no tags to match on, and hiding it would
  // turn "this host is down" into "this host has nothing tagged that" — the exact confusion the
  // row exists to prevent. `filtered` is models only, so counts stay a count of models.
  const visible = useMemo(
    () =>
      activeTags.size === 0
        ? rows
        : rows.filter((m) => isDegraded(m) || [...activeTags].every((t) => m.tags.includes(t))),
    [rows, activeTags],
  );
  const filtered = useMemo(() => visible.filter((m) => !isDegraded(m)), [visible]);

  const totalBytes = useMemo(() => filtered.reduce((sum, m) => sum + m.size_bytes, 0), [filtered]);

  // What the top bar reads while the Library is on screen. Tag-filter aware, so the bar answers
  // "how much of it am I looking at" rather than restating a constant.
  //
  // "CHAT MODELS", not "models": this page also lists voice models, under their own heading with
  // their own count, so a bare "28 models" above 28 chat cards and 3 voice cards is a number that
  // disagrees with the page it sits over. And the size term is DROPPED rather than zeroed — a
  // remote-only instance keeps its weights on another host, so "0 MB" would read as a tiny
  // library where the truth is that stabbur has no figure to give.
  const chip = useMemo(() => {
    if (models.length === 0) return null;
    const count = `${filtered.length}${filtered.length !== models.length ? ` / ${models.length}` : ""}`;
    const label = `${count} chat model${filtered.length === 1 && filtered.length === models.length ? "" : "s"}`;
    return totalBytes > 0 ? `${label} · ${formatBytes(totalBytes)}` : label;
  }, [filtered.length, models.length, totalBytes]);
  usePublishViewTitle("library", "Library", chip);

  const shape = useMemo(() => libraryShape(visible, multiBackend), [visible, multiBackend]);

  // `status.model` is an unqualified name (the API has no field naming the ACTIVE backend), so
  // two backends serving the same name would both read as loaded. Left as-is deliberately: the
  // fix is a backend name in /api/status, not a guess here from the upstream URL.
  const ctx: GridContext = {
    activeName: status?.model ?? null,
    loadingName,
    locked,
    busy,
    suggestions: allTags,
    onLoad,
    onChat,
    onSetTags,
    tagRegistry,
  };

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-5xl px-6 py-6">
        {allTags.length > 0 && (
          <div className="mb-4 flex flex-wrap items-center gap-1.5">
            <Tag className="h-3.5 w-3.5 text-muted-foreground" />
            {allTags.map((t) => {
              const on = activeTags.has(t);
              const s = tagStyle(t, tagRegistry);
              return (
                <button
                  key={t}
                  type="button"
                  onClick={() => toggleFilter(t)}
                  aria-pressed={on}
                  style={s.style}
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-xs transition-all",
                    s.className,
                    on ? "font-medium ring-1 ring-inset ring-current" : "opacity-70 hover:opacity-100",
                  )}
                >
                  {s.icon && <span className="mr-0.5">{s.icon}</span>}
                  {t}
                </button>
              );
            })}
            {activeTags.size > 0 && (
              <button
                type="button"
                onClick={() => setActiveTags(new Set())}
                className="ml-1 text-xs text-muted-foreground hover:text-foreground"
              >
                clear
              </button>
            )}
          </div>
        )}

        {!loaded && models.length === 0 && voiceModels.length === 0 ? (
          <div className="flex items-center gap-2 px-1 py-10 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading library…
          </div>
        ) : (
          <div className="space-y-8">
            {/* Chat models (LLMs) — loadable, taggable. */}
            <section>
              <div className="mb-3">
                {/* No count beside this heading: the top bar's chip is the same figure, filter and
                    all, and two copies of one number is one number wearing two costumes. The Voice
                    heading below keeps its own, because the bar says nothing about voice models. */}
                <h2 className="text-sm font-semibold tracking-tight">Chat</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Language models you talk to — text in and out. Some also read images or audio, or call tools.
                </p>
                {/* AT THE HEAD OF THE LIST, not the foot. This is the reason every Load button
                    below is greyed, and at the bottom of a long list it was a full scroll away
                    from the first one — read only by someone who had already given up on it. */}
                {locked && (
                  <p className="mt-1.5 text-sm text-muted-foreground">
                    This server is locked to a single model; switching is disabled.
                  </p>
                )}
              </div>
              {!loaded ? (
                <div className="flex items-center gap-2 px-1 py-4 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" /> Loading…
                </div>
              ) : models.length === 0 && error ? (
                <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-6 text-sm text-destructive">
                  Couldn't read the library: {error}. Check the drive is mounted and{" "}
                  <code className="font-mono">STABBUR_LIBRARY_ROOT</code> is set, then retry.
                </div>
              ) : (
                <div className="space-y-5">
                  {/* CHOSEN BY WHY IT IS EMPTY, which the old gate got backwards: it hung the
                      "no models yet" state on `degraded.length === 0`, so an empty library behind
                      a backend that was down fell through to "No chat models match the selected
                      tags" — with no tag selected, and nothing to clear. The question is whether a
                      filter is hiding them, and only `filtered` vs `models` answers that. Both
                      states wear the same box; an empty list is the same kind of statement
                      whichever emptied it. */}
                  {models.length === 0 ? (
                    <EmptyState>
                      No chat models yet. Pull one with <code className="font-mono">stabbur pull</code>.
                    </EmptyState>
                  ) : filtered.length === 0 ? (
                    <EmptyState>
                      No chat models match the selected tags.{" "}
                      <button
                        type="button"
                        onClick={() => setActiveTags(new Set())}
                        className="font-medium text-primary hover:underline"
                      >
                        Clear the filter
                      </button>
                      .
                    </EmptyState>
                  ) : null}
                  {/* A backend that could not be listed still gets its block, empty library or not:
                      that row IS the explanation for why the list is short. */}
                  {shape.kind === "formats"
                    ? shape.groups.length > 0 && <FormatGroups groups={shape.groups} ctx={ctx} />
                    : shape.sections.map((s) => <BackendGroup key={s.backend} section={s} ctx={ctx} />)}
                </div>
              )}
            </section>

            {/* Voice models (TTS/STT) — reference cards; used from the Voice studio. */}
            {voiceModels.length > 0 && (
              <section>
                <div className="mb-3">
                  <div className="flex items-baseline gap-2">
                    <h2 className="text-sm font-semibold tracking-tight">Voice</h2>
                    <span className="text-xs text-muted-foreground">
                      {voiceModels.length} model{voiceModels.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Audio in/out, not chat — <span className="font-medium">TTS</span> speaks text,{" "}
                    <span className="font-medium">STT</span> transcribes speech. Use them in the Voice studio.
                  </p>
                </div>
                <div className="space-y-6">
                  {voiceGroups.map(([kind, list]) => (
                    <section key={kind}>
                      <div className="mb-2 flex items-center gap-2">
                        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                          {kind}
                        </span>
                        <span className="text-xs text-muted-foreground">{list.length}</span>
                      </div>
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                        {list.map((m) => (
                          <VoiceCard key={m.name} model={m} />
                        ))}
                      </div>
                    </section>
                  ))}
                </div>
              </section>
            )}
          </div>
        )}
      </div>
      </div>
    </>
  );
}

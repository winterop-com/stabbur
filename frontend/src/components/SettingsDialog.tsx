import { useEffect, useState } from "react";
import { AudioLines, Moon, Palette, Server, Sun } from "lucide-react";

import { getModelInfo, type LibModel, type ModelInfo, type Status, type Voice } from "@/api";
import { Markdown } from "@/components/Markdown";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { THEMES, type Mode, type Theme } from "@/lib/store";
import { cn } from "@/lib/utils";

const SPEEDS = [0.8, 0.9, 1, 1.1, 1.25, 1.5];

/**
 * The three groups the left pane offers. Two axes, not four: what *you* set for this browser
 * (how it looks, how it speaks) and what the *server* brought with it (read-only). Splitting
 * "how it looks" from "how it speaks" is the division a reader already expects, and each side
 * has obvious room to grow — density and fonts land in Appearance, an engine or autoplay
 * switch in Voice — without anyone having to re-decide the category list.
 */
const CATEGORIES = [
  { id: "appearance", label: "Appearance", icon: Palette },
  { id: "voice", label: "Voice", icon: AudioLines },
  { id: "server", label: "Server", icon: Server },
] as const;

type CategoryId = (typeof CATEGORIES)[number]["id"];

/** A pill in a row of mutually-exclusive choices (speed, mode). */
function Choice({
  selected,
  onClick,
  children,
}: {
  selected: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs tabular-nums transition-colors",
        selected
          ? "bg-primary/15 font-medium text-primary"
          : "text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/** A titled settings section: heading + muted description. */
function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h3 className="text-sm font-semibold">{title}</h3>
      {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

/** Pick a couple of human-friendly metadata fields to surface, if present. */
function metaFields(meta: Record<string, unknown> | null): [string, string][] {
  if (!meta) return [];
  const out: [string, string][] = [];
  const push = (label: string, value: unknown) => {
    if (value == null || typeof value === "object") return;
    out.push([label, String(value)]);
  };
  push("Source", meta.source);
  push("Files", meta.file_count);
  push("Publisher", meta.publisher);
  push("Repo", meta.repo);
  return out;
}

/** Appearance: the mode (light/dark) and the theme (the named colour set), both stored here. */
function AppearancePane({
  mode,
  onToggleMode,
  theme,
  onChooseTheme,
}: {
  mode: Mode;
  onToggleMode: () => void;
  theme: Theme;
  onChooseTheme: (theme: Theme) => void;
}) {
  return (
    <>
      <Section title="Mode" description="Light or dark. Stored in this browser, not in the project.">
        <div className="flex flex-wrap items-center gap-1">
          <Choice selected={mode === "light"} onClick={() => mode !== "light" && onToggleMode()}>
            <Sun className="h-3.5 w-3.5" /> Light
          </Choice>
          <Choice selected={mode === "dark"} onClick={() => mode !== "dark" && onToggleMode()}>
            <Moon className="h-3.5 w-3.5" /> Dark
          </Choice>
        </div>
      </Section>

      <Section title="Theme" description="The named colour set the whole app draws from. Every one has both modes.">
        {/* Rows rather than the pill row the other choices use: a theme is picked by what it
            looks like, and the hint is the only thing here that says that — a pill has nowhere
            to put a sentence. THEMES rather than a list of our own, so adding a theme is one
            block pair in index.css and one row in that array. */}
        <div className="flex flex-col gap-0.5">
          {THEMES.map((t) => (
            <button
              key={t.name}
              type="button"
              onClick={() => onChooseTheme(t.name)}
              aria-pressed={theme === t.name}
              className={cn(
                "rounded-md px-2 py-1.5 text-left transition-colors",
                theme === t.name ? "bg-primary/15 text-primary" : "hover:bg-accent hover:text-foreground",
              )}
            >
              <span className="text-xs font-medium">{t.label}</span>
              <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">{t.hint}</span>
            </button>
          ))}
        </div>
      </Section>
    </>
  );
}

/** Voice: the defaults a new chat's Listen button inherits when it sets none of its own. */
function VoicePane({
  status,
  voices,
  ttsVoice,
  onChooseVoice,
  ttsSpeed,
  onChooseSpeed,
}: {
  status: Status | null;
  voices: Voice[];
  ttsVoice: string;
  onChooseVoice: (name: string) => void;
  ttsSpeed: number;
  onChooseSpeed: (speed: number) => void;
}) {
  return (
    <>
      <Section
        title="Default voice"
        description="Used by Listen in chats that don't set their own voice. 54 built-in Kokoro voices across 9 languages."
      >
        <select
          value={ttsVoice}
          onChange={(e) => onChooseVoice(e.target.value)}
          className="h-8 w-full max-w-md rounded-md border border-border bg-background/60 px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option value="">{status?.default_chat_voice ? "Project default" : "Built-in default"}</option>
          {Object.entries(
            voices.reduce<Record<string, Voice[]>>((acc, v) => {
              (acc[v.language || "Other"] ??= []).push(v);
              return acc;
            }, {}),
          ).map(([language, vs]) => (
            <optgroup key={language} label={language}>
              {vs.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label} · {v.gender === "female" ? "F" : "M"}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </Section>

      <Section title="Default speed" description="How fast Listen reads a reply back.">
        <div className="flex flex-wrap items-center gap-1">
          {SPEEDS.map((v) => (
            <Choice key={v} selected={ttsSpeed === v} onClick={() => onChooseSpeed(v)}>
              {v}x
            </Choice>
          ))}
        </div>
      </Section>
    </>
  );
}

/**
 * Server: the two read-only blocks — what the project's `heim.toml` contributes, and what the
 * runtime currently holds. The model card is fetched here rather than by the dialog, so it is
 * only read when someone actually opens this category (the pane mounts with the selection).
 */
function ServerPane({ status, library }: { status: Status | null; library: LibModel[] }) {
  const modelName = status?.model ?? null;
  const libEntry = library.find((m) => m.name === modelName) ?? null;

  const [info, setInfo] = useState<ModelInfo | null>(null);
  const [infoLoading, setInfoLoading] = useState(false);

  useEffect(() => {
    if (!modelName) {
      setInfo(null);
      return;
    }
    let cancelled = false;
    setInfoLoading(true);
    getModelInfo(modelName)
      .then((i) => !cancelled && setInfo(i))
      .catch(() => !cancelled && setInfo(null))
      .finally(() => !cancelled && setInfoLoading(false));
    return () => {
      cancelled = true;
    };
  }, [modelName]);

  const fmt = info?.model_format ?? libEntry?.model_format ?? null;
  const size = info?.size_human ?? libEntry?.size_human ?? null;
  const fields = metaFields(info?.metadata ?? null);

  return (
    <>
      <Section title="Project" description="What this server's heim.toml contributes as defaults.">
        <dl className="space-y-2 text-sm">
          <div className="flex gap-3">
            <dt className="w-28 shrink-0 text-muted-foreground">Model</dt>
            <dd className="min-w-0 truncate">
              {status?.project_model ?? <span className="text-muted-foreground">none — free-play</span>}
            </dd>
          </div>
          <div className="flex gap-3">
            <dt className="w-28 shrink-0 text-muted-foreground">Chat voice</dt>
            <dd className="min-w-0 truncate">
              {status?.default_chat_voice ?? <span className="text-muted-foreground">the built-in voice</span>}
            </dd>
          </div>
          <div className="flex gap-3">
            <dt className="w-28 shrink-0 text-muted-foreground">System prompt</dt>
            <dd className="min-w-0">
              {status?.default_system_prompt ? (
                <p className="whitespace-pre-wrap text-xs text-muted-foreground">{status.default_system_prompt}</p>
              ) : (
                <span className="text-muted-foreground">none</span>
              )}
            </dd>
          </div>
        </dl>
        <p className="mt-2 text-[11px] text-muted-foreground">
          Edit these in the project's <code className="font-mono">heim.toml</code>; machine defaults come from{" "}
          <code className="font-mono">heim config set</code>.
        </p>
      </Section>

      <Section title="Loaded model" description="What this server currently has loaded.">
        {modelName ? (
          <>
            <div className="truncate text-sm font-medium" title={modelName}>
              {modelName.split("/").pop() ?? modelName}
            </div>
            <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
              {fmt && <span className="rounded bg-muted px-1.5 py-0.5">{fmt}</span>}
              {size && <span className="py-0.5">{size}</span>}
            </div>
            {fields.length > 0 && (
              <dl className="mt-2 space-y-0.5 text-xs">
                {fields.map(([k, v]) => (
                  <div key={k} className="flex gap-2">
                    <dt className="shrink-0 text-muted-foreground">{k}</dt>
                    <dd className="truncate" title={v}>
                      {v}
                    </dd>
                  </div>
                ))}
              </dl>
            )}

            <div className="mt-4">
              <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Model card
              </div>
              {/* The card is the one unbounded thing in here, and the pane it sits in already
                  scrolls — so cap it, or a long card turns the whole dialog into one scroll. */}
              <div className="max-h-96 overflow-y-auto rounded-md border border-border bg-background/60 px-4 py-3">
                {infoLoading ? (
                  <p className="text-xs text-muted-foreground">Loading…</p>
                ) : info?.card ? (
                  <Markdown content={info.card} />
                ) : (
                  <p className="text-xs text-muted-foreground">No model card available.</p>
                )}
              </div>
            </div>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">No model loaded.</p>
        )}
      </Section>
    </>
  );
}

/**
 * Settings: **defaults and environment**, not per-chat knobs. It holds the appearance of this
 * browser, the defaults new conversations inherit (the Listen voice + speed), what the project
 * contributes (`heim.toml`), and reference info about the loaded model. Everything adjustable
 * for a single conversation lives in that chat's settings panel.
 *
 * A dialog rather than a primary view: it is a handful of thin sections, so a whole destination
 * reads as sparse — and it was competing in the nav with the three surfaces you actually move
 * between. Two panes (categories left, the selection right) rather than one long scrolling
 * column, so a group is a place you go rather than something you scroll past.
 *
 * Appearance state is not owned here — `useTheme` (App) owns both axes (mode and theme), and the
 * ⌘K palette drives the same handlers. Two surfaces, one source of truth, so switching in one is
 * instantly reflected in the other.
 */
export function SettingsDialog({
  open,
  onOpenChange,
  status,
  library,
  voices,
  ttsVoice,
  onChooseVoice,
  ttsSpeed,
  onChooseSpeed,
  mode,
  onToggleMode,
  theme,
  onChooseTheme,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  status: Status | null;
  library: LibModel[];
  voices: Voice[];
  ttsVoice: string;
  onChooseVoice: (name: string) => void;
  ttsSpeed: number;
  onChooseSpeed: (speed: number) => void;
  mode: Mode;
  onToggleMode: () => void;
  theme: Theme;
  onChooseTheme: (theme: Theme) => void;
}) {
  const [category, setCategory] = useState<CategoryId>("appearance");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* A fixed height (not a max) so the *content pane* is what scrolls: the model card can be
          thousands of lines, and a dialog that grows with it would leave the category list
          somewhere off-screen. Sized against the viewport so it never overflows one.
          aria-describedby is cleared because each category carries its own descriptions —
          there is no one sentence that describes the whole dialog. */}
      <DialogContent
        aria-describedby={undefined}
        className="flex h-[min(34rem,calc(100dvh-2rem))] w-[calc(100vw-2rem)] max-w-4xl flex-col gap-0 overflow-hidden rounded-lg p-0 md:h-[min(38rem,calc(100dvh-4rem))] md:flex-row"
      >
        {/* Below `md` two panes don't fit (224px of rail off a 390px phone leaves no content), so
            the category list lies down as a scrollable row of pills above the content instead of
            becoming a drill-down: every category stays one tap away, so there is never a "back"
            to find. `pr-12` keeps the last pill clear of the dialog's close button, which shares
            this corner only in the stacked layout. */}
        <nav
          aria-label="Settings categories"
          className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-border p-2 pr-12 md:w-56 md:flex-col md:items-stretch md:overflow-x-visible md:overflow-y-auto md:border-b-0 md:border-r md:bg-muted/40 md:p-3 md:pr-3"
        >
          {/* The dialog's accessible name, and the left pane's heading once there is a left pane. */}
          <DialogTitle className="sr-only md:not-sr-only md:mb-2 md:px-2 md:text-sm">Settings</DialogTitle>
          {CATEGORIES.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setCategory(id)}
              aria-current={category === id ? "page" : undefined}
              className={cn(
                "flex shrink-0 items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors",
                category === id
                  ? "bg-primary/10 font-medium text-primary"
                  : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
            </button>
          ))}
          {/* Only where the pane has vertical room to spare — the stacked layout is a single row. */}
          <p className="mt-auto hidden px-2 text-[11px] leading-snug text-muted-foreground md:block">
            System prompt, sampling, context and tools are per conversation — they live in the chat's
            own settings panel.
          </p>
        </nav>

        <div className="min-h-0 min-w-0 flex-1 space-y-8 overflow-y-auto px-5 py-5 md:px-6 md:pr-12">
          {category === "appearance" && (
            <AppearancePane mode={mode} onToggleMode={onToggleMode} theme={theme} onChooseTheme={onChooseTheme} />
          )}
          {category === "voice" && (
            <VoicePane
              status={status}
              voices={voices}
              ttsVoice={ttsVoice}
              onChooseVoice={onChooseVoice}
              ttsSpeed={ttsSpeed}
              onChooseSpeed={onChooseSpeed}
            />
          )}
          {category === "server" && <ServerPane status={status} library={library} />}
        </div>
      </DialogContent>
    </Dialog>
  );
}

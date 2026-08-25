import { useEffect, useState } from "react";

import { getModelInfo, type LibModel, type ModelInfo, type Status, type Voice } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Markdown } from "@/components/Markdown";
import type { ReasoningLevel, Settings } from "@/lib/store";

/** A titled settings section: heading + muted description, Capture-style. */
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
      <h2 className="text-sm font-semibold">{title}</h2>
      {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

/** Format a token count compactly: 262144 -> "256K", 8192 -> "8K". */
function fmtTokens(n: number): string {
  return n >= 1024 && n % 1024 === 0 ? `${n / 1024}K` : n.toLocaleString();
}

/** Common context sizes, capped at the model's trained max (which is always included). */
function contextPresets(max: number | null): number[] {
  const std = [4096, 8192, 16384, 32768, 65536, 131072, 262144];
  if (!max) return std;
  return [...new Set([...std.filter((v) => v <= max), max])].sort((a, b) => a - b);
}

/** Parse a numeric-string input into number | null (blank / invalid -> null). */
function parseNum(raw: string, opts: { int?: boolean; min?: number } = {}): number | null {
  const s = raw.trim();
  if (!s) return null;
  const n = opts.int ? parseInt(s, 10) : parseFloat(s);
  if (!Number.isFinite(n)) return null;
  if (opts.min != null && n < opts.min) return null;
  return n;
}

/** Pick a couple of human-friendly metadata fields to surface, if present. */
function metaFields(meta: Record<string, unknown> | null): [string, string][] {
  if (!meta) return [];
  const out: [string, string][] = [];
  const push = (label: string, value: unknown) => {
    if (value == null) return;
    if (typeof value === "object") return;
    out.push([label, String(value)]);
  };
  push("Source", meta.source);
  push("Files", meta.file_count);
  push("Publisher", meta.publisher);
  push("Repo", meta.repo);
  return out;
}

/**
 * The Settings page (a primary view, reached from the sidebar's bottom entry).
 * Live-applies every change (no Save button): each control writes back through
 * onChange immediately (App persists to localStorage). The model card is fetched
 * from /api/model when the view is open and the model changes.
 */
export function SettingsView({
  status,
  library,
  activeId,
  settings,
  onChange,
  onReloadContext,
  busy,
  voices,
  ttsVoice,
  onChooseVoice,
}: {
  status: Status | null;
  library: LibModel[];
  activeId: string | null;
  settings: Settings;
  onChange: (s: Settings) => void;
  onReloadContext: (nCtx: number | null) => void;
  busy: boolean;
  voices: Voice[];
  ttsVoice: string;
  onChooseVoice: (name: string) => void;
}) {
  const modelName = status?.model ?? null;
  const libEntry = library.find((m) => m.name === modelName) ?? null;

  const [info, setInfo] = useState<ModelInfo | null>(null);
  const [infoErr, setInfoErr] = useState<string | null>(null);
  const [infoLoading, setInfoLoading] = useState(false);

  // Local text state for the numeric inputs so partial edits (e.g. "0.") don't
  // get clobbered; the parsed value is pushed to settings on every change.
  const [maxTokens, setMaxTokens] = useState(settings.maxTokens != null ? String(settings.maxTokens) : "");
  const [temperature, setTemperature] = useState(settings.temperature != null ? String(settings.temperature) : "");
  const [topP, setTopP] = useState(settings.topP != null ? String(settings.topP) : "");
  const [context, setContext] = useState(settings.contextLength != null ? String(settings.contextLength) : "");
  // "Custom…" selected in the context dropdown → reveal a free-form token input.
  const [customCtx, setCustomCtx] = useState(false);

  // The view stays mounted across conversation switches, so re-seed the local input text from
  // the new conversation's settings when the active conversation changes (F-3). Keyed on
  // activeId only — not on `settings` — so a keystroke (which updates settings) doesn't clobber
  // an in-progress partial edit like "0.".
  useEffect(() => {
    setMaxTokens(settings.maxTokens != null ? String(settings.maxTokens) : "");
    setTemperature(settings.temperature != null ? String(settings.temperature) : "");
    setTopP(settings.topP != null ? String(settings.topP) : "");
    setContext(settings.contextLength != null ? String(settings.contextLength) : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  // Fetch the card whenever the model changes (guarded on modelName).
  useEffect(() => {
    if (!modelName) {
      setInfo(null);
      setInfoErr(null);
      return;
    }
    let cancelled = false;
    setInfoLoading(true);
    setInfoErr(null);
    getModelInfo(modelName)
      .then((i) => {
        if (!cancelled) setInfo(i);
      })
      .catch((e) => {
        if (!cancelled) {
          setInfo(null);
          setInfoErr(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setInfoLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [modelName]);

  const fmt = info?.model_format ?? libEntry?.model_format ?? null;
  const size = info?.size_human ?? libEntry?.size_human ?? null;
  const fields = metaFields(info?.metadata ?? null);
  // Model-recommended sampling → shown as the input placeholder so a blank field
  // clearly means "use the model's recommended value", not an arbitrary default.
  const rec = info?.sampling ?? null;
  const recPlaceholder = (v: number | null | undefined) =>
    v != null ? `${v} (recommended)` : "model default";

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-6 py-8">
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          The active conversation's generation settings, the reply voice, and the loaded model. Changes
          apply immediately.
        </p>

        <div className="mt-8 space-y-10">
          {/* System prompt — most important, kept at the top. */}
          <Section
            title="System prompt"
            description="How the assistant should behave, sent ahead of every conversation."
          >
            <Textarea
              value={settings.systemPrompt ?? ""}
              onChange={(e) => onChange({ ...settings, systemPrompt: e.target.value })}
              placeholder={
                settings.systemPrompt === null && status?.default_system_prompt
                  ? "Using the project default (shown below)"
                  : "e.g. You are a helpful assistant."
              }
              className="min-h-28 resize-y bg-background/60 text-sm"
            />
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              {settings.systemPrompt === null
                ? "Empty = the project default below. Type to override; clearing sends no system prompt."
                : "Overrides the project default. Blank = no system prompt (e.g. roleplay / uncensored models)."}
            </p>
            {status?.default_system_prompt && (
              <div className="mt-2 rounded-md border border-border bg-background/40 p-2.5">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="text-[11px] font-medium text-muted-foreground">
                    Project default (heim.toml){settings.systemPrompt === null ? " · in use" : ""}
                  </span>
                  {settings.systemPrompt !== null && (
                    <button
                      type="button"
                      onClick={() => onChange({ ...settings, systemPrompt: null })}
                      className="text-[11px] font-medium text-primary hover:underline"
                    >
                      Use
                    </button>
                  )}
                </div>
                <p className="line-clamp-3 text-[11px] text-muted-foreground" title={status.default_system_prompt}>
                  {status.default_system_prompt}
                </p>
              </div>
            )}
          </Section>

          {/* Sampling */}
          <Section
            title="Sampling"
            description="Generation parameters for this conversation. Blank fields use the model's recommended values."
          >
            <div className="grid gap-4 sm:grid-cols-3">
              <div className="flex flex-col gap-1">
                <label htmlFor="max-tokens" className="text-sm font-medium">
                  Max response tokens
                </label>
                <Input
                  id="max-tokens"
                  type="number"
                  min={1}
                  value={maxTokens}
                  onChange={(e) => {
                    setMaxTokens(e.target.value);
                    onChange({ ...settings, maxTokens: parseNum(e.target.value, { int: true, min: 1 }) });
                  }}
                  placeholder="unlimited"
                  className="h-8 bg-background/60"
                />
                <p className="text-[11px] text-muted-foreground">How long the reply can be. Blank = unlimited.</p>
              </div>

              <div className="flex flex-col gap-1">
                <label htmlFor="temperature" className="text-sm font-medium">
                  Temperature
                </label>
                <Input
                  id="temperature"
                  type="number"
                  step={0.1}
                  min={0}
                  value={temperature}
                  onChange={(e) => {
                    setTemperature(e.target.value);
                    onChange({ ...settings, temperature: parseNum(e.target.value, { min: 0 }) });
                  }}
                  placeholder={recPlaceholder(rec?.temperature)}
                  className="h-8 bg-background/60"
                />
                <p className="text-[11px] text-muted-foreground">
                  Blank = {rec?.temperature != null ? "the model's recommended value." : "model default."}
                </p>
              </div>

              <div className="flex flex-col gap-1">
                <label htmlFor="top-p" className="text-sm font-medium">
                  Top P
                </label>
                <Input
                  id="top-p"
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={topP}
                  onChange={(e) => {
                    setTopP(e.target.value);
                    onChange({ ...settings, topP: parseNum(e.target.value, { min: 0 }) });
                  }}
                  placeholder={recPlaceholder(rec?.top_p)}
                  className="h-8 bg-background/60"
                />
                <p className="text-[11px] text-muted-foreground">
                  Blank = {rec?.top_p != null ? "the model's recommended value." : "model default."}
                </p>
              </div>

              <div className="flex flex-col gap-1">
                <label htmlFor="reasoning" className="text-sm font-medium">
                  Reasoning
                </label>
                <select
                  id="reasoning"
                  value={settings.reasoning ?? ""}
                  onChange={(e) =>
                    onChange({ ...settings, reasoning: (e.target.value || null) as ReasoningLevel | null })
                  }
                  className="h-8 rounded-md border border-border bg-background/60 px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <option value="">Default · model decides</option>
                  <option value="off">Off · no thinking</option>
                  <option value="low">Low · up to 512 tokens</option>
                  <option value="medium">Medium · up to 2,048 tokens</option>
                  <option value="high">High · up to 8,192 tokens</option>
                  <option value="max">Max · unlimited</option>
                </select>
                <p className="text-[11px] text-muted-foreground">
                  How long a thinking model may reason before answering. Needs a reasoning model
                  served by llama.cpp; others ignore it.
                </p>
              </div>
            </div>
          </Section>

          {/* Context length — load-time; changing it reloads the model. */}
          <Section
            title="Context length"
            description="How many tokens the model can hold. Set at load time (GGUF); Apply reloads the model."
          >
            {(() => {
              const isMlx = (libEntry?.model_format ?? "").toLowerCase() === "mlx";
              const parsed = parseNum(context, { int: true, min: 1 });
              const max = libEntry?.context_length ?? null;
              const presets = contextPresets(max);
              const overMax = parsed != null && max != null && parsed > max;
              const dirty = (parsed ?? null) !== (status?.n_ctx ?? null);
              // Custom mode when explicitly chosen, or when the current value isn't a preset.
              const custom = customCtx || (context !== "" && !presets.some((p) => String(p) === context));
              const apply = () => {
                onChange({ ...settings, contextLength: parsed });
                onReloadContext(parsed);
              };
              return (
                <div className="max-w-md">
                  <div className="flex items-center gap-2">
                    <select
                      value={custom ? "custom" : context}
                      disabled={isMlx || !modelName}
                      onChange={(e) => {
                        if (e.target.value === "custom") {
                          setCustomCtx(true);
                        } else {
                          setCustomCtx(false);
                          setContext(e.target.value);
                        }
                      }}
                      className="h-8 flex-1 rounded-md border border-border bg-background/60 px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <option value="">Default{max ? ` — model's full ${fmtTokens(max)}` : ""}</option>
                      {presets.map((v) => (
                        <option key={v} value={String(v)}>
                          {fmtTokens(v)}
                          {v === max ? " (max)" : ""} · {v.toLocaleString()} tokens
                        </option>
                      ))}
                      <option value="custom">Custom…</option>
                    </select>
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={isMlx || !modelName || busy || overMax || !dirty}
                      onClick={apply}
                      className="h-8 shrink-0"
                    >
                      {busy ? "Loading…" : "Apply"}
                    </Button>
                  </div>
                  {custom && (
                    <Input
                      type="number"
                      min={1}
                      max={max ?? undefined}
                      value={context}
                      autoFocus
                      disabled={isMlx || !modelName}
                      onChange={(e) => setContext(e.target.value)}
                      placeholder={max ? `tokens (max ${max.toLocaleString()})` : "tokens"}
                      className="mt-2 h-8 bg-background/60"
                    />
                  )}
                  <p className="mt-1.5 text-[11px] text-muted-foreground">
                    {isMlx
                      ? "MLX derives context from the model; not adjustable here."
                      : overMax
                        ? `Exceeds the model's trained ${max?.toLocaleString()} tokens.`
                        : `Default loads the model's full trained context${max ? ` (${max.toLocaleString()} tokens)` : ""} — pick a smaller value to use less memory.`}
                  </p>
                  {status?.n_ctx != null && !isMlx && (
                    <p className="mt-0.5 text-[11px] text-muted-foreground">
                      Loaded with {status.n_ctx.toLocaleString()} tokens.
                    </p>
                  )}
                </div>
              );
            })()}
          </Section>

          {/* Voice — which Kokoro voice the Listen button uses (output speech),
              grouped by language. */}
          <Section
            title="Voice (text-to-speech)"
            description="Used by the Listen button on replies. 54 built-in Kokoro voices across 9 languages."
          >
            <select
              value={ttsVoice}
              onChange={(e) => onChooseVoice(e.target.value)}
              className="h-8 w-full max-w-md rounded-md border border-border bg-background/60 px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="">Default</option>
              {Object.entries(
                voices
                  .filter((v) => v.engine === "kokoro")
                  .reduce<Record<string, Voice[]>>((acc, v) => {
                    (acc[v.language] ??= []).push(v);
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

          {/* Model + card — reference info, kept at the bottom. */}
          <Section title="Model" description="The model this server currently has loaded.">
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
                  <div className="max-h-96 overflow-y-auto rounded-md border border-border bg-background/60 px-4 py-3">
                    {infoLoading ? (
                      <p className="text-xs text-muted-foreground">Loading…</p>
                    ) : infoErr ? (
                      <p className="text-xs text-muted-foreground">No model card available.</p>
                    ) : info?.card ? (
                      <Markdown content={info.card} />
                    ) : (
                      <p className="text-xs text-muted-foreground">No model card.</p>
                    )}
                  </div>
                </div>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">No model loaded.</p>
            )}
          </Section>
        </div>
      </div>
    </div>
  );
}

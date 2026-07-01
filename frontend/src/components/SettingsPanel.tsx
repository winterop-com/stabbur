import { useEffect, useState } from "react";
import { PanelRightClose } from "lucide-react";

import { getModelInfo, type LibModel, type ModelInfo, type Status, type TtsModel } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Markdown } from "@/components/Markdown";
import type { Settings } from "@/lib/store";

/** A labeled section with a subtle top divider (except the first). */
function Section({
  title,
  first,
  children,
}: {
  title: string;
  first?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className={first ? "px-4 py-4" : "border-t border-border px-4 py-4"}>
      <h3 className="mb-2.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </h3>
      {children}
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
 * LM Studio-style right rail. Live-applies every change (no Save button): each
 * control writes back through onChange immediately (App persists to localStorage).
 * The model card is fetched from /api/model when the panel is open and the model
 * changes.
 */
export function SettingsPanel({
  status,
  library,
  settings,
  onChange,
  onCollapse,
  onReloadContext,
  busy,
  ttsModels,
  ttsVoice,
  onChooseVoice,
}: {
  status: Status | null;
  library: LibModel[];
  settings: Settings;
  onChange: (s: Settings) => void;
  onCollapse: () => void;
  onReloadContext: (nCtx: number | null) => void;
  busy: boolean;
  ttsModels: TtsModel[];
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

  // Fetch the card whenever the panel model changes (panel is only mounted when
  // effectively open; App unmounts/keeps width, but we guard on modelName).
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
    <aside className="flex h-full w-full min-w-0 flex-col border-l border-border bg-muted/40 text-foreground">
      <div className="flex items-center justify-between px-4 py-3">
        <span className="text-sm font-semibold tracking-tight">Settings</span>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon-sm" onClick={onCollapse} aria-label="Close settings">
              <PanelRightClose className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Close settings</TooltipContent>
        </Tooltip>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* System prompt — most important, kept at the top. */}
        <Section title="System prompt" first>
          <Textarea
            value={settings.systemPrompt}
            onChange={(e) => onChange({ ...settings, systemPrompt: e.target.value })}
            placeholder="e.g. You are a helpful assistant."
            className="min-h-24 resize-y bg-background/60 text-sm"
          />
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            Overrides the project default. Blank = no system prompt (e.g. roleplay / uncensored
            models that break character or refuse under assistant framing).
          </p>
          {status?.default_system_prompt && status.default_system_prompt !== settings.systemPrompt && (
            <div className="mt-2 rounded-md border border-border bg-background/40 p-2">
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-[11px] font-medium text-muted-foreground">Project default (kodo.toml)</span>
                <button
                  type="button"
                  onClick={() => onChange({ ...settings, systemPrompt: status.default_system_prompt })}
                  className="text-[11px] font-medium text-primary hover:underline"
                >
                  Use
                </button>
              </div>
              <p className="line-clamp-3 text-[11px] text-muted-foreground" title={status.default_system_prompt}>
                {status.default_system_prompt}
              </p>
            </div>
          )}
        </Section>

        {/* Sampling */}
        <Section title="Sampling">
          <div className="flex flex-col gap-3">
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
          </div>
        </Section>

        {/* Context length — load-time; changing it reloads the model. */}
        <Section title="Context length">
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
              <>
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
                    <option value="">Default{max ? ` (up to ${fmtTokens(max)})` : ""}</option>
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
                      : "Set at load time (GGUF). Apply reloads the model. Blank = runtime default."}
                </p>
                {status?.n_ctx != null && !isMlx && (
                  <p className="mt-0.5 text-[11px] text-muted-foreground">
                    Loaded with {status.n_ctx.toLocaleString()} tokens.
                  </p>
                )}
              </>
            );
          })()}
        </Section>

        {/* Voice — which TTS model the Listen button uses (output speech). */}
        <Section title="Voice (text-to-speech)">
          <select
            value={ttsVoice}
            onChange={(e) => onChooseVoice(e.target.value)}
            className="h-8 w-full rounded-md border border-border bg-background/60 px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="">Default (OuteTTS)</option>
            {ttsModels.map((m) => (
              <option key={m.name} value={m.name}>
                {m.name.split("/").pop()}
                {m.languages.length > 1 ? ` · ${m.languages.length} langs` : ""}
              </option>
            ))}
          </select>
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            Used by the Listen button on replies. Pull more with{" "}
            <code className="text-[10px]">kodo pull … --vocoder …</code>.
          </p>
        </Section>

        {/* Model + card — reference info, kept at the bottom. */}
        <Section title="Model">
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

              <div className="mt-3">
                <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Model card
                </div>
                <div className="max-h-64 overflow-y-auto rounded-md border border-border bg-background/60 px-3 py-2">
                  {infoLoading ? (
                    <p className="text-xs text-muted-foreground">Loading…</p>
                  ) : infoErr ? (
                    <p className="text-xs text-destructive">{infoErr}</p>
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
    </aside>
  );
}

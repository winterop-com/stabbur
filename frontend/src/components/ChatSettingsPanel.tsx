import { useEffect, useMemo, useState } from "react";
import { PanelRightClose, RotateCcw, RotateCw, SlidersHorizontal, TriangleAlert, Wrench } from "lucide-react";

import {
  getModelInfo,
  type LibModel,
  type McpServerInfo,
  type McpSetting,
  type ModelInfo,
  type ModelSampling,
  type Status,
  type ToolInfo,
  type Voice,
} from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { ReasoningLevel, Settings } from "@/lib/store";
import type { McpServersState } from "@/lib/useMcpServers";
import { cn } from "@/lib/utils";

const SPEEDS = [0.8, 0.9, 1, 1.1, 1.25, 1.5];

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

/** A settings section: heading, optional description, and its controls. */
function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-border px-4 py-4 first:border-t-0">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
      {description && <p className="mt-0.5 text-[11px] text-muted-foreground">{description}</p>}
      <div className="mt-2.5">{children}</div>
    </section>
  );
}

/**
 * One field's label row: the name, plus a revert affordance when this chat overrides
 * the inherited default. ``inherited`` describes what a cleared field falls back to.
 */
function FieldLabel({
  label,
  htmlFor,
  overridden,
  inherited,
  onReset,
}: {
  label: string;
  htmlFor?: string;
  overridden: boolean;
  inherited?: string;
  onReset: () => void;
}) {
  return (
    <div className="mb-1 flex items-baseline justify-between gap-2">
      <label htmlFor={htmlFor} className="text-sm font-medium">
        {label}
      </label>
      {overridden ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={onReset}
              className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
            >
              <RotateCcw className="h-3 w-3" />
              Reset
            </button>
          </TooltipTrigger>
          <TooltipContent>{inherited ? `Back to ${inherited}` : "Back to the default"}</TooltipContent>
        </Tooltip>
      ) : (
        inherited && <span className="truncate text-[11px] text-muted-foreground">{inherited}</span>
      )}
    </div>
  );
}

/**
 * One sampling parameter as a slider, with a plain-language line saying what moving it does.
 *
 * ``value === null`` means this chat has chosen nothing, and the slider then sits on ``fallback`` —
 * the value the server will actually use (the model's own recommendation where it ships one, else
 * heim's default). The panel never invents a number: when the server can't tell us the default
 * (a backend older than ``status.default_sampling``), the readout says "default" and the knob just
 * parks mid-range, so nothing on screen claims to be a value that is being sent.
 */
function SamplingSlider({
  id,
  label,
  description,
  value,
  fallback,
  min,
  max,
  step,
  onChange,
  onReset,
}: {
  id: string;
  label: string;
  description: string;
  value: number | null;
  fallback: number | null;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  onReset: () => void;
}) {
  const effective = value ?? fallback;
  const fmt = (n: number) => (step >= 1 ? String(Math.round(n)) : String(Number(n.toFixed(2))));
  // A model may recommend a value outside the range we'd otherwise offer (a 1.5 temperature, a
  // top-k of 200): widen the track rather than silently clamping the knob to a lie.
  const hi = Math.max(max, effective ?? max);
  return (
    <div>
      <FieldLabel
        label={label}
        htmlFor={id}
        overridden={value != null}
        inherited={value != null ? (fallback != null ? `default ${fmt(fallback)}` : "the default") : "default"}
        onReset={onReset}
      />
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="range"
          min={min}
          max={hi}
          step={step}
          value={effective ?? (min + hi) / 2}
          onChange={(e) => onChange(Number(e.target.value))}
          className="h-1.5 min-w-0 flex-1 cursor-pointer appearance-none rounded-full bg-border accent-primary"
        />
        <span className="w-9 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
          {effective != null ? fmt(effective) : "—"}
        </span>
      </div>
      <p className="mt-1 text-[11px] leading-snug text-muted-foreground">{description}</p>
    </div>
  );
}

/**
 * What one MCP server is configured to do — the env it declares, showing the value actually in
 * force. A server's env is the whole of what it may reach ("Browse, read and search files under a
 * configured workspace root" left *which* root unknowable, so an assistant asked about `~/dev`
 * answered about wherever `heim serve` was launched). The effective value is therefore never
 * hidden — it fills the field, or greys it as the placeholder when nothing is configured — while
 * editing is gated to the case that can actually work: a server that is on and owned by the
 * machine-global mcp.json, which is the only file heim writes from a web request.
 *
 * Saves on Enter or blur, and only when the text really changed, so tabbing through a card is not
 * a write. Booleans save on the spot.
 */
function McpSettings({
  server,
  busy,
  onSave,
}: {
  server: McpServerInfo;
  busy: boolean;
  onSave: (name: string, env: Record<string, string>) => void;
}) {
  // Only fields the user has touched; a saved field drops out and falls back to the refreshed row.
  const [draft, setDraft] = useState<Record<string, string>>({});
  const configured = (s: McpSetting) => server.env[s.env] ?? "";
  const locked = !server.enabled || server.scope === "project";

  const commit = (s: McpSetting, value: string) => {
    setDraft((d) => {
      const next = { ...d };
      delete next[s.env];
      return next;
    });
    if (value !== configured(s)) onSave(server.name, { [s.env]: value });
  };

  return (
    <div className="flex flex-col gap-2 border-t border-border px-2.5 py-2">
      {server.settings.map((s) => (
        <div key={s.env} className="min-w-0">
          {s.type === "boolean" ? (
            <div className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-[11px] font-medium" title={`${s.env} — ${s.description}`}>
                {s.label}
              </span>
              <Switch
                checked={s.effective === "true"}
                disabled={busy || locked}
                onCheckedChange={(on) => onSave(server.name, { [s.env]: on ? "true" : "false" })}
                aria-label={s.label}
              />
            </div>
          ) : (
            <>
              <div className="mb-0.5 flex items-baseline justify-between gap-2">
                <span className="min-w-0 truncate text-[11px] font-medium" title={`${s.env} — ${s.description}`}>
                  {s.label}
                </span>
                {/* Clearing the field is the "back to the default" path, so it needs to be findable
                    without the user guessing that an empty string means "unset". */}
                {configured(s) && !locked && (
                  <button
                    type="button"
                    onClick={() => commit(s, "")}
                    className="inline-flex shrink-0 items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
                  >
                    <RotateCcw className="h-2.5 w-2.5" />
                    Reset
                  </button>
                )}
              </div>
              <Input
                value={draft[s.env] ?? configured(s)}
                disabled={busy || locked}
                spellCheck={false}
                // The effective value as the placeholder: unset is where the surprise lives, so the
                // resolved answer ("/Users/me/dev/heim") has to be on screen without a click.
                placeholder={s.effective || s.default}
                title={s.effective}
                onChange={(e) => setDraft((d) => ({ ...d, [s.env]: e.target.value }))}
                onBlur={(e) => commit(s, e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") e.currentTarget.blur();
                  if (e.key === "Escape")
                    setDraft((d) => {
                      const next = { ...d };
                      delete next[s.env];
                      return next;
                    });
                }}
                className="h-7 bg-background/60 font-mono text-[11px]"
              />
            </>
          )}
        </div>
      ))}
      {locked && (
        <p className="text-[11px] text-muted-foreground">
          {server.scope === "project"
            ? "Set by this project's .mcp.json — edit it there."
            : "Switch it on to change these."}
        </p>
      )}
    </div>
  );
}

/**
 * The per-chat settings rail (LM Studio shape): everything adjustable for *this*
 * conversation — system prompt, sampling, reasoning, context, voice, tools — with the
 * inherited default shown per field and one click to revert to it. Machine-wide
 * defaults live on the Settings page; this panel only holds per-conversation overrides.
 */
export function ChatSettingsPanel({
  status,
  library,
  activeId,
  settings,
  onChange,
  onCollapse,
  onReloadContext,
  busy,
  voices,
  defaultVoice,
  defaultSpeed,
  tools,
  disabled,
  allowedServers,
  mcp,
  onToggleUse,
  onToggleTool,
  onToggleServer,
  tab,
  onTabChange,
}: {
  status: Status | null;
  library: LibModel[];
  activeId: string | null;
  settings: Settings;
  onChange: (s: Settings) => void;
  onCollapse: () => void;
  onReloadContext: (nCtx: number | null) => void;
  busy: boolean;
  voices: Voice[];
  /** The voice a chat inherits when it sets none (the Settings-page default). */
  defaultVoice: string;
  defaultSpeed: number;
  tools: ToolInfo[];
  disabled: Set<string>;
  /** Servers this conversation may call — its own allow-list, or the resolved baseline. */
  allowedServers: Set<string>;
  /** The machine-wide server catalogue, owned by the app (the send path needs it too). */
  mcp: McpServersState;
  onToggleUse: (on: boolean) => void;
  onToggleTool: (name: string, enabled: boolean) => void;
  /** Add/remove one server from *this chat's* allow-list (never starts or stops it). */
  onToggleServer: (name: string, enabled: boolean) => void;
  /** Which tab is showing. Owned by the app so a "manage tools" affordance elsewhere can
   *  open this panel *on* the Tools tab instead of dropping the user on Parameters. */
  tab: "parameters" | "tools";
  onTabChange: (tab: "parameters" | "tools") => void;
}) {
  const modelName = status?.model ?? null;
  const libEntry = library.find((m) => m.name === modelName) ?? null;
  const visionModel = !!libEntry?.vision;
  const [info, setInfo] = useState<ModelInfo | null>(null);

  // Context length is only ours to set when heim loads the model itself. An upstream (`serve
  // --upstream`) serves a window it already chose, and MLX derives one from the checkpoint —
  // in both cases every control here is inert, so the section shows the reason and nothing to
  // click, rather than a picker and an Apply button that quietly do nothing.
  const modelFormat = (libEntry?.model_format ?? "").toLowerCase();
  const isMlx = modelFormat === "mlx";
  const isRemote = modelFormat === "remote";
  const contextInert = isMlx || isRemote;

  // Local text state for the numeric inputs so partial edits (e.g. "0.") aren't clobbered.
  const [maxTokens, setMaxTokens] = useState(settings.maxTokens != null ? String(settings.maxTokens) : "");
  const [context, setContext] = useState(settings.contextLength != null ? String(settings.contextLength) : "");
  const [customCtx, setCustomCtx] = useState(false);

  // Re-seed the local input text when the conversation changes (the panel stays mounted).
  // Keyed on activeId only, so a keystroke doesn't clobber an in-progress partial edit.
  useEffect(() => {
    setMaxTokens(settings.maxTokens != null ? String(settings.maxTokens) : "");
    setContext(settings.contextLength != null ? String(settings.contextLength) : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  // The model's recommended sampling — shown as each field's inherited default.
  useEffect(() => {
    if (!modelName) {
      setInfo(null);
      return;
    }
    let cancelled = false;
    getModelInfo(modelName)
      .then((i) => !cancelled && setInfo(i))
      .catch(() => !cancelled && setInfo(null));
    return () => {
      cancelled = true;
    };
  }, [modelName]);

  // The effective default per field: the model's own recommendation when it ships one, else heim's
  // own defaults *as the server reports them* (/api/status). Both numbers come from the server, so
  // a control shows the value that will really be sent rather than a copy here that can drift.
  const rec: ModelSampling | null = info?.sampling ?? status?.default_sampling ?? null;
  const defMaxTokens = status?.default_max_tokens ? String(status.default_max_tokens) : "unlimited";

  // Two filters, in the order the wire applies them: a tool counts only if its server is on this
  // chat's allow-list *and* the tool itself wasn't switched off inside that server.
  const enabledCount = tools.filter((t) => allowedServers.has(t.server) && !disabled.has(t.name)).length;

  // The MCP server catalogue (what heim *could* run), independent of what's attached. Owned by the
  // app, not this panel: a chat's baseline allow-list is derived from each server's scope, so the
  // send path needs the same list whether or not this panel was ever opened.
  const { servers, pending, notes, toggle: toggleMcpServer, saveEnv: saveMcpEnv } = mcp;

  /**
   * One row per server: the whole bundled catalogue, plus any server that is attached without
   * being one of ours (an external `.mcp.json` entry — listed so its tools stay reachable, but
   * with no on/off switch, since the toggle route is an allow-list over the bundled set).
   */
  const rows = useMemo(() => {
    const byServer: Record<string, ToolInfo[]> = {};
    for (const t of tools) (byServer[t.server] ??= []).push(t);
    const bundled = servers ?? [];
    const known = new Set(bundled.map((s) => s.name));
    const external = Object.keys(byServer)
      .filter((name) => !known.has(name))
      .sort((a, b) => a.localeCompare(b))
      .map((name) => ({ name, server: null as McpServerInfo | null, list: byServer[name] }));
    return [...bundled.map((s) => ({ name: s.name, server: s, list: byServer[s.name] ?? [] })), ...external];
  }, [servers, tools]);

  const voiceLabel = (id: string) => voices.find((v) => v.id === id)?.label ?? id;
  // Mirrors the server's fallback (heim.routers.serving.voice: kokoro:af_heart).
  const inheritedVoice = defaultVoice
    ? `${voiceLabel(defaultVoice)} (your default)`
    : status?.default_chat_voice
      ? `${voiceLabel(status.default_chat_voice)} (project)`
      : voiceLabel("kokoro:af_heart");

  return (
    <aside className="flex h-full w-full min-w-0 flex-col border-l border-border bg-muted/40 text-foreground">
      <div className="flex items-center justify-between px-4 py-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold tracking-tight">Chat settings</div>
          <div className="truncate text-[11px] text-muted-foreground">
            {activeId ? "This conversation" : "New chat"}
          </div>
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon-sm" onClick={onCollapse} aria-label="Close chat settings">
              <PanelRightClose className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Close</TooltipContent>
        </Tooltip>
      </div>

      {/* Two tabs, like LM Studio's: what the model does (parameters) vs what it can
          reach (MCP tools) — the tool list gets long, so it earns its own surface. */}
      <div className="flex items-center gap-1 px-3 pb-2">
        {(
          [
            ["parameters", "Parameters", <SlidersHorizontal key="p" className="h-3.5 w-3.5" />],
            ["tools", "Tools", <Wrench key="t" className="h-3.5 w-3.5" />],
          ] as const
        ).map(([id, label, icon]) => (
          <button
            key={id}
            type="button"
            onClick={() => onTabChange(id)}
            className={cn(
              "inline-flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium transition-colors",
              tab === id ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-accent hover:text-foreground",
            )}
          >
            {icon}
            {label}
            {id === "tools" && tools.length > 0 && (
              <span className="tabular-nums opacity-70">{settings.useTools ? enabledCount : 0}</span>
            )}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === "parameters" && (
          <>
        <Section title="System prompt">
          <FieldLabel
            label="Instructions"
            overridden={settings.systemPrompt !== null}
            inherited={status?.default_system_prompt ? "the project prompt" : "no system prompt"}
            onReset={() => onChange({ ...settings, systemPrompt: null })}
          />
          <Textarea
            value={settings.systemPrompt ?? ""}
            onChange={(e) => onChange({ ...settings, systemPrompt: e.target.value })}
            placeholder={
              settings.systemPrompt === null && status?.default_system_prompt
                ? "Using the project prompt (below)"
                : "e.g. You are a helpful assistant."
            }
            className="min-h-24 resize-y bg-background/60 text-sm"
          />
          {status?.default_system_prompt && (
            <div className="mt-2 rounded-md border border-border bg-background/40 p-2">
              <div className="mb-1 text-[11px] font-medium text-muted-foreground">
                Project default (heim.toml){settings.systemPrompt === null ? " · in use" : ""}
              </div>
              <p className="line-clamp-3 text-[11px] text-muted-foreground" title={status.default_system_prompt}>
                {status.default_system_prompt}
              </p>
            </div>
          )}
        </Section>

        <Section title="Sampling">
          <div className="flex flex-col gap-3.5">
            <div>
              <FieldLabel
                label="Max response tokens"
                htmlFor="p-max-tokens"
                overridden={settings.maxTokens != null}
                inherited={defMaxTokens}
                onReset={() => {
                  setMaxTokens("");
                  onChange({ ...settings, maxTokens: null });
                }}
              />
              <Input
                id="p-max-tokens"
                type="number"
                min={1}
                value={maxTokens}
                onChange={(e) => {
                  setMaxTokens(e.target.value);
                  onChange({ ...settings, maxTokens: parseNum(e.target.value, { int: true, min: 1 }) });
                }}
                placeholder={defMaxTokens}
                className="h-8 bg-background/60"
              />
            </div>

            <SamplingSlider
              id="p-temperature"
              label="Temperature"
              description="How adventurous the wording is. Low (0.2) stays focused and repeats itself; high (1.2+) is more varied and creative, and more likely to wander or invent."
              value={settings.temperature}
              fallback={rec?.temperature ?? null}
              min={0}
              max={2}
              step={0.05}
              onChange={(v) => onChange({ ...settings, temperature: v })}
              onReset={() => onChange({ ...settings, temperature: null })}
            />

            <SamplingSlider
              id="p-top-p"
              label="Top P"
              description="Considers only the likeliest words that together make up this share of the probability. 1.0 keeps every option; lower trims the unlikely tail."
              value={settings.topP}
              fallback={rec?.top_p ?? null}
              min={0}
              max={1}
              step={0.01}
              onChange={(v) => onChange({ ...settings, topP: v })}
              onReset={() => onChange({ ...settings, topP: null })}
            />

            {/* The three below are real knobs the runtime honours, but they are the ones you reach
                for after temperature hasn't fixed it — folded away so the tab stays readable in a
                narrow side panel, with the summary naming them so they're findable. */}
            <details className="rounded-md border border-border bg-background/40">
              <summary className="cursor-pointer select-none px-2.5 py-1.5 text-[11px] text-muted-foreground">
                More sampling · top-k, min-p, repeat penalty
              </summary>
              <div className="flex flex-col gap-3.5 border-t border-border px-2.5 py-2.5">
                <SamplingSlider
                  id="p-top-k"
                  label="Top K"
                  description="Never look past this many candidate words per step. Lower is safer and blander; 0 turns the cut-off off entirely."
                  value={settings.topK}
                  fallback={rec?.top_k ?? null}
                  min={0}
                  max={100}
                  step={1}
                  onChange={(v) => onChange({ ...settings, topK: v })}
                  onReset={() => onChange({ ...settings, topK: null })}
                />
                <SamplingSlider
                  id="p-min-p"
                  label="Min P"
                  description="Drops any word less likely than this fraction of the best one. A gentler Top P: higher cuts more, 0 cuts nothing."
                  value={settings.minP}
                  fallback={rec?.min_p ?? null}
                  min={0}
                  max={0.5}
                  step={0.01}
                  onChange={(v) => onChange({ ...settings, minP: v })}
                  onReset={() => onChange({ ...settings, minP: null })}
                />
                <SamplingSlider
                  id="p-repeat-penalty"
                  label="Repeat penalty"
                  description="Discourages words it has already used. 1.0 is off; a little above curbs models that fall into loops, too much makes them dodge words they need."
                  value={settings.repeatPenalty}
                  fallback={rec?.repeat_penalty ?? null}
                  min={1}
                  max={1.5}
                  step={0.01}
                  onChange={(v) => onChange({ ...settings, repeatPenalty: v })}
                  onReset={() => onChange({ ...settings, repeatPenalty: null })}
                />
              </div>
            </details>

            <div>
              <FieldLabel
                label="Reasoning"
                htmlFor="p-reasoning"
                overridden={settings.reasoning != null}
                onReset={() => onChange({ ...settings, reasoning: null })}
              />
              <select
                id="p-reasoning"
                value={settings.reasoning ?? ""}
                onChange={(e) => onChange({ ...settings, reasoning: (e.target.value || null) as ReasoningLevel | null })}
                className="h-8 w-full rounded-md border border-border bg-background/60 px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value="">Default · model decides</option>
                <option value="off">Off · no thinking</option>
                <option value="low">Low · up to 512 tokens</option>
                <option value="medium">Medium · up to 2,048 tokens</option>
                <option value="high">High · up to 8,192 tokens</option>
                <option value="max">Max · unlimited</option>
              </select>
              <p className="mt-1 text-[11px] text-muted-foreground">
                How long a thinking model may reason before answering.
              </p>
            </div>
          </div>
        </Section>

        <Section
          title="Context length"
          description={contextInert ? undefined : "Set when the model loads; applying reloads it."}
        >
          {(() => {
            const locked = !modelName;
            const parsed = parseNum(context, { int: true, min: 1 });
            const max = libEntry?.context_length ?? null;
            const presets = contextPresets(max);
            const overMax = parsed != null && max != null && parsed > max;
            const dirty = (parsed ?? null) !== (status?.n_ctx ?? null);
            const custom = customCtx || (context !== "" && !presets.some((p) => String(p) === context));
            return (
              <>
                {!contextInert && (
                  <>
                    <div className="flex items-center gap-2">
                      <select
                        value={custom ? "custom" : context}
                        disabled={locked}
                        onChange={(e) => {
                          if (e.target.value === "custom") setCustomCtx(true);
                          else {
                            setCustomCtx(false);
                            setContext(e.target.value);
                          }
                        }}
                        className="h-8 min-w-0 flex-1 rounded-md border border-border bg-background/60 px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <option value="">Default{max ? ` — full ${fmtTokens(max)}` : ""}</option>
                        {presets.map((v) => (
                          <option key={v} value={String(v)}>
                            {fmtTokens(v)}
                            {v === max ? " (max)" : ""}
                          </option>
                        ))}
                        <option value="custom">Custom…</option>
                      </select>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={locked || busy || overMax || !dirty}
                        onClick={() => {
                          onChange({ ...settings, contextLength: parsed });
                          onReloadContext(parsed);
                        }}
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
                        disabled={locked}
                        onChange={(e) => setContext(e.target.value)}
                        placeholder={max ? `tokens (max ${max.toLocaleString()})` : "tokens"}
                        className="mt-2 h-8 bg-background/60"
                      />
                    )}
                  </>
                )}
                <p className={cn("text-[11px] text-muted-foreground", !contextInert && "mt-1.5")}>
                  {isRemote
                    ? "The upstream server decides this model's context."
                    : isMlx
                      ? "MLX derives context from the model; not adjustable here."
                      : overMax
                        ? `Exceeds the model's trained ${max?.toLocaleString()} tokens.`
                        : status?.n_ctx != null
                          ? `Loaded with ${status.n_ctx.toLocaleString()} tokens.`
                          : "Default loads the model's full trained context."}
                </p>
              </>
            );
          })()}
        </Section>

        <Section title="Voice" description="Used by Listen on replies in this chat.">
          <FieldLabel
            label="Voice"
            htmlFor="p-voice"
            overridden={settings.ttsVoice != null}
            inherited={inheritedVoice}
            onReset={() => onChange({ ...settings, ttsVoice: null })}
          />
          <select
            id="p-voice"
            value={settings.ttsVoice ?? ""}
            onChange={(e) => onChange({ ...settings, ttsVoice: e.target.value || null })}
            className="h-8 w-full rounded-md border border-border bg-background/60 px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="">Default · {inheritedVoice}</option>
            {Object.entries(
              voices.reduce<Record<string, Voice[]>>((acc, v) => {
                (acc[v.language || "Other"] ??= []).push(v);
                return acc;
              }, {}),
            ).map(([language, vs]) => (
              <optgroup key={language} label={language}>
                {vs.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.label}
                    {v.gender ? ` · ${v.gender === "female" ? "F" : "M"}` : ""}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>

          <div className="mt-3">
            <FieldLabel
              label="Speed"
              overridden={settings.ttsSpeed != null}
              inherited={`${defaultSpeed}x`}
              onReset={() => onChange({ ...settings, ttsSpeed: null })}
            />
            <div className="flex flex-wrap items-center gap-1">
              {SPEEDS.map((v) => {
                const active = (settings.ttsSpeed ?? defaultSpeed) === v;
                return (
                  <button
                    key={v}
                    type="button"
                    onClick={() => onChange({ ...settings, ttsSpeed: v })}
                    className={cn(
                      "rounded-md px-2 py-1 text-[11px] tabular-nums transition-colors",
                      active
                        ? "bg-primary/15 font-medium text-primary"
                        : "text-muted-foreground hover:bg-accent hover:text-foreground",
                    )}
                  >
                    {v}x
                  </button>
                );
              })}
            </div>
          </div>
        </Section>

        <Section title="Attachments" description="How files you attach reach the model.">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-medium">Parse PDF as image</div>
              <div className="text-[11px] text-muted-foreground">
                Render pages instead of extracting text — keeps tables, charts, and layout.
                {!visionModel && " Falls back to text: this model can't see images."}
              </div>
            </div>
            <Switch
              checked={settings.pdfAsImage}
              onCheckedChange={(on) => onChange({ ...settings, pdfAsImage: on })}
              aria-label="Parse PDF as image"
            />
          </div>
        </Section>

          </>
        )}

        {tab === "tools" && (
          <>
            <Section
              title="Tools"
              description="MCP tools this chat may call. A new chat starts with the project's own servers; anything else you switch on here applies to this conversation only."
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium">Enable tools</div>
                  <div className="truncate text-[11px] text-muted-foreground">
                    {tools.length === 0
                      ? "Nothing attached — switch a server on below"
                      : settings.useTools
                        ? `${enabledCount} of ${tools.length} active`
                        : "Off for this chat"}
                  </div>
                </div>
                <Switch
                  checked={settings.useTools}
                  disabled={tools.length === 0}
                  onCheckedChange={onToggleUse}
                  aria-label="Enable tools"
                />
              </div>
            </Section>

            {/* The catalogue: every server heim ships, most of them off. Two switches with two
                different scopes live here, so they are deliberately kept apart — the row switch
                starts/stops the *server* for every chat on this machine (it writes mcp.json),
                while the switch on "Tools in this chat" says whether *this* conversation may call
                it. That second one is an allow-list, and it starts from the baseline rather than
                "everything running": switching a server on for one question must not leave it
                live in every chat you open afterwards. */}
            <Section
              title="MCP servers"
              description="What heim can run. Switching one on starts it for every chat on this machine."
            >
              {servers === null ? (
                <p className="text-[11px] text-muted-foreground">Loading…</p>
              ) : rows.length === 0 ? (
                <p className="text-[11px] text-muted-foreground">This server reports no MCP servers.</p>
              ) : (
                <div className="flex flex-col gap-1">
                  {rows.map(({ name, server, list }) => {
                    // This chat's two layers: the server has to be allowed at all, and then each of
                    // its tools can still be switched off inside it.
                    const allowed = allowedServers.has(name);
                    const on = allowed ? list.filter((t) => !disabled.has(t.name)).length : 0;
                    const note = notes[name];
                    // No switch for a server heim can't start (an uninstalled extra) or doesn't own
                    // (an external .mcp.json entry) — a control that cannot work is worse than none.
                    const canToggle = !!server && server.installed;
                    return (
                      <div key={name} className="rounded-md border border-border bg-background/40">
                        <div className="flex items-start justify-between gap-2 px-2.5 py-2">
                          <div className="min-w-0">
                            <div className="flex items-center gap-1.5">
                              <span className="truncate text-sm font-medium">{name}</span>
                              {list.length > 0 && (
                                <span className="shrink-0 rounded bg-muted px-1 py-px text-[10px] tabular-nums text-muted-foreground">
                                  {list.length} tools
                                </span>
                              )}
                              {server?.scope === "project" && (
                                <span className="shrink-0 rounded bg-muted px-1 py-px text-[10px] text-muted-foreground">
                                  .mcp.json
                                </span>
                              )}
                            </div>
                            {(server?.description || !server) && (
                              <p className="mt-0.5 text-[11px] text-muted-foreground">
                                {server?.description || "Configured by this project's .mcp.json."}
                              </p>
                            )}
                            {/* Why this row has no switch, or why its switch didn't do what it looks
                                like it did. Only one of these ever shows at a time. */}
                            {server && !server.installed ? (
                              <p className="mt-1 text-[11px] text-muted-foreground">
                                Not installed{server.setup ? ` — ${server.setup}` : "."}
                              </p>
                            ) : note ? (
                              <p
                                className={cn(
                                  "mt-1 flex items-start gap-1 text-[11px]",
                                  note.tone === "warn" ? "text-amber-700 dark:text-amber-400" : "text-destructive",
                                )}
                              >
                                {note.tone === "warn" ? (
                                  <RotateCw className="mt-px h-3 w-3 shrink-0" />
                                ) : (
                                  <TriangleAlert className="mt-px h-3 w-3 shrink-0" />
                                )}
                                <span>{note.text}</span>
                              </p>
                            ) : server?.enabled === false && list.length > 0 ? (
                              <p className="mt-1 flex items-start gap-1 text-[11px] text-amber-700 dark:text-amber-400">
                                <RotateCw className="mt-px h-3 w-3 shrink-0" />
                                <span>Off, but still running — its tools detach when heim serve restarts.</span>
                              </p>
                            ) : (
                              server?.enabled === true &&
                              list.length === 0 && (
                                <p className="mt-1 text-[11px] text-muted-foreground">On, but it attached no tools.</p>
                              )
                            )}
                          </div>
                          {canToggle && (
                            <Switch
                              checked={server.enabled}
                              disabled={pending === name}
                              onCheckedChange={(enabled) => toggleMcpServer(name, enabled)}
                              aria-label={`Run the ${name} MCP server`}
                            />
                          )}
                        </div>

                        {/* Above the per-chat tool list, because this is what the server *is*
                            (which directory, which hosts) rather than which of its tools this
                            conversation may call. */}
                        {server && server.settings.length > 0 && (
                          <McpSettings server={server} busy={pending === name} onSave={saveMcpEnv} />
                        )}

                        {settings.useTools && list.length > 0 && (
                          <details className="border-t border-border">
                            <summary className="flex cursor-pointer select-none items-center justify-between gap-2 px-2.5 py-1.5 text-[11px] text-muted-foreground">
                              <span className="min-w-0 flex-1 truncate">Tools in this chat</span>
                              <span className="shrink-0 tabular-nums">
                                {on}/{list.length}
                              </span>
                              {/* The wrapper swallows the click so hitting the switch doesn't
                                  also open/close the <details> it sits in. */}
                              <span className="shrink-0" onClick={(e) => e.preventDefault()}>
                                <Switch
                                  checked={allowed}
                                  onCheckedChange={(enabled) => onToggleServer(name, enabled)}
                                  aria-label={`Use ${name} tools in this chat`}
                                />
                              </span>
                            </summary>
                            <div className="flex flex-col gap-1 border-t border-border px-2.5 py-1.5">
                              {/* Off at the server level means these can't fire, so they read (and
                                  behave) as inert rather than showing an on switch that does nothing. */}
                              {list.map((t) => (
                                <div key={t.name} className="flex items-center justify-between gap-2">
                                  <span
                                    className={cn(
                                      "min-w-0 flex-1 truncate text-[11px]",
                                      !allowed && "text-muted-foreground",
                                    )}
                                    title={t.description || t.tool}
                                  >
                                    {t.tool}
                                  </span>
                                  <Switch
                                    checked={allowed && !disabled.has(t.name)}
                                    disabled={!allowed}
                                    onCheckedChange={(enabled) => onToggleTool(t.name, enabled)}
                                    aria-label={t.tool}
                                  />
                                </div>
                              ))}
                            </div>
                          </details>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </Section>
          </>
        )}
      </div>
    </aside>
  );
}

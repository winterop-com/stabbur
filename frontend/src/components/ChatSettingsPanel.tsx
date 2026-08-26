import { useCallback, useEffect, useMemo, useState } from "react";
import { PanelRightClose, RotateCcw, RotateCw, SlidersHorizontal, TriangleAlert, Wrench } from "lucide-react";

import {
  getMcpServers,
  getModelInfo,
  setMcpServer,
  type LibModel,
  type McpServerInfo,
  type ModelInfo,
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
  onToggleUse,
  onToggleTool,
  onToggleServer,
  onToolsChanged,
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
  onToggleUse: (on: boolean) => void;
  onToggleTool: (name: string, enabled: boolean) => void;
  onToggleServer: (names: string[], enabled: boolean) => void;
  /** Re-read /api/tools: a server switched on attaches its tools live, so the list is stale. */
  onToolsChanged: () => void;
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
  const [temperature, setTemperature] = useState(settings.temperature != null ? String(settings.temperature) : "");
  const [topP, setTopP] = useState(settings.topP != null ? String(settings.topP) : "");
  const [context, setContext] = useState(settings.contextLength != null ? String(settings.contextLength) : "");
  const [customCtx, setCustomCtx] = useState(false);

  // Re-seed the local input text when the conversation changes (the panel stays mounted).
  // Keyed on activeId only, so a keystroke doesn't clobber an in-progress partial edit.
  useEffect(() => {
    setMaxTokens(settings.maxTokens != null ? String(settings.maxTokens) : "");
    setTemperature(settings.temperature != null ? String(settings.temperature) : "");
    setTopP(settings.topP != null ? String(settings.topP) : "");
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

  // The effective default per field: the model's own recommendation when it ships one, else
  // heim's documented default. The server resolves the same way, so these are the real values.
  const rec = info?.sampling ?? null;
  const num = (v: number | null | undefined, fallback: number) => String(v ?? fallback);
  const defTemperature = num(rec?.temperature, 0.8);
  const defTopP = num(rec?.top_p, 0.95);
  const defMaxTokens = status?.default_max_tokens ? String(status.default_max_tokens) : "unlimited";

  const enabledCount = tools.filter((t) => !disabled.has(t.name)).length;

  // --- the MCP server catalogue (what heim *could* run), independent of what's attached ---
  const [servers, setServers] = useState<McpServerInfo[] | null>(null); // null = still loading
  const [pending, setPending] = useState<string | null>(null);
  // The last toggle outcome per server, kept only for this panel session: the server tells us
  // whether the change actually took effect, and that answer is the whole point of the control.
  const [notes, setNotes] = useState<Record<string, { tone: "warn" | "error"; text: string }>>({});

  useEffect(() => {
    let cancelled = false;
    getMcpServers()
      .then((s) => !cancelled && setServers(s))
      .catch(() => !cancelled && setServers([])); // an older backend has no route; show the attached tools only
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleMcpServer = useCallback(
    async (name: string, on: boolean) => {
      setPending(name);
      setNotes((n) => {
        const next = { ...n };
        delete next[name];
        return next;
      });
      try {
        const res = await setMcpServer(name, on);
        // The response carries the refreshed row, so trust it over the optimistic value —
        // an enable the project vetoes comes back still disabled.
        setServers((list) => (list ?? []).map((s) => (s.name === res.server.name ? res.server : s)));
        if (res.applied) onToolsChanged(); // its tools are attached (or gone) right now
        if (res.restart_required)
          setNotes((n) => ({
            ...n,
            [name]: { tone: "warn", text: res.detail || "restart heim serve for this to take effect" },
          }));
        else if (!res.applied)
          setNotes((n) => ({ ...n, [name]: { tone: "error", text: res.detail || "the change did not take effect" } }));
      } catch (e) {
        setNotes((n) => ({ ...n, [name]: { tone: "error", text: e instanceof Error ? e.message : String(e) } }));
      } finally {
        setPending(null);
      }
    },
    [onToolsChanged],
  );

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

            <div>
              <FieldLabel
                label="Temperature"
                htmlFor="p-temperature"
                overridden={settings.temperature != null}
                inherited={defTemperature}
                onReset={() => {
                  setTemperature("");
                  onChange({ ...settings, temperature: null });
                }}
              />
              <Input
                id="p-temperature"
                type="number"
                step={0.1}
                min={0}
                value={temperature}
                onChange={(e) => {
                  setTemperature(e.target.value);
                  onChange({ ...settings, temperature: parseNum(e.target.value, { min: 0 }) });
                }}
                placeholder={defTemperature}
                className="h-8 bg-background/60"
              />
            </div>

            <div>
              <FieldLabel
                label="Top P"
                htmlFor="p-top-p"
                overridden={settings.topP != null}
                inherited={defTopP}
                onReset={() => {
                  setTopP("");
                  onChange({ ...settings, topP: null });
                }}
              />
              <Input
                id="p-top-p"
                type="number"
                step={0.05}
                min={0}
                max={1}
                value={topP}
                onChange={(e) => {
                  setTopP(e.target.value);
                  onChange({ ...settings, topP: parseNum(e.target.value, { min: 0 }) });
                }}
                placeholder={defTopP}
                className="h-8 bg-background/60"
              />
            </div>

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
            <Section title="Tools" description="MCP tools this chat may call. New tools default on.">
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
                while the switches inside "Tools in this chat" are this conversation's denylist. */}
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
                    const names = list.map((t) => t.name);
                    const on = names.filter((n) => !disabled.has(n)).length;
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
                              onCheckedChange={(enabled) => void toggleMcpServer(name, enabled)}
                              aria-label={`Run the ${name} MCP server`}
                            />
                          )}
                        </div>

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
                                  checked={on > 0}
                                  onCheckedChange={(enabled) => onToggleServer(names, enabled)}
                                  aria-label={`Use ${name} tools in this chat`}
                                />
                              </span>
                            </summary>
                            <div className="flex flex-col gap-1 border-t border-border px-2.5 py-1.5">
                              {list.map((t) => (
                                <div key={t.name} className="flex items-center justify-between gap-2">
                                  <span className="min-w-0 flex-1 truncate text-[11px]" title={t.description || t.tool}>
                                    {t.tool}
                                  </span>
                                  <Switch
                                    checked={!disabled.has(t.name)}
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

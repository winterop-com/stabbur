import { useEffect, useState } from "react";
import { PanelRightClose } from "lucide-react";

import { getModelInfo, type LibModel, type ModelInfo, type Status } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
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
}: {
  status: Status | null;
  library: LibModel[];
  settings: Settings;
  onChange: (s: Settings) => void;
  onCollapse: () => void;
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

  return (
    <aside className="flex h-full w-[320px] shrink-0 flex-col border-l border-border bg-muted/40 text-foreground">
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
        {/* Model */}
        <Section title="Model" first>
          {modelName ? (
            <>
              <div className="truncate text-sm font-medium" title={modelName}>
                {modelName.split("/").pop() ?? modelName}
              </div>
              <div className="mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                {fmt && <span className="rounded bg-black/20 px-1.5 py-0.5">{fmt}</span>}
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

        {/* System prompt */}
        <Section title="System prompt">
          <Textarea
            value={settings.systemPrompt}
            onChange={(e) => onChange({ ...settings, systemPrompt: e.target.value })}
            placeholder="e.g. You are a helpful assistant."
            className="min-h-24 resize-y bg-background/60 text-sm"
          />
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            Applied to every conversation, prepended to each request.
          </p>
        </Section>

        {/* Tools */}
        <Section title="Tools">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <label htmlFor="use-tools" className="text-sm font-medium">
                Tools
              </label>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                Attach MCP tools (turn off for models that aren't tool-trained).
              </p>
            </div>
            <Switch
              id="use-tools"
              checked={settings.useTools}
              onCheckedChange={(v) => onChange({ ...settings, useTools: v })}
              aria-label="Attach MCP tools"
            />
          </div>
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
                placeholder="model default"
                className="h-8 bg-background/60"
              />
              <p className="text-[11px] text-muted-foreground">Blank = model default.</p>
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
                placeholder="model default"
                className="h-8 bg-background/60"
              />
              <p className="text-[11px] text-muted-foreground">Blank = model default.</p>
            </div>
          </div>
        </Section>

        {/* Context length — not yet supported at load time. */}
        <Section title="Context length">
          <div className="flex items-center justify-between gap-3 opacity-60">
            <Input disabled placeholder="coming soon" className="h-8 bg-background/60" />
          </div>
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            Needs load-time support (not built yet).
          </p>
        </Section>
      </div>
    </aside>
  );
}

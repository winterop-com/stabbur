import { useEffect, useState } from "react";

import { getModelInfo, type LibModel, type ModelInfo, type Status, type Voice } from "@/api";
import { Markdown } from "@/components/Markdown";
import { cn } from "@/lib/utils";

const SPEEDS = [0.8, 0.9, 1, 1.1, 1.25, 1.5];

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
      <h2 className="text-sm font-semibold">{title}</h2>
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

/**
 * The Settings page: **defaults and environment**, not per-chat knobs. It holds the
 * defaults new conversations inherit (the Listen voice + speed), what the project
 * contributes (`heim.toml`), and reference info about the loaded model. Everything
 * adjustable for a single conversation lives in that chat's settings panel.
 */
export function SettingsView({
  status,
  library,
  voices,
  ttsVoice,
  onChooseVoice,
  ttsSpeed,
  onChooseSpeed,
}: {
  status: Status | null;
  library: LibModel[];
  voices: Voice[];
  ttsVoice: string;
  onChooseVoice: (name: string) => void;
  ttsSpeed: number;
  onChooseSpeed: (speed: number) => void;
}) {
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
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-6 py-8">
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Defaults new chats inherit, and what this server contributes. To change the system prompt,
          sampling, context, voice, or tools for one conversation, open its settings panel from the
          chat.
        </p>

        <div className="mt-8 space-y-10">
          <Section
            title="Default voice"
            description="Used by Listen in chats that don't set their own voice. 54 built-in Kokoro voices across 9 languages."
          >
            <select
              value={ttsVoice}
              onChange={(e) => onChooseVoice(e.target.value)}
              className="h-8 w-full max-w-md rounded-md border border-border bg-background/60 px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="">
                {status?.default_chat_voice ? "Project default" : "Built-in default"}
              </option>
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

            <div className="mt-3">
              <div className="mb-1 text-sm font-medium">Default speed</div>
              <div className="flex flex-wrap items-center gap-1">
                {SPEEDS.map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => onChooseSpeed(v)}
                    className={cn(
                      "rounded-md px-2 py-1 text-xs tabular-nums transition-colors",
                      ttsSpeed === v
                        ? "bg-primary/15 font-medium text-primary"
                        : "text-muted-foreground hover:bg-accent hover:text-foreground",
                    )}
                  >
                    {v}x
                  </button>
                ))}
              </div>
            </div>
          </Section>

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
                    <p className="whitespace-pre-wrap text-xs text-muted-foreground">
                      {status.default_system_prompt}
                    </p>
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
        </div>
      </div>
    </div>
  );
}

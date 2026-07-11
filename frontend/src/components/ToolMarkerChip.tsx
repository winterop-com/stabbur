import { memo, useMemo, useState } from "react";
import { CornerDownRight, Settings2 } from "lucide-react";

import type { ToolMarker } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Parse `detail` as JSON, but only accept an object/array (a structured payload).
 *  Scalars, non-JSON, and truncated/unparseable JSON all return null so the caller
 *  falls back to the plain single-line rendering (unchanged behavior). */
function parseStructured(detail: string): object | null {
  try {
    const parsed: unknown = JSON.parse(detail);
    if (parsed !== null && typeof parsed === "object") return parsed;
  } catch {
    // Not JSON (or truncated by the server-side detail cap) -- render the raw line.
  }
  return null;
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

// A tool result that returned image(s) carries a trailing " [+N image(s)]" note appended by the
// agent loop. Stripping it before parsing lets an otherwise-valid JSON result still parse (and
// collapse); the note is re-appended to the summary line so it stays visible.
const IMAGE_SUFFIX = /\s\[\+\d+ image\(s\)\]$/;

function splitImageSuffix(detail: string): { core: string; suffix: string } {
  const m = IMAGE_SUFFIX.exec(detail);
  return m ? { core: detail.slice(0, m.index), suffix: m[0] } : { core: detail, suffix: "" };
}

/** One-line rendering of a value inside a digest: strings clipped, containers summarized. */
function summarizeValue(value: unknown): string {
  if (Array.isArray(value)) return `[${value.length} items]`;
  if (value !== null && typeof value === "object") return "{...}";
  if (typeof value === "string") return truncate(value, 40);
  return String(value);
}

/** Result digest: up to 3 top-level `key: value` pairs, then a plain ellipsis marker. */
function resultDigest(structured: object): string {
  if (Array.isArray(structured)) return `[${structured.length} items] ...`;
  const entries = Object.entries(structured as Record<string, unknown>).slice(0, 3);
  const pairs = entries.map(([key, value]) => `${key}: ${summarizeValue(value)}`).join("  ");
  return pairs ? `${pairs} ...` : "...";
}

/** Split a `name({...args})` call detail into its parts (best effort). */
function splitCall(detail: string): { name: string; args: string } {
  const match = /^([^(]+)\((.*)\)$/s.exec(detail);
  if (match) return { name: match[1], args: truncate(match[2], 80) };
  return { name: truncate(detail, 80), args: "" };
}

/** One-level inline: a top-level string value that itself parses as an object/array is
 *  shown parsed instead of as an escaped string (display-only, mirrors
 *  TargetBanner.flattenVerifyData's intent -- e.g. the exFAT bridge's stdout envelope). */
function inlineJsonStrings(structured: object): unknown {
  if (Array.isArray(structured)) return structured;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(structured as Record<string, unknown>)) {
    if (typeof value === "string") {
      try {
        const parsed: unknown = JSON.parse(value);
        if (parsed !== null && typeof parsed === "object") {
          out[key] = parsed;
          continue;
        }
      } catch {
        // Not JSON -- keep the raw string.
      }
    }
    out[key] = value;
  }
  return out;
}

/** A compact inline chip for a tool call / result within an assistant turn. */
function ToolMarkerChipImpl({ marker }: { marker: ToolMarker }) {
  const isCall = marker.kind === "call";
  // Strip a trailing image note before parsing so an image-bearing JSON result still collapses.
  const { core, suffix } = useMemo(() => splitImageSuffix(marker.detail), [marker.detail]);
  const structured = useMemo(() => parseStructured(core), [core]);
  // Lazily render (and stringify) the expanded body only once the user first opens the chip.
  const [expanded, setExpanded] = useState(false);
  const Icon = isCall ? Settings2 : CornerDownRight;

  return (
    <div
      className={cn(
        "flex max-w-full items-start gap-1.5 rounded-md border px-2 py-1 font-mono text-xs",
        isCall
          ? "border-primary/30 bg-primary/5 text-primary"
          : "border-border/60 bg-muted/40 text-muted-foreground",
      )}
    >
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      {structured === null ? (
        <span className="break-all">{marker.detail}</span>
      ) : (
        <details
          className="min-w-0 flex-1"
          onToggle={(e) => setExpanded((e.currentTarget as HTMLDetailsElement).open)}
        >
          <summary className="cursor-pointer break-all">
            {isCall ? (
              (() => {
                const { name, args } = splitCall(core);
                return (
                  <span>
                    <span className="font-medium">{name}</span>
                    {args ? `(${args})` : null}
                    {suffix}
                  </span>
                );
              })()
            ) : (
              <span>
                {resultDigest(structured)}
                {suffix}
              </span>
            )}
          </summary>
          {expanded ? (
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap text-xs">
              {JSON.stringify(inlineJsonStrings(structured), null, 2)}
            </pre>
          ) : null}
        </details>
      )}
    </div>
  );
}

export const ToolMarkerChip = memo(ToolMarkerChipImpl);

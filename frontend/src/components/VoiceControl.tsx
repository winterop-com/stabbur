import { Check, ChevronDown, Volume2 } from "lucide-react";

import type { Voice } from "@/api";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const SPEEDS = [0.8, 0.9, 1, 1.1, 1.25, 1.5];

/**
 * Composer-docked voice control: a pill showing the Listen voice (and a non-default
 * speed), opening a menu with speed chips and the voice list grouped by language.
 * Edits the same global preference as the Settings page, so the two stay in sync.
 */
export function VoiceControl({
  voices,
  selected,
  onChooseVoice,
  speed,
  onChooseSpeed,
}: {
  voices: Voice[];
  /** The raw stored pick ("" = the server default voice). */
  selected: string;
  onChooseVoice: (id: string) => void;
  speed: number;
  onChooseSpeed: (speed: number) => void;
}) {
  if (voices.length === 0) return null;
  const current = voices.find((v) => v.id === selected);
  const label = current ? current.label : "Voice";

  const byLanguage: Record<string, Voice[]> = {};
  for (const v of voices) (byLanguage[v.language || "Other"] ??= []).push(v);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          current || speed !== 1 ? "text-foreground" : "text-muted-foreground",
        )}
        title="Listen voice and speed"
      >
        <Volume2 className="h-3.5 w-3.5" />
        <span className="max-w-32 truncate">{label}</span>
        {speed !== 1 && <span className="tabular-nums text-muted-foreground">{speed}x</span>}
        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
      </DropdownMenuTrigger>

      <DropdownMenuContent align="start" className="max-h-96 w-64 overflow-y-auto">
        <div className="px-2.5 py-2">
          <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Speed</div>
          <div className="flex items-center gap-1">
            {SPEEDS.map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => onChooseSpeed(v)}
                className={cn(
                  "rounded-md px-1.5 py-1 text-[11px] tabular-nums transition-colors",
                  speed === v ? "bg-primary/15 font-medium text-primary" : "text-muted-foreground hover:bg-accent",
                )}
              >
                {v}x
              </button>
            ))}
          </div>
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem className="gap-2 px-2.5" onClick={() => onChooseVoice("")}>
          <span className="flex-1">Default voice</span>
          {!current && <Check className="h-3.5 w-3.5" />}
        </DropdownMenuItem>
        {Object.entries(byLanguage).map(([language, vs]) => (
          <div key={language}>
            <div className="px-2.5 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              {language}
            </div>
            {vs.map((v) => (
              <DropdownMenuItem key={v.id} className="gap-2 px-2.5" onClick={() => onChooseVoice(v.id)}>
                <span className="flex-1">
                  {v.label}
                  {v.gender ? ` · ${v.gender === "female" ? "F" : "M"}` : ""}
                </span>
                {selected === v.id && <Check className="h-3.5 w-3.5" />}
              </DropdownMenuItem>
            ))}
          </div>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

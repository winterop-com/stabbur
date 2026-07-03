import { Sparkles, Users, Wand2 } from "lucide-react";

import type { VoiceModelInfo } from "@/api";
import { cn } from "@/lib/utils";

const BACKEND_LABEL: Record<string, string> = {
  "kokoro-onnx": "Kokoro (ONNX)",
  "mlx-audio": "mlx-audio",
  "llama-tts": "llama-tts",
};

function shortName(name: string): string {
  return name.split("/").pop() ?? name;
}

/** A read-only reference card for one voice model (shown in the Library). */
export function VoiceCard({ model }: { model: VoiceModelInfo }) {
  return (
    <div className="flex flex-col rounded-xl border border-border p-3 transition-colors hover:border-primary/40">
      <div className="flex items-center justify-between gap-2">
        <span
          className={cn(
            "rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
            model.kind === "tts"
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              : "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
          )}
        >
          {model.kind}
        </span>
        <span className="text-xs text-muted-foreground">{model.size_human}</span>
      </div>
      <div className="mt-2 break-words text-sm font-medium leading-snug" title={model.name}>
        {model.display_name || shortName(model.name)}
      </div>
      <div className="truncate text-[11px] text-muted-foreground">{BACKEND_LABEL[model.backend] ?? model.backend}</div>
      {model.description && (
        <p className="mt-1.5 line-clamp-3 text-[11px] leading-relaxed text-muted-foreground">{model.description}</p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
        {!model.supported && (
          <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-600 dark:text-amber-400">
            not runnable yet
          </span>
        )}
        {model.chat_default && (
          <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-amber-600 dark:text-amber-400">
            <Sparkles className="h-2.5 w-2.5" /> chat voice
          </span>
        )}
        {model.cloneable && (
          <span className="inline-flex items-center gap-1 rounded-full border border-border px-1.5 py-0.5">
            <Wand2 className="h-2.5 w-2.5" /> clone
          </span>
        )}
        {model.multi_speaker && (
          <span className="inline-flex items-center gap-1 rounded-full border border-border px-1.5 py-0.5">
            <Users className="h-2.5 w-2.5" /> dialogue
          </span>
        )}
        {model.seeded && <span className="rounded-full border border-border px-1.5 py-0.5">seeded</span>}
        {model.languages.length > 0 && (
          <span className="rounded-full border border-border px-1.5 py-0.5">{model.languages.join(" ")}</span>
        )}
      </div>
    </div>
  );
}

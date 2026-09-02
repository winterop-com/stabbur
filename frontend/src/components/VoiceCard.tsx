import { Palette, Sparkles, Users, Wand2 } from "lucide-react";

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
    <div className="flex flex-col rounded-xl border border-border p-4 transition-colors hover:border-primary/40">
      <div className="flex items-center justify-between gap-2">
        <span
          className={cn(
            "rounded border px-2 py-0.5 text-xs font-medium uppercase tracking-wide",
            // The two voice directions, kept apart by the semantic set rather
            // than by two hand-picked hues: speaking is the thing the chat
            // actually does (`good`), transcribing is a fact about the audio
            // you gave it (`info`).
            model.kind === "tts" ? "border-good/30 bg-good/10 text-good-ink" : "border-info/30 bg-info/10 text-info",
          )}
        >
          {model.kind}
        </span>
        <span className="text-xs text-muted-foreground">{model.size_human}</span>
      </div>
      <div className="mt-2 break-words text-sm font-medium leading-snug" title={model.name}>
        {model.display_name || shortName(model.name)}
      </div>
      <div className="truncate text-xs text-muted-foreground">{BACKEND_LABEL[model.backend] ?? model.backend}</div>
      {model.description && (
        <p className="mt-1.5 line-clamp-3 text-sm leading-relaxed text-muted-foreground">{model.description}</p>
      )}
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
        {/* Two pills that used to share one amber and do not share a meaning:
            "not runnable yet" is a warning, "chat voice" is a fact about which
            voice this build speaks with. */}
        {!model.supported && (
          <span className="rounded-full border border-warning/30 bg-warning/10 px-2 py-0.5 text-warning-ink">
            not runnable yet
          </span>
        )}
        {model.chat_default && (
          <span className="inline-flex items-center gap-1 rounded-full border border-info/30 bg-info/10 px-2 py-0.5 text-info">
            <Sparkles className="h-3 w-3" /> chat voice
          </span>
        )}
        {model.cloneable && (
          <span className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5">
            <Wand2 className="h-3 w-3" /> clone
          </span>
        )}
        {model.multi_speaker && (
          <span className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5">
            <Users className="h-3 w-3" /> dialogue
          </span>
        )}
        {model.designable && (
          <span className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5">
            <Palette className="h-3 w-3" /> voice design
          </span>
        )}
        {model.seeded && <span className="rounded-full border border-border px-2 py-0.5">seeded</span>}
        {model.languages.length > 0 && (
          <span className="rounded-full border border-border px-2 py-0.5">{model.languages.join(" ")}</span>
        )}
      </div>
    </div>
  );
}

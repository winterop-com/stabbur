import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowUp, FileText, Loader2, Mic, Paperclip, Square, X } from "lucide-react";

import { transcribeAudio } from "@/api";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { BarVisualizer } from "@/components/ui/bar-visualizer";
import { startRecording, type Recording } from "@/lib/recorder";
import type { Attachment, MediaKind } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Which attachment kinds the loaded model accepts. `known` is false while the
 *  model's capabilities are still resolving — attachments are then accepted
 *  optimistically rather than dropped as "unsupported". */
export interface Accept {
  image: boolean;
  audio: boolean;
  known: boolean;
}

// Text/doc files we inline into the prompt (many carry an empty MIME type, so we
// also match by extension). These work with any model, not just multimodal ones.
const TEXT_EXT =
  /\.(txt|text|md|markdown|rst|json|jsonl|ndjson|csv|tsv|log|ya?ml|toml|ini|cfg|conf|env|xml|html?|css|scss|py|pyi|js|jsx|mjs|cjs|ts|tsx|go|rs|rb|java|kt|kts|scala|c|h|cpp|cc|cxx|hpp|cs|php|swift|sql|sh|bash|zsh|fish|ps1|r|lua|pl|pm|dart|ex|exs|clj|hs|ml|vue|svelte|tex|dockerfile|makefile|gitignore|proto|graphql|gql)$/i;
// File-picker accept hint: text/* plus the common code extensions above.
const TEXT_ACCEPT =
  "text/*,.md,.markdown,.rst,.json,.jsonl,.csv,.tsv,.log,.yaml,.yml,.toml,.ini,.cfg,.env,.xml,.html,.css,.scss,.py,.js,.jsx,.mjs,.cjs,.ts,.tsx,.go,.rs,.rb,.java,.kt,.c,.h,.cpp,.hpp,.cs,.php,.swift,.sql,.sh,.lua,.pl,.dart,.vue,.svelte,.tex";

function isTextFile(file: File): boolean {
  return (
    file.type.startsWith("text/") ||
    file.type === "application/json" ||
    file.type === "application/xml" ||
    TEXT_EXT.test(file.name)
  );
}

/** Classify a File into an accepted kind, or null. Text is always accepted (it's
 *  inlined into the prompt); image/audio only for capable models. */
function kindOf(file: File, accept: Accept): MediaKind | null {
  // While caps are unknown (library/status still loading), accept media
  // optimistically instead of dropping it — a genuine mismatch surfaces at send.
  if (file.type.startsWith("image/") && (accept.image || !accept.known)) return "image";
  if (file.type.startsWith("audio/") && (accept.audio || !accept.known)) return "audio";
  if (isTextFile(file)) return "text";
  return null;
}

/** Split files into accepted (with kind) and media rejected because the loaded
 *  model lacks that modality (so we can tell the user instead of dropping silently). */
function classifyFiles(
  files: File[],
  accept: Accept,
): { accepted: { f: File; kind: MediaKind }[]; rejected: Set<"image" | "audio"> } {
  const accepted: { f: File; kind: MediaKind }[] = [];
  const rejected = new Set<"image" | "audio">();
  for (const f of files) {
    const kind = kindOf(f, accept);
    if (kind) accepted.push({ f, kind });
    else if (f.type.startsWith("image/")) rejected.add("image");
    else if (f.type.startsWith("audio/")) rejected.add("audio");
    // other non-text, non-media files are ignored (nothing sensible to do with them)
  }
  return { accepted, rejected };
}

/** Read pre-classified files into typed attachments (image/audio → data URL, text → contents). */
async function readAttachments(accepted: { f: File; kind: MediaKind }[]): Promise<Attachment[]> {
  return Promise.all(
    accepted.map(
      ({ f, kind }) =>
        new Promise<Attachment>((resolve, reject) => {
          const r = new FileReader();
          r.onerror = reject;
          if (kind === "text") {
            r.onload = () => resolve({ kind: "text", name: f.name, text: r.result as string });
            r.readAsText(f);
          } else {
            r.onload = () => resolve({ kind, url: r.result as string });
            r.readAsDataURL(f);
          }
        }),
    ),
  );
}

/**
 * Rounded, elevated composer pinned at the bottom center. Holds the textarea, a
 * circular send button (swaps to Stop while streaming), and — for vision models
 * — image attachments via drag-drop, paste, or the picker. The model is chosen
 * from the top bar, not here.
 */
export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  streaming,
  ready,
  autoFocus,
  leftSlot,
  attachments,
  accept,
  canDictate,
  onAdd,
  onRemove,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop: () => void;
  streaming: boolean;
  ready: boolean;
  autoFocus?: boolean;
  /** Controls docked at the bottom-left of the composer (model picker, tools). */
  leftSlot?: React.ReactNode;
  /** Pending attachments (image/audio). */
  attachments: Attachment[];
  /** Which modalities the loaded model accepts (gates the attach affordances). */
  accept: Accept;
  /** A Whisper STT model is in the library, so speech can be dictated into the prompt. */
  canDictate?: boolean;
  onAdd: (items: Attachment[]) => void;
  onRemove: (index: number) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const recRef = useRef<Recording | null>(null);
  const [recState, setRecState] = useState<"idle" | "recording" | "encoding">("idle");
  const [micStream, setMicStream] = useState<MediaStream | null>(null); // live stream, shared with the visualizer
  // A transient note shown when a dropped/pasted file can't be used (e.g. audio on
  // a text model), so incompatible files aren't silently discarded.
  const [hint, setHint] = useState<string | null>(null);
  const hintTimer = useRef<number | null>(null);
  const showHint = (msg: string) => {
    setHint(msg);
    if (hintTimer.current) clearTimeout(hintTimer.current);
    hintTimer.current = window.setTimeout(() => setHint(null), 4000);
  };

  // Auto-grow the textarea to fit content (capped by max-height via CSS). When
  // empty we leave the natural rows={1} height (height:auto) rather than trust
  // scrollHeight — at mount, before layout/fonts settle, scrollHeight can read
  // the max and stick (the effect only re-runs on value change), leaving the box
  // stuck tall. Only measure-and-grow once there's actual content.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    if (el.value) el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  useEffect(() => {
    if (autoFocus) ref.current?.focus();
  }, [autoFocus]);

  const canSend = ready && !streaming && (value.trim().length > 0 || attachments.length > 0);
  // Attaching needs a loaded model (you can't send otherwise). Any ready model
  // takes text/doc files (inlined into the prompt); image/audio are added to the
  // accept hint only for capable models.
  const canAttach = ready;
  const acceptAttr = [accept.image && "image/*", accept.audio && "audio/*", TEXT_ACCEPT].filter(Boolean).join(",");

  const addFiles = async (files: FileList | File[]) => {
    const { accepted, rejected } = classifyFiles([...files], accept);
    if (rejected.size) {
      const need = rejected.has("image") && rejected.has("audio") ? "vision/audio" : rejected.has("image") ? "vision" : "audio";
      showHint(`The loaded model can't read ${[...rejected].join(" or ")} — switch to a ${need}-capable model.`);
    }
    if (accepted.length) onAdd(await readAttachments(accepted));
  };

  // Finalize a recording (from a manual stop or auto silence): encode + attach.
  const finishRecording = async () => {
    const rec = recRef.current;
    if (!rec) return;
    recRef.current = null;
    setRecState("encoding");
    setMicStream(null);
    try {
      const url = await rec.stop();
      onAdd([{ url, kind: "audio" }]);
    } catch {
      /* decode/permission error — drop it */
    } finally {
      setRecState("idle");
    }
  };

  // Mic capture: start → recording; click again (or a silence auto-stop) → attach.
  const toggleRecording = async () => {
    if (recState === "encoding") return;
    if (recState === "recording") return void finishRecording();
    try {
      recRef.current = await startRecording({ onSilence: () => void finishRecording() });
      setMicStream(recRef.current.stream);
      setRecState("recording");
    } catch {
      setRecState("idle"); // permission denied / unsupported
    }
  };

  // --- dictation: record, transcribe with Whisper, drop the text into the prompt.
  // Distinct from the audio-attach mic above (which feeds raw audio to audio-native
  // models); this turns speech into a text prompt that any model can read.
  const dictRef = useRef<Recording | null>(null);
  const [dictState, setDictState] = useState<"idle" | "recording" | "transcribing">("idle");
  const finishDictation = async () => {
    const rec = dictRef.current;
    if (!rec) return;
    dictRef.current = null;
    setDictState("transcribing");
    setMicStream(null);
    try {
      const wavUrl = await rec.stop();
      const blob = await (await fetch(wavUrl)).blob();
      const text = (await transcribeAudio(blob)).trim();
      if (text) onChange(value ? `${value} ${text}` : text);
    } catch (e) {
      showHint(e instanceof Error ? e.message : "transcription failed");
    } finally {
      setDictState("idle");
      ref.current?.focus();
    }
  };
  const toggleDictation = async () => {
    if (dictState === "transcribing") return;
    if (dictState === "recording") return void finishDictation();
    try {
      dictRef.current = await startRecording({ onSilence: () => void finishDictation() });
      setMicStream(dictRef.current.stream);
      setDictState("recording");
    } catch {
      setDictState("idle"); // permission denied / unsupported
    }
  };

  return (
    <div
      className={cn(
        "rounded-3xl border border-border bg-card shadow-sm transition-colors",
        dragOver && canAttach && "border-primary ring-2 ring-primary/40",
      )}
      onDragOver={(e) => {
        if (!canAttach) return;
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        setDragOver(false);
        if (!canAttach || !e.dataTransfer.files.length) return;
        e.preventDefault();
        void addFiles(e.dataTransfer.files);
      }}
    >
      {/* Attachment previews: images as thumbnails, audio as a player, text as a chip */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 px-4 pt-3">
          {attachments.map((att, i) => {
            if (att.kind === "image") {
              return (
                <div key={i} className="group relative h-16 w-16 overflow-hidden rounded-lg border border-border">
                  <img src={att.url} alt={`attachment ${i + 1}`} className="h-full w-full object-cover" />
                  <button
                    type="button"
                    onClick={() => onRemove(i)}
                    className="absolute right-0.5 top-0.5 rounded-full bg-background/80 p-0.5 text-foreground opacity-0 transition-opacity group-hover:opacity-100"
                    aria-label="Remove attachment"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              );
            }
            if (att.kind === "audio") {
              return (
                <div key={i} className="group relative flex items-center gap-2 rounded-lg border border-border px-2 py-1.5">
                  <audio src={att.url} controls className="h-8 max-w-[12rem]" />
                  <button
                    type="button"
                    onClick={() => onRemove(i)}
                    className="rounded-full bg-background/80 p-0.5 text-muted-foreground hover:text-foreground"
                    aria-label="Remove attachment"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              );
            }
            return (
              <div key={i} className="flex items-center gap-2 rounded-lg border border-border px-2.5 py-1.5">
                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="max-w-[12rem] truncate text-xs" title={att.name}>
                  {att.name}
                </span>
                <button
                  type="button"
                  onClick={() => onRemove(i)}
                  className="rounded-full bg-background/80 p-0.5 text-muted-foreground hover:text-foreground"
                  aria-label="Remove attachment"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {(recState === "recording" || dictState === "recording") && (
        <div className="flex items-center gap-2 px-4 pt-3 text-destructive">
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-destructive/60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-destructive" />
          </span>
          <BarVisualizer
            mediaStream={micStream}
            barCount={40}
            minHeight={4}
            centerAlign
            className="h-10 flex-1 gap-1 rounded-lg bg-transparent p-0 [&>div]:bg-destructive"
          />
          <span className="shrink-0 text-[11px]">{dictState === "recording" ? "Dictating…" : "Recording…"}</span>
        </div>
      )}

      <div className="px-4 pt-3">
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              if (canSend) onSend();
            }
          }}
          onPaste={(e) => {
            const files = [...e.clipboardData.items]
              .filter((it) => it.kind === "file")
              .map((it) => it.getAsFile())
              .filter((f): f is File => f !== null);
            if (canAttach && files.some((f) => kindOf(f, accept) !== null)) {
              e.preventDefault();
              void addFiles(files);
            }
          }}
          rows={1}
          placeholder={ready ? "Message kodo…" : "Select a model to start"}
          className="max-h-[200px] w-full resize-none bg-transparent text-[0.95rem] leading-relaxed outline-none placeholder:text-muted-foreground disabled:opacity-60"
        />
      </div>
      <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5 pt-1">
        <div className="flex min-w-0 items-center gap-1">
          {canAttach && (
            <>
              {/* Hidden file input opened by the paperclip button below. Kept in the
                  layout tree (sr-only, not display:none) so .click() reliably opens
                  the native picker. */}
              <input
                ref={fileRef}
                type="file"
                accept={acceptAttr}
                multiple
                className="sr-only"
                onChange={(e) => {
                  if (e.target.files) void addFiles(e.target.files);
                  e.target.value = ""; // allow re-picking the same file
                }}
              />
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => fileRef.current?.click()}
                aria-label="Attach file"
                title={`Attach a file${accept.image ? ", image" : ""}${accept.audio ? ", audio" : ""} — document, drag, or paste`}
              >
                <Paperclip className="h-4 w-4" />
              </Button>
            </>
          )}
          {accept.audio && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={toggleRecording}
                  aria-label={recState === "recording" ? "Stop recording" : "Record audio"}
                  className={cn(recState === "recording" && "text-destructive")}
                >
                  {recState === "encoding" ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : recState === "recording" ? (
                    <Square className="h-4 w-4 fill-current" />
                  ) : (
                    <Mic className="h-4 w-4" />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {recState === "recording" ? "Stop & attach recording" : "Record audio from your mic"}
              </TooltipContent>
            </Tooltip>
          )}
          {/* Dictation mic (Whisper -> prompt): shown for non-audio models when a
              Whisper STT model is in the library, so speech becomes a text prompt. */}
          {canAttach && canDictate && !accept.audio && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  onClick={toggleDictation}
                  aria-label={dictState === "recording" ? "Stop dictation" : "Dictate with your mic"}
                  className={cn(dictState === "recording" && "text-destructive")}
                >
                  {dictState === "transcribing" ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : dictState === "recording" ? (
                    <Square className="h-4 w-4 fill-current" />
                  ) : (
                    <Mic className="h-4 w-4" />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {dictState === "recording" ? "Stop & transcribe" : "Dictate (speech to text)"}
              </TooltipContent>
            </Tooltip>
          )}
          {leftSlot}
        </div>
        {streaming ? (
          <Button size="icon" onClick={onStop} className="h-9 w-9 rounded-full" aria-label="Stop generating">
            <Square className="h-4 w-4 fill-current" />
          </Button>
        ) : (
          <Button
            size="icon"
            onClick={onSend}
            disabled={!canSend}
            className={cn("h-9 w-9 rounded-full")}
            aria-label="Send message"
          >
            <ArrowUp className="h-4 w-4" />
          </Button>
        )}
      </div>
      {hint && <div className="-mt-1 px-4 pb-2 text-[11px] text-muted-foreground">{hint}</div>}
    </div>
  );
}

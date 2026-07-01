import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowUp, Loader2, Mic, Paperclip, Square, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { startRecording, type Recording } from "@/lib/recorder";
import type { Attachment, MediaKind } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Which attachment kinds the loaded model accepts. */
export interface Accept {
  image: boolean;
  audio: boolean;
}

/** Classify a File into an accepted media kind, or null. */
function kindOf(file: File, accept: Accept): MediaKind | null {
  if (file.type.startsWith("image/") && accept.image) return "image";
  if (file.type.startsWith("audio/") && accept.audio) return "audio";
  return null;
}

/** Read accepted image/audio Files into typed data-URL attachments. */
async function filesToAttachments(files: FileList | File[], accept: Accept): Promise<Attachment[]> {
  const wanted = [...files]
    .map((f) => ({ f, kind: kindOf(f, accept) }))
    .filter((x): x is { f: File; kind: MediaKind } => x.kind !== null);
  return Promise.all(
    wanted.map(
      ({ f, kind }) =>
        new Promise<Attachment>((resolve, reject) => {
          const r = new FileReader();
          r.onload = () => resolve({ url: r.result as string, kind });
          r.onerror = reject;
          r.readAsDataURL(f);
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
  onAdd: (items: Attachment[]) => void;
  onRemove: (index: number) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const recRef = useRef<Recording | null>(null);
  const [recState, setRecState] = useState<"idle" | "recording" | "encoding">("idle");

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
  const canAttach = accept.image || accept.audio;
  const acceptAttr = [accept.image && "image/*", accept.audio && "audio/*"].filter(Boolean).join(",");

  const addFiles = async (files: FileList | File[]) => {
    const items = await filesToAttachments(files, accept);
    if (items.length) onAdd(items);
  };

  // Finalize a recording (from a manual stop or auto silence): encode + attach.
  const finishRecording = async () => {
    const rec = recRef.current;
    if (!rec) return;
    recRef.current = null;
    setRecState("encoding");
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
      setRecState("recording");
    } catch {
      setRecState("idle"); // permission denied / unsupported
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
      {/* Attachment previews: images as thumbnails, audio as a small player */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 px-4 pt-3">
          {attachments.map((att, i) =>
            att.kind === "image" ? (
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
            ) : (
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
            ),
          )}
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
              <input
                ref={fileInput}
                type="file"
                accept={acceptAttr}
                multiple
                className="hidden"
                onChange={(e) => {
                  if (e.target.files) void addFiles(e.target.files);
                  e.target.value = ""; // allow re-picking the same file
                }}
              />
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => fileInput.current?.click()}
                    aria-label="Attach file"
                  >
                    <Paperclip className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  Attach {accept.image && accept.audio ? "image or audio" : accept.audio ? "audio" : "image"} (drag
                  or paste too)
                </TooltipContent>
              </Tooltip>
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
    </div>
  );
}

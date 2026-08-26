import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowUp, FileText, FileType, Loader2, Mic, Paperclip, Square, X } from "lucide-react";

import { transcribeAudio } from "@/api";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { BarVisualizer } from "@/components/ui/bar-visualizer";
import { acceptAttribute, prepareAttachments, type Accept } from "@/lib/attachments";
import { startRecording, type Recording } from "@/lib/recorder";
import type { Attachment } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Rounded, elevated composer pinned at the bottom center. Holds the textarea, a
 * circular send button (swaps to Stop while streaming), and attachments via
 * drag-drop, paste, or the picker — documents and PDFs on any model, images and
 * audio on models that can read them. The model is chosen from the top bar, not
 * here. Turning a file into an attachment is `lib/attachments`' job; the composer
 * only shows the result and surfaces whatever it couldn't do.
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
  pdfAsImage,
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
  /** Pending attachments (image/audio/text). */
  attachments: Attachment[];
  /** Which modalities the loaded model accepts (gates the attach affordances). */
  accept: Accept;
  /** A Whisper STT model is in the library, so speech can be dictated into the prompt. */
  canDictate?: boolean;
  /** Render attached PDFs as page images instead of extracting their text (this chat's setting). */
  pdfAsImage?: boolean;
  onAdd: (items: Attachment[]) => void;
  onRemove: (index: number) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const recRef = useRef<Recording | null>(null);
  const [recState, setRecState] = useState<"idle" | "recording" | "encoding">("idle");
  const [micStream, setMicStream] = useState<MediaStream | null>(null); // live stream, shared with the visualizer
  // A transient note shown when a dropped/pasted file can't be used as asked (e.g.
  // audio on a text model, a PDF with no text layer), so nothing is discarded in
  // silence. Longer notes get proportionally longer on screen — a three-file drop
  // can produce three sentences, and four seconds isn't enough to read them.
  const [hint, setHint] = useState<string | null>(null);
  const hintTimer = useRef<number | null>(null);
  const showHint = (msg: string) => {
    setHint(msg);
    if (hintTimer.current) clearTimeout(hintTimer.current);
    hintTimer.current = window.setTimeout(() => setHint(null), Math.min(12_000, 4000 + msg.length * 40));
  };
  // Reading a PDF (or downscaling a big image) is async and can take a beat, so the
  // composer says so rather than looking like the drop did nothing — the exact
  // complaint that made attachments feel broken.
  const [preparing, setPreparing] = useState(0);

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

  // Blocked while a file is still being read: otherwise Enter sends the turn the
  // attachment was meant for, and the attachment lands on the *next* one.
  const canSend = ready && !streaming && preparing === 0 && (value.trim().length > 0 || attachments.length > 0);
  // Attaching needs a loaded model (you can't send otherwise). Any ready model takes
  // documents and PDFs (they end up in the prompt as text); image/audio are added to
  // the accept hint only for capable models.
  const canAttach = ready;
  const acceptAttr = acceptAttribute(accept);

  const addFiles = async (files: FileList | File[]) => {
    const list = [...files];
    if (!list.length) return;
    setPreparing((n) => n + list.length);
    try {
      const { items, notes } = await prepareAttachments(list, accept, { pdfAsImage });
      if (items.length) onAdd(items);
      // Every file that didn't become an attachment (or didn't become the one asked
      // for) explains itself here — a silently ignored drop is the one outcome we
      // never want.
      if (notes.length) showHint(notes.join(" "));
    } finally {
      setPreparing((n) => Math.max(0, n - list.length));
    }
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

  // Tear down any in-flight capture if the composer unmounts mid-recording (F-2): the
  // empty-state composer unmounts after the first send, and navigating away unmounts it
  // too — without this the MediaRecorder, VAD AudioContext, and mic tracks keep running
  // (the recording indicator vanishes but the mic stays live) until a page reload.
  useEffect(
    () => () => {
      recRef.current?.cancel();
      dictRef.current?.cancel();
    },
    [],
  );
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
        // Always preventDefault for a file drag, even with no model loaded — otherwise the
        // browser's default drop action navigates away and destroys in-flight state (F-6).
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        if (canAttach) setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        if (!e.dataTransfer.types.includes("Files")) return;
        e.preventDefault(); // prevent the browser from opening the dropped file
        setDragOver(false);
        if (!canAttach) {
          showHint("Load a model first, then drop files to attach them.");
          return;
        }
        if (e.dataTransfer.files.length) void addFiles(e.dataTransfer.files);
      }}
    >
      {/* Attachment previews: images as thumbnails, audio as a player, documents as a chip */}
      {(attachments.length > 0 || preparing > 0) && (
        <div className="flex flex-wrap gap-2 px-4 pt-3">
          {attachments.map((att, i) => {
            if (att.kind === "image") {
              return (
                <div
                  key={i}
                  title={att.name}
                  className="group relative h-16 w-16 overflow-hidden rounded-lg border border-border"
                >
                  <img src={att.url} alt={att.name ?? `attachment ${i + 1}`} className="h-full w-full object-cover" />
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
            // Document chip. `pages` is only set for text lifted out of a PDF, so it
            // doubles as the marker that this chip is a PDF rather than a plain file.
            return (
              <div key={i} className="flex items-center gap-2 rounded-lg border border-border px-2.5 py-1.5">
                {att.pages ? (
                  <FileType className="h-4 w-4 shrink-0 text-muted-foreground" />
                ) : (
                  <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                )}
                <span className="min-w-0">
                  <span className="block max-w-[12rem] truncate text-xs" title={att.name}>
                    {att.name}
                  </span>
                  {att.pages != null && (
                    <span className="block text-xs text-muted-foreground">
                      PDF · {att.pages} {att.pages === 1 ? "page" : "pages"}
                    </span>
                  )}
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
          {preparing > 0 && (
            <div className="flex items-center gap-2 rounded-lg border border-dashed border-border px-2.5 py-1.5 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Reading {preparing} {preparing === 1 ? "file" : "files"}…
            </div>
          )}
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
          <span className="shrink-0 text-sm">{dictState === "recording" ? "Dictating…" : "Recording…"}</span>
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
            if (!canAttach || !files.length) return;
            // Only swallow the paste when the clipboard is *just* files. Copying from a
            // rich editor puts a rendered image alongside the text, and there the text is
            // what was meant — attach the file, but still let the text land.
            if (!e.clipboardData.types.includes("text/plain")) e.preventDefault();
            void addFiles(files);
          }}
          rows={1}
          placeholder={ready ? "Message heim…" : "Select a model to start"}
          className="max-h-[200px] w-full resize-none bg-transparent text-base leading-relaxed outline-none placeholder:text-muted-foreground disabled:opacity-60"
        />
      </div>
      <div className="flex items-center justify-between gap-2 px-2.5 pb-2.5 pt-1">
        <div className="flex min-w-0 items-center gap-1">
          {canAttach && (
            <>
              {/* Hidden file input opened by the paperclip button below. Kept in the
                  layout tree (sr-only, not display:none) so .click() reliably opens the
                  native picker — but taken out of the tab order and the a11y tree, or a
                  screen reader / Tab user meets a stray "Choose File" control sitting
                  next to the real Attach button. */}
              <input
                ref={fileRef}
                type="file"
                accept={acceptAttr}
                multiple
                tabIndex={-1}
                aria-hidden="true"
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
                title={`Attach a document or PDF${accept.image ? ", image" : ""}${accept.audio ? ", audio" : ""} — pick, drag, or paste`}
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
      {hint && <div className="-mt-1 px-4 pb-2 text-sm text-muted-foreground">{hint}</div>}
    </div>
  );
}

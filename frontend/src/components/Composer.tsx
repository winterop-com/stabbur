import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowUp, ImagePlus, Square, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** Read image File objects into data URLs (skips non-images). */
async function filesToDataUrls(files: FileList | File[]): Promise<string[]> {
  const imgs = [...files].filter((f) => f.type.startsWith("image/"));
  return Promise.all(
    imgs.map(
      (f) =>
        new Promise<string>((resolve, reject) => {
          const r = new FileReader();
          r.onload = () => resolve(r.result as string);
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
  canAttachImages,
  onAddImages,
  onRemoveImage,
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
  /** Attached image data URLs (pending send). */
  attachments: string[];
  /** Whether the loaded model accepts images (gates the attach affordances). */
  canAttachImages: boolean;
  onAddImages: (dataUrls: string[]) => void;
  onRemoveImage: (index: number) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

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

  const addFiles = async (files: FileList | File[]) => {
    if (!canAttachImages) return;
    const urls = await filesToDataUrls(files);
    if (urls.length) onAddImages(urls);
  };

  return (
    <div
      className={cn(
        "rounded-3xl border border-border bg-card shadow-sm transition-colors",
        dragOver && canAttachImages && "border-primary ring-2 ring-primary/40",
      )}
      onDragOver={(e) => {
        if (!canAttachImages) return;
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        setDragOver(false);
        if (!canAttachImages || !e.dataTransfer.files.length) return;
        e.preventDefault();
        void addFiles(e.dataTransfer.files);
      }}
    >
      {/* Attached-image thumbnails */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 px-4 pt-3">
          {attachments.map((src, i) => (
            <div key={i} className="group relative h-16 w-16 overflow-hidden rounded-lg border border-border">
              <img src={src} alt={`attachment ${i + 1}`} className="h-full w-full object-cover" />
              <button
                type="button"
                onClick={() => onRemoveImage(i)}
                className="absolute right-0.5 top-0.5 rounded-full bg-background/80 p-0.5 text-foreground opacity-0 transition-opacity group-hover:opacity-100"
                aria-label="Remove image"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
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
            if (canAttachImages && files.some((f) => f.type.startsWith("image/"))) {
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
          {canAttachImages && (
            <>
              <input
                ref={fileInput}
                type="file"
                accept="image/*"
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
                    aria-label="Attach image"
                  >
                    <ImagePlus className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Attach image (drag or paste too)</TooltipContent>
              </Tooltip>
            </>
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

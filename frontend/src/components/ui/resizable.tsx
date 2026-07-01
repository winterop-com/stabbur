import { PanelResizeHandle } from "react-resizable-panels";

import { cn } from "@/lib/utils";

/**
 * A subtle vertical drag divider between two horizontal panels. Renders as a
 * hairline that thickens/highlights on hover and while dragging, consistent
 * with the thin `border-border` dividers used elsewhere. The hit area is wider
 * than the visible line so it stays easy to grab.
 */
export function ResizeHandle({
  className,
  onDragging,
}: {
  className?: string;
  /** Forwarded so the layout can disable its collapse transition mid-drag (keeps drag 1:1). */
  onDragging?: (isDragging: boolean) => void;
}) {
  return (
    <PanelResizeHandle
      onDragging={onDragging}
      className={cn(
        "group relative flex w-px shrink-0 items-stretch justify-center bg-border outline-none",
        "transition-colors data-[resize-handle-state=hover]:bg-primary/50 data-[resize-handle-state=drag]:bg-primary/70",
        className,
      )}
    >
      {/* Widened invisible hit area so the 1px line is easy to grab. */}
      <span className="absolute inset-y-0 left-1/2 w-3 -translate-x-1/2" />
    </PanelResizeHandle>
  );
}

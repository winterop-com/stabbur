import { Settings } from "lucide-react";

import type { Status } from "@/api";
import { cn } from "@/lib/utils";

/**
 * The one strip across the foot of the window: Settings on the left, and — deliberately — nothing
 * on the right.
 *
 * THE BAR IS CHROME, NOT A READOUT, and the empty right side is the point rather than a gap. It
 * briefly carried a sentence about this stabbur (backend, model count, tool count), which was a
 * mistake twice over: every one of those facts is already a row in `stabbur doctor`, which the top
 * bar's health menu renders, so the line was a second copy of a list that maintains itself — and a
 * frame that closes the window earns its place by being a frame. One fact, one place; see
 * docs/ui-conventions.md, "Say it once".
 *
 * ONE STRIP, NOT TWO. Settings used to be a row pinned inside the rail, which the moment a status
 * bar existed put two stacked bands across the bottom of the window. It is the same control doing
 * the same thing; it just lives in the bar's left segment now — which IS the rail's column,
 * continued down past the rail's own foot, divider and all.
 *
 * SO THE SEGMENT TRACKS THE RAIL, never a fixed width: expanded it is as wide as the sidebar and
 * shows the word, collapsed it is the icon rail's 48px and is the gear alone. `width` comes from
 * App, which measures where the main panel actually starts — that follows a drag-resize too, which
 * a breakpoint could not.
 *
 * THE ONE THING IT STILL SAYS is that it cannot reach the stabbur that served it. That is not a
 * readout, it is an alarm, and it is the one state the health menu cannot report: a server that is
 * down does not answer `/api/doctor` either, so the menu renders nothing at all and this line is
 * the only thing on screen that says why.
 */
export function StatusBar({
  status,
  width,
  onOpenSettings,
}: {
  status: Status | null;
  /** Current width of the rail column, in px — the left segment matches it. */
  width: number;
  onOpenSettings: () => void;
}) {
  // Below this the word doesn't fit beside the gear, so the segment is the glyph, centred.
  const iconOnly = width < 112;

  return (
    // THE HEIGHT AND THE INSETS ARE THE SIBLING'S, MEASURED. 46px is the odd number here and it
    // is deliberate: it is what lets the Settings row be a CONTAINED 28px element with ~9px of
    // air over and under it rather than a full-height slab, which is the whole difference
    // between a footer row and a block of colour welded to the bottom of the window. Matched to
    // the pixel because the two apps' footers get compared side by side.
    <footer className="flex h-[46px] shrink-0 items-stretch border-t border-sidebar-border bg-sidebar text-sidebar-muted-foreground">
      {/* The segment is full height (it carries the divider that continues the rail's right edge
          straight down); the button inside it is not. The 8px of segment padding is what puts the
          gear 20px off the window edge and keeps the hover fill a pill instead of a slab. */}
      <div style={{ width }} className="flex shrink-0 items-center border-r border-sidebar-border px-2">
        <button
          type="button"
          onClick={onOpenSettings}
          aria-label="Settings"
          className={cn(
            "flex h-7 w-full items-center gap-2 rounded-md text-xs font-medium transition-colors hover:bg-sidebar-wash hover:text-sidebar-foreground",
            iconOnly ? "justify-center px-0" : "px-3",
          )}
        >
          <Settings className="h-4 w-4 shrink-0" />
          {!iconOnly && <span className="truncate">Settings</span>}
        </button>
      </div>
      {/* Empty unless stabbur is unreachable. The 48px inset is the content column's, not a hug
          against the divider, so the one line that ever appears here starts where every other line
          on the surface above it starts. */}
      <div className="flex min-w-0 flex-1 items-center pl-12 pr-4 text-sm leading-5">
        {status === null && <span className="truncate text-critical">Not connected to a stabbur server</span>}
      </div>
    </footer>
  );
}

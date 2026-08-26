import { Settings } from "lucide-react";

import type { LibModel, Status, ToolInfo } from "@/api";
import { cn, formatBytes } from "@/lib/utils";

/** An upstream base URL as a place: `http://msai:1234` -> `msai:1234`, and the raw string if it
 *  isn't parseable (an operator typed it; the bar shows what heim was actually given). */
function upstreamHost(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url.replace(/^https?:\/\//, "");
  }
}

/**
 * The one strip across the foot of the window: Settings on the left, and a plain sentence about
 * this heim on the right.
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
 * A SENTENCE, NOT A ROW OF CHIPS. Every fact here is reference material read at a glance, and an
 * icon per clause turns a quiet line into a toolbar competing with the composer above it.
 *
 * EVERYTHING IT SAYS IS ALREADY IN APP STATE — the status poll, the library scan and the tool list
 * are fetched for the surfaces above regardless — so the bar costs no request and no poll of its own.
 *
 * WHICH BACKEND IS THE POINT OF THE LINE. A remote model id looks exactly like a local one, so
 * nothing on screen used to say whether a reply came off this machine or a box across the room.
 * And `upstream` has THREE states, not two: a URL (remote), `null` (local — the server said so),
 * and *absent*, which is what a heim older than the field sends. heim's `/v1` is deliberately
 * attachable from older clients and a UI upgraded ahead of its server is ordinary, so absent drops
 * the clause entirely. Reading it as local would turn "I was not told" into a claim about where
 * the models run.
 *
 * THE LOADED MODEL IS DELIBERATELY ABSENT. `LoadedModelBadge` owns it in the top bar.
 */
export function StatusBar({
  status,
  library,
  tools,
  width,
  onOpenSettings,
}: {
  status: Status | null;
  library: LibModel[];
  tools: ToolInfo[];
  /** Current width of the rail column, in px — the left segment matches it. */
  width: number;
  onOpenSettings: () => void;
}) {
  // `in`, not a truthiness test or `?? null`: the whole point is telling an absent key from a null
  // value, and both of those collapse exactly the two states that must stay apart.
  const told = status !== null && "upstream" in status && status.upstream !== undefined;
  const upstream = told ? status.upstream : undefined;
  const bytes = library.reduce((sum, m) => sum + m.size_bytes, 0);
  const models = `${library.length} model${library.length === 1 ? "" : "s"} in the library`;
  const backend = upstream ? `Upstream ${upstreamHost(upstream)}` : told ? "Local runtime" : null;

  // Two phrasings rather than a pile of per-clause breakpoints: at a phone's width the backend and
  // the model count are what survive, and the reader should get a finished sentence either way.
  // The disk total is a clause of its own — folding it into the model count with a second "·" made
  // the line read as four facts where it holds three. An upstream's rows are remote ids carrying no
  // size, so the sum is 0 there and the clause simply doesn't appear; gated on the number itself,
  // which is the right answer in all three backend states.
  const full = [
    backend,
    models,
    bytes > 0 ? `${formatBytes(bytes)} on disk` : null,
    `${tools.length} tool${tools.length === 1 ? "" : "s"} attached`,
  ]
    .filter(Boolean)
    .join(" · ");
  // The narrow phrasing keeps the disk total and drops only the tool count and the prose tails.
  // It dropped the total too when this line was 11px; at 14px it measures 238px inside the 317px
  // a 390px phone leaves, so the room is there — and of the two, "how much of the drive is gone"
  // is the figure someone actually opens this app wondering about.
  const compact = [backend, `${library.length} model${library.length === 1 ? "" : "s"}`, bytes > 0 ? formatBytes(bytes) : null]
    .filter(Boolean)
    .join(" · ");

  // Below this the word doesn't fit beside the gear, so the segment is the glyph, centred.
  const iconOnly = width < 112;

  return (
    // THE TYPE IS THE SIBLING'S, NOT heim's SECONDARY SCALE. Built at `text-[11px]` in a 32px
    // strip this read as though the window had been zoomed out: the rest of the app happens to
    // sit at 10-11px for its chips and captions, but this is a line of prose someone reads, not
    // a caption on something else. So the sentence is 14px/20px regular and the Settings label
    // 12.8px medium, which is what the sibling's footer uses.
    //
    // AND THE HEIGHT FOLLOWS THE TYPE rather than being picked: a 28px row (20px line + 4px
    // either side) inside 6px of bar padding is 40px. Sizing the text up inside the old 32px
    // strip would have swapped one crammed line for another.
    <footer className="flex h-10 shrink-0 items-stretch border-t border-sidebar-border bg-sidebar text-sidebar-muted-foreground">
      <button
        type="button"
        onClick={onOpenSettings}
        aria-label="Settings"
        style={{ width }}
        className={cn(
          "flex shrink-0 items-center gap-2 border-r border-sidebar-border text-[0.8rem] font-medium transition-colors hover:bg-sidebar-wash hover:text-sidebar-foreground",
          iconOnly ? "justify-center px-0" : "px-3",
        )}
      >
        <Settings className="h-4 w-4 shrink-0" />
        {!iconOnly && <span className="truncate">Settings</span>}
      </button>
      <div className="flex min-w-0 flex-1 items-center px-3 text-sm leading-5">
        {status === null ? (
          // No status at all means this UI cannot reach the heim that served it (a stopped or
          // restarting server), which outranks every other fact in the line.
          <span className="truncate text-critical">Not connected to a heim server</span>
        ) : (
          <>
            <span className="hidden truncate sm:inline">{full}</span>
            <span className="truncate sm:hidden">{compact}</span>
          </>
        )}
      </div>
    </footer>
  );
}

import { useEffect, useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { type CheckStatus, type DoctorReport, type HealthCheck, overallStatus } from "@/api";
import { cn } from "@/lib/utils";

// The doctor's three verdicts, as the semantic word each of them is. `fail` is
// `--critical` rather than `--destructive`: it is a state heim observed, not a
// button that destroys something.
const DOT: Record<CheckStatus, string> = {
  ok: "bg-good",
  warn: "bg-warning",
  fail: "bg-critical",
};

const OVERALL_LABEL: Record<CheckStatus, string> = {
  ok: "All systems go",
  warn: "Warnings",
  fail: "Action needed",
};

/** Worst-first, so a parent row wears the verdict of the worst thing under it. */
const SEVERITY: Record<CheckStatus, number> = { ok: 0, warn: 1, fail: 2 };

/** One check plus whatever nests under it. */
interface CheckNode {
  check: HealthCheck;
  children: HealthCheck[];
}

/**
 * The report as a two-level tree, driven by each check's `group`.
 *
 * A check whose `group` names another check nests under it; everything else stays a top-level row
 * in the order the server sent it. THE STRUCTURE COMES FROM THE PAYLOAD AND NOTHING ELSE — not from
 * a naming convention, not from parsing a `detail` sentence, and not from this file knowing that
 * runtimes, the library and the tools happen to be the groups today. A backend that predates the
 * field (or one running ahead of this UI) simply sends no `group`, and every row renders flat
 * exactly as before: an unknown parent is not a reason to drop a check, because a health report
 * that silently omits a failing row is worse than one that is untidy.
 */
function tree(checks: HealthCheck[]): CheckNode[] {
  const names = new Set(checks.map((c) => c.name));
  const nodes: CheckNode[] = [];
  const byName = new Map<string, CheckNode>();
  for (const c of checks) {
    if (c.group != null && c.group !== c.name && names.has(c.group)) continue;
    const node: CheckNode = { check: c, children: [] };
    nodes.push(node);
    byName.set(c.name, node);
  }
  for (const c of checks) {
    if (c.group == null || c.group === c.name) continue;
    byName.get(c.group)?.children.push(c);
  }
  return nodes;
}

/** A node's verdict: its own, or the worst of anything nested under it. */
function verdict(node: CheckNode): CheckStatus {
  return [node.check, ...node.children].reduce<CheckStatus>(
    (worst, c) => (SEVERITY[c.status] > SEVERITY[worst] ? c.status : worst),
    node.check.status,
  );
}

/**
 * Window width below which a group expands in place instead of flying out.
 *
 * Arithmetic, not taste: a flyout needs the menu's own 320px, the panel's 288px, the 4px between
 * them, 8px of collision padding, and the ~50px the trigger sits in from the right edge of the
 * window — about 670. Under that there is no room on *either* side, and Radix does not rescue it:
 * `flip` picks whichever side overflows least and `shift` only ever moves a side-placed panel
 * vertically, so at 390px the panel lands three-quarters off-screen. A menu that runs off the
 * window is worse than one that reflows, so below the threshold the old accordion is the answer —
 * which is also what a menu on a phone-width screen is expected to do.
 */
const FLYOUT_MIN_WIDTH = 700;

/** Whether the window is wide enough for a flyout (live, so a resize switches behaviour). */
function useRoomForFlyout(): boolean {
  const query = `(min-width: ${FLYOUT_MIN_WIDTH}px)`;
  const [wide, setWide] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const sync = () => setWide(mq.matches);
    sync(); // the width may have changed between the first render and this effect
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, [query]);
  return wide;
}

/** One row: the dot, the name, the detail, and an optional hint. */
function CheckLine({ check, dot }: { check: HealthCheck; dot: CheckStatus }) {
  return (
    <>
      <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", DOT[dot])} />
      <div className="min-w-0 flex-1 text-left">
        <div className="text-sm">{check.name}</div>
        <div className="break-words text-sm text-muted-foreground">{check.detail}</div>
        {check.hint && <div className="break-words text-sm text-warning-ink">{check.hint}</div>}
      </div>
    </>
  );
}

/** The shared shape of a group row: the dot, the parent's own line, and a chevron. */
const GROUP_ROW = "flex w-full items-start gap-2.5 px-3 py-2 text-left [&>svg]:mt-0.5 [&>svg]:size-4 [&>svg]:shrink-0";

/** The rows of one group, as they appear inside a flyout panel or under an expanded row. */
function GroupChildren({ rows }: { rows: HealthCheck[] }) {
  return (
    <>
      {rows.map((c) => (
        <li key={c.name} className="flex items-start gap-2.5 px-3 py-2">
          <CheckLine check={c} dot={c.status} />
        </li>
      ))}
    </>
  );
}

/** A group as a hover-opened panel beside its row (see `FLYOUT_MIN_WIDTH` for when this is used). */
function FlyoutGroup({ node }: { node: CheckNode }) {
  return (
    <DropdownMenuSub>
      {/* rounded-none + full-width padding: these are rows in a list, not the pill-shaped items of a
          command menu. The chevron is shadcn's own, nudged onto the row's first line. */}
      <DropdownMenuSubTrigger className={cn(GROUP_ROW, "rounded-none")}>
        <CheckLine check={node.check} dot={verdict(node)} />
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent sideOffset={4} collisionPadding={8} className="w-72 p-0">
        {/* The panel names its own group: it is the one thing that still reads correctly if a
            future layout leaves it overlapping the row it came from. */}
        <div className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {node.check.name}
        </div>
        <ul className="max-h-[70vh] overflow-y-auto border-t border-border py-1">
          <GroupChildren rows={node.children} />
        </ul>
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  );
}

/** A group as a row that expands in place — the narrow-window fallback. */
function InlineGroup({ node }: { node: CheckNode }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={cn(GROUP_ROW, "transition-colors hover:bg-accent/50")}
      >
        <CheckLine check={node.check} dot={verdict(node)} />
        <ChevronRight className={cn("text-muted-foreground transition-transform", open && "rotate-90")} />
      </button>
      {open && (
        // Indented and hairlined rather than boxed: the same kind of row as the one above them, one
        // level in, not a different sort of thing.
        <ul className="ml-[1.4rem] border-l border-border">
          <GroupChildren rows={node.children} />
        </ul>
      )}
    </>
  );
}

/**
 * Top-bar system-health indicator: a status dot that opens a dropdown of the `heim doctor` checks —
 * same pattern as the model picker, so health lives in the bar rather than crammed into the
 * settings pane.
 *
 * THIS IS THE ONE PLACE THOSE FACTS ARE STATED. The status bar briefly carried a sentence naming
 * the backend, the model count and the tool count; all three are rows here, kept current by
 * `/api/doctor`, so the sentence was a second copy that could only ever drift. Nothing in this
 * component computes a fact — it renders what the doctor reports, in the order it reports it.
 *
 * TWO ANSWERS, THEN A SHELF. A menu of eight equal rows is a menu nobody reads: someone opening
 * this wants to know whether the backend is alive and which model is loaded, and the report is
 * shaped to put exactly those two at the top (see `doctor.py`) with everything else nested under a
 * handful of group parents. A parent wears the worst verdict beneath it (`verdict`), so a failure
 * inside a collapsed group still shows up as a coloured dot on the row you can see — collapsing
 * hides detail, never a fault.
 *
 * THE GROUPS FLY OUT, THEY DO NOT UNFOLD. They were an accordion, which pushed every row below the
 * one you clicked further down the menu and reflowed what you were reading. A Radix submenu opens on
 * hover, in its own portalled panel beside the row: nothing moves, and nothing lands on top of the
 * row it came from.
 *
 * It opens to the LEFT, and that is not a prop — Radix derives a submenu's side from the reading
 * direction and ignores `side` on `SubContent`. What decides it is collision detection, and the
 * layout decides that: the menu is pinned to the top-right of the window, so the gap to its right is
 * the width of an icon button or two and a 288px panel can never fit there, while the rest of the
 * window is to its left. Measured in a browser at 1440px — `data-side="left"`, panel right edge 3px
 * clear of the menu's left edge — not assumed. Where that reasoning stops holding is a window too
 * narrow for both panels at once, and there Radix does NOT keep the panel on screen; see
 * `FLYOUT_MIN_WIDTH` for what happens instead. The chevron stays a plain right-pointing "there is
 * more under here": which side the panel lands on is a function of the room available, so a
 * directional glyph would be a promise the layout is allowed to break.
 */
export function HealthMenu({ health }: { health: DoctorReport | null }) {
  const nodes = useMemo(() => (health ? tree(health.checks) : []), [health]);
  const flyout = useRoomForFlyout();
  if (!health) return null;
  const overall = overallStatus(health) ?? "ok";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="inline-flex h-8 w-8 items-center justify-center rounded-md transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`System health: ${OVERALL_LABEL[overall]}`}
        title={`System health: ${OVERALL_LABEL[overall]}`}
      >
        <span className={cn("h-2.5 w-2.5 rounded-full", DOT[overall])} />
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-80 p-0">
        {/* The verdict is the menu's headline, not a chip beside a title: "is this thing working"
            is the question that opens it, so it gets the one line here that is bold. */}
        <div className="px-3 py-2.5">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">System health</div>
          <div className="mt-1 flex items-center gap-2 text-sm font-semibold">
            <span className={cn("h-2 w-2 rounded-full", DOT[overall])} />
            {OVERALL_LABEL[overall]}
          </div>
        </div>
        <ul className="max-h-[70vh] overflow-y-auto border-t border-border py-1">
          {nodes.map((node) =>
            node.children.length > 0 ? (
              <li key={node.check.name}>
                {flyout ? <FlyoutGroup node={node} /> : <InlineGroup node={node} />}
              </li>
            ) : (
              <li key={node.check.name} className="flex items-start gap-2.5 px-3 py-2">
                <CheckLine check={node.check} dot={node.check.status} />
              </li>
            ),
          )}
        </ul>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

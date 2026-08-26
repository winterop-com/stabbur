import { useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";

import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
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
 * a naming convention, not from parsing a `detail` sentence. A backend that predates the field (or
 * one running ahead of this UI) simply sends no `group`, and every row renders flat exactly as
 * before: an unknown parent is not a reason to drop a check, because a health report that silently
 * omits a failing row is worse than one that is untidy.
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

/**
 * Top-bar system-health indicator: a status dot that opens a dropdown listing the `heim doctor`
 * checks (runtimes, library, project, MCP servers) — same pattern as the model picker, so health
 * lives in the bar rather than crammed into the settings pane.
 *
 * THIS IS THE ONE PLACE THOSE FACTS ARE STATED. The status bar briefly carried a sentence naming
 * the backend, the model count and the tool count; all three are rows here, kept current by
 * `/api/doctor`, so the sentence was a second copy that could only ever drift. Nothing in this
 * component computes a fact — it renders what the doctor reports.
 *
 * A COUNT IS NOT A LIST. "3 (datetime, network, files)" answers how many and leaves which-one to a
 * comma-separated sentence, so the servers nest under their parent row and expand to their own
 * checks — which is where a server that attached no tools, or failed to start, actually shows up.
 */
export function HealthMenu({ health }: { health: DoctorReport | null }) {
  // Collapsed by default: the parent row already carries the worst verdict under it, so a healthy
  // group costs one line and only a reader who wants the breakdown pays for it.
  const [open, setOpen] = useState<Set<string>>(new Set());
  const nodes = useMemo(() => (health ? tree(health.checks) : []), [health]);
  if (!health) return null;
  const overall = overallStatus(health) ?? "ok";
  const toggle = (name: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

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
        <div className="flex items-center justify-between px-3 py-2.5">
          <span className="text-sm font-semibold">System health</span>
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className={cn("h-2 w-2 rounded-full", DOT[overall])} />
            {OVERALL_LABEL[overall]}
          </span>
        </div>
        <ul className="max-h-[70vh] overflow-y-auto border-t border-border py-1">
          {nodes.map((node) => {
            const expandable = node.children.length > 0;
            const expanded = open.has(node.check.name);
            return (
              <li key={node.check.name}>
                {expandable ? (
                  <button
                    type="button"
                    onClick={() => toggle(node.check.name)}
                    aria-expanded={expanded}
                    className="flex w-full items-start gap-2.5 px-3 py-2 transition-colors hover:bg-accent/50"
                  >
                    <CheckLine check={node.check} dot={verdict(node)} />
                    <ChevronRight
                      className={cn(
                        "mt-0.5 h-4 w-4 shrink-0 text-muted-foreground transition-transform",
                        expanded && "rotate-90",
                      )}
                    />
                  </button>
                ) : (
                  <div className="flex items-start gap-2.5 px-3 py-2 transition-colors hover:bg-accent/50">
                    <CheckLine check={node.check} dot={node.check.status} />
                  </div>
                )}
                {expandable && expanded && (
                  // Indented and hairlined rather than boxed: these are the same kind of row as the
                  // one above them, one level in, not a different sort of thing.
                  <ul className="ml-[1.4rem] border-l border-border">
                    {node.children.map((c) => (
                      <li key={c.name} className="flex items-start gap-2.5 py-2 pl-3 pr-3 transition-colors hover:bg-accent/50">
                        <CheckLine check={c} dot={c.status} />
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { type CheckStatus, type DoctorReport, overallStatus } from "@/api";
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

/**
 * Top-bar system-health indicator: a status dot that opens a dropdown listing
 * the `heim doctor` checks (runtimes, library, project) — same pattern as the
 * model picker, so health lives in the bar, not crammed into the settings pane.
 */
export function HealthMenu({ health }: { health: DoctorReport | null }) {
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
        <div className="flex items-center justify-between px-3 py-2.5">
          <span className="text-sm font-semibold">System health</span>
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className={cn("h-2 w-2 rounded-full", DOT[overall])} />
            {OVERALL_LABEL[overall]}
          </span>
        </div>
        <ul className="max-h-[70vh] overflow-y-auto border-t border-border py-1">
          {health.checks.map((c) => (
            <li key={c.name} className="flex items-start gap-2.5 px-3 py-2 transition-colors hover:bg-accent/50">
              <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", DOT[c.status])} />
              <div className="min-w-0">
                <div className="text-sm">{c.name}</div>
                <div className="break-words text-[11px] text-muted-foreground">{c.detail}</div>
                {c.hint && (
                  <div className="break-words text-[11px] text-warning-ink">{c.hint}</div>
                )}
              </div>
            </li>
          ))}
        </ul>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

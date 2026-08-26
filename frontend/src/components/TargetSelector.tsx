import { Check, ChevronDown, Lock, Server } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { AssistantTarget } from "@/api";
import { cn } from "@/lib/utils";

/** A target's display label, falling back to its registry id when unnamed. */
function targetLabel(t: AssistantTarget): string {
  return t.name?.trim() || t.id;
}

/**
 * Inline target picker for a multi-target project ([[assistants]]): shows the selected target
 * (name + a read-only marker) and opens a menu to switch. The selection rides every chat turn as
 * `target`, so switching mid-conversation just re-routes the next turn (the server routes per turn,
 * lazily spawning a target's bridge on first use). Rendered only when the registry has >= 2 targets;
 * single-target and generic servers show nothing (App gates on `targets.length`).
 */
export function TargetSelector({
  targets,
  selectedId,
  onSelect,
}: {
  targets: AssistantTarget[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const selected = targets.find((t) => t.id === selectedId) ?? targets[0];
  if (!selected) return null;

  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger asChild>
          <DropdownMenuTrigger
            className={cn(
              "inline-flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            <Server className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="max-w-[14rem] truncate">{targetLabel(selected)}</span>
            {selected.readonly && <Lock className="h-3 w-3 text-muted-foreground" />}
            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
          </DropdownMenuTrigger>
        </TooltipTrigger>
        {selected.base_url && (
          <TooltipContent>
            <span className="break-all">{selected.base_url}</span>
          </TooltipContent>
        )}
      </Tooltip>

      <DropdownMenuContent align="start" collisionPadding={12} className="w-[22rem]">
        <div className="px-2 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Target
        </div>
        {targets.map((t) => {
          const active = t.id === selected.id;
          return (
            <Tooltip key={t.id}>
              <TooltipTrigger asChild>
                <DropdownMenuItem onSelect={() => onSelect(t.id)}>
                  <span className="flex-1 truncate">{targetLabel(t)}</span>
                  {t.readonly && (
                    <span className="ml-2 inline-flex items-center gap-1 text-xs text-muted-foreground">
                      <Lock className="h-3 w-3" />
                      read-only
                    </span>
                  )}
                  {/* Fixed slot so labels align whether or not a row is active. */}
                  <span className="ml-1 flex w-4 shrink-0 justify-center">
                    {active && <Check className="h-3.5 w-3.5 text-primary" />}
                  </span>
                </DropdownMenuItem>
              </TooltipTrigger>
              {t.base_url && (
                <TooltipContent side="right">
                  <span className="break-all">{t.base_url}</span>
                </TooltipContent>
              )}
            </Tooltip>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

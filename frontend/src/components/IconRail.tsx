import { AudioLines, Boxes, PanelLeft, SquarePen } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * The collapsed sidebar: a thin, always-visible icon rail (rather than hiding the
 * sidebar entirely). Keeps the primary destinations — new chat, Models, Voice —
 * one click away from any view and on mobile, and mirrors chapkit's
 * `collapsible="icon"` rail. Expanding restores the full sidebar.
 */
export function IconRail({
  view,
  onExpand,
  onNew,
  onShowLibrary,
  onShowVoice,
}: {
  view: "chat" | "library" | "voice";
  onExpand: () => void;
  onNew: () => void;
  onShowLibrary: () => void;
  onShowVoice: () => void;
}) {
  const item = (
    label: string,
    icon: React.ReactNode,
    onClick: () => void,
    active = false,
  ) => (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={onClick}
          aria-label={label}
          className={cn(
            "flex h-9 w-9 items-center justify-center rounded-lg transition-colors",
            active ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
          )}
        >
          {icon}
        </button>
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );

  return (
    <aside className="flex h-full w-12 shrink-0 flex-col items-center gap-1 border-r border-border bg-muted/40 py-3">
      {item("Expand sidebar", <PanelLeft className="h-4 w-4" />, onExpand)}
      <div className="my-1 h-px w-6 bg-border" />
      {item("New chat", <SquarePen className="h-4 w-4" />, onNew, view === "chat")}
      {item("Library", <Boxes className="h-4 w-4" />, onShowLibrary, view === "library")}
      {item("Voice", <AudioLines className="h-4 w-4" />, onShowVoice, view === "voice")}
    </aside>
  );
}

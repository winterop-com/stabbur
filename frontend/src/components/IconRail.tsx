import { AudioLines, Boxes, PanelLeft, SquarePen } from "lucide-react";

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
  voiceEnabled = true,
}: {
  view: "chat" | "library" | "voice";
  onExpand: () => void;
  onNew: () => void;
  onShowLibrary: () => void;
  onShowVoice: () => void;
  voiceEnabled?: boolean;
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
            // The same fills as the expanded rail's rows, on the same ground — collapsing the
            // sidebar must not change what "you are here" looks like. No reserved left border
            // here: at 36px square there is no text to hold still, and an edge marker on a
            // 48px rail reads as a stripe down the window rather than as part of the button.
            "flex h-9 w-9 items-center justify-center rounded-lg transition-colors",
            active
              ? "bg-sidebar-accent text-sidebar-accent-foreground"
              : "text-sidebar-muted-foreground hover:bg-sidebar-wash hover:text-sidebar-foreground",
          )}
        >
          {icon}
        </button>
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );

  return (
    <aside className="flex h-full w-12 shrink-0 flex-col items-center gap-1 border-r border-sidebar-border bg-sidebar py-3">
      {item("Expand sidebar", <PanelLeft className="h-4 w-4" />, onExpand)}
      <div className="my-1 h-px w-6 bg-sidebar-border" />
      {item("New chat", <SquarePen className="h-4 w-4" />, onNew, view === "chat")}
      {item("Library", <Boxes className="h-4 w-4" />, onShowLibrary, view === "library")}
      {voiceEnabled && item("Voice", <AudioLines className="h-4 w-4" />, onShowVoice, view === "voice")}
      {/* A tiny horizontal wordmark at the foot — it fits the narrow rail (unlike the full
          "Heim Studio") and keeps the brand present while collapsed. Settings is not here: the
          status bar's left segment shrinks to this rail's width and carries the gear itself, so
          putting one here too would stack two gears in the same corner. */}
      <div className="mt-auto flex flex-col items-center gap-1">
        <span className="pb-0.5 text-xs font-semibold tracking-tight text-sidebar-muted-foreground">heim</span>
      </div>
    </aside>
  );
}

import { AudioLines, Boxes, PanelLeft, Settings, SquarePen } from "lucide-react";

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
  onShowSettings,
  voiceEnabled = true,
}: {
  view: "chat" | "library" | "voice" | "settings";
  onExpand: () => void;
  onNew: () => void;
  onShowLibrary: () => void;
  onShowVoice: () => void;
  onShowSettings: () => void;
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
      {voiceEnabled && item("Voice", <AudioLines className="h-4 w-4" />, onShowVoice, view === "voice")}
      {/* Settings pinned at the bottom (mirrors the expanded sidebar), then a tiny horizontal
          wordmark — fits the narrow rail (unlike the full "Heim Studio") and keeps the brand
          present while collapsed. */}
      <div className="mt-auto flex flex-col items-center gap-1">
        {item("Settings", <Settings className="h-4 w-4" />, onShowSettings, view === "settings")}
        <span className="pb-0.5 text-[10px] font-semibold tracking-tight text-muted-foreground">heim</span>
      </div>
    </aside>
  );
}

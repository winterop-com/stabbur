import { useEffect, useMemo, useState } from "react";
import {
  AudioLines,
  Boxes,
  Download,
  Eraser,
  FileDown,
  MessagesSquare,
  Moon,
  PanelLeft,
  PanelRight,
  Palette,
  Settings,
  SquarePen,
  Sun,
} from "lucide-react";

import type { LibModel, Status } from "@/api";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandShortcut,
} from "@/components/ui/command";
import { THEME_PALETTES, type ThemePalette } from "@/lib/store";
import type { Conversation } from "@/lib/types";

/** Whether a keypress is the palette chord (Cmd+K on Apple, Ctrl+K elsewhere). */
export function opensPalette(e: KeyboardEvent): boolean {
  if (e.key !== "k" && e.key !== "K") return false;
  // Accept either modifier rather than sniffing the platform: Ctrl+K on a Mac is not bound
  // to anything here, and a user on an external PC keyboard reaches for Ctrl.
  return e.metaKey || e.ctrlKey;
}

export interface PaletteActions {
  onShowChat: () => void;
  onShowLibrary: () => void;
  onShowVoice: () => void;
  onShowSettings: () => void;
  onNewChat: () => void;
  onSelectConversation: (id: string) => void;
  onPickModel: (name: string) => void;
  onToggleSidebar: () => void;
  onToggleChatSettings: () => void;
  onToggleTheme: () => void;
  onChoosePalette: (palette: ThemePalette) => void;
  onClearChat: () => void;
  onExportMarkdown: () => void;
  onExportPdf: () => void;
}

/**
 * Cmd/Ctrl+K command palette: one keyboard surface for navigation, model switching,
 * recent conversations, and the view toggles — so the top bar doesn't have to carry a
 * row of icons for things used occasionally.
 *
 * Rows are plain `CommandItem`s rather than a catalogue abstraction: heim's action set is
 * small and static enough that indirection would cost more than it saves.
 */
export function CommandPalette({
  open,
  onOpenChange,
  status,
  library,
  conversations,
  theme,
  palette,
  voiceEnabled,
  hasConversation,
  actions,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  status: Status | null;
  library: LibModel[];
  conversations: Conversation[];
  theme: string;
  palette: ThemePalette;
  voiceEnabled: boolean;
  /** Whether there is an open conversation (gates clear/export rows). */
  hasConversation: boolean;
  actions: PaletteActions;
}) {
  const [query, setQuery] = useState("");

  // A fresh query each time it opens: the palette is a launcher, not a form to resume.
  useEffect(() => {
    if (open) setQuery("");
  }, [open]);

  const run = (fn: () => void) => () => {
    onOpenChange(false);
    fn();
  };

  // Most-recent first, and only a handful — the sidebar is the place to browse them all.
  const recents = useMemo(
    () => [...conversations].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 6),
    [conversations],
  );
  const loaded = status?.model ?? null;

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Command palette"
      description="Jump to a view, switch model, or open a recent chat"
    >
      <CommandInput
        value={query}
        onValueChange={setQuery}
        placeholder="Go to a view, switch model, open a recent chat…"
      />
      <CommandList>
        <CommandEmpty>Nothing matches that.</CommandEmpty>

        <CommandGroup heading="Go to">
          <CommandItem onSelect={run(actions.onShowChat)}>
            <MessagesSquare className="h-4 w-4 text-muted-foreground" />
            Chat
          </CommandItem>
          <CommandItem onSelect={run(actions.onShowLibrary)}>
            <Boxes className="h-4 w-4 text-muted-foreground" />
            Library
          </CommandItem>
          {voiceEnabled && (
            <CommandItem onSelect={run(actions.onShowVoice)}>
              <AudioLines className="h-4 w-4 text-muted-foreground" />
              Voice
            </CommandItem>
          )}
          <CommandItem onSelect={run(actions.onShowSettings)}>
            <Settings className="h-4 w-4 text-muted-foreground" />
            Settings
          </CommandItem>
        </CommandGroup>

        <CommandGroup heading="Chat">
          <CommandItem onSelect={run(actions.onNewChat)}>
            <SquarePen className="h-4 w-4 text-muted-foreground" />
            New chat
          </CommandItem>
          {hasConversation && (
            <>
              <CommandItem onSelect={run(actions.onClearChat)}>
                <Eraser className="h-4 w-4 text-muted-foreground" />
                Clear this conversation
              </CommandItem>
              <CommandItem onSelect={run(actions.onExportMarkdown)}>
                <FileDown className="h-4 w-4 text-muted-foreground" />
                Export as Markdown
              </CommandItem>
              <CommandItem onSelect={run(actions.onExportPdf)}>
                <Download className="h-4 w-4 text-muted-foreground" />
                Export as PDF
              </CommandItem>
            </>
          )}
        </CommandGroup>

        {library.length > 0 && (
          <CommandGroup heading="Switch model">
            {library.map((m) => (
              <CommandItem key={m.name} value={`model ${m.name}`} onSelect={run(() => actions.onPickModel(m.name))}>
                <Boxes className="h-4 w-4 text-muted-foreground" />
                <span className="truncate">{m.name.split("/").pop() ?? m.name}</span>
                <CommandShortcut>{m.name === loaded ? "loaded" : m.size_human}</CommandShortcut>
              </CommandItem>
            ))}
          </CommandGroup>
        )}

        {recents.length > 0 && (
          <CommandGroup heading="Recent chats">
            {recents.map((c) => (
              <CommandItem
                key={c.id}
                value={`chat ${c.title}`}
                onSelect={run(() => actions.onSelectConversation(c.id))}
              >
                <MessagesSquare className="h-4 w-4 text-muted-foreground" />
                <span className="truncate">{c.title}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}

        <CommandGroup heading="View">
          <CommandItem onSelect={run(actions.onToggleSidebar)}>
            <PanelLeft className="h-4 w-4 text-muted-foreground" />
            Toggle sidebar
          </CommandItem>
          <CommandItem onSelect={run(actions.onToggleChatSettings)}>
            <PanelRight className="h-4 w-4 text-muted-foreground" />
            Toggle chat settings
          </CommandItem>
          <CommandItem onSelect={run(actions.onToggleTheme)}>
            {theme === "dark" ? (
              <Sun className="h-4 w-4 text-muted-foreground" />
            ) : (
              <Moon className="h-4 w-4 text-muted-foreground" />
            )}
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </CommandItem>
        </CommandGroup>

        <CommandGroup heading="Theme">
          {THEME_PALETTES.map((p) => (
            <CommandItem key={p} value={`theme ${p}`} onSelect={run(() => actions.onChoosePalette(p))}>
              <Palette className="h-4 w-4 text-muted-foreground" />
              <span className="capitalize">{p}</span>
              {palette === p && <CommandShortcut>current</CommandShortcut>}
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}

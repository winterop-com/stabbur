import { useMemo } from "react";
import {
  AudioLines,
  Boxes,
  Download,
  FileDown,
  MessagesSquare,
  Moon,
  PanelLeft,
  PanelRight,
  Palette,
  Settings,
  SquarePen,
  Trash2,
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
import { paletteFilter, paletteRows, paletteShelves } from "@/lib/palette";
import { THEMES, type Theme } from "@/lib/store";
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
  onOpenSettings: () => void;
  onNewChat: () => void;
  onSelectConversation: (id: string) => void;
  onPickModel: (name: string) => void;
  onToggleSidebar: () => void;
  onToggleChatSettings: () => void;
  onToggleMode: () => void;
  onChooseTheme: (theme: Theme) => void;
  onDeleteChat: () => void;
  onExportMarkdown: () => void;
  onExportPdf: () => void;
}

/** How many recents the palette offers. The sidebar is the place to browse them all. */
const RECENTS_IN_PALETTE = 6;

/**
 * Cmd/Ctrl+K command palette: one keyboard surface for navigation, model switching,
 * recent conversations, and the view toggles — so the top bar doesn't have to carry a
 * row of icons for things used occasionally.
 *
 * THE ROWS ARE DATA AND THIS IS THE RENDERER. `lib/palette` answers what is offered and how well
 * each row answers what has been typed; this file knows what a row looks like and what choosing one
 * does, and nothing else. That split is not tidiness — cmdk's default filter is a fuzzy subsequence
 * match, which over heim's mix of sentences and model ids put three theme descriptions above
 * "Switch to dark mode" for the query `swit`. Scoring that lives in a pure module can be reasoned
 * about and asserted on; scoring inlined here could only ever be tried.
 */
export function CommandPalette({
  open,
  onOpenChange,
  status,
  library,
  conversations,
  mode,
  theme,
  voiceEnabled,
  hasConversation,
  actions,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  status: Status | null;
  library: LibModel[];
  conversations: Conversation[];
  mode: string;
  theme: Theme;
  voiceEnabled: boolean;
  /** Whether there is an open conversation (gates clear/export rows). */
  hasConversation: boolean;
  actions: PaletteActions;
}) {
  // Most-recent first, and only a handful.
  const recents = useMemo(
    () => [...conversations].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, RECENTS_IN_PALETTE),
    [conversations],
  );

  const rows = useMemo(
    () =>
      paletteRows({
        models: library,
        loaded: status?.model ?? null,
        recents,
        dark: mode === "dark",
        theme,
        themes: THEMES,
        voiceEnabled,
        hasConversation,
      }),
    [library, status?.model, recents, mode, theme, voiceEnabled, hasConversation],
  );
  const filter = useMemo(() => paletteFilter(rows), [rows]);
  const shelves = useMemo(() => paletteShelves(rows), [rows]);

  const run = (fn: () => void) => {
    onOpenChange(false);
    fn();
  };

  /** What choosing one row does. Keyed by the same ids `paletteRows` builds. */
  const select = (id: string) => {
    const [kind, rest] = splitId(id);
    switch (kind) {
      case "go":
        return run(rest === "library" ? actions.onShowLibrary : rest === "voice" ? actions.onShowVoice : actions.onShowChat);
      case "model":
        return run(() => actions.onPickModel(rest));
      case "recent":
        return run(() => actions.onSelectConversation(rest));
      case "theme":
        return run(() => actions.onChooseTheme(rest as Theme));
      case "chat":
        if (rest === "new") return run(actions.onNewChat);
        if (rest === "delete") return run(actions.onDeleteChat);
        if (rest === "export-markdown") return run(actions.onExportMarkdown);
        return run(actions.onExportPdf);
      default:
        if (rest === "sidebar") return run(actions.onToggleSidebar);
        if (rest === "chat-settings") return run(actions.onToggleChatSettings);
        if (rest === "mode") return run(actions.onToggleMode);
        return run(actions.onOpenSettings);
    }
  };

  /** The glyph one row wears. The mode row is the only one whose icon states a value, not a kind. */
  const icon = (id: string) => {
    const [kind, rest] = splitId(id);
    const cls = "h-4 w-4 shrink-0 text-muted-foreground";
    if (kind === "model") return <Boxes className={cls} />;
    if (kind === "recent") return <MessagesSquare className={cls} />;
    if (kind === "theme") return <Palette className={cls} />;
    if (kind === "go") return rest === "library" ? <Boxes className={cls} /> : rest === "voice" ? <AudioLines className={cls} /> : <MessagesSquare className={cls} />;
    if (kind === "chat")
      return rest === "new" ? (
        <SquarePen className={cls} />
      ) : rest === "delete" ? (
        <Trash2 className={cls} />
      ) : rest === "export-markdown" ? (
        <FileDown className={cls} />
      ) : (
        <Download className={cls} />
      );
    if (rest === "sidebar") return <PanelLeft className={cls} />;
    if (rest === "chat-settings") return <PanelRight className={cls} />;
    if (rest === "mode") return mode === "dark" ? <Sun className={cls} /> : <Moon className={cls} />;
    return <Settings className={cls} />;
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Command palette"
      description="Jump to a view, switch model, or open a recent chat"
      filter={filter}
    >
      <CommandInput placeholder="Go to a view, switch model, open a recent chat…" />
      <CommandList>
        <CommandEmpty>Nothing matches that.</CommandEmpty>
        {shelves.map((shelf) => (
          <CommandGroup key={shelf.group} heading={shelf.group}>
            {shelf.rows.map((r) => (
              // The value is the row's id and nothing else: it is what the filter looks the row up
              // by, and putting prose in it is what let cmdk's default matcher loose on sentences.
              <CommandItem key={r.id} value={r.id} onSelect={() => select(r.id)}>
                {icon(r.id)}
                <span className="min-w-0 shrink-0 truncate">{r.label}</span>
                {/* Truncates first and is gone entirely on a phone: the label and the trailing
                    marker are what the row is for, and neither may be pushed out by a sentence. */}
                {r.hint !== null && (
                  <span className="hidden min-w-0 truncate text-xs text-muted-foreground sm:inline">{r.hint}</span>
                )}
                {r.trailing !== null && <CommandShortcut>{r.trailing}</CommandShortcut>}
              </CommandItem>
            ))}
          </CommandGroup>
        ))}
      </CommandList>
    </CommandDialog>
  );
}

/** `"model:mlx-community/gemma"` -> `["model", "mlx-community/gemma"]`. The rest may contain colons. */
function splitId(id: string): [string, string] {
  const i = id.indexOf(":");
  return i < 0 ? [id, ""] : [id.slice(0, i), id.slice(i + 1)];
}
